"""Uploads a rendered Short to YouTube and Facebook (Page Reels)."""
import json, os, time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())


def build_texts(fact):
    tags = [f"#{t}" for t in fact.get("tags", [])]
    hashtags = " ".join(CFG["base_hashtags"] + tags)
    yt_title = f"{fact['title']} #shorts"[:100]
    body = "\n".join([fact["hook"], "", *fact["beats"], "", f"💬 {fact['question']}", "",
                      f"Source: {fact['source']}", "", f"{CFG['tagline']} Follow {CFG['handle']} for a new fact every day."])
    return yt_title, f"{body}\n\n{hashtags}", f"{fact['hook']} 🤯\n\n{fact['question']} 👇\n\nSource: {fact['source']}\n\n{hashtags.replace('#shorts ', '')}"


# ------------------------------------------------------------------ YouTube
def youtube_upload(video_path, fact):
    need = ["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
    if not all(os.environ.get(k) for k in need):
        print("YouTube: secrets missing, skipped"); return None
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                        token_uri="https://oauth2.googleapis.com/token",
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    title, desc, _ = build_texts(fact)
    body = {
        "snippet": {"title": title, "description": desc, "tags": fact.get("tags", []) + ["facts", "shorts"],
                    "categoryId": CFG["youtube_category_id"], "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": {"privacyStatus": CFG["youtube_privacy"], "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": False},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video_path), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print("YouTube: uploaded", resp["id"])
    return resp["id"]


# ------------------------------------------------------------------ Facebook Reels
def facebook_upload(video_path, fact):
    page, token = os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_TOKEN")
    if not (page and token):
        print("Facebook: secrets missing, skipped"); return None
    v = CFG["graph_api_version"]
    base = f"https://graph.facebook.com/{v}/{page}/video_reels"
    r = requests.post(base, data={"upload_phase": "start", "access_token": token}, timeout=60)
    r.raise_for_status()
    vid = r.json()["video_id"]
    size = Path(video_path).stat().st_size
    with open(video_path, "rb") as fh:
        r = requests.post(f"https://rupload.facebook.com/video-upload/{v}/{vid}", data=fh, timeout=600,
                          headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
    r.raise_for_status()
    _, _, caption = build_texts(fact)
    r = requests.post(base, timeout=60, data={"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                                              "description": caption, "access_token": token})
    r.raise_for_status()
    # wait for processing so failures show in the log
    for _ in range(20):
        time.sleep(15)
        st = requests.get(f"https://graph.facebook.com/{v}/{vid}", params={"fields": "status", "access_token": token}, timeout=30).json()
        status = st.get("status", {}).get("video_status")
        print("Facebook status:", status)
        if status in ("ready", "error"):
            break
    print("Facebook: published", vid)
    return vid


# ------------------------------------------------------------------ Instagram Reels
def instagram_upload(video_path, fact):
    ig, token = os.environ.get("IG_USER_ID"), os.environ.get("FB_PAGE_TOKEN")
    if not (ig and token):
        print("Instagram: secrets missing, skipped"); return None
    v = CFG["graph_api_version"]
    _, _, caption = build_texts(fact)
    r = requests.post(f"https://graph.facebook.com/{v}/{ig}/media", timeout=60, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "access_token": token})
    r.raise_for_status()
    cid = r.json()["id"]
    size = Path(video_path).stat().st_size
    with open(video_path, "rb") as fh:
        r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{v}/{cid}", data=fh, timeout=600,
                          headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
    r.raise_for_status()
    for _ in range(40):
        time.sleep(15)
        st = requests.get(f"https://graph.facebook.com/{v}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=30).json()
        code = st.get("status_code")
        print("Instagram status:", code)
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"Instagram processing error: {st}")
    r = requests.post(f"https://graph.facebook.com/{v}/{ig}/media_publish", timeout=60,
                      data={"creation_id": cid, "access_token": token})
    r.raise_for_status()
    mid = r.json()["id"]
    print("Instagram: published", mid)
    return mid
