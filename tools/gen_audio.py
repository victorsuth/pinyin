#!/usr/bin/env python3
"""用 MiniMax 语音合成生成预录音频，写入 audio/<voice>/ 和 audio/manifest.js。

用法（在仓库根目录）：
    python3 tools/gen_audio.py            # 只补缺的文件
    python3 tools/gen_audio.py --dry-run  # 只列出要生成什么

需要：能访问 api.minimax.cn，并带上 MiniMax 密钥——
环境变量 MINIMAX_API_KEY，或云端环境的 Network secrets（代理自动加 Authorization）。
文本从 index.html 里读（题库 ALL、PRAISE、LESSON_MSG、EXAM_MSG、GOAL_MSG），
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

# 单念一个字时容易读错的多音字：强制按题库里的拼音读
POLY = set("大地发熟说叶区雨刷家句少色看长")

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
    tail = ";console.log(JSON.stringify({items:ALL.map(i=>({c:i.c,w:i.w,p:i.p})),PRAISE,LESSON_MSG,EXAM_MSG,GOAL_MSG,TONE_DEMO}));"
    out = subprocess.run(["node", "-e", stub + js + tail], capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def jobs():
    d = page_data()
    seen, js = set(), []
    for it in d["items"]:
        key = f"item|{it['c']}|{it['p']}|{it['w']}"
        if key in seen:
            continue
        seen.add(key)
        a = SAY_AS.get(it["c"], it["c"])
        text = f"{a}。<#0.8#>{it['w']}。<#0.8#>{it['w']}的{a}。"
        pron = [f"{it['c']}/({it['p']})"] if it["c"] in POLY else None
        js.append(dict(key=key, text=text, pron=pron, speed=ITEM_SPEED, emotion=None))
    # 声调小课堂：mā má mǎ mà，平静、慢一点，读音写死
    for i, t in enumerate(d["TONE_DEMO"]):
        js.append(dict(key=f"say|{t['c']}", text=t["c"], pron=[f"{t['c']}/(ma{i + 1})"], speed=ITEM_SPEED, emotion=None))
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
    todo = [j for j in all_jobs if not os.path.exists(os.path.join(outdir, fnv(j["key"]) + ".mp3"))]
    print(f"{VOICE}: {len(todo)} 个文件要生成，约 {sum(len(j['text']) for j in todo)} 字")
    if "--dry-run" in sys.argv:
        for j in todo:
            print(fnv(j["key"]), j["key"], j["pron"] or "")
        return

    def run(j):
        path = os.path.join(outdir, fnv(j["key"]) + ".mp3")
        data = t2a(j, VOICES[VOICE])
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
        return j["key"], len(data)

    fails = []
    with ThreadPoolExecutor(2) as ex:
        for fut in [ex.submit(run, j) for j in todo]:
            try:
                k, n = fut.result()
                print("ok", k, n)
            except Exception as e:
                fails.append(str(e))
                print("FAIL", e)
    m = write_manifest()
    print("manifest:", {v: len(x) for v, x in m.items()}, "失败:", len(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
