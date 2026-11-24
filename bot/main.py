"""Trend Bot: find trending topic -> AI writes post -> make poster -> upload to FB + IG."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from . import ai, config, growth, insights, meta, poster, reel, trends

# extra safety net on top of the AI's own rules
BLOCK_WORDS = re.compile(
    r"\b(dead|deadly|dies|died|death|deaths|fatal|fatality|fatalities|killed|kill|kills|murder|shooting|shot|stabb|crash|victim|funeral|obituary|"
    r"suicide|attack|war|bomb|terror|rape|abuse|arrest|overdose|missing|gunfire|molotov|execution|executed|execute|lethal|shooter|firing squad|death row|convicted|sentenced|prison|trial|protests?|protesters?|riots?|teargas|"
    # legal trouble (owner, 10-27: risky for the page and easy to get wrong)
    r"indict\w*|raid|raided|raids|subpoena\w*|fbi|(federal|criminal|justice department|doj) (probe|investigation)|"
    r"under investigation|(home|house|office)s? (was |were )?searched)\b", re.I)
# what the trending test slot may be about (owner, 11-11): money and tech, the page's best-performing kinds of news
MONEY_TECH = re.compile(
    r"\b(stocks?|shares|market|wall street|dow|nasdaq|s&p|ipo|earnings|profits?|revenue|billion\w*|trillion\w*|"
    r"millionaires?|valuation|economy|prices?|tariffs?|bitcoin|crypto\w*|interest rates?|the fed|"
    r"ai|a\.i\.|openai|chatgpt|gemini|nvidia|apple|iphone|google|microsoft|amazon|meta|tesla|spacex|nasa|rocket|"
    r"robots?|robotaxi|chips?|semiconductor\w*|tech|startup|gadget|smartphone|electric vehicles?|evs?)\b", re.I)
# Elon Reels since 10-29 (owner, 11-11: "you should know what gets the best response"): the hits were Tesla products
# and huge money (Cybercab 26k, $450 billion 21k, Cybertruck 11k, Optimus 9k views); politics (avg 1.4k) and niche
# tech (Neuralink, orbit data centers, analyst targets, software: 300-400) did worst. Star topics are ranked by this.
STAR_HOT = re.compile(
    r"\b(cybercab|cybertruck|optimus|robotaxi|robots?|model [3ysx]|roadster|semi|tesla|starship|falcon|spacex|"
    r"\$?\d[\d.,]*\s*(billion|trillion)|billions?|trillions?|net worth|richest|wealth|pay ?package|wipeout|"
    r"revenue|ipo|stock|shares)\b", re.I)
STAR_COLD = re.compile(
    r"\b(midterms?|elections?|republicans?|democrats?|gop|campaign|senate|congress|voters?|ballot|trump|"
    r"neuralink|data cent(er|re)s?|price target|analysts?|morningstar|c\+\+|software update)\b", re.I)


def star_appeal(c):
    """How likely a star topic is to go viral on this page: +1 Tesla product / big money / SpaceX, -1 politics or
    niche tech (both can match: they cancel out)."""
    blob = c["topic"] + " " + " ".join(n["title"] for n in c["news"][:3])
    return bool(STAR_HOT.search(blob)) - bool(STAR_COLD.search(blob))


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


# names that are in almost every star post, so sharing them doesn't make two stories the same
COMMON_NAMES = {"elon", "musk", "musk's", "tesla", "tesla's", "spacex", "spacex's", "x", "ai", "us", "u.s.", "report",
                "reports", "breaking", "new", "the", "a", "his", "he", "why", "how", "what", "after", "inside", "is",
                "this", "in", "is", "just", "who", "when", "billionaire", "ceo", "world's", "richest"}


def same_name_today(headline, face, state, hours=24):
    """Two posts about the same star within a day must not share another name - 'Putin calls Musk a genius' and
    'SpaceX retreats as Putin criticizes Musk', or two Cybercab posts 2 hours apart (owner, 10-29: no duplicates).
    Returns the old headline or ''."""
    def names(h):
        words = re.findall(r"[A-Za-z][\w.'’-]*", h.replace("*", ""))
        return {w.lower().replace("’", "'") for w in words if w[0].isupper()} - COMMON_NAMES - _words(face)
    mine, now = names(headline), datetime.now(timezone.utc)
    for r in state["posted"]:
        try:
            if r.get("dry_run") or r.get("face") != face or now - datetime.fromisoformat(r["time"]) > timedelta(hours=hours):
                continue
        except Exception:
            continue
        if mine & names(r.get("headline", "")):
            return r.get("headline", "").replace("*", "")
    return ""


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


def add_facts(cands, n=4):
    """The strongest topics get the real reporting (several outlets + one full article), so the AI writes from
    facts and not from a single headline. Facts topics already carry their Wikipedia text."""
    for c in cands[:n]:
        if "facts" not in c and not any(x.get("source") == "Wikipedia" for x in c["news"]):
            c["facts"] = trends.article_facts(c["news"][0]["title"] if c["news"] else c["topic"])


def facts_for(p, cands):
    """Everything the bot knows about the post's topic, as text for the AI editor."""
    mine = set(norm(p.get("topic", "") + " " + p["headline"].replace("*", "")).split())
    best = max(cands, key=lambda c: len(mine & set(norm(c["topic"] + " " + " ".join(
        n["title"] for n in c["news"])).split())), default=None)
    if not best:
        return ""
    items = best["news"][:3] + (best.get("facts") or [])
    return "\n".join(f"- {n['title']}: {n.get('snippet', '')[:1200]}" for n in items)


