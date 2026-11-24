"""Collect what is trending right now in English-speaking countries (free, no keys)."""
import os
import re
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; TrendBot/1.0)"}

GOOGLE_TRENDS = "https://trends.google.com/trending/rss?geo={geo}"
GOOGLE_NEWS = {
    "US": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "GB": "https://news.google.com/rss?hl=en-GB&gl=GB&ceid=GB:en",
    "CA": "https://news.google.com/rss?hl=en-CA&gl=CA&ceid=CA:en",
    "AU": "https://news.google.com/rss?hl=en-AU&gl=AU&ceid=AU:en",
}


def _local(tag):
    return tag.split("}", 1)[-1]


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _text(el, name):
    c = _child(el, name)
    return (c.text or "").strip() if c is not None and c.text else ""


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()


def _get(url):
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return ET.fromstring(r.content)


def google_trends(geo):
    """Google Trends 'Trending now' feed: search topic + traffic + related news."""
    out = []
    root = _get(GOOGLE_TRENDS.format(geo=geo))
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        title = _text(item, "title")
        if not title:
            continue
        traffic = _text(item, "approx_traffic")
        news = []
        for n in _children(item, "news_item"):
            news.append({
                "title": _clean(_text(n, "news_item_title")),
                "snippet": _clean(_text(n, "news_item_snippet")),
                "source": _text(n, "news_item_source"),
                "url": _text(n, "news_item_url"),
            })
        out.append({"topic": title, "traffic": traffic, "country": geo,
                    "source": "google_trends", "news": news[:3]})
    return out


def google_news(geo):
    """Top headlines feed as a second source."""
    url = GOOGLE_NEWS.get(geo)
    if not url:
        return []
    out = []
    root = _get(url)
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        title = _clean(_text(item, "title"))
        src = _text(item, "source")
        if src and title.endswith(" - " + src):
            title = title[: -len(" - " + src)]
        if title:
            out.append({"topic": title, "traffic": "", "country": geo, "source": "google_news",
                        "news": [{"title": title, "snippet": _clean(_text(item, "description")),
                                  "source": src, "url": _text(item, "link")}]})
    return out[:15]


# The page's focus: famous US names in tech, AI and politics. Their latest US news is fetched on every run
# and anything that mentions them is ranked first. Override with the FOCUS variable (comma separated).
FOCUS = [t.strip() for t in (os.getenv("FOCUS", "") or (
    "Donald Trump, Elon Musk, OpenAI, ChatGPT, Sam Altman, Mark Zuckerberg, Meta, Google, Sundar Pichai, "
    "Nvidia, Jensen Huang, Microsoft, Satya Nadella, Apple, Tim Cook, YouTube, LinkedIn, Amazon, Jeff Bezos, "
    "Tesla, SpaceX, Gemini AI, AI tools")).split(",") if t.strip()]
FOCUS_BOOST = 4


def focus_news(terms=None, per_term=2):
    """Latest US Google News for each focus name (last 24 hours)."""
    out = []
    for term in terms or FOCUS:
        try:
            root = _get("https://news.google.com/rss/search?q=" + requests.utils.quote(f'"{term}" when:1d')
                        + "&hl=en-US&gl=US&ceid=US:en")
        except Exception as e:
            print(f"[trends] focus '{term}' failed: {e}")
            continue
        n = 0
        for item in root.iter():
            if _local(item.tag) != "item" or n >= per_term:
                continue
            title = _clean(_text(item, "title"))
            src = _text(item, "source")
            if src and title.endswith(" - " + src):
                title = title[: -len(" - " + src)]
            if title:
                out.append({"topic": title, "traffic": "30K+", "country": "US", "source": "focus",
                            "news": [{"title": title, "snippet": _clean(_text(item, "description")),
                                      "source": src, "url": _text(item, "link")}]})
                n += 1
    print(f"[trends] focus news: {len(out)} items for {len(terms or FOCUS)} names")
    return out


