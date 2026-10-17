"""Learn from the page's own results and keep the conversation going.

update_stats  - fetch views / reactions / comments / shares for recent posts
summary       - what is working (category, time slot, format) -> fed into the AI prompt
reply_comments- short friendly replies to new comments (AUTO_REPLY: off | preview | on)
first_comment - the page posts a question under its own new post
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

from . import ai, config

REPORT_FILE = os.path.join(config.BASE_DIR, "state", "report.md")
VIEW_METRICS = ("post_media_view", "post_impressions_unique", "post_impressions")


def _get(path, **params):
    params["access_token"] = config.FB_PAGE_TOKEN
    r = requests.get(f"https://graph.facebook.com/{config.GRAPH_VERSION}/{path}", params=params, timeout=30)
    data = r.json()
    if r.status_code != 200 or "error" in data:
        raise RuntimeError(str(data.get("error", data))[:200])
    return data


def _post(path, **data):
    data["access_token"] = config.FB_PAGE_TOKEN
    r = requests.post(f"https://graph.facebook.com/{config.GRAPH_VERSION}/{path}", data=data, timeout=30)
    out = r.json()
    if r.status_code != 200 or "error" in out:
        raise RuntimeError(str(out.get("error", out))[:200])
    return out


def _views(obj_id, is_reel):
    if is_reel:  # one metric per call: an unknown metric name fails the whole request
        for metric in ("fb_reels_total_plays", "blue_reels_play_count", "post_impressions_unique", "total_video_views"):
            try:
                data = _get(f"{obj_id}/video_insights", metric=metric)
                if data.get("data"):
                    return int(data["data"][0]["values"][0]["value"] or 0)
            except Exception as e:
                print(f"[insights] reel {obj_id} {metric}: {e}")
        return 0
    for metric in VIEW_METRICS:
        try:
            data = _get(f"{obj_id}/insights", metric=metric)
            if data.get("data"):
                return int(data["data"][0]["values"][0]["value"] or 0)
        except Exception:
            continue
    return 0


def _count(obj_id, edge):
    """Total on an edge (reactions / likes)."""
    d = _get(obj_id, fields=f"{edge}.summary(total_count).limit(0)")
    return d.get(edge, {}).get("summary", {}).get("total_count", 0)


def _people_comments(obj_id):
    """Comments by real people (the page's own first comment and replies don't count)."""
    data = _get(f"{obj_id}/comments", fields="from", filter="stream", limit=100).get("data", [])
    return sum(1 for c in data if (c.get("from") or {}).get("id") != config.FB_PAGE_ID)


def _age_hours(rec):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(rec["time"])).total_seconds() / 3600
    except Exception:
        return 1e9


def score(st):
    """One engagement number led by real reach (views). Shares are capped: the page owner shares every post
    to a few groups by hand, which would otherwise make any post look like a hit."""
    return (st.get("views", 0) / 5 + 2 * st.get("reactions", 0) + 3 * st.get("comments", 0)
            + 3 * min(st.get("shares", 0), 2))


def update_stats(state, max_posts=25):
    """Refresh stats for live posts from the last 7 days (each at most every 3 hours)."""
    if not (config.FB_PAGE_ID and config.FB_PAGE_TOKEN):
        return
    done = 0
    for rec in reversed(state["posted"]):
        oid = rec.get("facebook")
        if rec.get("dry_run") or not oid or _age_hours(rec) > 24 * 7 or done >= max_posts:
            continue
        last = rec.get("stats", {}).get("checked")
        if "reactions" not in rec.get("stats", {}):  # last check failed -> retry now
            last = None
        if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)) < timedelta(hours=3):
            continue
        is_reel = rec.get("format") == "reel"
        st = {"views": _views(oid, is_reel)}
        for name, fn in (("reactions", lambda: _count(oid, "reactions")),
                         ("comments", lambda: _people_comments(oid)),
                         ("shares", lambda: 0 if is_reel else _get(oid, fields="shares").get("shares", {}).get("count", 0))):
            try:
                st[name] = fn()
            except Exception as e:
                if name == "reactions":  # videos may only expose likes
                    try:
                        st[name] = _count(oid, "likes")
                        continue
                    except Exception:
                        pass
                print(f"[insights] {rec['key']} {name}: {e}")
        st["checked"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        st["score"] = round(score(st), 1)
        rec["stats"] = st
        done += 1
    if done:
        print(f"[insights] refreshed stats for {done} post(s)")


def _group(recs, key):
    g = defaultdict(list)
    for r in recs:
        g[key(r)].append(r["stats"]["score"])
    return sorted(((k, sum(v) / len(v), len(v)) for k, v in g.items() if k), key=lambda x: -x[1])


def summary(state):
    """Plain-English performance notes for the AI prompt + report file. '' if not enough data."""
    recs = [r for r in state["posted"] if r.get("stats") and not r.get("dry_run")]
    if len(recs) < 4:
        return ""
    for r in recs:  # re-score old posts with the current formula
        r["stats"]["score"] = round(score(r["stats"]), 1)
    by_cat = _group(recs, lambda r: (r.get("badge") or "").upper())
    by_fmt = _group(recs, lambda r: r.get("format", "photo"))
    by_slot = _group(recs, lambda r: r.get("slot", ""))
    top = sorted(recs, key=lambda r: -r["stats"]["score"])[:3]
    lines = [f"Based on the last {len(recs)} posts on this page:"]
    if by_cat:
        lines.append("Best categories: " + ", ".join(f"{k} ({v:.1f})" for k, v, n in by_cat[:3]))
        if len(by_cat) > 3:
            lines.append("Weakest categories: " + ", ".join(f"{k} ({v:.1f})" for k, v, n in by_cat[-2:]))
    if len(by_fmt) > 1:
        lines.append("Format: " + ", ".join(f"{k} ({v:.1f}, {n} posts)" for k, v, n in by_fmt))
    if by_slot:
        lines.append("Best posting slots (UTC): " + ", ".join(f"{k}:00 ({v:.1f})" for k, v, n in by_slot[:3]))
    faces = face_stats(state)
    if faces:
        lines.append("Best faces on this page (avg views per post): "
                     + ", ".join(f"{f} {v:.0f} ({n} posts)" for f, _, v, n in faces[:6])
                     + ". Prefer the top faces; faces with low numbers only for very big news.")
    lines.append("Top posts: " + " | ".join(f"{r['headline'].replace('*', '')} ({r['stats']['score']})" for r in top))
    text = "\n".join(lines)
    _write_report(recs, text)
    return text


def _face_of(rec):
    """The famous person a post was about (stored since 2026-10-09, guessed from the headline for older posts)."""
    if rec.get("face"):
        return rec["face"]
    from .trends import FACES, FOCUS
    text = rec.get("headline", "").replace("*", "").lower()
    names = [n for n in FOCUS + FACES if " " in n and not n.lower().endswith((" ai", " tools"))]
    hits = sorted((text.find(n.lower()), n) for n in names if n.lower() in text)
    return hits[0][1] if hits else ""


def face_stats(state):
    """[(face, avg score, avg views, posts)] best first: which famous faces work on THIS page."""
    g = defaultdict(list)
    for r in state["posted"]:
        if r.get("stats") and not r.get("dry_run") and _face_of(r):
            g[_face_of(r)].append(r["stats"])
    rows = [(f, sum(score(s) for s in v) / len(v), sum(s.get("views", 0) for s in v) / len(v), len(v))
            for f, v in g.items()]
    return sorted(rows, key=lambda x: -x[1])


def star_face(state):
    """The page's best-performing face (needs 2+ posts), else Elon Musk."""
    return next((f for f, _, _, n in face_stats(state) if n >= 2), "Elon Musk")


