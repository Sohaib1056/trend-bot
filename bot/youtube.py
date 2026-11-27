"""YouTube Shorts: the same Reel as Facebook/Instagram, with YouTube's own title, description, hashtags and tags
(YouTube is a search engine: people find Shorts by keywords, so the wording differs from the Facebook caption).

Needs three GitHub Secrets, made once by the owner with tools/youtube_auth.py:
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN. Without them the bot skips YouTube quietly.
Quota: an upload costs 1,600 of the free 10,000 daily API units, so at most 6 Shorts a day (the bot makes 5 Reels).
"""
import json
import os
import re

import requests

from . import ai, config

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CATEGORY = "28"  # Science & Technology (Elon: rockets, cars, AI)


def configured():
    return config.POST_TO_YOUTUBE and all(os.getenv(k, "").strip() for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET",
                                                                             "YT_REFRESH_TOKEN"))


def _access_token():
    r = requests.post(TOKEN_URL, timeout=30, data={
        "client_id": os.getenv("YT_CLIENT_ID", "").strip(), "client_secret": os.getenv("YT_CLIENT_SECRET", "").strip(),
        "refresh_token": os.getenv("YT_REFRESH_TOKEN", "").strip(), "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise RuntimeError(f"YouTube token refresh failed: HTTP {r.status_code} {r.text[:150]}")
    return r.json()["access_token"]


def _clean(text, limit):
    text = re.sub(r"[<>]", "", str(text or "")).replace("*", "").strip()  # YouTube rejects < and > in titles/descriptions
    return text[:limit].rstrip()


def metadata(post, caption):
    """YouTube title, description and tags written for YouTube search and the Shorts feed (US audience)."""
    head = post["headline"].replace("*", "")
    prompt = ("You upload YouTube Shorts for a channel with daily Elon Musk news (SpaceX, Tesla, X, Neuralink, xAI) "
              "for a US audience. Write the YouTube metadata for this Short.\n"
              f"Headline: {head}\nNarration: {post.get('voiceover', '')}\nFacebook caption: {post.get('caption', '')[:600]}\n\n"
              "Rules: use ONLY the facts above; words people type into YouTube search (names, products, the event).\n"
              "- title: max 70 characters, starts with the most searched name or keyword, says what happened, curious "
              "but true, no clickbait lies, ends with ' #Shorts'.\n"
              "- description: 2-3 short sentences with the main keywords, then one line 'Subscribe for daily Elon "
              "Musk news.', then 3-5 hashtags (e.g. #ElonMusk #Tesla #SpaceX).\n"
              "- tags: 10-15 search keywords and phrases, lowercase, no #, e.g. 'elon musk', 'tesla news', "
              "'spacex starship'.\n"
              'Reply JSON only: {"title": "...", "description": "...", "tags": ["...", "..."]}')
    data = {}
    for engine in (ai._light, ai._groq):  # never Gemini: its small daily quota is kept for the posts
        try:
            data = ai._parse_json(engine(prompt))
            if isinstance(data, dict) and str(data.get("title", "")).strip():
                break
        except Exception as e:
            print(f"[youtube] metadata AI failed ({getattr(engine, '__name__', 'ai')}): {str(e)[:120]}")
        data = {}
    title = _clean(data.get("title") or head, 100)
    if "#shorts" not in title.lower():
        title = _clean(title[:91] + " #Shorts", 100)
    desc = _clean(data.get("description") or f"{head}\n\nSubscribe for daily Elon Musk news.\n#ElonMusk #Tesla #SpaceX", 4000)
    credits = [c for c in [post.get("_credit")] + list(post.get("_reel_credits") or []) if c]
    if credits:  # free-licence photos need attribution
        desc += "\n\nPhotos: " + "; ".join(c.replace("📷 Photo: ", "") for c in credits)
    tags, total = [], 0
    for t in data.get("tags") or ["elon musk", "tesla", "spacex", "elon musk news"]:
        t = re.sub(r"[<>#,]", "", str(t)).strip().lower()[:30]
        if t and t not in tags and total + len(t) + 2 <= 450:  # YouTube allows ~500 characters of tags in all
            tags.append(t)
            total += len(t) + 2
    return {"title": title, "description": desc, "tags": tags}


def upload_short(video, post, caption):
    """Uploads the Reel as a YouTube Short. Returns the video id."""
    meta = metadata(post, caption)
    token = _access_token()
    body = {"snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"],
                        "categoryId": CATEGORY, "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    size = os.path.getsize(video)
    r = requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, timeout=60,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                               "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                      data=json.dumps(body))
    if r.status_code != 200 or "Location" not in r.headers:
        raise RuntimeError(f"YouTube upload start failed: HTTP {r.status_code} {r.text[:200]}")
    with open(video, "rb") as f:
        r = requests.put(r.headers["Location"], data=f, timeout=600,
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"YouTube upload failed: HTTP {r.status_code} {r.text[:200]}")
    vid = r.json()["id"]
    print(f"[youtube] Short posted: https://youtube.com/shorts/{vid} | {meta['title']}")
    return vid
