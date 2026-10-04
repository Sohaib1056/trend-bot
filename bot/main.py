"""Trend Bot: find trending topic -> AI writes post -> make poster -> upload to FB + IG."""
import json
import os
import re
import sys
from datetime import datetime, timezone

from . import ai, config, meta, poster, trends

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


def main():
    print(f"== Trend Bot | page: {config.PAGE_NAME} | countries: {config.COUNTRIES} | DRY_RUN={config.DRY_RUN}")
    meta.check_connection()
    state = load_state()
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
        else:
            res = meta.publish(img, caption)
            record.update(res)
            if not any(k in res for k in ("facebook", "instagram")):
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
