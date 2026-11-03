"""测音频里一个音节的音高曲线，判断声调够不够标准（给 gen_audio.py 用）。

需要 praat-parselmouth 和 numpy（pip install praat-parselmouth numpy）以及 ffmpeg。
曲线用半音表示（相对 100Hz），萌萌女童的高音大约 26–28，低音大约 8–12。
"""
import os, subprocess, tempfile
import numpy as np

def contour(path, which="first"):
    """第一个（first）/ 最后一个（last）/ 最长（longest）有声段的音高，单位半音"""
    import parselmouth
    wav = tempfile.mktemp(suffix=".wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-ac", "1", "-ar", "24000", wav], check=True)
    snd = parselmouth.Sound(wav); os.remove(wav)
    f = snd.to_pitch(time_step=0.01, pitch_floor=120, pitch_ceiling=700).selected_array["frequency"]
    segs, cur, gap = [], [], 0
    for i, v in enumerate(f):           # 有声段，允许 2 帧以内的空洞
        if v > 0:
            cur.append(i); gap = 0
        elif cur:
            gap += 1
            if gap > 2:
                segs.append(cur); cur = []; gap = 0
    if cur: segs.append(cur)
    segs = [s for s in segs if len(s) >= 8]
    if not segs: return None
    seg = {"first": segs[0], "last": segs[-1]}.get(which) or max(segs, key=len)
    v = f[seg[0]:seg[-1] + 1]
    return 12 * np.log2(v[v > 0] / 100.0)

def describe(st):
    n = len(st); k = max(2, n // 5)
    return dict(start=float(st[:k].mean()), end=float(st[-k:].mean()), min=float(st.min()),
                min_at=float(st.argmin() / max(1, n - 1)), range=float(st.max() - st.min()), mean=float(st.mean()))

def ok(tone, d):
    """够不够标准：一声高平，二声从低处直接上扬，三声先降后升、最低点低，四声从高处一路降"""
    a, z, lo, at, rg = d["start"], d["end"], d["min"], d["min_at"], d["range"]
    if tone == 1: return abs(z - a) <= 2.5 and rg <= 4 and d["mean"] >= 22
    if tone == 2: return z - a >= 5 and at <= 0.45 and a - lo <= 2.5
    if tone == 3: return 0.25 <= at <= 0.8 and a - lo >= 2.5 and z - lo >= 3 and lo <= 15
    if tone == 4: return a - z >= 7 and at >= 0.85 and a >= 23
    return True

def score(tone, d):
    """不够标准时挑最接近的"""
    a, z, lo, at, rg = d["start"], d["end"], d["min"], d["min_at"], d["range"]
    if tone == 1: return -abs(z - a) * 2 - rg + d["mean"] / 4
    if tone == 2: return (z - a) - max(0, at - 0.3) * 10 - max(0, a - lo - 1.5) * 2
    if tone == 3: return min(a - lo, z - lo) * 2 - abs(at - 0.5) * 8 - lo / 4
    if tone == 4: return (a - z) - max(0, 0.85 - at) * 10
    return 0
