"""Ask a free AI (Gemini, with optional Groq backup) to pick topics and write posts."""
import json
import re

import requests

from . import config

THEMES = ["red", "blue", "purple", "orange", "green", "dark", "pink", "teal"]


def build_prompt(candidates, already_posted, n, performance=""):
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(f"{i}. TOPIC: {c['topic']} | power: {c.get('power', 0):,} | trending in: {c['country']}")
        for nw in c["news"][:3]:
            lines.append(f"   - {nw['title']} ({nw['source']}): {nw['snippet'][:300]}")
    politics = ("Skip party politics, elections and politicians." if config.AVOID_POLITICS else "")
    return f"""You are the social media manager of a Facebook page and Instagram account called "{config.PAGE_NAME}".
Page niche: {config.NICHE}. Audience: the general public in the US, UK, Canada and Australia.

Below are topics trending RIGHT NOW with related news headlines.
{chr(10).join(lines)}

{('WHAT WORKS ON THIS PAGE (learn from it, favour topics like the best ones):' + chr(10) + performance + chr(10) + chr(10)) if performance else ''}Topics already posted recently (do NOT repeat these or close variants): {', '.join(already_posted[-60:]) or 'none'}

TASK: Choose the {n} topic(s) with the BIGGEST mass appeal and write one post for each.
Topics are sorted strongest first. "power" = how many people are searching it, boosted when it trends
in several countries at once. Pick what the general public in the US, UK, Canada and Australia will
stop scrolling for, react to and share: big-name celebrities, major sports moments, huge movies/TV/music,
viral moments, major tech launches. Prefer high power and multi-country topics. Avoid niche or local
stories (small leagues, regional news, people most of the audience has never heard of).
RULES:
- Use ONLY facts stated in the headlines/snippets above. Never invent numbers, quotes, scores or claims. If unsure, keep it general.
- If two snippets disagree (e.g. different rankings or numbers), leave that detail out.
- SKIP: deaths, tragedies, disasters with victims, crime/violence, shootings, war, medical claims, adult content, gambling. {politics}
- Prefer: entertainment, celebrities (positive/neutral), sports results, tech/gadgets, viral moments, movies/TV, music, lifestyle, science wonders, big sales/events.
- English only. Friendly, energetic, not clickbait-lying.

Return JSON exactly in this shape:
{{"posts": [{{
  "key": "short-lowercase-slug-of-topic",
  "topic": "topic name",
  "badge": "exactly one of: SPORTS, CELEBRITY, TV & FILM, MUSIC, TECH, GAMING, SHOPPING, ROYALS, WEATHER, VIRAL, BUSINESS, SCIENCE",
  "first_comment": "one short, fun question the page itself will comment under the post to spark replies (max 15 words, 1 emoji, e.g. 'Would you watch this? Yes or no? 👇')",
  "headline": "6-10 words in normal sentence case (NOT all caps), written like the big viral news pages (Pubity, Pop Base): lead with the famous name, use an emotional or surprising angle, and leave a little curiosity while still saying what happened (not vague clickbait). No emoji. Wrap the 1-3 most striking words in *asterisks* for a highlighter, e.g. 'Zoe Saldaña just *turned down* Taylor Sheridan's next big show'",
  "subtext": "max 14 words, one supporting line for the poster with the key fact",
  "wiki_title": "exact English Wikipedia article title of the ONE main PERSON in the story (e.g. 'Zoe Saldaña', 'Aryna Sabalenka', 'Erling Haaland'), or empty string if the story is about a show, film, team, product or event rather than a person",
  "image_prompt": "one vivid sentence describing a professional, photorealistic, high-contrast scene that explains the story at a glance - its actual subject matter (its sport, venue, objects, setting - e.g. tennis upset -> a tennis racket on a clay court under stadium lights; TV casting news -> a film set with a director's chair and cameras), with the main subject large in the upper half of the frame (no text, no real person's face, no logos)",
  "template": "one of viral, breaking, frame (viral = celebrity/entertainment/viral moments, breaking = news/sports/events, frame = tech/lifestyle/products)",
  "caption": "80-140 words. First line = strong hook with 1-2 emojis. Short paragraphs. Explain what happened using only given facts. End with a question to drive comments, then a short share nudge like 'Send this to a friend who needs to see it 👀'.",
  "hashtags": ["10 to 15 hashtags without #, mix of broad popular tags and topic-specific tags"],
  "image_query": "2-4 words for a vibrant, eye-catching, professional stock photo that captures the excitement or emotion of the story, not a dull literal object (e.g. 'online shopping sale' not 'cardboard boxes', 'football stadium crowd' not 'football'). No person names.",
  "theme": "one of {THEMES}"
}}]}}
"""


def _parse(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    data = json.loads(text)
    if isinstance(data, list):  # some models return the posts list (or [{"posts": [...]}]) directly
        data = data[0] if len(data) == 1 and isinstance(data[0], dict) and "posts" in data[0] else {"posts": data}
    posts = data.get("posts", [])
    good = []
    for p in posts:
        if not all(p.get(k) for k in ("key", "headline", "caption")):
            continue
        p["hashtags"] = [h.strip().lstrip("#").replace(" ", "") for h in p.get("hashtags", []) if h.strip()][:15]
        p["caption"] = p["caption"].replace("*", "")  # Facebook shows asterisks literally
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


def write_posts(candidates, already_posted, n, performance=""):
    prompt = build_prompt(candidates, already_posted, n, performance)
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


def judge_image(img, headline):
    """Ask Gemini (vision) how well a background photo fits the headline and how good it looks.
    Returns (relevance 0-10, quality 0-10), or None if no judge is available."""
    if not config.GEMINI_API_KEY:
        return None
    import base64, io
    small = img.copy()
    small.thumbnail((768, 768))
    buf = io.BytesIO()
    small.convert("RGB").save(buf, "JPEG", quality=85)
    prompt = (f'A Facebook news page will use this photo as the background of a poster with the headline: "{headline}".\n'
              "Score it like a strict photo editor of a big viral news page.\n"
              "relevance: 0-10, does the photo clearly match the story (its person, sport, place, product or event)?\n"
              "quality: 0-10, is it sharp, well lit, professional and eye-catching (not blurry, dull, amateur or cluttered)?\n"
              'Reply JSON only: {"relevance": n, "quality": n, "why": "few words"}')
    for m in _gemini_models()[:3]:
        try:
            r = requests.post(f"{GEMINI_BASE}/models/{m}:generateContent",
                              headers={"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
                              json={"contents": [{"parts": [
                                  {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
                                  {"text": prompt}]}],
                                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}},
                              timeout=60)
            if r.status_code != 200:
                continue
            parts = r.json()["candidates"][0]["content"]["parts"]
            data = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
            if isinstance(data, list):
                data = data[0]
            print(f"[judge] relevance {data.get('relevance')}/10, quality {data.get('quality')}/10 - {data.get('why', '')}")
            return float(data.get("relevance", 0)), float(data.get("quality", 0))
        except Exception as e:
            print(f"[judge] {m} failed: {e}")
    return None
