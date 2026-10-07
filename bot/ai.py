"""Ask a free AI (Gemini, with optional Groq backup) to pick topics and write posts."""
import json
import re

import requests

from . import config

THEMES = ["red", "blue", "purple", "orange", "green", "dark", "pink", "teal"]


def build_prompt(candidates, already_posted, n, performance=""):
    lines = []
    for i, c in enumerate(candidates, 1):
        star = " | PAGE FOCUS" if c.get("focus") else ""
        lines.append(f"{i}. TOPIC: {c['topic']} | power: {c.get('power', 0):,} | trending in: {c['country']}{star}")
        for nw in c["news"][:3]:
            lines.append(f"   - {nw['title']} ({nw['source']}): {nw['snippet'][:300]}")
    politics = ("Skip campaign politics (polls, debates, fundraising, local races). World-famous US leaders such as "
                "Donald Trump ARE allowed when the story is big news: report it neutrally and factually, no opinions, "
                "no party attacks, no election claims." if config.AVOID_POLITICS else "")
    from .trends import FOCUS
    return f"""You are the social media manager of a Facebook page and Instagram account called "{config.PAGE_NAME}".
Page focus: the most famous US names in tech, AI and world news - {', '.join(FOCUS)} - and the AI tools
everyone talks about. Audience: the general public, mainly in the USA.

Below are topics trending RIGHT NOW with related news headlines.
{chr(10).join(lines)}

{('WHAT WORKS ON THIS PAGE (learn from it, favour topics like the best ones):' + chr(10) + performance + chr(10) + chr(10)) if performance else ''}Topics already posted recently (do NOT repeat these or close variants): {', '.join(already_posted[-60:]) or 'none'}

TASK: Choose the {n} topic(s) with the BIGGEST mass appeal and write one post for each.
Stories built around ONE famous face (a person people recognise instantly) get far more views than stories
about companies or markets: prefer them. Topics marked PAGE FOCUS are about the page's core names (big tech, AI, Trump, Elon...): pick those FIRST,
as long as the story is genuinely interesting (a launch, a big move, money, a clash, a surprising statement).
Topics are sorted strongest first. "power" = how many people are searching it, boosted when it trends
in several countries at once. Pick what the general public in the US, UK, Canada and Australia will
stop scrolling for, react to and share: big-name celebrities, major sports moments, huge movies/TV/music,
viral moments, major tech launches. Prefer high power and multi-country topics. Avoid niche or local
stories (small leagues, regional news, people most of the audience has never heard of, stars famous mainly
in India/Asia such as Bollywood or K-drama actors unless they are huge in the US/UK too).
RULES:
- Use ONLY facts stated in the headlines/snippets above. Never invent numbers, quotes, scores or claims. If unsure, keep it general.
- If two snippets disagree (e.g. different rankings or numbers), leave that detail out.
- SKIP: deaths, tragedies, disasters with victims, crime/violence, shootings, war, medical claims, adult content, gambling. {politics}
- Prefer: big tech & AI news (OpenAI, Google, Meta, Nvidia, Microsoft, Apple...), famous billionaires and CEOs, new AI tools, world-famous US leaders, then big celebrity/viral stories.
- English only. Friendly, energetic, not clickbait-lying.

Return JSON exactly in this shape:
{{"posts": [{{
  "key": "short-lowercase-slug-of-topic",
  "topic": "topic name",
  "badge": "exactly one of: AI, TECH, POLITICS, BUSINESS, CELEBRITY, SPORTS, TV & FILM, MUSIC, GAMING, SHOPPING, VIRAL, SCIENCE",
  "first_comment": "one short, fun question the page itself will comment under the post to spark replies (max 15 words, 1 emoji, e.g. 'Would you watch this? Yes or no? 👇')",
  "share_groups": ["2-3 short Facebook group search terms where fans of this exact topic hang out, e.g. 'Manchester City fans', 'Premier League fans'"],
  "headline": "6-10 words in normal sentence case (NOT all caps), written like the big viral news pages (Pubity, Pop Base). Use the PERSONALITY + MONEY + CLASH formula: lead with the famous person's name (not the company), then the most dramatic true angle - a big dollar figure, a rivalry or clash (X vs Y, slams, fires back, warns), a shock move or a surprising statement - and leave a little curiosity while still saying what happened (never invent or exaggerate). No emoji. Wrap the 1-3 most striking words in *asterisks* for a highlighter, e.g. 'Elon Musk just *fired back* at Sam Altman over OpenAI', 'Jensen Huang's fortune *jumps $20 billion* in one day'",
  "subtext": "max 14 words, one supporting line for the poster with the key fact",
  "wiki_title": "exact English Wikipedia article title of the ONE main famous PERSON in the story (e.g. 'Sam Altman', 'Jensen Huang', 'Donald Trump'). If the story is about a company or product, use its famous leader ONLY if the snippets name them. Empty string only if no famous person is involved. Stories with a famous face do much better, so prefer them.",
  "image_prompt": "one vivid sentence describing a professional, photorealistic, high-contrast scene that explains the story at a glance - its actual subject matter (its sport, venue, objects, setting - e.g. tennis upset -> a tennis racket on a clay court under stadium lights; TV casting news -> a film set with a director's chair and cameras), with the main subject large in the upper half of the frame (no text, no real person's face, no logos)",
  "template": "one of viral, breaking, frame (viral = celebrity/entertainment/viral moments, breaking = news/sports/events, frame = tech/lifestyle/products)",
  "caption": "80-140 words. First line = strong hook with 1-2 emojis. Short paragraphs. Explain what happened using only given facts. End with a question to drive comments, then a short share nudge like 'Send this to a friend who needs to see it 👀'.",
  "hashtags": ["10 to 15 hashtags without #, mix of broad popular tags and topic-specific tags"],
  "image_query": "2-4 words for a vibrant, eye-catching, professional stock photo that captures the excitement or emotion of the story, not a dull literal object (e.g. 'online shopping sale' not 'cardboard boxes', 'football stadium crowd' not 'football'). No person names.",
  "voiceover": "the Reel narration, read aloud by an energetic US news voice: 22-32 words, 2-3 short punchy spoken sentences. Open with a hook that names the famous person/thing, then the key fact (only given facts). Plain words, no emoji, no hashtags, no asterisks. Do NOT end with a follow request (added automatically).",
  "video_queries": ["2-3 searches (2-3 words each) for real stock VIDEO footage showing the story's action and mood, for fast cuts in a Reel, e.g. tennis upset -> 'tennis match', 'stadium crowd cheering'; app hack -> 'smartphone notification', 'shocked woman phone'. No person names."],
  "theme": "one of {THEMES}"
}}]}}
"""


