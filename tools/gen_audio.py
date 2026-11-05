#!/usr/bin/env python3
"""用 MiniMax 语音合成生成预录音频，写入 audio/<voice>/ 和 audio/manifest.js。

用法（在仓库根目录）：
    python3 tools/gen_audio.py                 # 只补缺的文件
    python3 tools/gen_audio.py --dry-run       # 只列出要生成什么
    python3 tools/gen_audio.py --recheck-tones # 已有的字也重新测声调，不标准的重做

读音检查（装了 pypinyin 时自动开启）：用 MiniMax 语音识别听一遍，确认念的音节对
（比如 ü 曾被念成 yī）；识别出同音字也算对，只比音节不比声调。
声调检查（装了 praat-parselmouth 时自动开启，见 tools/tones.py）：单念一个字时，
语音合成常把一声念成升调或降调、三声只降不升。所以每个字的音频生成后测一下开头那个字的
音高曲线，不标准就换写法（标点、语气、语速）重做，直到标准，实在不行用最接近的那一版。

需要：能访问 api.minimax.cn，并带上 MiniMax 密钥——
环境变量 MINIMAX_API_KEY，或云端环境的 Network secrets（代理自动加 Authorization）。
文本从 index.html 里读（题库 ALL、PRAISE、LESSON_MSG、EXAM_MSG、GOAL_MSG、TONE_DEMO、LETTERS），
文件名是 fnv1a(key) 的 36 进制，算法和 index.html 里的 fnv()/clipKey() 一致。
"""
import json, os, re, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.minimax.cn/v1/t2a_v2"
MODEL = "speech-2.8-hd"

# 声音：目录名 -> MiniMax 音色。目录名带版本号，换音色或参数时改版本，避免浏览器缓存旧文件
VOICES = {"girl-v1": "lovely_girl"}
VOICE = "girl-v1"

# 指定读音也管不住的单字：单独念的那一下改用同音、没有多音的字去合成（孩子只听声音）。
# 熟 单念曾读成 shóu，后来按家长要求换掉了课本没教过另一个读音的常见多音字，所以现在是空的
SAY_AS = {}

ITEM_SPEED, SAY_SPEED = 0.8, 1.0


def fnv(s):
    h = 0x811C9DC5
    for ch in s:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    d, out = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while True:
        h, r = divmod(h, 36)
        out = d[r] + out
        if not h:
            return out


