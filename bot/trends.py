"""Collect what is trending right now in English-speaking countries (free, no keys)."""
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
        if it["source"] == "google_trends":
            m["source"] = "google_trends"
        for n in it["news"]:
            if len(m["news"]) < 3 and n["title"] not in {x["title"] for x in m["news"]}:
                m["news"].append(n)
    out = list(merged.values())
    for m in out:
        multi = 1 + 0.6 * (len(m["countries"]) - 1)
        m["power"] = int(m["traffic_sum"] * multi * (1.0 if m["source"] == "google_trends" else 0.4))
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
    return items
