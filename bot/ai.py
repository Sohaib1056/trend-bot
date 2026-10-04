"""Ask a free AI (Gemini, with optional Groq backup) to pick topics and write posts."""
import json
import re

import requests

from . import config

THEMES = ["red", "blue", "purple", "orange", "green", "dark", "pink", "teal"]


def build_prompt(candidates, already_posted, n):
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(f"{i}. TOPIC: {c['topic']} | country: {c['country']} | traffic: {c['traffic'] or 'n/a'}")
        for nw in c["news"][:3]:
            lines.append(f"   - {nw['title']} ({nw['source']}): {nw['snippet'][:300]}")
    politics = ("Skip party politics, elections and politicians." if config.AVOID_POLITICS else "")
    return f"""You are the social media manager of a Facebook page and Instagram account called "{config.PAGE_NAME}".
Page niche: {config.NICHE}. Audience: people in the US and UK.

Below are topics trending RIGHT NOW with related news headlines.
{chr(10).join(lines)}

Topics already posted recently (do NOT repeat these or close variants): {', '.join(already_posted[-60:]) or 'none'}

TASK: Choose the {n} best topic(s) for high engagement that fit the niche, and write one post for each.
RULES:
- Use ONLY facts stated in the headlines/snippets above. Never invent numbers, quotes, scores or claims. If unsure, keep it general.
- SKIP: deaths, tragedies, disasters with victims, crime/violence, shootings, war, medical claims, adult content, gambling. {politics}
- Prefer: entertainment, celebrities (positive/neutral), sports results, tech/gadgets, viral moments, movies/TV, music, lifestyle, science wonders, big sales/events.
- English only. Friendly, energetic, not clickbait-lying.

Return JSON exactly in this shape:
{{"posts": [{{
  "key": "short-lowercase-slug-of-topic",
  "topic": "topic name",
  "badge": "one or two words like TRENDING, BREAKING, SPORTS, TECH, MUST WATCH",
  "headline": "max 10 words, punchy viral-page style poster headline. Wrap the 1-3 most exciting words in *asterisks* so they get highlighted, e.g. 'Apple Just *Dropped* The *iPhone 18*'",
  "subtext": "max 18 words, one supporting line for the poster",
  "image_prompt": "one vivid sentence describing a dramatic, eye-catching background picture for this story (no text, no real person's face, no logos)",
  "template": "one of viral, breaking, frame (viral = celebrity/entertainment/viral moments, breaking = news/sports/events, frame = tech/lifestyle/products)",
  "caption": "80-140 words. First line = strong hook with 1-2 emojis. Short paragraphs. Explain what happened using only given facts. End with a question to drive comments, then a short share nudge like 'Send this to a friend who needs to see it 👀'.",
  "hashtags": ["10 to 15 hashtags without #, mix of broad popular tags and topic-specific tags"],
  "image_query": "2-4 generic words for a stock photo background (no person names), e.g. 'football stadium night'",
  "theme": "one of {THEMES}"
}}]}}
"""


def _parse(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    data = json.loads(text)
    posts = data.get("posts", data if isinstance(data, list) else [])
    good = []
    for p in posts:
        if not all(p.get(k) for k in ("key", "headline", "caption")):
            continue
        p["hashtags"] = [h.strip().lstrip("#").replace(" ", "") for h in p.get("hashtags", []) if h.strip()][:15]
        p["theme"] = p.get("theme") if p.get("theme") in THEMES else "red"
        p["key"] = re.sub(r"[^a-z0-9-]+", "-", p["key"].lower()).strip("-")[:60]
        good.append(p)
    return good


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
SKIP_WORDS = ("image", "tts", "audio", "live", "embed", "vision", "guard", "whisper", "native", "robotics")


def _version(name):
    m = re.search(r"(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def _gemini_models():
    """Configured models first, then whatever flash models this key can actually use (newest first)."""
    wanted = [config.GEMINI_MODEL] + config.GEMINI_FALLBACK_MODELS
    try:
        r = requests.get(f"{GEMINI_BASE}/models", params={"pageSize": 200},
                         headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=30)
        found = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        found = [m for m in found if "flash" in m and not any(w in m for w in SKIP_WORDS)
                 and "preview" not in m and "exp" not in m]
        found.sort(key=lambda m: ("lite" in m, -_version(m)))
        wanted += found
    except Exception as e:
        print(f"[ai] could not list Gemini models: {e}")
    return list(dict.fromkeys(wanted))[:6]


def _gemini(prompt):
    last = None
    for m in _gemini_models():
        try:
            r = requests.post(
                f"{GEMINI_BASE}/models/{m}:generateContent",
                headers={"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}},
                timeout=90,
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            parts = r.json()["candidates"][0]["content"]["parts"]
            print(f"[ai] Gemini model used: {m}")
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except Exception as e:
            print(f"[ai] Gemini {m} failed: {e}")
            last = e
    raise RuntimeError(f"All Gemini models failed: {last}")


def _groq_models():
    wanted = [config.GROQ_MODEL]
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}, timeout=30)
        found = [m["id"] for m in r.json().get("data", []) if m.get("active", True)]
        found = [m for m in found if not any(w in m.lower() for w in SKIP_WORDS)]
        # bigger general models first
        found.sort(key=lambda m: (not any(k in m for k in ("70b", "120b", "versatile", "maverick")), m))
        wanted += found
    except Exception as e:
        print(f"[ai] could not list Groq models: {e}")
    return list(dict.fromkeys(wanted))[:5]


def _groq(prompt):
    last = None
    for m in _groq_models():
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={"model": m, "temperature": 0.8,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=90,
        )
        if r.status_code == 200:
            print(f"[ai] Groq model used: {m}")
            return r.json()["choices"][0]["message"]["content"]
        last = f"Groq {m} HTTP {r.status_code}: {r.text[:200]}"
        print(f"[ai] {last}")
    raise RuntimeError(last)


def write_posts(candidates, already_posted, n):
    prompt = build_prompt(candidates, already_posted, n)
    engines = []
    if config.GEMINI_API_KEY:
        engines.append(_gemini)
    if config.GROQ_API_KEY:
        engines.append(_groq)
    if not engines:
        raise RuntimeError("No AI key found. Add GEMINI_API_KEY (or GROQ_API_KEY) in GitHub Secrets.")
    for engine in engines:
        try:
            posts = _parse(engine(prompt))
            if posts:
                return posts[:n]
        except Exception as e:
            print(f"[ai] {engine.__name__} error: {e}")
    raise RuntimeError("AI could not write any post.")
