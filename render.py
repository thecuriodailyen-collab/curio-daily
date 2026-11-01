"""Curio Daily - renders one vertical 1080x1920 Short from a fact script.

Usage:
    python render.py --id 1 --out out/video.mp4            # with voice (edge-tts, needs internet)
    python render.py --id 1 --out out/video.mp4 --no-voice # silent preview, estimated timings
"""
import argparse, asyncio, json, math, os, random, subprocess, tempfile, wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent
W, H, FPS = 1080, 1920, 30
CFG = json.loads((ROOT / "config.json").read_text())
def _font_path(name):
    for d in (ROOT / "fonts", Path("/usr/share/fonts/opentype/inter"), Path("/usr/share/fonts/truetype/inter")):
        if (d / name).exists():
            return str(d / name)
    raise SystemExit(f"Font {name} not found (install fonts-inter)")


FONT_BLACK = _font_path("InterDisplay-Black.otf")
FONT_BOLD = _font_path("InterDisplay-Bold.otf")
FONT_SEMI = _font_path("InterDisplay-SemiBold.otf")
GAP = 0.28  # pause between sentences (s)


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


C_BG = hex2rgb(CFG["colors"]["bg"])
C_TEXT = hex2rgb(CFG["colors"]["text"])
C_ACCENT = hex2rgb(CFG["colors"]["accent"])
C_MUTED = hex2rgb(CFG["colors"]["muted"])

_font_cache = {}


def font(path, size):
    key = (path, int(size))
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, int(size))
    return _font_cache[key]


# ---------------------------------------------------------------- narration
def segments_for(fact):
    """Ordered list of (kind, text) that will be spoken."""
    segs = [("hook", fact["hook"])]
    segs += [("beat", b) for b in fact["beats"]]
    segs.append(("question", fact["question"]))
    return segs


VOICE_FALLBACKS = ["en-US-AndrewNeural", "en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-AriaNeural"]


def tts(text, path):
    """Try the configured voice, then fallbacks, with retries."""
    import time
    voices = [CFG["voice"]] + [v for v in VOICE_FALLBACKS if v != CFG["voice"]]
    last = None
    for attempt in range(3):
        for v in voices:
            try:
                words = asyncio.run(_tts_segment(text, path, v))
                if os.path.getsize(path) > 0:
                    return words
            except Exception as e:  # NoAudioReceived, network hiccups...
                last = e
                print(f"TTS failed with {v}: {e}")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"All TTS voices failed: {last}")


async def _tts_segment(text, path, voice):
    import edge_tts
    try:
        com = edge_tts.Communicate(text, voice, rate=CFG["voice_rate"], boundary="WordBoundary")
    except TypeError:  # older edge-tts versions
        com = edge_tts.Communicate(text, voice, rate=CFG["voice_rate"])
    words = []
    with open(path, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append([chunk["text"], start, start + chunk["duration"] / 1e7])
    return words


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    return float(out.stdout.strip())


def estimate_words(text, t0):
    """Fallback timings (~2.7 words/s weighted by length)."""
    words, t = [], t0
    for w in text.split():
        d = 0.16 + 0.055 * len(w)
        words.append([w, t, t + d])
        t += d
    return words, t


def build_timeline(fact, workdir, voice=True):
    """Returns (timeline, total_duration, voice_wav_or_None).
    timeline = list of segments {kind, start, end, words:[[w,s,e],...]}"""
    timeline, t, parts = [], 0.35, []
    for i, (kind, text) in enumerate(segments_for(fact)):
        if voice:
            mp3 = os.path.join(workdir, f"seg{i}.mp3")
            words = tts(text, mp3)
            dur = probe_duration(mp3)
            if not words:  # boundary events missing -> estimate inside real duration
                est, end = estimate_words(text, 0)
                k = dur / max(end, 0.01)
                words = [[w, s * k, e * k] for w, s, e in est]
            words = [[w, s + t, e + t] for w, s, e in words]
            parts.append((mp3, t))
            end = t + dur
        else:
            words, end = estimate_words(text, t)
        timeline.append({"kind": kind, "text": text, "start": t, "end": end, "words": words})
        t = end + GAP
    total = t + 0.9  # breathing room for the loop
    voice_wav = None
    if voice:
        voice_wav = os.path.join(workdir, "voice.wav")
        inputs, filters = [], []
        for j, (mp3, off) in enumerate(parts):
            inputs += ["-i", mp3]
            filters.append(f"[{j}:a]adelay={int(off*1000)}|{int(off*1000)}[a{j}]")
        mix = "".join(f"[a{j}]" for j in range(len(parts)))
        filters.append(f"{mix}amix=inputs={len(parts)}:normalize=0,apad,atrim=0:{total:.3f}[out]")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex",
                        ";".join(filters), "-map", "[out]", "-ar", "44100", "-ac", "2", voice_wav], check=True)
    return timeline, total, voice_wav


