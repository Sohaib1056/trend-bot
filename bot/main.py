"""Trend Bot: find trending topic -> AI writes post -> make poster -> upload to FB + IG."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from . import ai, config, meta, poster, reel, trends

# extra safety net on top of the AI's own rules
BLOCK_WORDS = re.compile(
    r"\b(dead|dies|died|death|killed|kill|murder|shooting|shot|stabb|crash|victim|funeral|obituary|"
    r"suicide|attack|war|bomb|terror|rape|abuse|arrest|overdose|missing)\b", re.I)


def load_state():
    try:
        with open(config.STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"posted": []}


def save_state(state):
    state["posted"] = state["posted"][-300:]
    os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def current_slot(now):
    """Start time of the posting slot we are inside, or None."""
    for day in (0, -1):
        for h in sorted(config.POST_SLOTS_UTC, reverse=True):
            start = (now + timedelta(days=day)).replace(hour=h, minute=0, second=0, microsecond=0)
            if start <= now < start + timedelta(hours=config.SLOT_WINDOW_HOURS):
                return start
    return None


def should_post_now(state):
    """Hourly GitHub runs and cron-job.org triggers post once per slot window.
    A manual 'Run workflow' with dry_run set to true/false always runs (for testing)."""
    event = os.getenv("GITHUB_EVENT_NAME", "")
    if event != "schedule" and not (event == "workflow_dispatch" and os.getenv("RUN_MODE", "default") in ("", "default")):
        return True
    now = datetime.now(timezone.utc)
    slot = current_slot(now)
    if slot is None:
        print(f"[main] {now:%H:%M} UTC is not in a posting window {config.POST_SLOTS_UTC} -> nothing to do")
        return False
    for p in reversed(state["posted"]):
        try:
            t = datetime.fromisoformat(p["time"])
        except Exception:
            continue
        if t >= slot and bool(p.get("dry_run", False)) == config.DRY_RUN:
            print(f"[main] slot {slot:%H:%M} UTC already done ({p.get('topic', p['key'])}) -> nothing to do")
            return False
    print(f"[main] posting for slot {slot:%H:%M} UTC")
    return True


def main():
    print(f"== Trend Bot | page: {config.PAGE_NAME} | countries: {config.COUNTRIES} | DRY_RUN={config.DRY_RUN}")
    state = load_state()
    if not should_post_now(state):
        return 0
    meta.check_connection()
    done_keys = {p["key"] for p in state["posted"]}
    done_topics = {norm(p.get("topic", "")) for p in state["posted"]}

    items = trends.rank(trends.collect(config.COUNTRIES))
    seen, candidates = set(), []
    for it in items:
        t = norm(it["topic"])
        blob = it["topic"] + " " + " ".join(n["title"] + " " + n["snippet"] for n in it["news"])
        if not t or t in seen or t in done_topics or BLOCK_WORDS.search(blob):
            continue
        seen.add(t)
        candidates.append(it)
    candidates = candidates[:25]
    print(f"[main] {len(candidates)} safe, fresh candidate topics (strongest first)")
    for c in candidates[:5]:
        print(f"[main]   power {c['power']:>9,} | {c['country']:<11} | {c['topic']}")
    if not candidates:
        print("Nothing new to post right now.")
        return 0

    already = [p.get("topic", p["key"]) for p in state["posted"]]
    posts = ai.write_posts(candidates, already, config.POSTS_PER_RUN)

    made = 0
    for p in posts:
        if p["key"] in done_keys or BLOCK_WORDS.search(p["headline"] + " " + p["caption"]):
            print(f"[main] skipping {p['key']}")
            continue
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        img = poster.make_poster(p, os.path.join(config.OUTPUT_DIR, f"{stamp}-{p['key']}.jpg"))
        caption = p["caption"].strip() + "\n\n" + " ".join("#" + h for h in p["hashtags"])
        if p.get("_credit"):  # photo licence requires attribution; kept off the poster, in the caption
            caption += "\n\n" + p["_credit"]
        with open(img.replace(".jpg", ".txt"), "w") as f:
            f.write(caption)
        print(f"\n--- POST: {p['headline']}\n{caption}\nimage: {img}\n")

        record = {"key": p["key"], "topic": p.get("topic", ""), "headline": p["headline"],
                  "time": datetime.now(timezone.utc).isoformat(timespec="minutes")}
        if config.DRY_RUN:
            record["dry_run"] = True
            print("[main] DRY_RUN is on -> not uploading. Set DRY_RUN=false to go live.")
        fmt = config.POST_FORMAT
        if fmt == "alternate":
            live = sum(1 for x in state["posted"] if not x.get("dry_run"))
            fmt = "reel" if live % 2 == 0 else "photo"
        video = None
        if fmt in ("reel", "both") and p.get("_photo") is not None:
            video = reel.make_reel(p, p["_photo"], img.replace(".jpg", ".mp4"))
        record["format"] = "reel" if video and fmt == "reel" else ("both" if video and fmt == "both" else "photo")
        print(f"[main] format: {record['format']}")
        res = {}
        if config.DRY_RUN:
            pass
        elif record["format"] == "reel":
            try:
                record["facebook_reel"] = record["facebook"] = meta.publish_reel(video, caption)
                res = {"facebook": record["facebook"]}
            except Exception as e:  # reel upload failed -> fall back to the photo post
                print(f"[main] Reel failed ({e}) -> posting the photo instead")
                record["format"] = "photo"
                res = meta.publish(img, caption)
                record.update(res)
        else:
            res = meta.publish(img, caption)
            record.update(res)
            if record["format"] == "both":
                try:
                    record["facebook_reel"] = meta.publish_reel(video, caption)
                except Exception as e:
                    print(f"[main] Reel failed: {e}")
        if not config.DRY_RUN and not any(k in res for k in ("facebook", "instagram")):
            print("[main] Upload failed on both platforms.")
            save_state(state)
            return 1
        state["posted"].append(record)
        done_keys.add(p["key"])
        made += 1

    save_state(state)
    print(f"== Done. {made} post(s) made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