# Instantly recognisable faces on US Facebook (boosted when they appear in a topic; news is not fetched for each)
FACES = ["Taylor Swift", "Kim Kardashian", "Beyonce", "Rihanna", "Selena Gomez", "Kanye West", "Drake", "MrBeast",
         "LeBron James", "Cristiano Ronaldo", "Lionel Messi", "Barack Obama", "Joe Biden", "Kamala Harris", "JD Vance",
         "Oprah", "Bill Gates", "Warren Buffett", "Jeff Bezos", "Mark Cuban", "Travis Kelce", "Justin Bieber",
         "Kylie Jenner", "Dwayne Johnson", "Tom Cruise", "Prince Harry", "Meghan Markle", "King Charles"]
_TRENDING_PEOPLE = []  # filled each run from Wikipedia's most-viewed people


def _get_text(url, headers=None):
    r = requests.get(url, headers=headers or BROWSER_UA, timeout=20)
    r.raise_for_status()
    return r.text


BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0 Safari/537.36"}


def _news_for(term):
    """One fresh US news item about a term, so the AI has real facts (None if nothing in the last day)."""
    try:
        root = _get("https://news.google.com/rss/search?q=" + requests.utils.quote(f"{term} when:1d")
                    + "&hl=en-US&gl=US&ceid=US:en")
    except Exception:
        return None
    for item in root.iter():
        if _local(item.tag) == "item":
            title = _clean(_text(item, "title"))
            src = _text(item, "source")
            if src and title.endswith(" - " + src):
                title = title[: -len(" - " + src)]
            return {"title": title, "snippet": _clean(_text(item, "description")), "source": src,
                    "url": _text(item, "link")}
    return None


X_JUNK = re.compile(r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend|morning|night|\beve\b)"
                    r"|\b(token|coin|crypto|airdrop|nft|giveaway|presale)\b|^\$|^#?(gm|gn)$", re.I)
X_SOURCES = [  # trend-tracker pages for X (Twitter) US; the first that answers is used
    ("https://trends24.in/united-states/", r"class=trend-link>([^<]+)</a>"),
    ("https://getdaytrends.com/united-states/", r'<a[^>]*href="/united-states/trend/[^"]*"[^>]*>([^<]+)</a>'),
]


def x_trends(limit=12):
    """What is trending on X (Twitter) in the US right now, each with one news item for facts."""
    found, last = [], None
    for url, pattern in X_SOURCES:
        try:
            found = re.findall(pattern, _get_text(url))
            if found:
                break
        except Exception as e:
            last = e
    if not found:
        raise RuntimeError(f"no X trend source answered ({last})")
    names = []
    for n in found:
        n = _clean(n)
        if n and n not in names:
            names.append(n)
        if len(names) >= 25:
            break
    out = []
    for rank, name in enumerate(names):
        if len(out) >= limit:
            break
        if X_JUNK.search(name):
            continue
        term = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name.lstrip("#"))  # #TaylorSwift -> Taylor Swift
        news = _news_for(term)
        # the news must really be about the trend (else 'Good Thursday' matches a golf story)
        if news and all(w.lower() in (news["title"] + " " + news["snippet"]).lower() for w in term.split()[:3]):
            out.append({"topic": news["title"], "traffic": f"{max(20, 120 - rank * 6)}K", "country": "US",
                        "source": "x", "news": [news], "trend": f"X trend #{rank + 1}: {name}"})
    print(f"[trends] X trends: {len(out)} with news (of {len(names)})")
    return out


REDDIT = "https://www.reddit.com/r/news+technology+entertainment+popculturechat+Fauxmoi+nottheonion+movies+nba+nfl/top/.rss?t=day&limit=25"


