"""Ask a free AI (Gemini, with optional Groq backup) to pick topics and write posts."""
import json
import os
import re
import time

import requests

from . import config

THEMES = ["red", "blue", "purple", "orange", "green", "dark", "pink", "teal"]


def build_prompt(candidates, already_posted, n, performance="", special=""):
    lines = []
    for i, c in enumerate(candidates, 1):
        star = " | FAMOUS FACE" if c.get("focus") else ""
        where = (" | buzz: " + "; ".join(c["trends"][:2])) if c.get("trends") else ""
        lines.append(f"{i}. TOPIC: {c['topic']} | power: {c.get('power', 0):,} | trending in: {c['country']}{star}{where}")
        for nw in c["news"][:3]:
            lines.append(f"   - {nw['title']} ({nw['source']}): {nw['snippet'][:900 if nw.get('source') == 'Wikipedia' else 300]}")
        for f in c.get("facts") or []:  # the real reporting: numbers, quotes, dates from several outlets
            lines.append(f"   * REPORT ({f['source']}): {f['title']}: {f['snippet'][:1200]}")
    politics = ("Skip campaign politics (polls, debates, fundraising, local races). World-famous US leaders such as "
                "Donald Trump ARE allowed when the story is big news: report it neutrally and factually, no opinions, "
                "no party attacks, no election claims." if config.AVOID_POLITICS else "")
    from .trends import FOCUS, _TRENDING_PEOPLE
    return f"""You are the social media manager of a Facebook page and Instagram account called "{config.PAGE_NAME}".
Page focus: FAMOUS FACES - the people Americans recognise instantly and talk about: big tech & AI bosses
({', '.join(FOCUS[:12])}...), world-famous politicians like Trump, and A-list celebrities, athletes and creators.
People the world is looking up most TODAY: {', '.join(_TRENDING_PEOPLE[:15]) or 'n/a'}.
Audience: the general public, mainly in the USA.

Below are topics trending RIGHT NOW with related news headlines.
{chr(10).join(lines)}

{('WHAT WORKS ON THIS PAGE (learn from it, favour topics like the best ones):' + chr(10) + performance + chr(10) + chr(10)) if performance else ''}Topics already posted recently (do NOT repeat these or close variants): {', '.join(already_posted[-60:]) or 'none'}

{special}TASK: Choose the {n} topic(s) with the BIGGEST mass appeal and write one post for each.
Stories built around ONE famous face (a person people recognise instantly) get far more views than stories
about companies or markets: prefer them. Topics marked FAMOUS FACE involve such a person: pick those FIRST,
as long as the story is genuinely interesting (a big move, money, a clash, a surprising statement, a launch).
"buzz" shows where people are talking about it right now (X trends, Reddit top posts, most looked-up people):
the same story buzzing on several platforms is the strongest pick.
Topics are sorted strongest first. "power" = how many people are searching it, boosted when it trends
in several countries at once. Pick what the general public in the US, UK, Canada and Australia will
stop scrolling for, react to and share: big-name celebrities, major sports moments, huge movies/TV/music,
viral moments, major tech launches. Prefer high power and multi-country topics. Avoid niche or local
stories (small leagues, regional news, people most of the audience has never heard of, stars famous mainly
in India/Asia such as Bollywood or K-drama actors unless they are huge in the US/UK too).
Lines marked "* REPORT" are the actual news reports (several outlets, sometimes the full article): topics that have
them can be told with real detail, so prefer them when they are as big as the others.
RULES:
- Use ONLY facts stated in the headlines/snippets/reports above. Never invent numbers, quotes, scores or claims. If unsure, keep it general.
- If two snippets disagree (e.g. different rankings or numbers), leave that detail out.
- SKIP: deaths, tragedies, disasters with victims, crime/violence, shootings, war, medical claims, adult content, gambling. {politics}
- Prefer: big tech & AI news (OpenAI, Google, Meta, Nvidia, Microsoft, Apple...), famous billionaires and CEOs, new AI tools, world-famous US leaders, then big celebrity/viral stories.
- FACEBOOK-SAFE WORDING: clashes, feuds and scandals are allowed (people love them), but word them safely: say who claims it ('Musk claims', 'according to reports'), never state an accusation as fact, no sexual, graphic or violent words, no profanity, insults or slurs, no medical or money-making promises.
- English only. Friendly, energetic, not clickbait-lying.

Return JSON exactly in this shape:
{{"posts": [{{
  "key": "short-lowercase-slug-of-topic",
  "topic": "topic name",
  "badge": "exactly one of: AI, TECH, POLITICS, BUSINESS, CELEBRITY, SPORTS, TV & FILM, MUSIC, GAMING, SHOPPING, VIRAL, SCIENCE",
  "first_comment": "one short, fun question the page itself will comment under the post to spark replies (max 15 words, 1 emoji, e.g. 'Would you watch this? Yes or no? 👇')",
  "share_groups": ["2-3 short Facebook group search terms where fans of this exact topic hang out, e.g. 'Manchester City fans', 'Premier League fans'"],
  "headline": "6-10 words in normal sentence case (NOT all caps). It is the poster title AND the big hook text on top of the Reel for the whole video, so on its own it must say what happened and make people stop scrolling; written like the big viral news pages (Pubity, Pop Base). Use the PERSONALITY + MONEY + CLASH formula: lead with the famous person's name (not the company), then the most dramatic true angle - a big dollar figure, a rivalry or clash (X vs Y, slams, fires back, warns), a shock move or a surprising statement - and leave a little curiosity while still saying what happened (never invent or exaggerate). No emoji. Wrap the 1-3 most striking words in *asterisks* for a highlighter, e.g. 'Elon Musk just *fired back* at Sam Altman over OpenAI', 'Jensen Huang's fortune *jumps $20 billion* in one day'",
  "subtext": "max 14 words, one supporting line for the poster with the key fact",
  "wiki_title": "exact English Wikipedia article title of the ONE main famous PERSON in the story (e.g. 'Sam Altman', 'Jensen Huang', 'Donald Trump'). If the story is about a company or product, use its famous leader ONLY if the snippets name them. Empty string only if no famous person is involved. Stories with a famous face do much better, so prefer them.",
  "image_prompt": "one vivid sentence describing a professional, photorealistic, high-contrast scene that explains the story at a glance - its actual subject matter (its sport, venue, objects, setting - e.g. tennis upset -> a tennis racket on a clay court under stadium lights; TV casting news -> a film set with a director's chair and cameras), with the main subject large in the upper half of the frame (no text, no real person's face, no logos)",
  "template": "one of viral, breaking, frame (viral = celebrity/entertainment/viral moments, breaking = news/sports/events, frame = tech/lifestyle/products)",
  "caption": "80-140 words. First line = strong hook with 1-2 emojis. Short paragraphs. Explain what happened using only given facts. End with ONE natural question to spark comments. No engagement bait (Facebook cuts the reach of posts that say 'share this', 'tag a friend', 'like if', 'send this to a friend', 'comment YES').",
  "hashtags": ["10 to 15 hashtags without #, mix of broad popular tags and topic-specific tags"],
  "image_query": "2-4 words for a vibrant, eye-catching, professional stock photo that captures the excitement or emotion of the story, not a dull literal object (e.g. 'online shopping sale' not 'cardboard boxes', 'football stadium crowd' not 'football'). No person names.",
  "voiceover": "the Reel narration, read aloud by an energetic US news voice: 30-36 words (the Reel is 15-17 seconds), 4-5 SHORT punchy spoken sentences (max 10 words each, so the visuals can change often), built like the top viral news pages: (1) HOOK, max 8 words, said in the first 2 seconds: the single most shocking TRUE fact with the famous name - a big number, a clash or a wild move - so nobody scrolls past (e.g. 'Elon Musk is giving every American kid SpaceX stock.'); (2-3) the STORY in concrete facts from the reports - numbers, dates, who said what - so a viewer who knows nothing fully understands what happened; (4) the TWIST: the most surprising true detail or what it means for the viewer (e.g. 'That could cost him over 10 billion dollars.' or 'But nobody knows how it works yet.'); (5) the question. Every sentence must carry a concrete fact: NEVER vague filler like 'this is huge', 'people are talking', 'it could change everything', 'fans are excited'. Plain words, no emoji, no hashtags, no asterisks. End with ONE short direct question the viewer can answer in a word in the comments (e.g. 'Is he right?' or 'Would you buy one?'). Do NOT add a follow request (added automatically). Each sentence should say something that can be SHOWN (the person, a place, an object, an action).",
  "scenes": [{{"line": "the exact words of ONE sentence or phrase of the voiceover, in order, together covering the whole voiceover (4-6 lines)", "show": "face (show the famous person) or video (stock footage)", "query": "for video: 2-4 word stock video search that shows exactly what this line says, concrete and filmable, never abstract (e.g. 'rocket launch', 'electric car driving', 'stock market screen', 'robot arm factory'); no person names", "photo": "2-4 word search for a REAL photo of the exact thing this line names, as a backup (e.g. 'Tesla Roadster', 'Falcon 9 launch', 'Starlink dish', 'Tesla factory'); no person names"}}],
  "video_queries": ["2-3 searches (2-3 words each) for real stock VIDEO footage showing the story's action and mood, for fast cuts in a Reel, e.g. tennis upset -> 'tennis match', 'stadium crowd cheering'; app hack -> 'smartphone notification', 'shocked woman phone'. No person names."],
  "theme": "one of {THEMES}"
}}]}}
"""