def star_angles(state, star, top=6, bottom=4):
    """What worked for the star on this page: the best and worst of their posts (at least a day old, so every post
    had time to collect views), for the AI to pick angles people stop, like, comment and share for."""
    now = datetime.now(timezone.utc)
    rows = []
    for r in state["posted"]:
        st = r.get("stats")
        if not st or r.get("dry_run") or _face_of(r) != star:
            continue
        try:
            if now - datetime.fromisoformat(r["time"]) < timedelta(hours=20):
                continue
        except Exception:
            continue
        rows.append((score(st), f"{r['headline'].replace('*', '')} ({r.get('format', 'post')}: "
                                f"{st.get('views', 0)} views, {st.get('reactions', 0)} likes, "
                                f"{st.get('comments', 0)} comments)"))
    if len(rows) < 4:
        return ""
    rows.sort(key=lambda x: -x[0])
    return (f"{star} posts that did BEST here (people stopped, liked, commented, shared): "
            + " | ".join(t for _, t in rows[:top])
            + f"\n{star} posts that did WORST here (avoid these kinds of angles): "
            + " | ".join(t for _, t in rows[-bottom:]) + "\n")


def best_slot(state):
    """The posting slot (UTC hour) with the best results (needs 2+ posts), else 19."""
    recs = [r for r in state["posted"] if r.get("stats") and not r.get("dry_run") and r.get("slot")]
    rows = [(k, v, n) for k, v, n in _group(recs, lambda r: r.get("slot", "")) if n >= 2]
    return int(rows[0][0]) if rows else 19


