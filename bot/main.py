"""Trend Bot: find trending topic -> AI writes post -> make poster -> upload to FB + IG."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from . import ai, config, growth, insights, meta, poster, reel, trends

# extra safety net on top of the AI's own rules
BLOCK_WORDS = re.compile(
    r"\b(dead|dies|died|death|killed|kill|murder|shooting|shot|stabb|crash|victim|funeral|obituary|"
    r"suicide|attack|war|bomb|terror|rape|abuse|arrest|overdose|missing|gunfire|molotov|execution|executed|execute|lethal|shooter|firing squad|death row|convicted|sentenced|prison|trial|protests?|protesters?|riots?|teargas)\b", re.I)


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


STOP = set("the a an and or of to in on for with from at by as is are was be after over amid new his her their its "
           "says said just will about into this that than more most facts fact wild first again big top shocking huge".split())


# words Facebook's rules penalise (sexual, slurs, profanity): such wording is never posted
RISKY = re.compile(r"\b(sex|sexual|sexually|porn|nude|naked|traffick\w*|p[a]?edophil\w*|molest\w*|grooming|incest|fuck\w*|shit\w*|bitch\w*|bastard|whore|slut|retard\w*)\b", re.I)

# if the star has nothing new: only faces as big as the star (people stop, like, share and follow for them)
BIG_FACES = ["Donald Trump", "Mark Zuckerberg", "Jeff Bezos", "Bill Gates", "Sam Altman", "Jensen Huang", "Taylor Swift",
             "Cristiano Ronaldo", "Lionel Messi", "LeBron James", "Kim Kardashian", "Dwayne Johnson", "MrBeast",
             "Barack Obama", "Rihanna", "Oprah Winfrey", "Tom Cruise", "Kylie Jenner"]

# words that tell the same story in other words ('richest' = 'trillionaire' = 'net worth')
SAME = {w: "wealth" for w in ("rich", "richer", "richest", "wealth", "wealthy", "wealthiest", "trillion", "trillionaire",
                              "billion", "billionaire", "net", "worth", "fortune")}


def _words(text):
    return {SAME.get(w, w) for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def recent_duplicate(text, face, state, hours=48, min_common=2, must=""):
    """Same person + same story posted in the last 2 days (wording may differ), e.g. 'Nicolas Cage refuses AI
    waiver' vs 'Nicolas Cage rejects Amazon AI waiver'. Returns the old headline or ''."""
    now = datetime.now(timezone.utc)
    name = _words(face or "")
    mine = _words(text) - name
    for r in state["posted"]:
        if r.get("dry_run"):
            continue
        try:
            if now - datetime.fromisoformat(r["time"]) > timedelta(hours=hours):
                continue
        except Exception:
            continue
        old_text = r.get("topic", "") + " " + r.get("headline", "")
        theirs = _words(old_text) - name
        same_person = bool(name) and name <= _words(old_text)
        common = mine & theirs
        if must and must not in common:
            continue
        if (same_person and len(common) >= min_common) or len(common) >= 4:
            return r.get("headline", "").replace("*", "")
    return ""


def choose_format(state):
    fmt = config.POST_FORMAT
    if fmt == "slots":  # 2 photos (US morning + lunch) and 4 Reels (afternoon to prime time) a day
        slot = current_slot(datetime.now(timezone.utc))
        return "photo" if slot is not None and slot.hour in config.PHOTO_SLOTS_UTC else "reel"
    if fmt == "alternate":
        n = sum(1 for x in state["posted"] if not x.get("dry_run"))
        winner = insights.best_format(state)  # once one format clearly wins, use it 2 of every 3 posts
        if winner:
            fmt = winner if n % 3 else ("photo" if winner == "reel" else "reel")
        else:
            fmt = "reel" if n % 2 == 0 else "photo"
    return fmt