# ---------------------------------------------------------------- music (original, generated)
def make_music(path, duration, seed):
    sr = 44100
    rng = random.Random(seed)
    roots = [57, 53, 60, 55]  # A F C G
    rng.shuffle(roots)
    n = int(sr * duration)
    t = np.arange(n) / sr
    out = np.zeros(n)
    bar = 2.4
    for k in range(int(duration / bar) + 1):
        root = roots[k % 4]
        s, e = int(k * bar * sr), min(n, int((k + 1) * bar * sr + 0.4 * sr))
        if s >= n:
            break
        tt = t[s:e] - k * bar
        env = np.minimum(1, tt / 0.6) * np.exp(-tt * 0.35)
        for iv in (0, 7, 12, 16 if k % 2 else 15):
            f = 440 * 2 ** ((root + iv - 69) / 12)
            out[s:e] += env * (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(4 * np.pi * f * tt)) * 0.12
        # soft pulse
        for b in range(4):
            ps = s + int(b * bar / 4 * sr)
            pe = min(n, ps + int(0.12 * sr))
            if ps < n:
                pt = np.arange(pe - ps) / sr
                out[ps:pe] += 0.05 * np.sin(2 * np.pi * 110 * pt) * np.exp(-pt * 40)
    out /= max(1e-6, np.abs(out).max())
    fade = int(sr * 0.8)
    out[-fade:] *= np.linspace(1, 0, fade)
    data = (out * 32767 * 0.9).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(data.tobytes())


# ---------------------------------------------------------------- drawing helpers
def make_blob(color, radius):
    size = radius * 2
    y, x = np.mgrid[0:size, 0:size]
    d = np.sqrt((x - radius) ** 2 + (y - radius) ** 2) / radius
    a = np.clip(1 - d, 0, 1) ** 2.2
    img = np.zeros((size, size, 4), np.uint8)
    img[..., :3] = color
    img[..., 3] = (a * 255 * 0.55).astype(np.uint8)
    return Image.fromarray(img, "RGBA")


def make_background(cat_color):
    bg = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(bg)
    for yy in range(0, H, 54):
        for xx in range(0, W, 54):
            d.ellipse((xx - 1.5, yy - 1.5, xx + 1.5, yy + 1.5), fill=(28, 34, 50))
    return bg


def wrap(text, fnt, max_w, draw):
    lines, cur = [], []
    for w in text.split():
        test = " ".join(cur + [w])
        if cur and draw.textlength(test, font=fnt) > max_w:
            lines.append(cur); cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def ease_out(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def pill(draw, x, y, text, fnt, fill, txt_fill, pad=(26, 12), anchor="l"):
    tw = draw.textlength(text, font=fnt)
    asc, desc = fnt.getmetrics()
    w, h = tw + pad[0] * 2, asc + desc * 0.3 + pad[1] * 2
    if anchor == "r":
        x -= w
    elif anchor == "m":
        x -= w / 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h / 2, fill=fill)
    draw.text((x + pad[0], y + pad[1] - 2), text, font=fnt, fill=txt_fill)
    return w, h


def chunk_words(words, max_words=3, max_chars=16):
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or len(" ".join(x[0] for x in cur + [w])) > max_chars
                    or cur[-1][0][-1:] in ".,!?;:"):
            chunks.append(cur); cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------- frame renderer