def engage(state):
    """Live runs: answer comments and the daily growth to-do."""
    if config.DRY_RUN:
        return
    try:
        insights.reply_comments(state)
        growth.daily_todo(state)
    except Exception as e:
        print(f"[insights] skipped: {e}")
    save_state(state)


def main():
    print(f"== Trend Bot | page: {config.PAGE_NAME} | countries: {config.COUNTRIES} | DRY_RUN={config.DRY_RUN}")
    state = load_state()
    try:  # read-only, so it also runs in test mode
        insights.update_stats(state)
    except Exception as e:
        print(f"[insights] stats skipped: {e}")
    if not should_post_now(state):
        engage(state)  # between slots: answer comments + daily to-do
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
    if not candidates and datetime.now(timezone.utc) >= config.ALL_STAR_UNTIL:  # star-only: the star's own news
        print("Nothing new to post right now.")                                  # and facts below still work
        return 0

    already = [p.get("topic", p["key"]) for p in live]
    performance = insights.summary(state)
    if performance:
        print("[insights] " + performance.replace("\n", "\n[insights] "))
    # a couple of spares, so one skipped post doesn't leave the slot empty
    special, facts_topic, star, all_candidates = "", "", "", candidates
    life_key = ""  # life-story slot: the chapter of the star's life this post tells
    slot_now = current_slot(datetime.now(timezone.utc))
    if slot_now is None and config.DRY_RUN:  # test run between slots: behave like the next slot
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        slot_now = next(now + timedelta(hours=k) for k in range(1, 25) if (now + timedelta(hours=k)).hour in config.POST_SLOTS_UTC)
        print(f"[main] test run: acting as the {slot_now:%H}:00 UTC slot")
    star_slots = set(config.STAR_SLOTS_UTC)
    all_star = datetime.now(timezone.utc) < config.ALL_STAR_UNTIL
    if all_star:  # owner's request: every post (Reels and photos) about the star for now
        star_slots |= set(config.POST_SLOTS_UTC)
    star_slots -= config.TREND_SLOTS_UTC  # trending slots are about whatever is hottest, not the star
    if slot_now is not None and slot_now.hour in config.TREND_SLOTS_UTC:
        # money & tech only (what this page's audience watches most); the topic buzzing on the most platforms
        # (X, Reddit, Google, Wikipedia) first, then the biggest search traffic
        money_tech = [c for c in candidates
                      if MONEY_TECH.search(c["topic"] + " " + " ".join(n["title"] for n in c["news"][:3]))]
        if money_tech:
            candidates = sorted(money_tech, key=lambda c: (-len(c.get("trends") or []), -c["power"]))
            special = ("TRENDING SLOT: this post MUST be about the single HOTTEST MONEY or TECH topic right now (stocks, "
                       "big money, companies, AI, gadgets, space, cars) - the one trending on the most platforms (X, "
                       "Reddit, Google, Wikipedia) that a US audience most wants to see. Topics are listed hottest "
                       "first; take the top one unless it is unsafe, political or has no famous person or real image. "
                       "It does NOT have to be about the page's usual star.\n\n")
            print(f"[main] trending slot {slot_now:%H}:00 UTC -> hottest money/tech topic: {candidates[0]['topic'][:60]}")
        else:  # nothing hot about money or tech: the slot stays an Elon slot
            print(f"[main] trending slot {slot_now:%H}:00 UTC: no money/tech topic -> star post instead")
            star_slots.add(slot_now.hour)
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
        # what goes viral here first (Tesla products, big money, SpaceX; politics and niche tech last), then
        # buzzing on several platforms; politics is still used when it is the only fresh news
        pool.sort(key=lambda c: (-star_appeal(c), -len(c.get("trends") or [])))
        for c in pool[:5]:
            print(f"[main]   appeal {star_appeal(c):+d} | {c['topic'][:70]}")
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
                       f"never the kind that did worst. Stories buzzing on several platforms are the strongest.\n"
                       f"On this page the biggest hits were Tesla PRODUCTS and HUGE MONEY: 'Tesla adopts Cybercab "
                       f"inverter for 2026 Model 3' (26k views), '$450 billion two-day wipeout' (21k), 'extra shift to "
                       f"Cybertruck before Cybercab' (11k), 'Optimus robot reservations' (9k). Politics averaged 1.4k "
                       f"and niche tech (Neuralink, orbit data centers, analyst targets, software) 300-400: pick those "
                       f"ONLY when no Tesla / SpaceX / money story is listed. Topics are listed best first. HOOK: name "
                       f"the product or the dollar figure in the first 8 words, in patterns like '[product] just [what "
                       f"happened].' or 'Elon Musk [lost/made/spent] $[figure] in [time].', using ONLY facts and "
                       f"numbers from the reports.\n\n")
            print(f"[main] star slot {slot_now:%H}:00 UTC -> Reel about {star} ({len(pool)} topics)")
        chapters = trends.LIFE_CHAPTERS.get(star, [])
        if slot_now.hour in config.LIFE_SLOTS_UTC and chapters:
            # a chapter of the star's life no post told yet (a different one first each day, so a chapter the
            # duplicate check keeps rejecting, e.g. Zip2 told on 11-06, never blocks the rest); when all are told,
            # the one told longest ago
            last = {r["life"]: r["time"] for r in state["posted"] if r.get("life") and not r.get("dry_run")}
            recent = [r["headline"].replace("*", "") for r in live if star.lower() in r.get("headline", "").lower()][-30:]
            day = datetime.now(timezone.utc).timetuple().tm_yday
            order = sorted(range(len(chapters)), key=lambda i: (last.get(chapters[i][0], ""), (i - day) % len(chapters)))
            for ch in (chapters[i] for i in order):
                try:
                    ft = trends.life_facts(star, ch)
                except Exception as e:
                    print(f"[main] life story '{ch[1]}' failed: {e}")
                    continue
                candidates, life_key, facts_topic = [ft], ch[0], norm(ft["topic"])
                special = (f"LIFE STORY SLOT: write ONE Reel about a chapter of {star}'s life, told like a gripping "
                           f"mini documentary: topic exactly '{ft['topic']}', wiki_title '{star}', badge 'VIRAL'. "
                           f"This Reel is LONGER, 25-30 seconds: voiceover 60-72 words in 7-9 short sentences (this "
                           f"replaces the 30-36 word rule) and 8-10 scenes. "
                           f"HOOK (first 8 words): the most surprising TRUE moment of this chapter (an age, a dollar "
                           f"figure, a near-failure). Then what happened step by step, the turning point, and how it "
                           f"led to who he is today; end with a question. Use ONLY facts from the text, never invent "
                           f"quotes, numbers or feelings. The little-known details from the feature stories are the "
                           f"most engaging part: use the best one. A real quote from Wikiquote may be used word for "
                           f"word with when he said it (e.g. 'In 1998 he said: ...'), never changed. The page already posted these {star} stories, tell this "
                           f"chapter from a different angle: {' | '.join(recent) or 'none'}\n\n")
                print(f"[main] life-story slot {slot_now:%H}:00 UTC -> {star}: {ch[1]}")
                break
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

    add_facts(candidates)
    known = candidates + all_candidates  # what the AI editor checks the scripts against
    posts = star_only(ai.write_posts(candidates, already, config.POSTS_PER_RUN + 2, performance, special))

    posts.sort(key=lambda p: not p.get("wiki_title"))  # stories about a famous person first
    made, i, faceless, repeats, retries = 0, 0, [], [], 0
    weak, edits, fact_tries = [], 0, 0  # scripts scored under 8, scripts checked, extra star facts asked for
    max_facts, world_tries = (5 if all_star else 3), 0
    while made < config.POSTS_PER_RUN:
        if i < len(posts):
            p = posts[i]
            i += 1
        elif star and retries < 2 and not weak:  # no usable star post yet: ask the AI again (the facts topic always works)
            retries += 1
            print(f"[main] no new {star} post yet -> asking the AI again ({retries}/2)")
            posts += star_only(ai.write_posts(candidates, already, config.POSTS_PER_RUN + 2, performance, special +
                                              f"Every post MUST be about {star} (wiki_title '{star}'). " +
                                              ("REJECTED because the page already told these stories - do something "
                                               "else: " + " | ".join(repeats) if repeats else "") + "\n\n"))
            repeats = []
            continue
        elif weak:  # no script scored 8+: post the best-scored one (never an empty slot)
            weak.sort(key=lambda x: -x[0])
            score, p = weak.pop(0)
            print(f"[main] no script scored 8+ -> posting the best one ({score:.0f}/10): {p['headline'][:60]}")
        elif star and fact_tries < max_facts:  # no new star story: new facts from another part of their article
            fact_tries += 1                      # (owner, 10-27: star slots stay about the star, not Oprah)
            print(f"[main] no new {star} story -> facts from another part of their life ({fact_tries}/{max_facts})")
            try:
                ft = trends.person_facts(star, shift=fact_tries)
            except Exception as e:
                print(f"[main] facts for {star} failed: {e}")
                continue
            known.append(ft)
            posts += star_only(ai.write_posts([ft], already, 1, performance, special +
                                              f"Write ONE FACTS & STATS post about {star} from the '{star} facts' topic "
                                              f"(topic exactly '{star} facts'), using ONLY facts from its first "
                                              "section - facts the page never posted before. " +
                                              ("Already told, do NOT repeat: " + " | ".join(repeats) if repeats else "")
                                              + "\n\n"))
            repeats = []
            continue
        elif star and all_star and world_tries < 4:  # owner, 10-29: ONLY the star, no repeats: facts about one
            world_tries += 1                           # of the star's companies/products, told with the star's face
            items = trends.STAR_WORLD.get(star, [])
            if not items:
                world_tries = 4
                continue
            now = datetime.now(timezone.utc)
            article = items[(now.timetuple().tm_yday * 7 + now.hour + world_tries * 5) % len(items)]
            print(f"[main] still nothing new about {star} -> facts about {star}'s {article} ({world_tries}/4)")
            try:
                ft = trends.person_facts(article, shift=world_tries)
            except Exception as e:
                print(f"[main] facts for {article} failed: {e}")
                continue
            known.append(ft)
            facts_topic = norm(ft["topic"])
            posts += star_only(ai.write_posts([ft], already, 1, performance, special +
                                              f"Write ONE FACTS & STATS post about {star}'s {article} from the "
                                              f"'{article} facts' topic (topic exactly '{article} facts'): the headline "
                                              f"starts with {star}'s name, wiki_title '{star}', ONLY facts from the "
                                              "text, facts the page never posted before. " +
                                              ("Already told, do NOT repeat: " + " | ".join(repeats) if repeats else "")
                                              + "\n\n"))
            repeats = []
            continue
        elif star and not faceless and not all_star:  # nothing new about the star: another world-famous face
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
            add_facts(big)
            known += big
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
            if not dup and star and face == star:  # same name within a day: 'Putin calls Musk a genius' twice
                dup = same_name_today(p["headline"], face, state)
            if not dup and face:  # same story in other words ('teases Roadster' = 'unveils flying Tesla')
                since = datetime.now(timezone.utc) - timedelta(days=7)
                told = [r["headline"].replace("*", "") for r in live if r.get("face") == face
                        and datetime.fromisoformat(r["time"]) > since][-20:]
                dup = ai.same_story(p["headline"].replace("*", ""), told)
            if dup:
                print(f"[main] skipping {p['key']}: same story as '{dup[:60]}'")
                repeats.append(p["headline"].replace("*", ""))
                continue
            if not p.get("_edited") and edits < 3:
                # a strict AI editor scores the post (Reels: hook, clarity, sense, facts; photos: facts - the
                # headline must say exactly what the reports say) and gets weak or inaccurate ones rewritten
                p["_edited"], edits = True, edits + 1
                score, p = ai.edit_reel(p, facts_for(p, known), reel=choose_format(state) == "reel",
                                        long=bool(life_key and facts))  # life-story series: 25-30s script
                if score is not None and score < 8:
                    print(f"[main] editor: weak script ({score:.0f}/10) -> trying the next story first")
                    weak.append((score, p))
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
        follow_line, part = growth.FOLLOW_LINE, 0
        if life_key and facts_topic and norm(p.get("topic", "")) == facts_topic:
            # a numbered series: people follow so they don't miss the next part (owner, 11-11: 10k followers)
            part = 1 + sum(1 for r in state["posted"] if r.get("life") and not r.get("dry_run"))
            p["_follow"] = f"part {part + 1} of his life story tomorrow"
            p["_life_part"] = part  # 'ELON'S LIFE • PART n' on top and a 25-30 second Reel
            if not p["caption"].startswith("📖"):
                p["caption"] = f"📖 Elon Musk's Life, Part {part}\n\n" + p["caption"].strip()
            follow_line = f"👉 Follow Hype Atlas so you don't miss Part {part + 1} tomorrow 🚀 #HypeAtlas"
        caption = p["caption"].strip() + "\n\n" + follow_line + "\n\n" + " ".join("#" + h for h in tags)
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
        if life_key and facts_topic and norm(p.get("topic", "")) == facts_topic:
            record["life"], record["life_part"] = life_key, part  # this chapter is told: the next slot takes another
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
            # each platform on its own: 11-23 Facebook blocked the Page (identity check, code 368) and the Instagram
            # Reels were lost too, because a Facebook failure turned the whole post into a photo
            try:
                record["facebook_reel"] = record["facebook"] = res["facebook"] = meta.publish_reel(video, caption)
            except Exception as e:
                print(f"[main] Facebook Reel failed: {e}")
                record["facebook_error"] = str(e)[:300]
            if config.POST_TO_INSTAGRAM and config.IG_USER_ID:  # same Reel on Instagram
                try:
                    record["instagram"] = res["instagram"] = meta.post_instagram_reel(video, caption)
                    print(f"[meta] Instagram Reel posted: {record['instagram']}")
                except Exception as e:
                    print(f"[meta] Instagram Reel failed: {e}")
            if not res:  # the Reel went nowhere -> fall back to the photo post
                print("[main] Reel failed on every platform -> posting the photo instead")
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
    engage(state)  # after posting, so comment replies never use up the AI quota the post needs
    return 0


if __name__ == "__main__":
    sys.exit(main())