def main():
    print(f"== Trend Bot | page: {config.PAGE_NAME} | countries: {config.COUNTRIES} | DRY_RUN={config.DRY_RUN}")
    state = load_state()
    try:  # read-only, so it also runs in test mode
        insights.update_stats(state)
    except Exception as e:
        print(f"[insights] stats skipped: {e}")
    if not config.DRY_RUN:  # answer comments + daily to-do on every live run
        try:
            insights.reply_comments(state)
            growth.daily_todo(state)
        except Exception as e:
            print(f"[insights] skipped: {e}")
        save_state(state)
    if not should_post_now(state):
        return 0
    meta.check_connection()
    live = [p for p in state["posted"] if not p.get("dry_run")]  # test runs must not use up topics
    done_keys = {p["key"] for p in live}
    done_topics = {norm(p.get("topic", "")) for p in live}

    items = trends.rank(trends.collect(config.COUNTRIES))
    seen, candidates = set(), []
    for it in items:
        t = norm(it["topic"])
        blob = it["topic"] + " " + " ".join(n["title"] + " " + n["snippet"] for n in it["news"])
        if not t or t in seen or t in done_topics or BLOCK_WORDS.search(blob):
            continue
        dup = recent_duplicate(it["topic"] + " " + " ".join(n["title"] for n in it["news"][:1]), "", state)
        if dup:
            print(f"[main] already covered recently: {it['topic'][:60]} (~ {dup[:50]})")
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

    already = [p.get("topic", p["key"]) for p in live]
    performance = insights.summary(state)
    if performance:
        print("[insights] " + performance.replace("\n", "\n[insights] "))
    # a couple of spares, so one skipped post doesn't leave the slot empty
    special, facts_topic, star, all_candidates = "", "", "", candidates
    slot_now = current_slot(datetime.now(timezone.utc))
    if slot_now is None and config.DRY_RUN:  # test run between slots: behave like the next slot
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        slot_now = next(now + timedelta(hours=k) for k in range(1, 25) if (now + timedelta(hours=k)).hour in config.POST_SLOTS_UTC)
        print(f"[main] test run: acting as the {slot_now:%H}:00 UTC slot")
    star_slots = set(config.STAR_SLOTS_UTC)
    all_star = datetime.now(timezone.utc) < config.ALL_STAR_UNTIL
    if all_star:  # owner's request: every post (Reels and photos) about the star for now
        star_slots |= set(config.POST_SLOTS_UTC)
    if (all_star or choose_format(state) == "reel") and slot_now is not None and slot_now.hour in star_slots:
        # the page's best slot always gets a Reel about its best-performing face: fresh news, else facts & stats
        star = insights.star_face(state)
        facts_topic = norm(f"{star} facts")
        terms = [t.lower() for t in trends.STAR_TERMS.get(star, [star])]
        about = lambda c: any(t in (c["topic"] + " " + " ".join(n["title"] for n in c["news"])).lower() for t in terms)
        pool = [c for c in candidates if about(c)]
        try:  # more sources: Google News for the star and their companies + their fan communities on Reddit
            seen_topics = {norm(c["topic"]) for c in pool}
            for c in trends.rank(trends.star_sources(star)):
                blob = c["topic"] + " " + " ".join(n["title"] + " " + n.get("snippet", "") for n in c["news"])
                if (norm(c["topic"]) in seen_topics or norm(c["topic"]) in done_topics or BLOCK_WORDS.search(blob)
                        or recent_duplicate(c["topic"], "", state)):
                    continue
                seen_topics.add(norm(c["topic"]))
                pool.append(c)
        except Exception as e:
            print(f"[main] extra sources for {star} failed: {e}")
        pool.sort(key=lambda c: -len(c.get("trends") or []))  # buzzing on several platforms first
        pool = pool[:10]
        try:
            pool.append(trends.person_facts(star))
        except Exception as e:
            print(f"[main] facts for {star} failed: {e}")
        if pool:
            candidates = pool
            recent = [r["headline"].replace("*", "") for r in live if star.lower() in r.get("headline", "").lower()][-30:]
            special = (f"PEAK SLOT: this post MUST be about {star}, the face that performs best on this page. "
                       f"If a fresh news topic about {star} is listed, use it. Otherwise write a FACTS & STATS Reel "
                       f"(or photo post) from the '{star} facts' topic (e.g. 'X wild facts about {star}', 'his wildest records'): set "
                       f"topic to exactly '{star} facts', and use ONLY numbers and facts stated in the text. "
                       f"wiki_title must be '{star}'.\n"
                       f"The page already posted these {star} stories. NEVER repeat any of them or their main fact "
                       f"(e.g. the same net worth figure again). A NEW angle on money or power is fine. Pick a clearly "
                       f"NEW story, or new facts from a different area (rockets, cars, AI, X, Neuralink, childhood, "
                       f"records, habits...): "
                       f"{' | '.join(recent) or 'none'}\n"
                       f"{insights.star_angles(state, star)}"
                       f"Choose the story most likely to make people STOP scrolling, like, comment, share and follow: "
                       f"the kind of angle that did best above (personality, power, money, a clash, a shock move), "
                       f"never the kind that did worst. Stories buzzing on several platforms are the strongest.\n\n")
            print(f"[main] star slot {slot_now:%H}:00 UTC -> Reel about {star} ({len(pool)} topics)")
    def star_only(new):
        """Star slots: only posts about the star (with the star's face), e.g. no faceless 'DOGE records' story."""
        if not star:
            return new
        keep = []
        for q in new:
            if q.get("wiki_title") != star and star.lower() in (q["headline"] + " " + q.get("topic", "")).lower():
                q["wiki_title"] = star
            if q.get("wiki_title") == star:
                keep.append(q)
            else:
                print(f"[main] star slot: dropping '{q['headline'][:60]}' (not about {star})")
        return keep

    posts = star_only(ai.write_posts(candidates, already, config.POSTS_PER_RUN + 2, performance, special))

    posts.sort(key=lambda p: not p.get("wiki_title"))  # stories about a famous person first
    made, i, faceless, repeats, retries = 0, 0, [], [], 0
    while made < config.POSTS_PER_RUN:
        if i < len(posts):
            p = posts[i]
            i += 1
        elif star and retries < 2:  # no usable star post yet: ask the AI again (the facts topic always works)
            retries += 1
            print(f"[main] no new {star} post yet -> asking the AI again ({retries}/2)")
            posts += star_only(ai.write_posts(candidates, already, config.POSTS_PER_RUN + 2, performance, special +
                                              f"Every post MUST be about {star} (wiki_title '{star}'). " +
                                              ("REJECTED because the page already told these stories - do something "
                                               "else: " + " | ".join(repeats) if repeats else "") + "\n\n"))
            repeats = []
            continue
        elif star and not faceless:  # still nothing new about the star: another world-famous face, not an empty slot
            print(f"[main] no new {star} story today -> a post about another world-famous face instead")
            star = ""
            big = [c for c in all_candidates if any(f.lower() in (c["topic"] + " " + " ".join(
                n["title"] for n in c["news"])).lower() for f in BIG_FACES)][:6]
            now = datetime.now(timezone.utc)
            alt = BIG_FACES[(now.timetuple().tm_yday * 24 + now.hour) % len(BIG_FACES)]
            try:
                big.append(trends.person_facts(alt))
            except Exception as e:
                print(f"[main] facts for {alt} failed: {e}")
            facts_topic = norm(f"{alt} facts")
            new = ai.write_posts(big, already, config.POSTS_PER_RUN + 2, performance,
                                 f"This post MUST be about ONE of these world-famous people: {', '.join(BIG_FACES)}. "
                                 f"wiki_title must be exactly that person's name as written here. If no fresh news "
                                 f"topic about them is listed, write a FACTS & STATS post from the '{alt} facts' topic "
                                 f"(set topic to exactly '{alt} facts'), using ONLY facts stated in the text.\n\n")
            posts += [q for q in new if q.get("wiki_title") in BIG_FACES]
            continue
        elif faceless:  # no story had a real photo of its person: post the best one anyway (never skip a slot)
            p = faceless.pop(0)
            print(f"[main] no story with a real face photo -> posting '{p['headline'][:60]}' with its best image")
        else:
            break
        if p.get("_img"):
            img = p["_img"]
        else:
            facts = facts_topic and norm(p.get("topic", "")) == facts_topic
            if facts:  # facts Reels may repeat the topic (one per slot), never the same facts
                p["key"] = f"{p['key'][:40]}-{datetime.now(timezone.utc):%Y%m%d%H}"
            elif p["key"] in done_keys or norm(p.get("topic", "")) in done_topics:
                print(f"[main] skipping {p['key']} (already posted)")
                continue
            face = p.get("wiki_title", "")
            dup = recent_duplicate(p["headline"] if facts else p.get("topic", "") + " " + p["headline"], face, state)
            if not dup and star and face == star:  # star Reels: no story or theme the page already told
                dup = (recent_duplicate(p["headline"], face, state, hours=72, min_common=1, must="wealth") if "wealth" in _words(p["headline"])
                       else recent_duplicate(p["headline"], face, state, hours=24 * 60, min_common=3))
            if dup:
                print(f"[main] skipping {p['key']}: same story as '{dup[:60]}'")
                repeats.append(p["headline"].replace("*", ""))
                continue
            words = p["headline"] + " " + p["caption"] + " " + str(p.get("voiceover", ""))
            if p["key"] in done_keys or BLOCK_WORDS.search(p["headline"] + " " + p["caption"]) or RISKY.search(words):
                print(f"[main] skipping {p['key']}")
                continue
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
            img = poster.make_poster(p, os.path.join(config.OUTPUT_DIR, f"{stamp}-{p['key']}.jpg"))
            p["_img"] = img
            if not p.get("_person"):  # owner's rule: the picture must show the person -> try the next story first
                print(f"[main] no real photo of {p.get('wiki_title') or 'a famous person'} for '{p['headline'][:50]}' "
                      f"-> trying the next story first")
                faceless.append(p)
                continue
        tags = [h for h in p["hashtags"] if h.lower() != "hypeatlas"]
        caption = p["caption"].strip() + "\n\n" + growth.FOLLOW_LINE + "\n\n" + " ".join("#" + h for h in tags)
        if p.get("_credit"):  # photo licence requires attribution; kept off the poster, in the caption
            caption += "\n\n" + p["_credit"]
        with open(img.replace(".jpg", ".txt"), "w") as f:
            f.write(caption)
        print(f"\n--- POST: {p['headline']}\n{caption}\nimage: {img}\n")

        slot = current_slot(datetime.now(timezone.utc))
        record = {"key": p["key"], "topic": p.get("topic", ""), "headline": p["headline"],
                  "badge": p.get("badge", ""), "slot": f"{slot:%H}" if slot else "",
                  "face": p.get("wiki_title", "") if p.get("_person") else "",
                  "share_groups": p.get("share_groups", [])[:3],
                  "time": datetime.now(timezone.utc).isoformat(timespec="minutes")}
        if config.DRY_RUN:
            record["dry_run"] = True
            print("[main] DRY_RUN is on -> not uploading. Set DRY_RUN=false to go live.")
        fmt = choose_format(state)
        video = None
        if fmt in ("reel", "both") and p.get("_photo") is not None:
            video = reel.make_reel(p, p["_photo"], img.replace(".jpg", ".mp4"))
        record["format"] = "reel" if video and fmt == "reel" else ("both" if video and fmt == "both" else "photo")
        if video and p.get("_reel_credits"):  # extra photos used in the Reel need attribution too
            caption += "\n📷 Also: " + "; ".join(p["_reel_credits"])
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
        if res.get("facebook") and not config.DRY_RUN:  # cross-post to Threads when configured
            try:
                if record["format"] == "reel":
                    media, vid = meta.video_source(record["facebook"]), True
                else:
                    media, vid = meta.photo_public_url(res.get("facebook_photo_id") or res["facebook"]), False
                tid = growth.post_threads(media, growth.threads_text(p["headline"], p["caption"]), vid)
                if tid:
                    record["threads"] = tid
            except Exception as e:
                print(f"[threads] skipped: {e}")
        if config.FIRST_COMMENT and res.get("facebook"):
            insights.first_comment(res["facebook"], p.get("first_comment", ""))
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