class Renderer:
    def __init__(self, fact, timeline, total):
        self.f, self.tl, self.total = fact, timeline, total
        self.cat_color = hex2rgb(CFG["category_colors"].get(fact["category"], CFG["colors"]["accent"]))
        self.bg = make_background(self.cat_color)
        self.blob1 = make_blob(self.cat_color, 520)
        self.blob2 = make_blob(C_ACCENT, 380)
        rng = random.Random(fact["id"])
        self.phase = rng.random() * 6.28
        for seg in timeline:
            seg["chunks"] = chunk_words(seg["words"]) if seg["kind"] == "beat" else None
        self.beats_start = next((s["start"] for s in timeline if s["kind"] == "beat"), 0)
        self.q_start = next(s["start"] for s in timeline if s["kind"] == "question")

    def frame(self, t):
        im = self.bg.copy()
        # drifting colour blobs
        p = self.phase
        x1 = int(W * 0.25 + 220 * math.sin(t * 0.35 + p)) - 520
        y1 = int(H * 0.30 + 160 * math.cos(t * 0.27 + p)) - 520
        x2 = int(W * 0.80 + 180 * math.cos(t * 0.31 + p)) - 380
        y2 = int(H * 0.78 + 200 * math.sin(t * 0.22 + p)) - 380
        im.paste(self.blob1, (x1, y1), self.blob1)
        im.paste(self.blob2, (x2, y2), self.blob2)
        d = ImageDraw.Draw(im)

        # progress bar
        d.rectangle((0, 0, W, 14), fill=(30, 36, 52))
        d.rectangle((0, 0, int(W * min(1, t / self.total)), 14), fill=C_ACCENT)

        # header
        fb = font(FONT_BLACK, 44)
        d.text((80, 170), "CURIO", font=fb, fill=C_TEXT)
        cw = d.textlength("CURIO ", font=fb)
        d.text((80 + cw, 170), "DAILY", font=fb, fill=C_ACCENT)
        pill(d, W - 80, 166, f"FACT #{self.f['id']:03d}", font(FONT_BOLD, 34), (255, 255, 255, 30) if False else (34, 40, 58), C_TEXT, anchor="r")
        pill(d, 80, 246, self.f["category"].upper(), font(FONT_BOLD, 30), self.cat_color, C_BG)

        seg = next((s for s in self.tl if s["start"] - 0.05 <= t < s["end"] + GAP), None)
        if t < self.tl[0]["start"]:
            seg = self.tl[0]
        if t >= self.q_start:
            self.draw_question(d, t)
        elif seg is None or seg["kind"] == "hook":
            self.draw_hook(d, t)
        else:
            self.draw_stat(d, t)
            self.draw_captions(d, seg, t)
        return im

    def draw_hook(self, d, t):
        seg = self.tl[0]
        fnt = font(FONT_BLACK, 104)
        lines = wrap(seg["text"].upper(), fnt, W - 170, d)
        if len(lines) > 5:
            fnt = font(FONT_BLACK, 86)
            lines = wrap(seg["text"].upper(), fnt, W - 170, d)
        lh = fnt.size * 1.12
        y = 820 - len(lines) * lh / 2
        idx = 0
        spoken = sum(1 for w in seg["words"] if w[1] <= t)
        # spoken words light up in accent
        for li, line in enumerate(lines):
            x = 85
            for w in line:
                col = C_ACCENT if idx < spoken else C_TEXT
                d.text((x, y), w, font=fnt, fill=col)
                x += d.textlength(w + " ", font=fnt)
                idx += 1
            y += lh
        # underline swoosh
        k = ease_out((t - 0.2) / 0.6)
        d.rounded_rectangle((85, y + 30, 85 + int(260 * k), y + 44), radius=7, fill=self.cat_color)

    def draw_stat(self, d, t):
        k = ease_out((t - self.beats_start) / 0.45)
        if k <= 0:
            return
        big = self.f["stat"]["big"]
        fnt = font(FONT_BLACK, int(230 * (0.85 + 0.15 * k)))
        while d.textlength(big, font=fnt) > W - 170:
            fnt = font(FONT_BLACK, fnt.size - 10)
        y = 470 + (1 - k) * 60
        d.text((W / 2, y), big, font=fnt, fill=self.cat_color, anchor="mm")
        lab = self.f["stat"]["label"].upper()
        d.text((W / 2, y + fnt.size * 0.62), lab, font=font(FONT_BOLD, 40), fill=C_MUTED, anchor="mm")

    def draw_captions(self, d, seg, t):
        chunks = seg["chunks"]
        cur = chunks[0]
        for ch in chunks:
            if ch[0][1] - 0.02 <= t:
                cur = ch
        local = t - cur[0][1]
        pop = 0.86 + 0.14 * ease_out(local / 0.12)
        fnt = font(FONT_BLACK, 118 * pop)
        words = [w[0].upper() for w in cur]
        lines = wrap(" ".join(words), fnt, W - 180, d)
        lh = fnt.size * 1.1
        y = 1080 - len(lines) * lh / 2
        idx = 0
        for line in lines:
            lw = d.textlength(" ".join(line), font=fnt)
            x = (W - lw) / 2
            for w in line:
                active = cur[idx][1] <= t < cur[idx][2] + 0.08
                col = C_ACCENT if active else C_TEXT
                # shadow for readability
                d.text((x + 5, y + 7), w, font=fnt, fill=(0, 0, 0))
                d.text((x, y), w, font=fnt, fill=col)
                x += d.textlength(w + " ", font=fnt)
                idx += 1
            y += lh

    def draw_question(self, d, t):
        k = ease_out((t - self.q_start) / 0.4)
        y0 = 560 + (1 - k) * 80
        # speech bubble icon
        cx = W / 2
        d.rounded_rectangle((cx - 90, y0, cx + 90, y0 + 130), radius=40, fill=C_ACCENT)
        d.polygon([(cx - 40, y0 + 125), (cx - 70, y0 + 175), (cx + 5, y0 + 125)], fill=C_ACCENT)
        for i in (-45, 0, 45):
            d.ellipse((cx + i - 13, y0 + 52, cx + i + 13, y0 + 78), fill=C_BG)
        pill(d, W / 2, y0 + 220, "COMMENT BELOW", font(FONT_BOLD, 36), self.cat_color, C_BG, anchor="m")
        fnt = font(FONT_BLACK, 92)
        q = self.tl[-1]
        lines = wrap(q["text"].upper(), fnt, W - 180, d)
        y = y0 + 360
        spoken = sum(1 for w in q["words"] if w[1] <= t)
        idx = 0
        for line in lines:
            lw = d.textlength(" ".join(line), font=fnt)
            x = (W - lw) / 2
            for w in line:
                d.text((x, y), w, font=fnt, fill=C_ACCENT if idx < spoken else C_TEXT)
                x += d.textlength(w + " ", font=fnt)
                idx += 1
            y += fnt.size * 1.12
        d.text((W / 2, y + 70), "FOLLOW FOR A NEW FACT EVERY DAY", font=font(FONT_BOLD, 36), fill=C_MUTED, anchor="mm")


