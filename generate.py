"""Picks today's fact. Uses the hand-written bank first, then asks Claude for a new one."""
import json, os, re
from pathlib import Path

import requests

ROOT = Path(__file__).parent
BANK = ROOT / "facts.json"
GENERATED = ROOT / "generated.json"
STATE = ROOT / "posted.json"
CATEGORIES = ["Space", "Animals", "History", "Human Body", "Science", "Geography", "Nature", "Food", "Language", "Technology"]

PROMPT = """You write scripts for "Curio Daily", a YouTube Shorts / Facebook Reels channel that teaches one surprising general-knowledge fact per day, in simple spoken English.

Write ONE new fact in the category "{category}". It must be:
- TRUE and well established (accepted by NASA, Britannica, a museum, a university, a government agency, or a peer-reviewed study). No myths, no contested claims, no rounding that changes the meaning.
- Surprising enough that people stop scrolling.
- Different from every fact already covered: {covered}

Return ONLY a JSON object with exactly these keys:
{{
  "category": "{category}",
  "hook": "One punchy sentence, max 14 words, that states the surprising fact itself.",
  "beats": ["3 short sentences (max 18 words each) explaining WHY or HOW, each adding new information"],
  "stat": {{"big": "short number or word, max 9 characters", "label": "max 5 words describing it"}},
  "question": "One short question to invite comments, max 10 words.",
  "title": "Catchy title, max 60 characters, no hashtags, no clickbait lies",
  "tags": ["4 lowercase single-word tags"],
  "source": "The reputable source a viewer can check",
  "emoji": ["5 single emojis that illustrate, in order: the hook, beat 1, beat 2, beat 3, the question"]
}}"""


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def covered_titles():
    return [p["title"] for p in load(STATE, [])]


def next_fact():
    posted_ids = {p["id"] for p in load(STATE, [])}
    for f in load(BANK, []) + load(GENERATED, []):
        if f["id"] not in posted_ids:
            return f
    return generate_new()


def generate_new():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("Fact bank is empty and ANTHROPIC_API_KEY is not set.")
    cfg = json.loads((ROOT / "config.json").read_text())
    done = load(STATE, [])
    # rotate categories so the feed stays varied
    last = [p.get("category") for p in done[-3:]]
    pool = [c for c in CATEGORIES if c not in last] or CATEGORIES
    category = pool[len(done) % len(pool)]
    covered = "; ".join(covered_titles()[-300:]) or "none yet"
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": cfg.get("claude_model", "claude-sonnet-5-5"), "max_tokens": 1200,
              "messages": [{"role": "user", "content": PROMPT.format(category=category, covered=covered)}]})
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json()["content"])
    fact = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    all_ids = [f["id"] for f in load(BANK, []) + load(GENERATED, [])]
    fact["id"] = max(all_ids + [0]) + 1
    fact["generated"] = True
    gen = load(GENERATED, [])
    gen.append(fact)
    GENERATED.write_text(json.dumps(gen, indent=2, ensure_ascii=False))
    return fact


if __name__ == "__main__":
    print(json.dumps(next_fact(), indent=2, ensure_ascii=False))