def _parse_json(text):
    """JSON from a model answer: strips ``` fences and any words around the outermost {...}."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])


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
        p["caption"] = BAIT.sub("", p["caption"]).strip()  # engagement bait gets a post's reach cut
        p["theme"] = p.get("theme") if p.get("theme") in THEMES else "red"
        p["key"] = re.sub(r"[^a-z0-9-]+", "-", p["key"].lower()).strip("-")[:60]
        good.append(p)
    return good


# "share this / tag a friend / like if..." sentences: Facebook demotes engagement bait
BAIT = re.compile(r"[^.!?\n]*\b(send this to|share this|tag (a|your) friends?|like if|comment yes|smash (that|the) like)\b[^.!?\n]*[.!?]*[ \t]*(?:\S*[^\x00-\x7f]\S*)?", re.I)


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


def _light(prompt):
    """Small, fast Groq model with its own (much bigger) rate limit: for short jobs like comment replies,
    so they never use up the quota the posts need."""
    if not config.GROQ_API_KEY:
        raise RuntimeError("no Groq key")
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=60,
                      headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                      json={"model": "llama-3.1-8b-instant", "temperature": 0.7, "max_tokens": 2000,
                            "response_format": {"type": "json_object"},
                            "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        raise RuntimeError(f"Groq light HTTP {r.status_code}: {r.text[:120]}")
    return r.json()["choices"][0]["message"]["content"]


def _cloudflare_text(prompt):
    """Last backup writer: Cloudflare Workers AI (free daily allowance, separate from Gemini and Groq)."""
    acc, tok = os.getenv("CF_ACCOUNT_ID", "").strip(), os.getenv("CF_API_TOKEN", "").strip()
    if not acc or not tok:
        raise RuntimeError("no Cloudflare keys")
    r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                      headers={"Authorization": f"Bearer {tok}"}, timeout=120,
                      json={"messages": [{"role": "user", "content": prompt + "\nReply with the JSON only."}],
                            "max_tokens": 6000, "temperature": 0.7})
    if r.status_code != 200:
        raise RuntimeError(f"Cloudflare HTTP {r.status_code}: {r.text[:120]}")
    out = (r.json().get("result") or {}).get("response")
    if isinstance(out, (dict, list)):
        out = json.dumps(out)
    print("[ai] Cloudflare model used: llama-3.3-70b")
    return out or ""


def write_posts(candidates, already_posted, n, performance="", special=""):
    engines = []
    if config.GEMINI_API_KEY:
        engines.append(_gemini)
    if config.GROQ_API_KEY:
        engines.append(_groq)
    if os.getenv("CF_ACCOUNT_ID") and os.getenv("CF_API_TOKEN"):
        engines.append(_cloudflare_text)
    if not engines:
        raise RuntimeError("No AI key found. Add GEMINI_API_KEY (or GROQ_API_KEY) in GitHub Secrets.")
    for attempt in range(2):
        if attempt:  # every engine busy (per-minute limits): wait for the limits to reset, then once more
            print("[ai] all AI engines busy -> waiting 65 s and trying again")
            time.sleep(65)
        for size in dict.fromkeys((n, 1)):  # if a long answer fails, ask for a single post
            prompt = build_prompt(candidates, already_posted, size, performance, special)
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


def same_story(headline, recent):
    """Is this the same story as one the page already posted, even in other words ('teases next-gen Roadster' =
    'unveils flying Tesla car')? Asks the small AI. Returns the old headline, or '' (also when the AI is busy)."""
    if not recent:
        return ""
    lines = "\n".join(f"{i}. {h}" for i, h in enumerate(recent, 1))
    prompt = (f'NEW headline: "{headline}"\n\nHeadlines already posted:\n{lines}\n\n'
              "Is the NEW headline about the SAME news event or the same product/announcement as one of the posted "
              "ones (even if worded differently)? Different events about the same person are NOT the same. "
              'Reply JSON: {"same": 0} if none, or {"same": n} with the number of the matching headline.')
    try:
        n = int(_parse_json(_light(prompt)).get("same", 0) or 0)
    except Exception as e:
        print(f"[ai] same-story check skipped: {str(e)[:100]}")
        return ""
    return recent[n - 1] if 1 <= n <= len(recent) else ""


def _ask(prompt):
    """One JSON answer from the big writers (Gemini -> Groq -> Cloudflare), or None when all are busy."""
    engines = [e for e, ok in ((_gemini, config.GEMINI_API_KEY), (_groq, config.GROQ_API_KEY),
                               (_cloudflare_text, os.getenv("CF_ACCOUNT_ID") and os.getenv("CF_API_TOKEN"))) if ok]
    for engine in engines:
        try:
            data = _parse_json(engine(prompt))
            return data[0] if isinstance(data, list) and data else data
        except Exception as e:
            print(f"[editor] {engine.__name__} error: {str(e)[:150]}")
    return None


def _review(post, facts):
    prompt = (f"You are the strict editor of a viral US news page (like Pubity or Pop Base). Score this 15-second "
              f"Reel script.\n\nFACTS (the only true information):\n{facts}\n\nREEL:\n"
              f"on-screen headline (shown big for the whole video): {post['headline'].replace('*', '')}\n"
              f"narration: {post.get('voiceover', '')}\n\n"
              "Score 0-10 each, be harsh (8 = as good as the best viral pages):\n"
              "hook: do the headline and the first sentence make a scroller STOP within 2 seconds (a shocking true "
              "fact, a big number, a clash, a famous name)?\n"
              "clarity: would a viewer who never heard of this story fully understand what happened and why it matters?\n"
              "sense: does every sentence make sense, follow from the one before, and carry a real fact (no vague "
              "filler like 'this is huge' or 'people are talking')?\n"
              "facts: is everything said backed by the FACTS (no invented numbers, quotes or claims)?\n"
              'Reply JSON only: {"hook": n, "clarity": n, "sense": n, "facts": n, '
              '"problems": "exactly what is weak, in one or two sentences"}')
    data = _ask(prompt)
    if not isinstance(data, dict):
        return None, ""
    try:
        score = min(float(data.get(k, 0)) for k in ("hook", "clarity", "sense", "facts"))
    except (TypeError, ValueError):
        return None, ""
    print(f"[editor] {score:.0f}/10 (hook {data.get('hook')}, clarity {data.get('clarity')}, sense {data.get('sense')}, "
          f"facts {data.get('facts')}) - {data.get('problems', '')}")
    return score, str(data.get("problems", ""))


def edit_reel(post, facts):
    """A strict AI editor scores the Reel script (hook, clarity, sense, facts) and has it rewritten once when it
    is weak. Returns (score 0-10, post); score None means no editor was available, so the post is used as it is."""
    score, problems = _review(post, facts)
    if score is None or score >= 8:
        return score, post
    keys = ("headline", "voiceover", "scenes", "first_comment")
    old = json.dumps({k: post.get(k) for k in keys}, ensure_ascii=False)
    prompt = (f"You write Reels for a viral US news page (like Pubity or Pop Base). The editor rejected this "
              f"15-second Reel script:\n{old}\n\nEDITOR: {problems}\n\nFACTS (the only true information):\n{facts}\n\n"
              "Rewrite it so it fixes every problem. Rules:\n"
              "- headline: 6-10 words, sentence case, famous name first, the most shocking TRUE angle, it must say "
              "what happened on its own; wrap the 1-3 most striking words in *asterisks*.\n"
              "- voiceover: 30-36 words, 4-5 short sentences (max 10 words each): HOOK (max 8 words, the most "
              "shocking true fact with the name), the STORY in concrete facts (numbers, dates, who said what), the "
              "TWIST (the most surprising true detail or what it means for the viewer), then ONE short question. "
              "Every sentence carries a real fact; no vague filler; no follow request; plain words, no emoji.\n"
              "- scenes: 4-6 items covering the new voiceover in order, each {\"line\": exact words of the "
              "voiceover, \"show\": \"face\" or \"video\", \"query\": 2-4 word stock video search showing exactly "
              "that line, \"photo\": 2-4 word search for a real photo of the thing named}; no person names in "
              "query or photo.\n"
              "- first_comment: one short fun question (max 15 words, 1 emoji).\n"
              "- Use ONLY the FACTS. Facebook-safe wording.\n"
              'Reply JSON only with the keys "headline", "voiceover", "scenes", "first_comment".')
    data = _ask(prompt)
    if not isinstance(data, dict) or not str(data.get("voiceover", "")).strip() or not str(data.get("headline", "")).strip():
        print("[editor] rewrite failed -> keeping the first version")
        return score, post
    new = dict(post)
    new["headline"], new["voiceover"] = str(data["headline"]).strip(), str(data["voiceover"]).strip()
    scenes = [s for s in (data.get("scenes") or []) if isinstance(s, dict) and str(s.get("line", "")).strip()]
    new["scenes"] = scenes if len(scenes) >= 2 else []
    if str(data.get("first_comment", "")).strip():
        new["first_comment"] = str(data["first_comment"]).strip()
    print(f"[editor] rewritten: {new['headline']} | {new['voiceover']}")
    score2, _ = _review(new, facts)
    if score2 is None or score2 >= score:
        return (score2 if score2 is not None else score), new
    return score, post