# ---------------------------------------------------------------- main
def render(fact, out_path, voice=True):
    out_path = Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        timeline, total, voice_wav = build_timeline(fact, tmp, voice)
        music = os.path.join(tmp, "music.wav")
        make_music(music, total, fact["id"])
        r = Renderer(fact, timeline, total)
        n = int(total * FPS)
        silent = os.path.join(tmp, "video.mp4")
        proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                 "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
                                 "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", silent],
                                stdin=subprocess.PIPE)
        for i in range(n):
            proc.stdin.write(r.frame(i / FPS).tobytes())
        proc.stdin.close(); proc.wait()
        mv = CFG["music_volume_db"]
        if voice_wav:
            fc = f"[2:a]volume={mv}dB[m];[1:a][m]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5[a]"
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", voice_wav, "-i", music,
                   "-filter_complex", fc, "-map", "0:v", "-map", "[a]"]
        else:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", music,
                   "-filter_complex", f"[1:a]volume={mv + 12}dB[a]", "-map", "0:v", "-map", "[a]"]
        subprocess.run(cmd + ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                              "-movflags", "+faststart", str(out_path)], check=True)
    return total


def load_fact(fid):
    for f in json.loads((ROOT / "facts.json").read_text()):
        if f["id"] == fid:
            return f
    raise SystemExit(f"fact {fid} not found")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, default=1)
    ap.add_argument("--fact-json", help="path to a single fact json (overrides --id)")
    ap.add_argument("--out", default="out/video.mp4")
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    fact = json.loads(Path(a.fact_json).read_text()) if a.fact_json else load_fact(a.id)
    dur = render(fact, a.out, voice=not a.no_voice)
    print(f"rendered {a.out} ({dur:.1f}s)")