def reddit_top(limit=15, url=REDDIT):
    """Today's top posts in Reddit's big news/tech/celebrity communities (may be rate-limited: then skipped)."""
    root = ET.fromstring(_get_text(url).encode())
    out = []
    for rank, entry in enumerate(e for e in root.iter() if _local(e.tag) == "entry"):
        title = _clean(_text(entry, "title"))
        cat = _child(entry, "category")
        sub = cat.get("term", "") if cat is not None else ""
        if title and len(out) < limit:
            out.append({"topic": title, "traffic": f"{max(15, 80 - rank * 4)}K", "country": "US", "source": "reddit",
                        "news": [{"title": title, "snippet": f"Top post today on Reddit r/{sub}", "source": f"r/{sub}",
                                  "url": ""}], "trend": f"Reddit r/{sub} top #{rank + 1}"})
    print(f"[trends] Reddit: {len(out)} items")
    return out


# the star's world: news about their companies counts too, and the fan communities show what fans talk about
STAR_TERMS = {"Elon Musk": ["Elon Musk", "Tesla", "SpaceX", "Starlink", "xAI Grok", "Neuralink", "Cybertruck", "Optimus robot"]}
# the star's world: Wikipedia articles with professional real photos for Reel scenes (rockets, cars, robots...)
STAR_WORLD = {"Elon Musk": ["SpaceX Starship", "Tesla Cybertruck", "Optimus (robot)", "Falcon 9", "Starlink", "Tesla, Inc.",
                            "SpaceX", "Neuralink", "Gigafactory Texas", "Tesla Model S", "X (social network)",
                            "The Boring Company", "Tesla Roadster (second generation)", "SpaceX Dragon 2", "Tesla Cybercab",
                            "Tesla Model Y", "Starbase, Texas", "Tesla Semi", "Grok (chatbot)", "xAI (company)"]}
STAR_SUBS = {"Elon Musk": "elonmusk+teslamotors+SpaceX+spacex+TeslaLounge+starlink"}


def star_sources(star):
    """Fresh material about the star from several places: Google News for them and their companies, and today's
    top posts in their fan communities on Reddit. X trends and Reddit's big subs are already in the normal topics."""
    items = focus_news(STAR_TERMS.get(star, [star]), per_term=3)
    for it in items:
        it["trend"] = "Google News (last 24h)"
    if star in STAR_SUBS:
        try:
            items += reddit_top(8, f"https://www.reddit.com/r/{STAR_SUBS[star]}/top/.rss?t=day&limit=15")
        except Exception as e:
            print(f"[trends] Reddit fan communities for {star} failed: {e}")
    print(f"[trends] {len(items)} fresh items about {star} and their world")
    return items


def wiki_people(limit=20):
    """The people the world looked up most on Wikipedia yesterday (= who has 'face value' right now)."""
    from datetime import date, timedelta
    day = (date.today() - timedelta(days=1)).strftime("%Y/%m/%d")
    for attempt in range(3):
        try:
            r = requests.get("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/" + day,
                             headers={"User-Agent": "HypeAtlasBot/1.0 (facebook.com/hypeatlasnews)"}, timeout=30)
            r.json()
            break
        except Exception:
            if attempt == 2:
                raise
    arts = [a["article"] for a in r.json()["items"][0]["articles"]
            if ":" not in a["article"] and a["article"] != "Main_Page"][:60]
    ua = {"User-Agent": "HypeAtlasBot/1.0 (facebook.com/hypeatlasnews)"}
    q = requests.get("https://en.wikipedia.org/w/api.php", headers=ua, timeout=20, params={
        "action": "query", "format": "json", "prop": "pageprops", "ppprop": "wikibase_item",
        "titles": "|".join(arts[:50])}).json()
    qids = {p.get("pageprops", {}).get("wikibase_item"): p["title"] for p in q["query"]["pages"].values()
            if p.get("pageprops", {}).get("wikibase_item")}
    w = requests.get("https://www.wikidata.org/w/api.php", headers=ua, timeout=25, params={
        "action": "wbgetentities", "format": "json", "props": "claims", "ids": "|".join(list(qids)[:50])}).json()
    people = []
    for qid, ent in w.get("entities", {}).items():
        p31 = [c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for c in ent.get("claims", {}).get("P31", [])]
        claims = ent.get("claims", {})
        dead = "P570" in claims  # date of death: skip (no death stories)
        criminal = "P1399" in claims  # 'convicted of': people famous for crimes are not the page's faces
        if "Q5" in p31 and not dead and not criminal:
            people.append(qids[qid])
    order = {t.replace("_", " "): i for i, t in enumerate(arts)}
    people.sort(key=lambda t: order.get(t, 99))
    print(f"[trends] most looked-up people: {', '.join(people[:limit])}")
    return people[:limit]


