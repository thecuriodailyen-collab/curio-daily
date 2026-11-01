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
  "emoji": ["5 single emojis that illustrate, in order: the hook, beat 1, beat 2, beat 3, the question"],
  "footage": ["5 stock-video search queries (2-3 concrete, filmable English words, e.g. 'octopus underwater', 'lightning storm'), in order: hook, beat 1, beat 2, beat 3, question. Each must show what that sentence talks about; no abstract words, no people's names"]
}}"""


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def covered_titles():
    return [p["title"] for p in load(STATE, [])]


def next_fact():
    posted_ids = {p["id"] for p in load(STATE, [])}
    # a generated fact that was rendered but never published is reused first
    for f in load(GENERATED, []):
        if f["id"] not in posted_ids:
            return f
    cfg = json.loads((ROOT / "config.json").read_text())
    if cfg.get("trending", True) and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return generate_trending()
        except Exception as e:  # never miss a post because of the trend search
            print(f"Trending fact failed ({e}), using the fact bank instead")
    for f in load(BANK, []):
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


TREND_PROMPT = """Today is {today}. You pick the daily topic for "Curio Daily", a YouTube Shorts / Facebook / Instagram Reels channel for viewers in the United States and Canada that teaches one surprising general-knowledge fact per video, in simple spoken English.

Step 1. Use web search to find what people in the US and Canada are searching for and talking about right now (this week): trending news, upcoming holidays or events, space missions, science discoveries, big sports games, new movies, viral topics, anniversaries.
Step 2. Pick the ONE trend most likely to make people stop scrolling that you can connect to a surprising, TRUE, well-established general-knowledge fact.
Rules:
- AVOID: deaths, tragedies, disasters with victims, crime, war, terrorism, elections and politics, religion, health or medical advice, celebrity gossip, rumors, anything still unconfirmed.
- The fact itself must be verifiable (NASA, Britannica, a museum, a university, a government agency, a peer-reviewed study) and must still be true weeks from now. The hook may mention the trend to feel timely.
- It must be different from every fact already covered: {covered}

When you are done searching, answer with ONLY a JSON object (no other text) with exactly these keys:
{{
  "trend": "the trending topic it connects to, max 8 words",
  "category": "one of: {categories}",
  "hook": "One punchy sentence, max 14 words, that states the surprising fact (it can mention the trend).",
  "beats": ["3 short sentences (max 18 words each) explaining WHY or HOW, each adding new information"],
  "stat": {{"big": "short number or word, max 9 characters", "label": "max 5 words describing it"}},
  "question": "One short question to invite comments, max 10 words.",
  "title": "Catchy title, max 60 characters, no hashtags, no clickbait lies",
  "tags": ["4 lowercase single-word tags, one of them about the trend"],
  "source": "The reputable source a viewer can check",
  "emoji": ["5 single emojis that illustrate, in order: the hook, beat 1, beat 2, beat 3, the question"],
  "footage": ["5 stock-video search queries (2-3 concrete, filmable English words, e.g. 'octopus underwater', 'lightning storm'), in order: hook, beat 1, beat 2, beat 3, question. Each must show what that sentence talks about; no abstract words, no people's names, no brand names"]
}}"""


def generate_trending():
    import datetime
    key = os.environ["ANTHROPIC_API_KEY"]
    cfg = json.loads((ROOT / "config.json").read_text())
    covered = "; ".join(covered_titles()[-300:]) or "none yet"
    prompt = TREND_PROMPT.format(today=datetime.date.today().strftime("%A %B %d, %Y"), covered=covered,
                                 categories=", ".join(CATEGORIES))
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6,
              "user_location": {"type": "approximate", "country": "US", "timezone": "America/New_York"}}]
    data = None
    for _ in range(4):  # web search can pause a long turn; resume it
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=300, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": cfg.get("claude_model", "claude-sonnet-5-5"), "max_tokens": 4000,
                  "messages": messages, "tools": tools})
        r.raise_for_status()
        data = r.json()
        if data.get("stop_reason") != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": data["content"]}]
    blocks = data["content"]
    last_tool = max([i for i, b in enumerate(blocks) if b.get("type") == "web_search_tool_result"] + [-1])
    text = "".join(b.get("text", "") for b in blocks[last_tool + 1:] if b.get("type") == "text")
    fact = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    for k in ["category", "hook", "beats", "stat", "question", "title", "tags", "source"]:
        if not fact.get(k):
            raise ValueError(f"missing {k}")
    if len(fact["beats"]) != 3:
        raise ValueError("need 3 beats")
    if fact["category"] not in CATEGORIES:
        fact["category"] = "Science"
    all_ids = [f["id"] for f in load(BANK, []) + load(GENERATED, [])]
    fact["id"] = max(all_ids + [0]) + 1
    fact["generated"] = True
    print(f"Trending topic: {fact.get('trend')} -> {fact['title']}")
    gen = load(GENERATED, [])
    gen.append(fact)
    GENERATED.write_text(json.dumps(gen, indent=2, ensure_ascii=False))
    return fact


if __name__ == "__main__":
    print(json.dumps(next_fact(), indent=2, ensure_ascii=False))
