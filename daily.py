"""Daily job: pick fact -> render -> publish -> record."""
import datetime, json, sys
from pathlib import Path

from generate import next_fact, load, STATE
from render import render
from publish import youtube_upload, facebook_upload

ROOT = Path(__file__).parent


def main():
    fact = next_fact()
    print(f"Today: #{fact['id']} {fact['title']}")
    out = ROOT / "out" / f"curio_{fact['id']:03d}.mp4"
    voice = "--no-voice" not in sys.argv
    dur = render(fact, out, voice=voice)
    print(f"Rendered {out} ({dur:.1f}s)")
    if "--render-only" in sys.argv:
        return
    errors, yt, fb = [], None, None
    try:
        yt = youtube_upload(out, fact)
    except Exception as e:
        errors.append(f"YouTube: {e}")
    try:
        fb = facebook_upload(out, fact)
    except Exception as e:
        errors.append(f"Facebook: {e}")
    posted = load(STATE, [])
    posted.append({"id": fact["id"], "title": fact["title"], "category": fact["category"],
                   "date": datetime.date.today().isoformat(), "youtube_id": yt, "facebook_id": fb})
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(posted, indent=2, ensure_ascii=False))
    if errors:
        print("\n".join(errors))
        sys.exit(1)


if __name__ == "__main__":
    main()
