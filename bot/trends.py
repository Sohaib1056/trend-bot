"""Collect what is trending right now in English-speaking countries (free, no keys)."""
import os
import re
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


X_JUNK = re.compile(r"\b(good|happy)\s+\w*(morning|night|day|eve|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
                    r"|\b(token|coin|crypto|airdrop|nft|giveaway|presale)\b|^\$|^#?(gm|gn)$", re.I)


def x_trends(limit=12):
    """What is trending on X (Twitter) in the US right now (trends24.in), each with one news item for facts."""
    html = _get_text("https://trends24.in/united-states/")
    names = []
    for n in re.findall(r"class=trend-link>([^<]+)</a>", html):
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


def reddit_top(limit=15):
    """Today's top posts in Reddit's big news/tech/celebrity communities (may be rate-limited: then skipped)."""
    root = ET.fromstring(_get_text(REDDIT).encode())
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
        dead = "P570" in ent.get("claims", {})  # date of death: skip (no death stories)
        if "Q5" in p31 and not dead:
            people.append(qids[qid])
    order = {t.replace("_", " "): i for i, t in enumerate(arts)}
    people.sort(key=lambda t: order.get(t, 99))
    print(f"[trends] most looked-up people: {', '.join(people[:limit])}")
    return people[:limit]


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