def best_format(state):
    """'reel' or 'photo' if one clearly wins (3+ posts each, 30%+ better), else None."""
    recs = [r for r in state["posted"] if r.get("stats") and not r.get("dry_run")]
    for r in recs:  # compare formats on organic reach only
        r["stats"]["score"] = round(score(r["stats"]), 1)
    fm = {k: (v, n) for k, v, n in _group(recs, lambda r: r.get("format", "photo"))}
    if "reel" in fm and "photo" in fm and fm["reel"][1] >= 3 and fm["photo"][1] >= 3:
        r, p = fm["reel"][0], fm["photo"][0]
        if r > p * 1.3:
            return "reel"
        if p > r * 1.3:
            return "photo"
    return None


def _write_report(recs, text):
    rows = "\n".join(
        f"| {r['time'][:16]} | {r.get('format', 'photo')} | {(r.get('badge') or '').upper()} | "
        f"{r['headline'].replace('*', '').replace('|', '/')} | {r['stats'].get('views', 0)} | "
        f"{r['stats'].get('reactions', 0)} | {r['stats'].get('comments', 0)} | {r['stats'].get('shares', 0)} | "
        f"{r['stats']['score']} |"
        for r in sorted(recs, key=lambda r: r["time"], reverse=True)[:40])
    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(f"# Hype Atlas performance report\n\n_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC_\n\n"
                f"```\n{text}\n```\n\n| Time (UTC) | Format | Category | Headline | Views | Reactions | "
                f"Comments | Shares | Score |\n|---|---|---|---|---|---|---|---|---|\n{rows}\n")


# --------------------------------------------------------------- comments
def first_comment(obj_id, text):
    if not text or config.DRY_RUN:
        return
    try:
        _post(f"{obj_id}/comments", message=text)
        print(f"[insights] first comment posted: {text[:60]}")
    except Exception as e:
        print(f"[insights] first comment failed: {e}")