def _parse(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
    except ValueError:  # model wrote extra words around the JSON: keep the outermost {...}
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
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


_GEMINI = {"down": False, "models": None}  # once Gemini times out / is out of quota, skip it for the rest of the run


def _gemini_failed(e):
    if isinstance(e, requests.exceptions.Timeout) or "HTTP 429" in str(e):
        _GEMINI["down"] = True
        print("[ai] Gemini is slow or out of quota -> using the backups for the rest of this run")


def _gemini_models():
    """Configured models first, then whatever flash models this key can actually use (newest first)."""
    if _GEMINI["models"] is not None:
        return _GEMINI["models"]
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
    _GEMINI["models"] = list(dict.fromkeys(wanted))[:4]
    return _GEMINI["models"]


def _gemini(prompt):
    last = None
    for m in _gemini_models():
        if _GEMINI["down"]:
            break
        try:
            r = requests.post(
                f"{GEMINI_BASE}/models/{m}:generateContent",
                headers={"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}},
                timeout=45,
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            parts = r.json()["candidates"][0]["content"]["parts"]
            print(f"[ai] Gemini model used: {m}")
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except Exception as e:
            print(f"[ai] Gemini {m} failed: {str(e)[:160]}")
            _gemini_failed(e)
            last = e
    raise RuntimeError(f"All Gemini models failed: {last}")


def _groq_models():
    wanted = [config.GROQ_MODEL]
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}, timeout=30)
        found = [m["id"] for m in r.json().get("data", []) if m.get("active", True)]
        # only big general chat models (the list also has speech, guard and tiny models)
        good = ("gpt-oss-120b", "llama-3.3-70b", "kimi-k2", "qwen3", "llama-4-maverick", "deepseek", "gpt-oss-20b")
        found = [m for m in found if any(k in m.lower() for k in good) and not any(w in m.lower() for w in SKIP_WORDS)]
        found.sort(key=lambda m: next(i for i, k in enumerate(good) if k in m.lower()))
        wanted += found
    except Exception as e:
        print(f"[ai] could not list Groq models: {e}")
    return list(dict.fromkeys(wanted))[:4]


def _groq(prompt):
    last = None
    for m in _groq_models():
        for strict in (True, False):  # strict JSON mode sometimes rejects long answers: retry in plain mode
            body = {"model": m, "temperature": 0.7, "max_tokens": 6000,
                    "messages": [{"role": "user", "content": prompt + ("" if strict else "\nReply with the JSON only.")}]}
            if strict:
                body["response_format"] = {"type": "json_object"}
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}, json=body, timeout=90)
            if r.status_code == 200:
                print(f"[ai] Groq model used: {m}{'' if strict else ' (plain mode)'}")
                return r.json()["choices"][0]["message"]["content"]
            last = f"Groq {m} HTTP {r.status_code}: {r.text[:160]}"
            print(f"[ai] {last}")
            if "json_validate_failed" not in r.text:
                break
    raise RuntimeError(last)


def write_posts(candidates, already_posted, n, performance=""):
    engines = []
    if config.GEMINI_API_KEY:
        engines.append(_gemini)
    if config.GROQ_API_KEY:
        engines.append(_groq)
    if not engines:
        raise RuntimeError("No AI key found. Add GEMINI_API_KEY (or GROQ_API_KEY) in GitHub Secrets.")
    for size in dict.fromkeys((n, 1)):  # if a long answer fails, ask for a single post
        prompt = build_prompt(candidates, already_posted, size, performance)
        for engine in engines:
            try:
                posts = _parse(engine(prompt))
                if posts:
                    return posts[:size]
            except Exception as e:
                print(f"[ai] {engine.__name__} error: {str(e)[:200]}")
    raise RuntimeError("AI could not write any post.")


