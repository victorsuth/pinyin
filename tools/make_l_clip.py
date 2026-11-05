"""l 的读音（letter|l|了|le1，家长选定）是这样做出来的：
单念 lē 总被合成成 lēi（「嘞」），所以念「乐，快乐的乐。」，剪出最后一个「乐」
（在响度最低处和前面的「的」分开），再用 Praat 把音高拉平成一声
（≈395Hz，和 b m d g 一样高）。家长选的是 c 那一版。
用法：venv/bin/python tools/make_l_clip.py   （需要 praat-parselmouth、ffmpeg、MiniMax 密钥）
结果在临时目录里，试听后手工复制到 audio/girl-v1/<fnv('letter|l|了|le1')>.mp3
同样的办法可以用来修别的单念念不准的音。"""
import sys, os, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parselmouth
from parselmouth.praat import call
from gen_audio import t2a, asr_text
from tones import contour, describe, ok

D = tempfile.mkdtemp(prefix='l-clip-')
print('输出目录', D)
TARGET_HZ = 395

def to_wav(mp3):
    w = tempfile.mktemp(suffix='.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', mp3, '-ac', '1', '-ar', '24000', w], check=True)
    return w

def last_voiced(snd):
    f = snd.to_pitch(time_step=0.01, pitch_floor=120, pitch_ceiling=700).selected_array['frequency']
    segs, cur, gap = [], [], 0
    for i, v in enumerate(f):
        if v > 0: cur.append(i); gap = 0
        elif cur:
            gap += 1
            if gap > 2: segs.append(cur); cur = []; gap = 0
    if cur: segs.append(cur)
    s = [x for x in segs if len(x) >= 6][-1]
    return s[0] * 0.01, s[-1] * 0.01

for tag, e, sp, stretch in [('a', None, 0.8, 1.0), ('b', None, 0.8, 1.25), ('c', 'calm', 0.8, 1.15)]:
    src = f'{D}/src-{tag}.mp3'
    if not os.path.exists(src):
        open(src, 'wb').write(t2a(dict(key='l', text='乐，<#0.4#>快乐的乐。', pron=['乐/(le4)'], emotion=e, speed=sp), 'lovely_girl'))
    snd = parselmouth.Sound(to_wav(src))
    t0, t1 = last_voiced(snd)
    # 「的乐」连在一起：在这段里找响度最低的地方（的 和 乐 之间），从那里开始剪
    inten = snd.to_intensity(time_step=0.005)
    ts = [t for t in inten.xs() if t0 + 0.05 < t < t1 - 0.15]
    if ts:
        t0 = min(ts, key=lambda t: inten.get_value(t))
    part = snd.extract_part(from_time=max(0, t0 - 0.03), to_time=t1 + 0.04, preserve_times=False)
    dur = part.get_total_duration()
    man = call(part, 'To Manipulation', 0.01, 120, 700)
    pt = call(man, 'Extract pitch tier')
    call(pt, 'Remove points between', 0, dur)
    call(pt, 'Add point', 0.0, TARGET_HZ); call(pt, 'Add point', dur, TARGET_HZ)   # 一声：高而平
    call([pt, man], 'Replace pitch tier')
    if stretch != 1.0:   # 拉长一点，听得清
        dt = call(man, 'Extract duration tier'); call(dt, 'Add point', 0, stretch); call(dt, 'Add point', dur, stretch)
        call([man, dt], 'Replace duration tier')
    out = call(man, 'Get resynthesis (overlap-add)')
    call(out, 'Scale intensity', 70)
    wav = f'{D}/l-{tag}.wav'; out.save(wav, 'WAV')
    mp3 = f'{D}/l-{tag}.mp3'
    # 前后各补 0.15 秒静音、末尾淡出，和别的字母录音差不多
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', wav, '-af', 'adelay=150,apad=pad_dur=0.15,afade=t=out:st=%.2f:d=0.06' % (0.15 + dur * stretch - 0.04),
                    '-ac', '1', '-ar', '24000', '-c:a', 'libmp3lame', '-b:a', '64k', mp3], check=True)
    d = describe(contour(mp3))
    print(tag, f'剪出 {t0:.2f}-{t1:.2f}s', '原句识别:', asr_text(src), '| 剪后识别:', asr_text(mp3),
          '| 一声' + ('达标' if ok(1, d) else '不达标'), f"{d['start']:.1f}→{d['end']:.1f} 范围{d['range']:.1f}", flush=True)
