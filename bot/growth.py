"""Growth helpers that stay inside Meta's rules.

daily_todo   - once a day, a GitHub issue (GitHub e-mails it to you) with 5-minute growth tasks:
               which posts to share in which Facebook Groups, and which posts to open to
               "Invite" the people who reacted. Bots may not do these actions themselves.
post_threads - cross-post to Threads (needs THREADS_USER_ID + THREADS_TOKEN secrets).
"""
import os
import time
import urllib.parse
from datetime import datetime, timezone

import requests

from . import config

FOLLOW_LINE = "👉 Follow Hype Atlas for the biggest US tech, AI & trending news every day! #HypeAtlas"


def post_url(rec):
    oid = rec.get("facebook", "")
    if rec.get("format") == "reel":
        return f"https://www.facebook.com/reel/{oid}"
    if "_" in oid:
        page, post = oid.split("_", 1)
        return f"https://www.facebook.com/{page}/posts/{post}"
    return f"https://www.facebook.com/{oid}"


def _group_search(term):
    return "https://www.facebook.com/search/groups/?q=" + urllib.parse.quote(term)


def _todo_body(state):
    today = datetime.now(timezone.utc).date()
    live = [r for r in state["posted"] if r.get("facebook") and not r.get("dry_run")]
    recent = [r for r in live if (today - datetime.fromisoformat(r["time"]).date()).days <= 1]
    if not recent:
        return None
    best = sorted(recent, key=lambda r: -(r.get("stats", {}).get("score", 0)))[:3]
    lines = ["**Roz 5 minute = asli followers.** Facebook ye kaam bot ko karne nahi deta, isliye ye aapke liye hain 🙏", "",
             "## 1. In posts ko Facebook Groups mein share karein", "",
             "Post kholein → **Share** → **Share to a group**. Sirf un groups mein jahan ijazat ho, roz max 2-3 groups.", ""]
    for i, r in enumerate(best, 1):
        lines.append(f"**{i}. {r['headline'].replace('*', '')}**  ")
        lines.append(f"Post: {post_url(r)}  ")
        groups = r.get("share_groups") or []
        if groups:
            lines.append("Groups dhoondhein: " + " · ".join(f"[{g}]({_group_search(g)})" for g in groups[:3]))
        lines.append("")
    reacted = [r for r in live if r.get("stats", {}).get("reactions", 0) > 0][-5:]
    lines += ["## 2. Like karne walon ko Invite karein", "",
              "Har post kholein → reactions (👍❤️ wali ginti) par tap karein → har naam ke saath **Invite** dabayein.", ""]
    if reacted:
        lines += [f"- {post_url(r)} ({r['stats']['reactions']} reactions)" for r in reacted]
    else:
        lines.append("- Abhi koi reaction nahi. Kal dobara dekhein.")
    lines += ["", "## 3. Comments", "",
              "Bot khud jawab deta hai. Aap khud bhi kuch comments like kar dein, page zinda lagta hai.", "",
              "_Kaam ho jaye to ye issue Close kar dein. Kal naya aayega._"]
    return "\n".join(lines)


def daily_todo(state):
    """Opens one GitHub issue per day (uses the workflow's GITHUB_TOKEN)."""
    token, repo = os.getenv("GITHUB_TOKEN", ""), os.getenv("GITHUB_REPOSITORY", "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not token or not repo or state.get("todo_date") == today or datetime.now(timezone.utc).hour < 3:
        return
    body = _todo_body(state)
    if not body:
        return
    try:
        r = requests.post(f"https://api.github.com/repos/{repo}/issues", timeout=30,
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                          json={"title": f"📈 Aaj ka growth kaam ({today})", "body": body, "labels": []})
        r.raise_for_status()
        state["todo_date"] = today
        print(f"[growth] daily to-do issue created: {r.json().get('html_url')}")
    except Exception as e:
        print(f"[growth] could not create to-do issue: {e}")


# ------------------------------------------------------------------ Threads
THREADS = "https://graph.threads.net/v1.0"


def threads_text(headline, caption):
    first = caption.strip().split("\n")[0]
    text = f"{headline.replace('*', '')}\n\n{first}\n\nFollow for daily trends 🔥 #HypeAtlas"
    return text[:490]


def post_threads(media_url, text, is_video=False):
    """Cross-post to Threads. Returns the Threads post id, or None if not configured / failed."""
    uid, tok = os.getenv("THREADS_USER_ID", "").strip(), os.getenv("THREADS_TOKEN", "").strip()
    if not uid or not tok or not media_url or config.DRY_RUN:
        return None
    try:
        data = {"media_type": "VIDEO" if is_video else "IMAGE", "text": text, "access_token": tok,
                ("video_url" if is_video else "image_url"): media_url}
        r = requests.post(f"{THREADS}/{uid}/threads", data=data, timeout=60)
        cid = r.json()["id"]
        for _ in range(30 if is_video else 6):  # Threads needs a moment to fetch the media
            time.sleep(10 if is_video else 5)
            st = requests.get(f"{THREADS}/{cid}", params={"fields": "status", "access_token": tok}, timeout=30).json()
            if st.get("status") in ("FINISHED", None):
                break
            if st.get("status") == "ERROR":
                raise RuntimeError(f"Threads processing error: {st}")
        r = requests.post(f"{THREADS}/{uid}/threads_publish", data={"creation_id": cid, "access_token": tok}, timeout=60)
        pid = r.json()["id"]
        print(f"[threads] posted: {pid}")
        return pid
    except Exception as e:
        print(f"[threads] failed: {e}")
        return None