def judge_image(img, headline, broll=False):
    """Ask Gemini (vision) how well a background photo fits the headline and how good it looks.
    broll=True: a stock video frame used as B-roll, judged on theme and mood, not on showing the exact story.
    Returns (relevance 0-10, quality 0-10, face_y 0-1 or None), or None if no judge is available."""
    if not config.GEMINI_API_KEY and not config.GROQ_API_KEY:
        return None
    import base64, io
    small = img.copy()
    small.thumbnail((768, 768))
    buf = io.BytesIO()
    small.convert("RGB").save(buf, "JPEG", quality=85)
    if broll:
        task = (f'A frame of stock video footage that a viral news page may use as B-roll in a Reel about: "{headline}".\n'
                "Stock footage never shows the real people or the exact event; judge it like a video editor.\n"
                "relevance: 0-10, does it fit the story's theme, setting and mood (e.g. any campus crowd for a student "
                "story, any stadium for a football story)? Give 0-3 only if it is unrelated, misleading or shows a "
                "different well-known person.\n")
    else:
        task = (f'A Facebook news page will use this photo as the background of a poster with the headline: "{headline}".\n'
                "Score it like a strict photo editor of a big viral news page.\n"
                "relevance: 0-10, does the photo clearly match the story (its person, sport, place, product or event)?\n")
    prompt = (task +
              "quality: 0-10, is it sharp, well lit, professional and eye-catching (not blurry, dull, amateur or cluttered)?\n"
              "face_y: vertical position of the centre of the main person's face, from 0.0 (top edge of the photo) "
              "to 1.0 (bottom edge), or -1 if no face is clearly visible.\n"
              'Reply JSON only: {"relevance": n, "quality": n, "face_y": n, "why": "few words"}')
    b64 = base64.b64encode(buf.getvalue()).decode()
    for m in (_gemini_models()[:2] if config.GEMINI_API_KEY else []):
        if _GEMINI["down"]:
            break
        try:
            r = requests.post(f"{GEMINI_BASE}/models/{m}:generateContent",
                              headers={"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
                              json={"contents": [{"parts": [
                                  {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                                  {"text": prompt}]}],
                                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}},
                              timeout=25)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            parts = r.json()["candidates"][0]["content"]["parts"]
            data = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
            return _score(data)
        except Exception as e:
            print(f"[judge] {m} failed: {str(e)[:120]}")
            _gemini_failed(e)
    return _groq_judge(b64, prompt) or _cloudflare_judge(b64, prompt)


def _score(data):
    if isinstance(data, list):
        data = data[0]
    print(f"[judge] relevance {data.get('relevance')}/10, quality {data.get('quality')}/10, "
          f"face {data.get('face_y')} - {data.get('why', '')}")
    try:
        face = float(data.get("face_y"))
        face = face if 0 <= face <= 1 else None
    except (TypeError, ValueError):
        face = None
    return float(data.get("relevance", 0)), float(data.get("quality", 0)), face


def _groq_judge(b64, prompt):
    """Backup judge when Gemini is busy (503): a Groq vision model (Llama 4)."""
    if not config.GROQ_API_KEY:
        return None
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}, timeout=30)
        models = [m["id"] for m in r.json().get("data", [])
                  if any(k in m["id"].lower() for k in ("llama-4", "vision", "scout", "maverick", "-vl"))]
    except Exception:
        models = []
    for m in models[:2]:
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}, timeout=60,
                              json={"model": m, "temperature": 0, "response_format": {"type": "json_object"},
                                    "messages": [{"role": "user", "content": [
                                        {"type": "text", "text": prompt},
                                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
            return _score(json.loads(r.json()["choices"][0]["message"]["content"]))
        except Exception as e:
            print(f"[judge] Groq {m} failed: {e}")
    return None


def _cloudflare_judge(b64, prompt):
    """Second backup judge: Llama 4 Scout (vision) on Cloudflare Workers AI (same free account as the images)."""
    import os
    acc, tok = os.getenv("CF_ACCOUNT_ID", "").strip(), os.getenv("CF_API_TOKEN", "").strip()
    if not acc or not tok:
        return None
    try:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/meta/llama-4-scout-17b-16e-instruct",
            headers={"Authorization": f"Bearer {tok}"}, timeout=90,
            json={"temperature": 0, "max_tokens": 200, "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]})
        res = (r.json().get("result") or {}).get("response")
        if r.status_code != 200 or res is None:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
        if isinstance(res, str):
            res = json.loads(re.search(r"\{.*\}", res, re.S).group(0))
        return _score(res)
    except Exception as e:
        print(f"[judge] Cloudflare vision failed: {e}")
        return None