FACT_STOP = {"with", "from", "that", "this", "after", "over", "into", "about", "says", "said", "will", "have", "just",
             "their", "they", "what", "when", "your", "more", "than", "news", "report", "reports"}


def article_facts(query, want=4, bodies=1, recent=True):
    """The real reporting behind a headline, so the AI writes from facts and not from one line: Bing News summaries
    from several outlets (numbers, quotes, dates) plus the full text of the first article that can be read.
    Google News RSS only gives headlines. recent=False searches all dates (feature stories about the past).
    Returns news items like the other sources ([] if nothing found)."""
    import html
    import urllib.parse as up
    try:
        r = requests.get("https://www.bing.com/news/search", headers=BROWSER_UA, timeout=20,
                         params={"q": query, "format": "rss", **({"qft": 'interval="7"'} if recent else {})})
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"[facts] Bing News failed for '{query[:50]}': {e}")
        return []
    # Bing also returns unrelated stories for loose searches (e.g. someone else's 'split'): keep only reports that
    # share most of the search's main words (not just the person's name), or the AI may mix two stories into one
    key = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 3} - FACT_STOP
    need = max(min(2, len(key)), -(-len(key) * 3 // 5))  # 60%, rounded up
    out, read = [], 0
    for item in root.iter("item"):
        if len(out) >= want:
            break
        title, link = _clean(_text(item, "title")), _text(item, "link")
        summary = _clean(html.unescape(_text(item, "description")))
        url = up.parse_qs(up.urlparse(link).query).get("url", [link])[0]
        source = up.urlparse(url).netloc.replace("www.", "")
        if not title or not summary:
            continue
        if len(key & set(re.findall(r"[a-z0-9]+", (title + " " + summary).lower()))) < need:
            continue
        body = ""
        if read < bodies:
            try:
                import trafilatura
                page = requests.get(url, headers=BROWSER_UA, timeout=15)
                body = (trafilatura.extract(page.text) or "") if page.status_code == 200 else ""
                if len(body) > 300:
                    read += 1
                else:
                    body = ""
            except Exception as e:
                print(f"[facts] could not read {source}: {str(e)[:80]}")
        out.append({"title": title, "snippet": (body or summary)[:1500], "source": source, "article": True})
    print(f"[facts] {len(out)} reports ({read} full article) for: {query[:60]}")
    return out


# Life-story Reels (owner, 11-11: "Reels on Elon Musk's life that engage the audience, never duplicated"): one
# dramatic chapter per day, told like a mini documentary; each chapter is used once (record["life"] in posted.json)
# until all are told. (key, title, Wikipedia article, words that find the right paragraphs)
LIFE_CHAPTERS = {"Elon Musk": [
    ("blastar", "sold his first video game at 12", "Elon Musk", ["blastar", "video game", "computer programming"]),
    ("canada", "left South Africa alone at 17", "Elon Musk", ["canada", "queen's", "moved", "citizenship"]),
    ("stanford", "quit a Stanford PhD after two days", "Elon Musk", ["stanford", "two days", "ph.d", "doctoral"]),
    ("zip2", "Zip2, his first company", "Zip2", ["zip2", "compaq", "million", "founded"]),
    ("paypal", "X.com, PayPal and being fired as CEO", "Elon Musk", ["x.com", "paypal", "ousted", "ebay", "confinity"]),
    ("falcon1", "three failed rockets before success", "Falcon 1", ["falcon 1", "failure", "fourth", "flight", "orbit", "2008"]),
    ("2008", "2008: Tesla and SpaceX nearly went bankrupt", "Elon Musk", ["2008", "bankrupt", "nasa", "contract", "recession"]),
    ("roadster", "a Tesla Roadster sent into space", "Elon Musk's Tesla Roadster", ["roadster", "falcon heavy", "starman", "orbit", "mars"]),
    ("demo2", "first private rocket to fly NASA astronauts", "Crew Dragon Demo-2", ["crew dragon", "astronauts", "first", "launch", "iss", "behnken"]),
    ("boring", "The Boring Company and the flamethrowers", "The Boring Company", ["flamethrower", "tunnel", "las vegas", "sold"]),
    ("optimus", "the Optimus robot story", "Optimus (robot)", ["optimus", "unveiled", "prototype", "humanoid", "2021", "walk"]),
    ("chopsticks", "catching a giant rocket with chopsticks", "Starship flight test 5", ["chopsticks", "catch", "tower", "booster"]),
    ("twitter", "buying Twitter for $44 billion", "Acquisition of Twitter by Elon Musk", ["twitter", "44 billion", "acquisition", "takeover"]),
    ("richest", "becoming the richest person on Earth", "Elon Musk", ["richest", "net worth", "forbes", "bloomberg"]),
    ("falconheavy", "the Falcon Heavy gamble", "Falcon Heavy test flight", ["falcon heavy", "boosters", "landed", "side", "launch"]),
]}


def _wiki_text(article, site="en.wikipedia.org"):
    pages = requests.get(f"https://{site}/w/api.php", timeout=20, params={
        "action": "query", "format": "json", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": article},
        headers={"User-Agent": "HypeAtlasBot/1.0 (facebook.com/hypeatlasnews)"}).json()["query"]["pages"]
    return next(iter(pages.values())).get("extract", "")


# no swearing and no politics in a life story ('video game' also found a 2025 political post)
LIFE_UNSAFE = re.compile(r"\b(shit|bullshit|fuck\w*|damn|hell|political|politics|left|right[- ]wing|woke|democrat\w*|"
                         r"republican\w*|trump|biden|election\w*|government|immigra\w*|"
                         r"investigation\w*|lawsuit\w*|court|sued|probe\w*|indict\w*)\b", re.I)  # nor legal trouble


def _about_chapter(text, words):
    """The chapter's own word, or two of its words: 'Elon Musk' alone is not enough (every Musk story has it)."""
    t = text.lower()
    return words[0] in t or sum(w in t for w in words[1:]) >= 2


def _quotes(name, words, want=2):
    """Real things the person said about this chapter, word for word with where and when (Wikiquote)."""
    lines = [l.strip() for l in _wiki_text(name, "en.wikiquote.org").split("\n") if l.strip()]
    source = re.compile(r"^(interview|from|as quoted|quoted|in |on |at |speech|tweet|post|statement|talk)|\(\d{4}\)|\b(19|20)\d\d\b")
    out = []
    for i, q in enumerate(lines[:-1]):
        nxt = lines[i + 1]
        if LIFE_UNSAFE.search(q) or not _about_chapter(q, words):
            continue
        if 40 < len(q) < 300 and not source.search(q.lower()[:40]) and source.search(nxt.lower()) and len(nxt) < 250:
            out.append((sum(w in q.lower() for w in words), f'"{q}" ({nxt})'))
    return [q for _, q in sorted(out, key=lambda x: -x[0])[:want]]


def life_facts(name, chapter):
    """Facts for a life-story Reel, from three sources: the paragraphs of the chapter's Wikipedia article that match
    its words (the trusted base), what the person really said about it (Wikiquote, word for word, with the source),
    and feature stories about it from any year (Bing News: the little-known details people love)."""
    key, title, article, words = chapter
    paras = [p.strip() for p in _wiki_text(article).split("\n") if len(p.strip()) > 150 and not p.startswith("=")]
    hits = lambda p: 3 * (words[0] in p.lower()) + sum(w in p.lower() for w in words[1:])  # the chapter's own word first
    best = sorted((p for p in paras if hits(p)), key=lambda p: -hits(p))[:3]
    if not best:
        raise RuntimeError(f"nothing about '{title}' in {article}")
    news = [{"title": f"{name}: {title} ({article}, Wikipedia)", "snippet": p[:1200], "source": "Wikipedia", "url": ""}
            for p in best]
    extra = []  # 'facts': the writer and the editor both see all of these (news only the first 3)
    try:
        quotes = _quotes(name, words)
        if quotes:
            extra.append({"title": f"What {name} really said about it (Wikiquote, exact words, may be quoted with "
                                   f"where and when)", "snippet": " ".join(quotes)[:1200], "source": "Wikiquote"})
    except Exception as e:
        print(f"[trends] Wikiquote for {name} failed: {e}")
    # short search (the article, or the chapter's own word): Bing keeps only reports with most of the search words
    stories = article_facts(f"{name} {article if article != name else words[0]}", want=4, bodies=2, recent=False)
    # feature stories must name the chapter itself ("deal" + "completed" let a Starbase jobs story into the
    # Twitter chapter on 11-23)
    extra += [s for s in stories if words[0] in (s["title"] + " " + s["snippet"]).lower()
              and not LIFE_UNSAFE.search(s["title"])][:2]
    print(f"[trends] life story for {name}: {title} ({article}; {len(best)} Wikipedia paragraphs, "
          f"{len(extra)} quotes/feature stories)")
    return {"topic": f"{name} life: {title}", "traffic": "", "country": "US", "source": "focus", "life": key,
            "news": news, "facts": extra, "power": 1, "focus": True, "trends": ["a chapter of the star's life"],
            "countries": ["US"]}


def person_facts(name, shift=0):
    """A 'facts & stats' topic about a famous person: Wikipedia summary + their latest news, for the star slot.
    shift > 0 picks another section of their article (when this slot's section was already told)."""
    news = []
    try:
        r = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + requests.utils.quote(name.replace(" ", "_")),
                         headers={"User-Agent": "HypeAtlasBot/1.0 (facebook.com/hypeatlasnews)"}, timeout=20).json()
        if r.get("extract"):
            news.append({"title": f"{name} (Wikipedia)", "snippet": r["extract"][:900], "source": "Wikipedia",
                         "url": (r.get("content_urls") or {}).get("desktop", {}).get("page", "")})
    except Exception as e:
        print(f"[trends] Wikipedia summary for {name} failed: {e}")
    try:  # a different part of their life each slot (rockets, cars, childhood...), so facts posts don't repeat
        pages = requests.get("https://en.wikipedia.org/w/api.php", timeout=20, params={
            "action": "query", "format": "json", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": name},
            headers={"User-Agent": "HypeAtlasBot/1.0 (facebook.com/hypeatlasnews)"}).json()["query"]["pages"]
        text = next(iter(pages.values())).get("extract", "")
        parts = re.split(r"\n==+ *([^=\n]+?) *==+\n", text)
        sections = [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)
                    if len(parts[i + 1].strip()) > 400 and not re.search(
                        r"reference|see also|note|bibliograph|external|further|legal|lawsuit|controvers|criticism|"
                        r"politic|views|death|illness|health|accusation|epstein|salute|marriage|relationship|international", parts[i], re.I)]
        if sections:
            now = datetime.now(timezone.utc)
            title, body = sections[(now.timetuple().tm_yday * 24 + now.hour + shift * 7) % len(sections)]
            news.insert(0, {"title": f"{name}: {title} (Wikipedia)", "snippet": body[:1200], "source": "Wikipedia",
                            "url": ""})
            print(f"[trends] facts angle for {name}: {title}")
    except Exception as e:
        print(f"[trends] Wikipedia sections for {name} failed: {e}")
    for it in focus_news([name], per_term=3):
        news += it["news"]
    return {"topic": f"{name} facts", "traffic": "", "country": "US", "source": "focus", "news": news[:4],
            "power": 1, "focus": True, "trends": ["the page's best-performing face"], "countries": ["US"]}


def _is_focus(m):
    blob = (m["topic"] + " " + " ".join(n["title"] for n in m["news"])).lower()
    return any(re.search(r"\b" + re.escape(t.lower()) + r"\b", blob) for t in FOCUS + FACES + _TRENDING_PEOPLE)


COUNTRY_WEIGHT = {"US": 1.0, "GB": 0.8, "CA": 0.5, "AU": 0.5}


def traffic_num(t):
    """'200K+' -> 200000, '1M+' -> 1000000, '' -> 0"""
    m = re.match(r"([\d.,]+)\s*([KM]?)", (t or "").replace(",", "").upper())
    if not m:
        return 0
    n = float(m.group(1) or 0)
    return int(n * {"K": 1_000, "M": 1_000_000}.get(m.group(2), 1))


def _key(topic):
    return re.sub(r"[^a-z0-9]+", " ", topic.lower()).strip()


def rank(items):
    """Merge the same topic across countries and sort by 'power':
    search traffic (weighted by country) x bonus for trending in several countries.
    News-only headlines get a small base score so big search trends come first."""
    merged = {}
    for it in items:
        k = _key(it["topic"])
        if not k:
            continue
        m = merged.setdefault(k, {**it, "countries": [], "traffic_sum": 0, "news": []})
        if it["country"] not in m["countries"]:
            m["countries"].append(it["country"])
        w = COUNTRY_WEIGHT.get(it["country"], 0.5)
        m["traffic_sum"] += (traffic_num(it["traffic"]) or 5_000) * w
        if it["source"] in ("google_trends", "focus", "x", "reddit"):
            m["source"] = it["source"]
        if it.get("trend"):
            m.setdefault("trends", [])
            if it["trend"] not in m["trends"]:
                m["trends"].append(it["trend"])
        for n in it["news"]:
            if len(m["news"]) < 3 and n["title"] not in {x["title"] for x in m["news"]}:
                m["news"].append(n)
    out = list(merged.values())
    for m in out:
        multi = 1 + 0.6 * (len(m["countries"]) - 1)
        m["power"] = int(m["traffic_sum"] * multi * (1.0 if m["source"] in ("google_trends", "focus", "x", "reddit") else 0.4))
        if m.get("trends"):  # people are talking about it right now on X / Reddit / Wikipedia
            m["power"] = int(m["power"] * (1 + len(m["trends"])))
        m["focus"] = _is_focus(m)
        if m["focus"]:
            m["power"] *= FOCUS_BOOST
        m["country"] = ",".join(m["countries"])
    out.sort(key=lambda m: m["power"], reverse=True)
    return out


def collect(countries):
    items = []
    for geo in countries:
        for fn in (google_trends, google_news):
            try:
                got = fn(geo)
                print(f"[trends] {fn.__name__}({geo}): {len(got)} items")
                items.extend(got)
            except Exception as e:  # keep going if one feed fails
                print(f"[trends] {fn.__name__}({geo}) failed: {e}")
    items.extend(focus_news())
    for fn in (x_trends, reddit_top):  # where people are talking right now
        try:
            items.extend(fn())
        except Exception as e:
            print(f"[trends] {fn.__name__} failed: {e}")
    try:  # who the world is looking up today: boost them, and fetch why they are in the news
        _TRENDING_PEOPLE[:] = wiki_people()
        fresh = [p for p in _TRENDING_PEOPLE if p not in FOCUS][:6]
        for it in focus_news(fresh, per_term=1):
            it["trend"] = "most looked-up person on Wikipedia today"
            items.append(it)
    except Exception as e:
        print(f"[trends] wiki_people failed: {e}")
    return items
