"""Real stock footage behind each scene, from the free Pixabay API (or Pexels).

Needs the PIXABAY_API_KEY (or PEXELS_API_KEY) environment variable. Without it (or if anything fails)
the video falls back to the animated graphic background, so a missing key never
breaks the daily post.
"""
import os, subprocess, tempfile
from pathlib import Path

import numpy as np
import requests
from PIL import Image

W, H, FPS = 1080, 1920, 30
API = "https://api.pexels.com/videos/search"


def _pick_file(video):
    files = [f for f in video.get("video_files", []) if f.get("link") and f.get("height")]
    files = [f for f in files if f.get("file_type", "video/mp4") == "video/mp4"] or files
    if not files:
        return None
    # smallest portrait file that is still at least ~1280 tall, else the biggest one
    good = sorted([f for f in files if f["height"] >= 1280 and f["height"] <= 2600 and f["height"] >= f["width"]],
                  key=lambda f: f["height"])
    return (good or sorted(files, key=lambda f: f["height"], reverse=True))[0]["link"]


def search(query, key, min_dur, used):
    r = requests.get(API, headers={"Authorization": key}, timeout=20,
                     params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 15})
    r.raise_for_status()
    vids = [v for v in r.json().get("videos", []) if v["id"] not in used]
    # prefer clips long enough for the scene, then the shortest of those (tighter, more relevant)
    long_enough = [v for v in vids if v.get("duration", 0) >= min_dur]
    for v in long_enough + [v for v in vids if v not in long_enough]:
        link = _pick_file(v)
        if link:
            return v["id"], link
    return None, None


def search_pixabay(query, key, min_dur, used):
    r = requests.get("https://pixabay.com/api/videos/", timeout=20,
                     params={"key": key, "q": query[:100], "safesearch": "true", "per_page": 20, "video_type": "film"})
    r.raise_for_status()
    hits = [h for h in r.json().get("hits", []) if h["id"] not in used]
    # portrait first, then clips long enough for the scene
    hits.sort(key=lambda h: (not any(v.get("height", 0) > v.get("width", 1) for v in h["videos"].values()),
                             h.get("duration", 0) < min_dur))
    for h in hits:
        vs = [v for v in h["videos"].values() if v.get("url")]
        good = sorted([v for v in vs if min(v["width"], v["height"]) >= 720], key=lambda v: v.get("size", 0))
        pick = (good or sorted(vs, key=lambda v: v.get("width", 0), reverse=True))
        if pick:
            return h["id"], pick[0]["url"]
    return None, None


def fetch(fact, durations, workdir=None):
    """Return one local mp4 path (or None) per scene."""
    pix = os.environ.get("PIXABAY_API_KEY", "").strip()
    key = pix or os.environ.get("PEXELS_API_KEY", "").strip()
    finder = search_pixabay if pix else search
    fact["_source"] = "Pixabay" if pix else "Pexels"
    queries = list(fact.get("footage") or [])
    if not key or not queries:
        return [None] * len(durations)
    workdir = Path(workdir or tempfile.mkdtemp(prefix="pexels_"))
    while len(queries) < len(durations):
        queries.append(queries[0])
    used, out = set(), []
    for i, (q, dur) in enumerate(zip(queries, durations)):
        path = None
        for attempt in (q, " ".join(q.split()[:1]), fact.get("category", "")):
            if not attempt:
                continue
            try:
                vid, link = finder(attempt, key, dur, used)
                if not vid:
                    continue
                path = workdir / f"scene{i}_{vid}.mp4"
                with requests.get(link, stream=True, timeout=60) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as fh:
                        for chunk in resp.iter_content(1 << 20):
                            fh.write(chunk)
                used.add(vid)
                print(f"  footage scene {i}: '{attempt}' -> {fact['_source']} {vid}")
                break
            except Exception as e:
                print(f"  footage scene {i}: '{attempt}' failed ({e})")
                path = None
        out.append(str(path) if path else None)
    return out


class Clip:
    """Streams frames of one clip, scaled/cropped to 1080x1920 @30fps, looped if too short."""

    def __init__(self, path, duration):
        self.path, self.dur = path, duration
        self.proc, self.last = None, None

    def _open(self):
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
              "eq=saturation=1.12:contrast=1.04")
        self.proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-stream_loop", "-1", "-i", self.path, "-t", f"{self.dur + 1:.2f}",
             "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def next(self):
        if self.proc is None:
            self._open()
        buf = self.proc.stdout.read(W * H * 3)
        if len(buf) == W * H * 3:
            self.last = Image.frombuffer("RGB", (W, H), buf, "raw", "RGB", 0, 1).copy()
        return self.last

    def close(self):
        if self.proc:
            self.proc.stdout.close(); self.proc.kill(); self.proc.wait()
            self.proc = None


def make_shade():
    """Dark gradient so white captions stay readable over any footage."""
    y = np.linspace(0, 1, H)[:, None]
    a = 0.45 + 0.40 * np.clip((0.22 - y) / 0.22, 0, 1) + 0.30 * np.clip((y - 0.45) / 0.4, 0, 1)
    a = (np.clip(a, 0, 0.85) * 255).astype(np.uint8)
    rgba = np.zeros((H, W, 4), np.uint8)
    rgba[..., 3] = np.repeat(a, W, axis=1)
    return Image.fromarray(rgba, "RGBA")