def page_data():
    """在 node 里跑 index.html 的脚本（DOM 打桩），取出题库和各种提示语"""
    html = open(os.path.join(ROOT, "index.html"), encoding="utf8").read()
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    stub = r"""
const mem={};global.localStorage={getItem:k=>mem[k]??null,setItem:(k,v)=>mem[k]=String(v)};
global.navigator={userAgent:'node',platform:'x',maxTouchPoints:0};
const el={innerHTML:'',querySelectorAll:()=>[],onclick:null,hidden:false,textContent:''};
global.document={getElementById:()=>el,querySelectorAll:()=>[],addEventListener(){},createElement:()=>({...el,remove(){}}),body:{appendChild(){}}};
global.window={};
"""
    tail = ";console.log(JSON.stringify({items:ALL.map(i=>({c:i.c,w:i.w,p:i.p})),PRAISE,LESSON_MSG,EXAM_MSG,GOAL_MSG,TONE_DEMO,LETTERS}));"
    out = subprocess.run(["node", "-e", stub + js + tail], capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


# 单字声调不标准时依次试的写法：(单字后的标点, 语气, 语速)
ITEM_TRIES = [("。", None, 0.8), ("。", "calm", 0.8), ("！", None, 0.8), ("？", None, 0.8), ("。", "happy", 0.8),
              ("！", "calm", 0.8), ("？", "calm", 0.8), ("。", None, 0.95), ("！", "happy", 0.95),
              ("？", "happy", 0.95), ("。", "calm", 0.95), ("？", None, 0.95)]
# 声调小课堂：「第一声，妈。」这样带引导语念，比单念一个字准（测最后那个音节）
DEMO_TRIES = [("happy", 0.95), ("happy", 0.75), (None, 0.95), (None, 0.75), ("calm", 0.95), ("calm", 0.75), ("happy", 0.85)]


def jobs():
    d = page_data()
    seen, js = set(), []
    for it in d["items"]:
        key = f"item|{it['c']}|{it['p']}|{it['w']}"
        if key in seen:
            continue
        seen.add(key)
        a = SAY_AS.get(it["c"], it["c"])
        # 每个字都写死读音（ü 的写法接口文档没说，带 v 的不写）
        pron = [f"{it['c']}/({it['p']})"] if "v" not in it["p"] else None
        tries = [dict(text=f"{a}{pu}<#0.8#>{it['w']}。<#0.8#>{it['w']}的{a}。", emotion=e, speed=sp) for pu, e, sp in ITEM_TRIES]
        js.append(dict(key=key, pron=pron, tone=int(it["p"][-1]), syl=it["p"][:-1], seg="first", tries=tries, **tries[0]))
    for i, t in enumerate(d["TONE_DEMO"]):
        tries = [dict(text=f"第{'一二三四'[i]}声，{t['c']}。", emotion=e, speed=sp) for e, sp in DEMO_TRIES]
        js.append(dict(key=f"say|{t['c']}", pron=[f"{t['c']}/(ma{i + 1})"], tone=i + 1, seg="last", tries=tries, **tries[0]))  # 引导语在前，不做读音识别
    # 听音连连看：声母、韵母的呼读音（用一个同音字合成，读音写死，也要过声调检查）
    for x in d["LETTERS"]:
        tries = [dict(text=f"{x['c']}{pu}", emotion=e, speed=sp) for pu, e, sp in ITEM_TRIES]
        js.append(dict(key=f"letter|{x['l']}", pron=[f"{x['c']}/({x['p']})"], tone=int(x["p"][-1]), syl=x["p"][:-1], seg="first", tries=tries, **tries[0]))
    says = d["PRAISE"] + d["LESSON_MSG"] + d["EXAM_MSG"] + [d["GOAL_MSG"]]
    says += [f"连对{n}题！" for n in range(3, 31)]
    says += [f"{n}分！" for n in range(0, 101)]
    for t in says:
        t = re.sub(r"\s+", "", t)
        js.append(dict(key=f"say|{t}", text=t, pron=None, speed=SAY_SPEED, emotion="happy"))
    return js


def t2a(job, voice_id):
    body = {"model": MODEL, "text": job["text"], "stream": False, "language_boost": "Chinese",
            "voice_setting": {"voice_id": voice_id, "speed": job["speed"], "vol": 1, "pitch": 0},
            "audio_setting": {"sample_rate": 24000, "bitrate": 64000, "format": "mp3", "channel": 1}}
    if job["emotion"]:
        body["voice_setting"]["emotion"] = job["emotion"]
    if job["pron"]:
        body["pronunciation_dict"] = {"tone": job["pron"]}
    headers = {"Content-Type": "application/json"}
    if os.environ.get("MINIMAX_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["MINIMAX_API_KEY"]
    for attempt in range(6):
        try:
            req = urllib.request.Request(API, data=json.dumps(body).encode(), headers=headers)
            d = json.load(urllib.request.urlopen(req, timeout=90))
            br = d.get("base_resp", {})
            if br.get("status_code") == 0:
                return bytes.fromhex(d["data"]["audio"])
            err = br
            if br.get("status_code") == 1002:  # 每分钟请求数限流：等一会儿再试
                time.sleep(20 * (attempt + 1))
                continue
        except Exception as e:  # 网络抖动：退避重试
            err = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{job['key']}: {err}")


def asr_text(path):
    """MiniMax 语音识别，返回识别出的文字"""
    b = "----pinyin" + str(time.time_ns())
    body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                    for k, v in [("model", "asr-1.0"), ("response_format", "json")])
    body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.mp3"\r\n'
             f"Content-Type: audio/mpeg\r\n\r\n").encode() + open(path, "rb").read() + f"\r\n--{b}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={b}", "language": "zh"}
    if os.environ.get("MINIMAX_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["MINIMAX_API_KEY"]
    for attempt in range(6):
        try:
            req = urllib.request.Request(API.replace("t2a_v2", "speech_to_text"), data=body, headers=headers)
            return json.load(urllib.request.urlopen(req, timeout=90)).get("text", "")
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
        except Exception:
            pass
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("语音识别失败")


def heard_ok(path, syllable):
    """识别出的第一个字有没有这个音节的读音（同音字也算）"""
    from pypinyin import pinyin, Style
    text = asr_text(path)
    ch = next((c for c in text if "\u4e00" <= c <= "\u9fff"), None)
    if not ch:
        return False, text
    reads = {re.sub(r"\d", "", x).replace("ü", "v") for x in pinyin(ch, heteronym=True, style=Style.TONE3)[0]}
    return syllable in reads, text


def write_manifest():
    adir = os.path.join(ROOT, "audio")
    m = {v: sorted(f[:-4] for f in os.listdir(os.path.join(adir, v)) if f.endswith(".mp3"))
         for v in sorted(os.listdir(adir)) if os.path.isdir(os.path.join(adir, v))}
    with open(os.path.join(adir, "manifest.js"), "w", encoding="utf8") as f:
        f.write("/* 由 tools/gen_audio.py 生成，不要手改 */\nwindow.AUDIO_CLIPS=" + json.dumps(m, separators=(",", ":")) + ";\n")
    return m


def main():
    outdir = os.path.join(ROOT, "audio", VOICE)
    os.makedirs(outdir, exist_ok=True)
    all_jobs = jobs()
    # 题库或提示语改掉后不再用到的旧文件：删掉
    keep = {fnv(j["key"]) + ".mp3" for j in all_jobs}
    stale = [f for f in os.listdir(outdir) if f.endswith(".mp3") and f not in keep]
    if "--dry-run" not in sys.argv:
        for f in stale:
            os.remove(os.path.join(outdir, f))
    print(f"{VOICE}: 删掉 {len(stale)} 个不再用的文件")
    try:
        import tones
        tones.contour  # noqa
    except ImportError:
        tones = None
        print("没装 praat-parselmouth，不检查声调")
    try:
        import pypinyin  # noqa
        asr = True
    except ImportError:
        asr = False
        print("没装 pypinyin，不做读音识别检查")
    recheck = tones and "--recheck-tones" in sys.argv
    todo = [j for j in all_jobs if not os.path.exists(os.path.join(outdir, fnv(j["key"]) + ".mp3"))
            or (recheck and j.get("tone"))]
    print(f"{VOICE}: {len(todo)} 个文件要生成，约 {sum(len(j['text']) for j in todo)} 字")
    if "--dry-run" in sys.argv:
        for j in todo:
            print(fnv(j["key"]), j["key"], j["pron"] or "")
        return

    def check(j, path):
        st = tones.contour(path, j["seg"])
        if st is None:
            return False, -99
        d = tones.describe(st)
        good, sc = tones.ok(j["tone"], d), tones.score(j["tone"], d)
        if asr and j.get("syl"):  # 音节念错比声调不准更糟：直接压到最低分
            right, heard = heard_ok(path, j["syl"])
            if not right:
                print(f"  {j['key']} 听成了「{heard}」", flush=True)
                return False, sc - 100
        return good, sc

    def run(j):
        path = os.path.join(outdir, fnv(j["key"]) + ".mp3")
        if not (tones and j.get("tone")):
            data = t2a(j, VOICES[VOICE])
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
            return j["key"], "ok"
        best = None
        if os.path.exists(path):  # --recheck-tones：已有的够标准就留着
            good, sc = check(j, path)
            if good:
                return j["key"], "声调标准，保留"
            best = (False, sc, open(path, "rb").read(), "原来的")
        for n, tr in enumerate(j["tries"], 1):
            data = t2a({**j, **tr}, VOICES[VOICE])
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            good, sc = check(j, path + ".tmp")
            if best is None or (good, sc) > best[:2]:  # 达标的优先，其次看分数
                best = (good, sc, data, f"第{n}种写法")
            if good:
                break
        with open(path + ".tmp", "wb") as f:
            f.write(best[2])
        os.replace(path + ".tmp", path)
        return j["key"], ("声调标准，用" if best[0] else "都不够标准，用最接近的") + best[3]

    fails = []
    with ThreadPoolExecutor(2) as ex:
        for fut in [ex.submit(run, j) for j in todo]:
            try:
                k, msg = fut.result()
                print("ok", k, msg, flush=True)
            except Exception as e:
                fails.append(str(e))
                print("FAIL", e)
    m = write_manifest()
    print("manifest:", {v: len(x) for v, x in m.items()}, "失败:", len(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