def _reply_batch(post_headline, comments):
    """Short replies for up to ~20 comments in ONE AI call (saves the free AI quota). {comment id: reply}.
    A reply of "SKIP" means: don't answer (spam, abuse, money requests...)."""
    lines = "\n".join(f'{n}. "{text[:300]}"' for n, (_, text) in enumerate(comments, 1))
    prompt = (f'You manage the Facebook page "{config.PAGE_NAME}". People commented on our post "{post_headline}":\n'
              f"{lines}\n\n"
              "Write ONE short, warm, natural reply for EACH comment (max 12 words, max 1 emoji). Make every reply "
              "different and specific to that comment; if it asks a question, answer briefly from the post only. "
              "Do not add new facts, do not argue, no politics, no links. "
              'For spam, abuse, hate, sexual content or requests for money/personal info, the reply is exactly "SKIP". '
              'Return JSON: {"replies": [{"n": 1, "reply": "..."}, ...]}')
    for engine in (ai._light, ai._groq):  # never Gemini: its small daily quota is kept for the posts
        try:
            data = ai._parse_json(engine(prompt))
            items = data.get("replies", data) if isinstance(data, dict) else data
            out = {}
            for it in items:
                n = int(it.get("n", 0))
                if 1 <= n <= len(comments) and str(it.get("reply", "")).strip():
                    out[comments[n - 1][0]] = str(it["reply"]).strip()
            if out:
                return out
        except Exception as e:
            print(f"[reply] AI batch failed ({getattr(engine, '__name__', 'ai')}): {str(e)[:120]}")
    return {}


def _all_comments(obj_id, cap=2000):
    """Every top-level comment on a post (follows Facebook's pages of 100)."""
    data = _get(f"{obj_id}/comments", fields="id,message,from", filter="toplevel", limit=100)
    out = data.get("data", [])
    nxt = (data.get("paging") or {}).get("next")
    while nxt and len(out) < cap:
        page = requests.get(nxt, timeout=30).json()
        out += page.get("data", [])
        nxt = (page.get("paging") or {}).get("next")
    return out


def reply_comments(state, max_replies=100, batch=20, gap=1.2):
    """Answers EVERY new comment on posts from the last 7 days (a short, different reply each), up to max_replies
    per run (the bot runs every hour). AUTO_REPLY=preview only prints the replies; on = actually reply."""
    import time
    mode = config.AUTO_REPLY
    if mode == "off" or not (config.FB_PAGE_ID and config.FB_PAGE_TOKEN):
        return
    replied = set(state.setdefault("replied", []))
    sent = 0
    for rec in reversed(state["posted"]):
        oid = rec.get("facebook")
        if rec.get("dry_run") or not oid or _age_hours(rec) > 24 * 7 or sent >= max_replies:
            continue
        try:
            comments = _all_comments(oid)
        except Exception as e:
            print(f"[insights] comments for {rec['key']}: {e}")
            continue
        new = [(c["id"], c["message"]) for c in comments if c["id"] not in replied and c.get("message")
               and (c.get("from") or {}).get("id") != config.FB_PAGE_ID]
        for k in range(0, len(new), batch):
            if sent >= max_replies:
                break
            chunk = new[k:k + batch]
            replies = _reply_batch(rec["headline"].replace("*", ""), chunk)
            for cid, text in chunk:
                reply = replies.get(cid, "")
                if not reply:
                    continue  # AI gave nothing: try again next run
                if reply.upper().strip('". ') == "SKIP":
                    replied.add(cid)
                    continue
                if mode == "on" and not config.DRY_RUN:
                    try:
                        _post(f"{cid}/comments", message=reply)
                        replied.add(cid)
                        sent += 1
                        print(f"[reply] '{text[:50]}' -> '{reply}'")
                        time.sleep(gap)  # gentle pace, so Facebook doesn't see a burst
                    except Exception as e:
                        print(f"[reply] failed: {e}")
                        if "limit" in str(e).lower() or "spam" in str(e).lower():
                            state["replied"] = list(replied)[-20000:]
                            return  # Facebook asked us to slow down: continue next hour
                else:
                    print(f"[reply preview] '{text[:50]}' -> '{reply}'")
                    sent += 1
    if sent:
        print(f"[reply] answered {sent} comment(s) this run")
    state["replied"] = list(replied)[-20000:]
