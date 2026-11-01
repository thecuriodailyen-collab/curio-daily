"""Curio Daily - dynamic scene renderer (v2): big animated emoji per scene,
TikTok-style word pop captions, count-up stats, transitions, camera moves, SFX."""
import math, random, re, wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import render as R

W, H = R.W, R.H
EMOJI_FONT = None
for p in ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", str(R.ROOT / "fonts/NotoColorEmoji.ttf")):
    if Path(p).exists():
        EMOJI_FONT = p
        break
CAT_EMOJI = {"Space": "🪐", "Animals": "🐾", "History": "🏛️", "Human Body": "🧠", "Science": "🔬",
             "Geography": "🌍", "Nature": "🌳", "Food": "🍽️", "Language": "💬", "Technology": "💡"}


def ease_out_back(x, s=1.9):
    x = max(0.0, min(1.0, x)) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def ease_out(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


_emoji_cache = {}


def emoji_img(ch, size):
    key = (ch, size)
    if key in _emoji_cache:
        return _emoji_cache[key]
    img = None
    if EMOJI_FONT:
        f = ImageFont.truetype(EMOJI_FONT, 109)
        im = Image.new("RGBA", (220, 160), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((10, 10), ch, font=f, embedded_color=True)
        bb = im.getbbox()
        if bb:
            img = im.crop(bb)
    if img is None:  # fallback: big letter in a circle
        img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((0, 0, 199, 199), fill=R.C_ACCENT)
        d.text((100, 100), "?", font=R.font(R.FONT_BLACK, 140), fill=R.C_BG, anchor="mm")
    k = size / max(img.size)
    img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    # soft dark shadow
    sh = Image.new("RGBA", (img.width + 60, img.height + 60), (0, 0, 0, 0))
    alpha = img.split()[3].point(lambda a: int(a * 0.45))
    blk = Image.new("RGBA", img.size, (0, 0, 0, 255))
    blk.putalpha(alpha)
    sh.paste(blk, (38, 46), blk)
    from PIL import ImageFilter
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    sh.paste(img, (30, 30), img)
    _emoji_cache[key] = sh
    return sh


def make_rays(color, size=1500, n=14):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size / 2
    for i in range(n):
        a0 = 2 * math.pi * i / n
        a1 = a0 + math.pi / n * 0.8
        d.polygon([(c, c), (c + c * 1.5 * math.cos(a0), c + c * 1.5 * math.sin(a0)),
                   (c + c * 1.5 * math.cos(a1), c + c * 1.5 * math.sin(a1))], fill=color + (26,))
    # fade edges with radial mask
    y, x = np.mgrid[0:size, 0:size]
    r = np.sqrt((x - c) ** 2 + (y - c) ** 2) / c
    mask = (np.clip(1 - r, 0, 1) ** 1.3 * 255).astype(np.uint8)
    a = np.array(im.split()[3]).astype(np.float32) * mask / 255
    im.putalpha(Image.fromarray(a.astype(np.uint8)))
    return im


def parse_number(s):
    m = re.search(r"\d[\d,\.]*", s)
    if not m:
        return None
    raw = m.group(0)
    try:
        val = float(raw.replace(",", ""))
    except ValueError:
        return None
    return m.start(), m.end(), raw, val


def fmt_like(raw, v):
    if "." in raw and "," not in raw:
        dec = len(raw.split(".")[1])
        return f"{v:.{dec}f}"
    s = f"{int(round(v)):,}" if "," in raw else str(int(round(v)))
    return s


class Scenes:
    def __init__(self, fact, timeline, total):
        self.f, self.tl, self.total = fact, timeline, total
        self.cat = R.hex2rgb(R.CFG["category_colors"].get(fact["category"], R.CFG["colors"]["accent"]))
        emo = list(fact.get("emoji") or [])
        base = CAT_EMOJI.get(fact["category"], "✨")
        while len(emo) < len(timeline):
            emo.append(base)
        self.emo = emo
        self.bg = R.make_background(self.cat)
        self.blob = R.make_blob(self.cat, 560)
        self.blob2 = R.make_blob(R.C_ACCENT, 360)
        self.rays = make_rays(self.cat)
        self.stat_scene = 2 if len(timeline) > 3 else 1  # second beat shows the stat
        rnd = random.Random(fact["id"])
        self.layout = [rnd.choice([-1, 1]) for _ in timeline]
        for s in timeline:
            s["chunks"] = R.chunk_words(s["words"]) if s["kind"] == "beat" else None
        self.starts = [max(0.0, s["start"] - 0.12) for s in timeline]
        self.starts[0] = 0.0
        # real stock footage per scene (falls back to the graphic background)
        import footage
        ends = self.starts[1:] + [total]
        durs = [max(0.5, e - s) for s, e in zip(self.starts, ends)]
        paths = footage.fetch(fact, durs)
        fact["_pexels"] = any(paths)
        self.clips = [footage.Clip(p, d) if p else None for p, d in zip(paths, durs)]
        self.shade = footage.make_shade() if any(self.clips) else None
        self.cur_clip = None

    def scene_at(self, t):
        i = 0
        for k, st in enumerate(self.starts):
            if t >= st:
                i = k
        return i

    # ---------------------------------------------------------------- frame
    def frame(self, t):
        i = self.scene_at(t)
        seg = self.tl[i]
        local = t - self.starts[i]
        clip = self.clips[i]
        if clip is not self.cur_clip and self.cur_clip is not None:
            self.cur_clip.close()
        self.cur_clip = clip
        real = clip.next() if clip else None
        if real is not None:
            im = real.copy()
            im.paste(self.shade, (0, 0), self.shade)
        else:
            im = self.bg.copy()
            # moving blobs, side alternates per scene
            side = self.layout[i]
            bx = int(W / 2 + side * 260 + 120 * math.sin(t * 0.6)) - 560
            by = int(620 + 90 * math.cos(t * 0.5)) - 560
            im.paste(self.blob, (bx, by), self.blob)
            im.paste(self.blob2, (int(W / 2 - side * 330) - 360, 1500 - 360), self.blob2)
            # rotating rays behind the visual
            rays = self.rays.rotate(t * 12 % 360, resample=Image.BILINEAR)
            im.paste(rays, (W // 2 - rays.width // 2, 640 - rays.height // 2), rays)
        d = ImageDraw.Draw(im)

        kind = seg["kind"]
        if kind == "question":
            self.draw_question(im, d, seg, local, t)
        else:
            if i == self.stat_scene and kind == "beat":
                self.draw_stat(d, local)
            elif real is None:  # with real footage the video itself is the illustration
                self.draw_emoji(im, self.emo[i], local, t, big=(kind == "hook"))
            if kind == "hook":
                self.draw_hook(d, seg, t)
            else:
                self.draw_caption(d, seg, t)
        self.draw_header(d, t, i)
        self.draw_whoosh(d, local, i)

        # camera: slow push-in per scene + shake on the hook
        z = 1.0 + 0.045 * ease_out(local / 2.5)
        if z > 1.001:
            nw, nh = int(W * z), int(H * z)
            im = im.resize((nw, nh), Image.BILINEAR)
            ox, oy = (nw - W) // 2, (nh - H) // 2
            if kind == "hook" and t < 0.35:
                ox += int(10 * math.sin(t * 90)); oy += int(8 * math.cos(t * 77))
            im = im.crop((ox, oy, ox + W, oy + H))
        return im

    # ---------------------------------------------------------------- parts
    def draw_header(self, d, t, i):
        n = len(self.tl)
        gap, x0, w = 10, 40, (W - 80 - 10 * (n - 1)) / n
        for k in range(n):
            x = x0 + k * (w + gap)
            d.rounded_rectangle((x, 26, x + w, 36), radius=5, fill=(40, 46, 64))
            if k < i:
                frac = 1
            elif k == i:
                end = self.starts[k + 1] if k + 1 < n else self.total
                frac = min(1, (t - self.starts[k]) / max(0.1, end - self.starts[k]))
            else:
                frac = 0
            if frac > 0:
                d.rounded_rectangle((x, 26, x + max(10, w * frac), 36), radius=5, fill=R.C_ACCENT)
        fb = R.font(R.FONT_BLACK, 44)
        d.text((80, 170), "CURIO", font=fb, fill=R.C_TEXT)
        d.text((80 + d.textlength("CURIO ", font=fb), 170), "DAILY", font=fb, fill=R.C_ACCENT)
        R.pill(d, W - 80, 166, f"FACT #{self.f['id']:03d}", R.font(R.FONT_BOLD, 34), (34, 40, 58), R.C_TEXT, anchor="r")
        R.pill(d, 80, 246, self.f["category"].upper(), R.font(R.FONT_BOLD, 30), self.cat, R.C_BG)

    def draw_whoosh(self, d, local, i):
        if i == 0 or local > 0.22:
            return
        k = local / 0.22
        x = -400 + k * (W + 800)
        d.polygon([(x - 180, 0), (x + 60, 0), (x + 260, H), (x + 20, H)], fill=self.cat)
        d.polygon([(x + 80, 0), (x + 130, 0), (x + 330, H), (x + 280, H)], fill=R.C_ACCENT)

    def draw_emoji(self, im, ch, local, t, big=False):
        size = 330 if big else 420
        e = emoji_img(ch, size)
        s = max(0.05, ease_out_back(local / 0.38))
        bob = 14 * math.sin(t * 2.4)
        ang = 5 * math.sin(t * 1.7)
        img = e.rotate(ang, resample=Image.BICUBIC, expand=True)
        if abs(s - 1) > 0.01:
            img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.BILINEAR)
        cy = 470 if big else 640
        im.paste(img, (W // 2 - img.width // 2, int(cy + bob - img.height / 2)), img)

    def draw_stat(self, d, local):
        big, label = self.f["stat"]["big"], self.f["stat"]["label"].upper()
        k = ease_out(local / 0.9)
        shown = big
        num = parse_number(big)
        if num:
            a, b, raw, val = num
            shown = big[:a] + fmt_like(raw, val * k) + big[b:]
        pop = ease_out_back(local / 0.35)
        size = int(210 * max(0.3, pop))
        fnt = R.font(R.FONT_BLACK, size)
        while d.textlength(big, font=R.font(R.FONT_BLACK, size)) > W - 160 and size > 60:
            size -= 8
        fnt = R.font(R.FONT_BLACK, size)
        # burst ring
        rr = 120 + 260 * ease_out(local / 0.6)
        alpha_w = max(0, int(14 * (1 - local / 0.6)))
        if alpha_w:
            d.ellipse((W / 2 - rr, 600 - rr, W / 2 + rr, 600 + rr), outline=self.cat, width=alpha_w)
        d.text((W / 2 + 6, 610), shown, font=fnt, fill=(0, 0, 0), anchor="mm")
        d.text((W / 2, 600), shown, font=fnt, fill=R.C_ACCENT, anchor="mm")
        R.pill(d, W / 2, 600 + size * 0.62, label, R.font(R.FONT_BOLD, 38), self.cat, R.C_BG, anchor="m")

    def draw_hook(self, d, seg, t):
        fnt = R.font(R.FONT_BLACK, 96)
        lines = R.wrap(seg["text"].upper(), fnt, W - 160, d)
        if len(lines) > 4:
            fnt = R.font(R.FONT_BLACK, 80)
            lines = R.wrap(seg["text"].upper(), fnt, W - 160, d)
        lh = fnt.size * 1.12
        y = 1020 - len(lines) * lh / 2 + 60
        spoken = sum(1 for w in seg["words"] if w[1] <= t)
        idx = 0
        for line in lines:
            lw = d.textlength(" ".join(line), font=fnt)
            x = (W - lw) / 2
            for w in line:
                on = idx < spoken
                if on and idx == spoken - 1:
                    tw = d.textlength(w, font=fnt)
                    d.rounded_rectangle((x - 12, y - 4, x + tw + 12, y + fnt.size * 1.02), radius=18, fill=R.C_ACCENT)
                    d.text((x, y), w, font=fnt, fill=R.C_BG)
                else:
                    d.text((x + 5, y + 6), w, font=fnt, fill=(0, 0, 0))
                    d.text((x, y), w, font=fnt, fill=R.C_ACCENT if on else R.C_TEXT)
                x += d.textlength(w + " ", font=fnt)
                idx += 1
            y += lh

    def draw_caption(self, d, seg, t):
        chunks = seg["chunks"]
        cur = chunks[0]
        for ch in chunks:
            if ch[0][1] - 0.02 <= t:
                cur = ch
        base = 112
        fnt = R.font(R.FONT_BLACK, base)
        words = [w[0].upper() for w in cur]
        lines = R.wrap(" ".join(words), fnt, W - 160, d)
        lh = base * 1.15
        y = 1150 - len(lines) * lh / 2
        idx = 0
        for line in lines:
            lw = d.textlength(" ".join(line), font=fnt)
            x = (W - lw) / 2
            for w in line:
                ws, we = cur[min(idx, len(cur) - 1)][1], cur[min(idx, len(cur) - 1)][2]
                if t < ws - 0.03:  # not spoken yet: hidden (words appear as they are said)
                    x += d.textlength(w + " ", font=fnt)
                    idx += 1
                    continue
                pop = ease_out_back((t - ws + 0.03) / 0.16)
                sz = max(10, int(base * (0.55 + 0.45 * pop)))
                f2 = R.font(R.FONT_BLACK, sz)
                tw = d.textlength(w, font=fnt)
                cx, cy = x + tw / 2, y + base * 0.5
                active = ws <= t < we + 0.06
                is_num = bool(re.search(r"\d", w))
                if active:
                    bw = d.textlength(w, font=f2)
                    d.rounded_rectangle((cx - bw / 2 - 14, cy - sz * 0.55, cx + bw / 2 + 14, cy + sz * 0.55), radius=20, fill=R.C_ACCENT)
                    d.text((cx, cy), w, font=f2, fill=R.C_BG, anchor="mm")
                else:
                    d.text((cx + 5, cy + 7), w, font=f2, fill=(0, 0, 0), anchor="mm")
                    d.text((cx, cy), w, font=f2, fill=R.C_ACCENT if is_num else R.C_TEXT, anchor="mm")
                x += d.textlength(w + " ", font=fnt)
                idx += 1
            y += lh

    def draw_question(self, im, d, seg, local, t):
        if self.clips[-1] is None:
            self.draw_emoji(im, self.emo[-1], local, t, big=True)
        y0 = 760
        R.pill(d, W / 2, y0, "COMMENT BELOW", R.font(R.FONT_BOLD, 38), self.cat, R.C_BG, anchor="m")
        fnt = R.font(R.FONT_BLACK, 90)
        lines = R.wrap(seg["text"].upper(), fnt, W - 170, d)
        y = y0 + 120
        spoken = sum(1 for w in seg["words"] if w[1] <= t)
        idx = 0
        for line in lines:
            lw = d.textlength(" ".join(line), font=fnt)
            x = (W - lw) / 2
            for w in line:
                d.text((x + 5, y + 6), w, font=fnt, fill=(0, 0, 0))
                d.text((x, y), w, font=fnt, fill=R.C_ACCENT if idx < spoken else R.C_TEXT)
                x += d.textlength(w + " ", font=fnt)
                idx += 1
            y += fnt.size * 1.12
        k = ease_out((local - 0.6) / 0.4)
        if k > 0:
            d.text((W / 2, y + 60), "FOLLOW FOR A NEW FACT EVERY DAY", font=R.font(R.FONT_BOLD, 36),
                   fill=tuple(int(c * k) for c in R.C_MUTED), anchor="mm")


# ---------------------------------------------------------------- sound effects (generated)
def make_sfx(path, total, scene_starts, stat_time=None):
    sr = 44100
    n = int(sr * total)
    out = np.zeros(n)
    rng = np.random.default_rng(7)
    for k, st in enumerate(scene_starts):
        if k > 0:  # whoosh
            L = int(0.32 * sr)
            s0 = int(st * sr)
            noise = rng.standard_normal(L)
            # simple moving-average band shaping that sweeps
            env = np.sin(np.linspace(0, math.pi, L)) ** 2
            kern = np.ones(6) / 6
            wsh = np.convolve(noise, kern, mode="same") * env * 0.35
            e = min(n, s0 + L)
            out[s0:e] += wsh[: e - s0]
        # pop when the visual appears
        L = int(0.09 * sr)
        s0 = int((st + 0.05) * sr)
        tt = np.arange(L) / sr
        f = np.linspace(900, 300, L)
        pop = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-tt * 45) * 0.5
        e = min(n, s0 + L)
        if s0 < n:
            out[s0:e] += pop[: e - s0]
    if stat_time is not None:  # rising tick for the counter
        for j in range(10):
            s0 = int((stat_time + j * 0.08) * sr)
            L = int(0.03 * sr)
            tt = np.arange(L) / sr
            tick = np.sin(2 * np.pi * (1200 + 80 * j) * tt) * np.exp(-tt * 120) * 0.25
            e = min(n, s0 + L)
            if s0 < n:
                out[s0:e] += tick[: e - s0]
    out = np.clip(out, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((out * 32767 * 0.8).astype(np.int16).tobytes())
