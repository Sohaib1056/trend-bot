"""Fast-cut vertical Reel (1080x1920), built like the big viral news pages:

  0.0s  hook: the famous face (or the best footage) and the first spoken line as subtitles (no headline on top)
  every ~2s a new scene, never the same picture twice in a row: real moving footage first (NASA for space stories,
        Pixabay, Wikimedia Commons), real photos only when no clip fits the line, all checked by the AI judge;
        clean hard cuts
  voice: AI narration (Cloudflare Deepgram Aura-2) with big word-by-word subtitles in the middle of the screen
  end   "FOLLOW HYPE ATLAS" banner over the footage (no black screen, so the Reel loops smoothly)
  All text stays inside the Reels safe zone (Facebook covers the bottom ~35% with the caption and buttons).
Music: a random track from assets/music/ (royalty-free), quieter under the voice.
"""
import base64
import collections
import glob
import os
import random
import re
import subprocess

import requests
from PIL import Image, ImageDraw, ImageFilter

from . import config
from .growth import FOLLOW_PROMISE
from .poster import (COMMONS_SKIP, FREE_LICENSE, MARKER, INK, PAD, PINK, ORANGE, YELLOW, WHITE, WIKI_UA,
                     BROWSER_UA, _cloudflare_image, _cover, _download, _enhance, _fit, _font, _hgradient, _logo,
                     _wm_thumb, openverse_person,
                     _paste_rounded, _tokens, _vfade)

RW, RH = 1080, 1920
FPS = 30
PHOTO_W = 1920       # Wikimedia copy width: 1280px photos were blown up ~1.5-2x on a 1080x1920 screen and looked soft
SCENE = 2.0          # target seconds per scene
MAX_SCENES = 9
VOICE_AT = 0.3       # narration starts after the hook frame
FOLLOW_FROM = 1.0   # the follow label from the 1st second (owner, 11-24: ask viewers to follow from second 1)
END_LEN = 1.8        # follow banner at the end (when there is no voice to time it)
SUB_Y = 1225         # centre of the subtitles: low in the Reels safe zone, under the face (owner: a bit lower)
FACE_Y = 720         # where a face is placed: below the logo, above the subtitles
MUSIC_DIR = os.path.join(config.BASE_DIR, "assets", "music")
# backup footage searches per category, so every Reel gets moving video even when the story is hard to match
BROLL = {
    "SPORTS": ["stadium crowd cheering", "sports fans celebrating"],
    "CELEBRITY": ["red carpet cameras", "paparazzi flash"],
    "TV & FILM": ["film set camera", "cinema audience"],
    "MUSIC": ["concert crowd lights", "stage lights singer"],
    "TECH": ["technology digital", "smartphone screen"],
    "AI": ["artificial intelligence", "robot technology", "data center servers"],
    "POLITICS": ["white house washington", "american flag waving", "us capitol"],
    "GAMING": ["gaming setup neon", "video game controller"],
    "SHOPPING": ["shopping bags city", "online shopping phone"],
    "ROYALS": ["london buckingham palace", "royal guard london"],
    "WEATHER": ["storm clouds timelapse", "heavy rain city"],
    "BUSINESS": ["stock market screen", "city skyline business"],
    "SCIENCE": ["science laboratory", "space stars galaxy"],
    "VIRAL": ["city night timelapse", "crowd phones filming"],
}
CLIP_DIR = os.path.join(config.OUTPUT_DIR, "clips")


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _with_alpha(layer, a):
    if a >= 1:
        return layer
    out = layer.copy()
    out.putalpha(layer.getchannel("A").point(lambda v: int(v * max(0.0, a))))
    return out


# ------------------------------------------------------------------ footage
def _pixabay_videos(post, want=3, queries=None, context=None, seen=None, max_judge=9):
    """Real stock video clips for the story, each checked by the AI judge (via its thumbnail).
    queries/context: search for one scene of the narration and judge the clip against that line."""
    from .ai import judge_image
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if queries is None:
        queries = [q for q in (post.get("video_queries") or []) if isinstance(q, str) and q.strip()]
        queries += [post.get("image_query") or ""] + BROLL.get((post.get("badge") or "").upper(), BROLL["VIRAL"])
    if not key or want <= 0:
        return []
    headline = context or post["headline"].replace("*", "")
    os.makedirs(CLIP_DIR, exist_ok=True)
    clips, judged = [], 0
    seen = set() if seen is None else seen
    for q in queries:  # space stories: real NASA footage of actual launches first, stock footage after
        if len(clips) < want and q.strip() and SPACE.search(q + " " + headline):
            clips += _nasa_videos(q, want - len(clips), headline, seen)
    for q in queries:
        if len(clips) >= want or judged >= max_judge or not q.strip():
            continue
        try:
            r = requests.get("https://pixabay.com/api/videos/", timeout=20, params={
                "key": key, "q": q[:100], "safesearch": "true", "order": "popular", "per_page": 12})
            hits = [h for h in r.json().get("hits", []) if h.get("duration", 0) >= 3 and h["id"] not in seen]
        except Exception as e:
            print(f"[reel] Pixabay video search failed for '{q}': {e}")
            continue
        # full-HD (or bigger) first: smaller clips look soft when cropped to 9:16; vertical clips first
        hits.sort(key=lambda h: (max(h["videos"]["large"].get("width", 0), h["videos"]["large"].get("height", 0)) < 1920,
                                 h["videos"]["large"].get("height", 0) <= h["videos"]["large"].get("width", 1)))
        taken = 0
        for h in hits[:4]:
            if len(clips) >= want or judged >= max_judge or taken >= 2:
                break
            seen.add(h["id"])
            v = h["videos"].get("large") or {}
            if not v.get("url") or v.get("width", 0) < 1280:
                v = h["videos"].get("medium") or {}
            if not v.get("url") or max(v.get("width", 0), v.get("height", 0)) < 1280:  # soft, low-res clips look cheap
                continue
            try:
                judged += 1
                score = judge_image(_download(v["thumbnail"]), headline, broll=True) if v.get("thumbnail") else None
                if score is not None and (score[0] < (6 if context else 5) or score[1] < 6):  # a line's clip must fit it
                    print(f"[reel] clip {h['id']} for '{q}' rejected by the judge")
                    continue
                path = os.path.join(CLIP_DIR, f"{h['id']}.mp4")
                if not os.path.exists(path):
                    data = requests.get(v["url"], timeout=90)
                    data.raise_for_status()
                    with open(path, "wb") as f:
                        f.write(data.content)
                clips.append((path, h.get("duration", 0)))
                taken += 1
                print(f"[reel] clip {h['id']} for '{q}' ({v.get('width')}x{v.get('height')}, {h.get('duration')}s)")
            except Exception as e:
                print(f"[reel] clip {h.get('id')} failed: {e}")
    for q in queries:  # Pixabay short: free Wikimedia Commons videos
        if len(clips) < want and q.strip():
            clips += _commons_videos(q, want - len(clips), headline, seen)
    return clips


SPACE = re.compile(r"\b(space|rocket|launch|nasa|starship|spacex|mars|moon|orbit|astronaut|satellite|starlink|iss)\b", re.I)


def _judge_clip(thumb_url, context, headers=None):
    from .ai import judge_image
    try:
        score = judge_image(_download(thumb_url, headers), context, broll=True)
    except Exception:
        return True  # no thumbnail to check: accept
    return score is None or (score[0] >= 5 and score[1] >= 6)


def _nasa_videos(q, want, context, seen):
    """NASA image & video library (public domain). Streamed straight from NASA, skipping the opening titles."""
    out = []
    try:
        items = requests.get("https://images-api.nasa.gov/search", params={"q": q, "media_type": "video"},
                             timeout=20).json()["collection"]["items"]
    except Exception as e:
        print(f"[reel] NASA search failed: {e}")
        return out
    for it in items[:6]:
        if len(out) >= want:
            break
        nid = (it.get("data") or [{}])[0].get("nasa_id", "")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        try:
            files = [f.replace(" ", "%20").replace("http://", "https://")
                     for f in requests.get(it["href"].replace(" ", "%20"), timeout=20).json()]
            # full HD first: a 720p clip cropped to 9:16 is blown up ~2.7x and looks blurry
            mp4 = next((f for size in ("~large.mp4", "~medium.mp4") for f in files if f.endswith(size)), None)
            thumb = next((f for f in files if f.endswith("~small.jpg") or f.endswith("~thumb.jpg")), None)
            if mp4 and (not thumb or _judge_clip(thumb, context)):
                out.append((mp4, 60, 20.0))  # from 20s: past the title card NASA puts at the start
                print(f"[reel] NASA clip for '{q}': {nid}")
        except Exception as e:
            print(f"[reel] NASA clip {nid} failed: {e}")
    return out


def _commons_videos(q, want, context, seen):
    """Free-licensed videos on Wikimedia Commons, streamed from Commons."""
    out = []
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"{q} filetype:video", "gsrlimit": 10,
            "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 640})
        pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    except Exception as e:
        print(f"[reel] Commons video search failed: {e}")
        return out
    for p in pages:
        if len(out) >= want:
            break
        ii = (p.get("imageinfo") or [{}])[0]
        lic = ii.get("extmetadata", {}).get("LicenseShortName", {}).get("value", "")
        if not ii.get("url") or ii["url"] in seen or not lic.lower().startswith(FREE_LICENSE) or ii.get("size", 0) > 300e6:
            continue
        if min(ii.get("width", 0), ii.get("height", 0)) < 720:  # HD only
            continue
        seen.add(ii["url"])
        if ii.get("thumburl") and not _judge_clip(ii["thumburl"], context, WIKI_UA):
            continue
        out.append((ii["url"], 30, 1.0))
        print(f"[reel] Commons video for '{q}': {p.get('title', '')[:60]} ({lic})")
    return out


def _base_file(title):
    """'File:X, official portrait (cropped).jpg' and 'X, official portrait.jpg' are the same picture."""
    return re.sub(r"\s*\([^)]*\)|\.\w+$|^file:", "", title.lower().replace("_", " ")).strip()


def _person_photos(post, want=3, used=()):
    """More real photos of the person in the story (Wikimedia Commons, free licences only), checked by the judge."""
    from .ai import judge_image
    name = (post.get("wiki_title") or "").strip()
    if not name or not post.get("_person") or want <= 0:
        return []
    last = name.split()[-1].lower()
    out, judged = [], 0
    used = {_base_file(u) for u in used if u}
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f'"{name}" filetype:bitmap', "gsrlimit": 30,
            "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": PHOTO_W})
        pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    except Exception as e:
        print(f"[reel] Commons search for '{name}' failed: {e}")
        pages = []
    for p in pages:
        if len(out) >= want or judged >= 5:
            break
        title, ii = p.get("title", ""), (p.get("imageinfo") or [{}])[0]
        if last not in title.lower() or not title.lower().endswith((".jpg", ".jpeg")) or COMMONS_SKIP.search(title):
            continue
        if _base_file(title) in used:  # a crop of a photo already in the Reel
            continue
        if min(ii.get("width", 0), ii.get("height", 0)) < 900:
            continue
        meta = ii.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if not lic.lower().startswith(FREE_LICENSE) or "non-free" in lic.lower():
            continue
        try:
            im = _download(ii.get("thumburl") or ii["url"], WIKI_UA)
            judged += 1
            score = judge_image(im, post["headline"].replace("*", ""))
            if score is not None and (score[0] < 6 or score[1] < 6):
                print(f"[reel] photo {title[:50]} rejected by the judge")
                continue
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()[:40] or "Wikimedia Commons"
            used.add(_base_file(title))
            out.append((im, f"{artist}, {lic}, via Wikimedia Commons", score[2] if score else None))
            print(f"[reel] extra photo of {name}: {title[:60]}")
        except Exception as e:
            print(f"[reel] photo {title[:40]} failed: {e}")
    if len(out) < want:  # Commons busy or short: more free photos from Openverse (Flickr etc.)
        for im, credit, face in openverse_person(name, post["headline"].replace("*", ""), want - len(out)):
            out.append((im, credit.replace("📷 Photo: ", ""), face))
    return out


_OPENVERSE = {"down": False}  # Openverse often times out: after one failure, skip it for the rest of the run


def _real_photo(query, line, seen):
    """A real, free-licensed photo (Openverse: Flickr, Wikimedia...) of the exact thing a line names, e.g. 'Tesla
    Roadster' or 'Falcon 9 launch'; the judge must find it on-topic and good-looking. Returns (img, credit) or None."""
    from .ai import judge_image
    if _OPENVERSE["down"]:
        return None
    try:
        r = requests.get("https://api.openverse.org/v1/images/", headers=WIKI_UA, timeout=15, params={
            "q": query, "license_type": "commercial", "page_size": 20})
        results = r.json().get("results", [])
    except Exception as e:
        print(f"[reel] Openverse search for '{query}' failed: {str(e)[:120]} -> skipping Openverse for this run")
        _OPENVERSE["down"] = True
        return None
    good = [x for x in results if min(x.get("width") or 0, x.get("height") or 0) >= 900 and x.get("url") not in seen
            and not COMMONS_SKIP.search(x.get("title") or "")]
    for x in good[:3]:
        seen.add(x["url"])
        url = _wm_thumb(x["url"], PHOTO_W) if x.get("source") == "wikimedia" and (x.get("width") or 0) > PHOTO_W else x["url"]
        try:
            im = _download(url, WIKI_UA if x.get("source") == "wikimedia" else BROWSER_UA)
            score = judge_image(im, line)
            if score is not None and (score[0] < 6 or score[1] < 7):  # professional-looking only
                print(f"[reel] photo for '{query}' rejected by the judge")
                continue
            lic = x.get("license", "")
            lic = "public domain" if lic in ("cc0", "pdm") else f"CC {lic.upper()} {x.get('license_version', '')}".strip()
            print(f"[reel] real photo for '{query}': {(x.get('title') or '')[:50]} ({x.get('source')})")
            return im, f"{(x.get('creator') or 'unknown')[:40]}, {lic}, via {(x.get('source') or 'Openverse').title()}"
        except Exception as e:
            print(f"[reel] photo for '{query}' failed: {e}")
    return None


# article pictures that are not scenes: statistics, documents, maps
NOT_A_SCENE = re.compile(r"trends?\b|graph|statistic|percent|survey|census|by (race|age|state|year)|population|"
                         r"locator|location|document|letter|plaque|table|timeline", re.I)
_WIKI_MEDIA = {}  # article title -> its free photos, so several lines about the same thing share one lookup


def _wiki_photos(title, line, seen, skip="", tries=3):
    """A real photo of exactly what a line names, from the photos in its Wikipedia article (e.g. 'SpaceX Starship',
    'Greg Abbott', 'Tesla Cybertruck'): free licences only, big enough, judged on-topic and good-looking.
    `skip` = the main person's last name: their face is shown once, at the start. Returns (img, credit) or None."""
    from .ai import judge_image
    if title not in _WIKI_MEDIA:
        try:
            r = requests.get("https://en.wikipedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
                "action": "query", "format": "json", "generator": "images", "titles": title, "gimlimit": 50,
                "redirects": 1, "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": PHOTO_W})
            pages = list(((r.json().get("query") or {}).get("pages") or {}).values())
        except Exception as e:
            print(f"[reel] Wikipedia photos for '{title}' failed: {e}")
            pages = []
        words = set(re.findall(r"[a-z0-9]+", title.lower()))
        items = []
        for p in pages:
            name, ii = p.get("title", ""), (p.get("imageinfo") or [{}])[0]
            lic = ii.get("extmetadata", {}).get("LicenseShortName", {}).get("value", "")
            if (not name.lower().endswith((".jpg", ".jpeg")) or COMMONS_SKIP.search(name) or NOT_A_SCENE.search(name)
                    or min(ii.get("width", 0), ii.get("height", 0)) < 800
                    or not lic.lower().startswith(FREE_LICENSE) or "non-free" in lic.lower()):
                continue
            artist = re.sub(r"<[^>]+>", "", ii.get("extmetadata", {}).get("Artist", {}).get("value", "")).strip()[:40]
            items.append((name, ii.get("thumburl") or ii["url"], f"{artist or 'Wikimedia Commons'}, {lic}, via Wikimedia Commons"))
        # photos whose file name matches the article (e.g. 'Tesla Cybertruck.jpg') first
        items.sort(key=lambda x: -len(words & set(re.findall(r"[a-z0-9]+", x[0].lower()))))
        _WIKI_MEDIA[title] = items
    for name, url, credit in _WIKI_MEDIA[title]:
        if tries <= 0:
            break
        if url in seen or _base_file(name) in seen or (skip and skip in name.lower()):
            continue
        seen.update((url, _base_file(name)))
        tries -= 1
        try:
            im = _download(url, WIKI_UA)
            score = judge_image(im, line)
            if score is not None and (score[0] < 5 or score[1] < 7):  # professional-looking only
                print(f"[reel] Wikipedia photo {name[5:55]} rejected by the judge")
                continue
            print(f"[reel] real photo from '{title}': {name[5:65]}")
            return im, credit
        except Exception as e:
            print(f"[reel] photo {name[5:45]} failed: {e}")
    return None


def _good_still(im, line):
    """Every scene must look good: an AI picture is used only if the judge rates it sharp and fitting."""
    from .ai import judge_image
    score = judge_image(im, line, broll=True)
    if score is not None and (score[0] < 5 or score[1] < 7):
        print(f"[reel] AI picture for '{line[:40]}' rejected by the judge (quality {score[1]})")
        return False
    return True


def _ai_images(post, n):
    """Extra AI scenes (different angles of the story) when real footage is short."""
    base = post.get("image_prompt") or post.get("image_query") or ""
    out = []
    for k, angle in enumerate(("dramatic close-up detail shot of", "wide cinematic establishing shot of",
                               "dynamic low-angle shot of")[:n]):
        im = _cloudflare_image(f"{angle} {base}", abs(hash(post["key"])) % 100000 + 17 * (k + 1))
        if im is not None:
            out.append(im)
    return out


# ------------------------------------------------------------------ voice
def _voice_text(post):
    text = post.get("voiceover") or (post["headline"] + ". " + post.get("subtext", ""))
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "‑": "-", "–": ", ", "—": ", "}))
    text = re.sub(r"[*#_]|[^\x00-ɏ\s]", "", text).strip()  # drop emoji
    return f"{text} Follow Hype Atlas for {post.get('_follow') or FOLLOW_PROMISE}."  # series: 'part 5 tomorrow'


KOKORO_FILES = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def _kokoro(text, path):
    """Kokoro: free, open-source voice that runs on the GitHub runner itself (no key, no limit).
    Installed and downloaded (~340 MB) only when it is needed."""
    import sys
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "kokoro-onnx", "soundfile"], check=True, timeout=600)
    import soundfile
    from kokoro_onnx import Kokoro
    cache = os.path.join(os.path.expanduser("~"), ".cache", "kokoro")
    os.makedirs(cache, exist_ok=True)
    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        target = os.path.join(cache, name)
        if not os.path.exists(target):
            with requests.get(KOKORO_FILES + name, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(target + ".part", "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            os.replace(target + ".part", target)
    voice = Kokoro(os.path.join(cache, "kokoro-v1.0.onnx"), os.path.join(cache, "voices-v1.0.bin"))
    samples, rate = voice.create(text, voice=os.getenv("KOKORO_VOICE", "af_heart"), speed=1.05, lang="en-us")
    wav = path.rsplit(".", 1)[0] + ".wav"
    soundfile.write(wav, samples, rate)
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", wav, "-b:a", "160k", path], check=True)
    return True


def _tts(text, path):
    """Voiceover: Cloudflare Workers AI (Deepgram Aura-2, natural pacing like a real presenter; Aura-1 as backup)
    first, then Kokoro (free, unlimited, runs locally), then Cloudflare MeloTTS. VOICE_ENGINE=kokoro puts Kokoro
    first. Returns True on success."""
    acc, tok = os.getenv("CF_ACCOUNT_ID", "").strip(), os.getenv("CF_API_TOKEN", "").strip()
    base = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/"
    speaker = os.getenv("VOICE", "asteria")  # the page's usual voice; Aura-2 has the same speaker names
    engines = [("@cf/deepgram/aura-2-en", {"text": text, "speaker": speaker, "encoding": "mp3"}),
               ("@cf/deepgram/aura-1", {"text": text, "speaker": speaker, "encoding": "mp3"}),
               ("kokoro", None), ("@cf/myshell-ai/melotts", {"prompt": text, "lang": "en"})]
    if os.getenv("VOICE_ENGINE", "").strip().lower() == "kokoro":
        engines.sort(key=lambda e: e[0] != "kokoro")
    for model, body in engines:
        if model == "kokoro":
            try:
                _kokoro(text, path)
                print(f"[reel] voiceover by Kokoro: \"{text}\"")
                return True
            except Exception as e:
                print(f"[reel] voice Kokoro failed: {e}")
            continue
        if not acc or not tok:
            continue
        try:
            r = requests.post(base + model, headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=90)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
            if "json" in r.headers.get("content-type", ""):
                audio = (r.json().get("result") or {}).get("audio")
                if not audio:
                    raise RuntimeError(f"no audio: {r.text[:150]}")
                data = base64.b64decode(audio)
            else:
                data = r.content
            if len(data) < 2000:
                raise RuntimeError("audio too short")
            with open(path, "wb") as f:
                f.write(data)
            print(f"[reel] voiceover by {model}: \"{text}\"")
            return True
        except Exception as e:
            print(f"[reel] voice {model} failed: {e}")
    return False


def _audio_secs(exe, path):
    out = subprocess.run([exe, "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


# ------------------------------------------------------------------ scenes
def _people(post, photo, want):
    """Real photos of the story's famous person, with where the face is: [(img, face_y)]."""
    people = []
    if post.get("_person"):
        from .ai import judge_image
        score = judge_image(photo, post["headline"].replace("*", ""))  # where is the face?
        people.append((photo, score[2] if score else None))
        extra_people = _person_photos(post, want=want, used=[photo.info.get("wm_file", "")])
        people += [(im, face) for im, _, face in extra_people]
        if extra_people:
            post["_reel_credits"] = [c for _, c, _ in extra_people]
    return people


MIN_SCENE, MAX_SCENE = 1.0, 2.2  # seconds: a new picture at least every ~2s (owner: more scenes)
REEL_LEN = 15.0  # Reels run 15 seconds...
MAX_LEN = 17.0   # ...or up to 17 when the full story needs it (owner's rule)
LIFE_LEN, LIFE_MAX = 25.0, 30.0  # life-story series Reels: 25-30 seconds (owner, 11-11)


def _planned_scenes(post, photo, words, duration):
    """Scenes that follow the narration: the AI split the voiceover into lines and named what each one is about.
    Owner's rule: the person's face once, at the start; then real scenes of exactly what is said - a real photo
    from the Wikipedia article of the thing/person/place the line names, else real footage, else other real
    photos. Returns [(kind, item, frames)] or None."""
    plan = [s for s in (post.get("scenes") or []) if isinstance(s, dict) and str(s.get("line", "")).strip()]
    if len(plan) < 2 or not words:
        return None
    # start time of each line = when its first word is spoken
    starts, idx = [], 0
    for s in plan:
        starts.append(0.0 if not starts else words[min(idx, len(words) - 1)][1])
        idx += len(str(s["line"]).split())
    bounds = [(starts[i], starts[i + 1] if i + 1 < len(plan) else duration) for i in range(len(plan))]
    segs = []  # merge too-short lines into the previous one, split long ones so every scene is ~2s
    for s, (a, b) in zip(plan, bounds):
        if segs and b - a < MIN_SCENE:
            segs[-1][2] = b
            continue
        n = max(1, -(-(b - a) // MAX_SCENE))
        n = int(n)
        segs += [[s, a + (b - a) * j / n, a + (b - a) * (j + 1) / n, j] for j in range(n)]
    people = _people(post, photo, want=1)  # the opening face (+ one spare, only if nothing else can be found)
    others = [im for im in post.get("_extras", []) if im is not photo] + ([] if post.get("_person") else [photo])
    main = (post.get("wiki_title") or "").strip()
    skip = main.split()[-1].lower() if main and post.get("_person") else ""  # the face is shown once, at the start
    wikis = list(dict.fromkeys(str(s.get("wiki") or "").strip() for s in plan if str(s.get("wiki") or "").strip()))
    from .trends import STAR_WORLD  # the star's world (rockets, cars, robots...): professional photos for any line
    story = (post["headline"] + " " + str(post.get("voiceover", ""))).lower()
    world = sorted(STAR_WORLD.get(main, []), key=lambda t: not any(w in story for w in re.findall(r"[a-z0-9]{4,}", t.lower())))
    seen, clips_cache, out, stills, credits, used = set(), {}, [], {}, [], set()
    shown = collections.Counter()  # fallback photos taken from each article
    def key(item):  # clips by file, photos (and faces) by object
        first = item[0] if isinstance(item, tuple) else item
        return first if isinstance(first, str) else id(first)

    def new_photo(get):
        got = get()
        if got and key(got[0]) not in used:
            credits.append(got[1])
            return got[0]
        return None

    def new_clip(cache_key, fetch):
        if cache_key not in clips_cache:
            clips_cache[cache_key] = fetch()
        return next((c for c in clips_cache[cache_key] if key(c) not in used), None)

    for k, (s, a, b, part) in enumerate(segs):
        query = str(s.get("query") or "").strip()
        wiki = str(s.get("wiki") or "").strip()
        line = str(s["line"])
        q = str(s.get("photo") or query).strip()
        tries = []
        if wiki and wiki.lower() != main.lower():  # a real photo of exactly what the line names
            tries.append(("photo", lambda: new_photo(lambda: _wiki_photos(wiki, line, seen, skip))))
            shown[wiki] += 1
        if query:  # real footage of the action
            tries.append(("video", lambda: new_clip(query, lambda: _pixabay_videos(
                post, want=3, queries=[query], context=line, seen=seen, max_judge=4))))
        if q:  # a real photo (Openverse) of the thing the line names
            tries.append(("photo", lambda: new_photo(lambda: _real_photo(q, line, seen))))
        # owner, 11-11: moving footage like a professional Reel, not a slideshow of photos: a clip that fits the
        # line (the judge checks it) comes first, a photo only when there is none
        tries.sort(key=lambda t: t[0] != "video")
        kind = item = None
        if k == 0 and people:  # owner's rule: the person's face once, at the start; then real scenes
            kind, item = "face", people[0]
        for kd, fn in ([] if kind else tries):
            item = fn()
            if item is not None:
                kind = kd
                break
        if kind is None:  # another professional real photo: the story's other articles, then the star's world
            pool = [w for w in wikis + world if w.lower() != main.lower()]
            for w in sorted(pool, key=lambda w: shown[w]):  # a different article each time (not 5x Cybertruck)
                item = new_photo(lambda: _wiki_photos(w, line, seen, skip, tries=2))
                if item is not None:
                    kind = "photo"
                    shown[w] += 1
                    break
        if kind is None:  # footage for the story in general (never a clip already shown)
            item = new_clip("_story", lambda: _pixabay_videos(post, want=4, seen=seen, max_judge=5))
            kind = "video" if item is not None else None
        if kind is None and len(people) > 1:  # last resort: another photo of the person
            kind, item = "face", people[1]
        if kind is None:
            kind, item = "photo", (others[len(out) % len(others)] if others else photo)
        used.add(key(item))
        out.append([kind, item, int(round((b - a) * FPS))])
        print(f"[reel] {a:4.1f}-{b:4.1f}s {kind:5} for: {line[:60]}")
    if credits:  # real photos used need attribution in the caption
        post["_reel_credits"] = list(dict.fromkeys((post.get("_reel_credits") or []) + credits))
    if people and out[0][0] != "face":  # a Reel about a famous person always opens on their face
        out[0][0], out[0][1] = "face", people[0]
        print("[reel] first scene -> the face")
    out[-1][2] += int(duration * FPS) - sum(n for _, _, n in out)  # exact total length
    return [tuple(x) for x in out]


def _scenes(post, photo, count):
    """Ordered list of ('photo', img) / ('video', (path, duration)) scenes, no picture shown twice in a row."""
    people = _people(post, photo, want=0)[:1]  # owner's rule: the face once, at the start
    others =([] if post.get("_person") else [photo]) + [im for im in post.get("_extras", []) if im is not photo]
    videos = _pixabay_videos(post, want=min(4, count - len(people)))
    scenes = []
    if people:  # a real face is the strongest hook
        scenes.append(("face", people.pop(0)))
    elif videos:
        scenes.append(("video", videos.pop(0)))
    while len(scenes) < count and (videos or people or others):
        for pool, kind in ((videos, "video"), (people, "face"), (others, "photo")):
            if pool and len(scenes) < count:
                scenes.append((kind, pool.pop(0)))
    distinct = len(scenes)
    k, reuse = 0, [x for x in scenes if x[0] != "face"] or scenes[:]  # the face is not repeated
    while len(scenes) < count:  # still short: re-use pictures, never next to themselves
        cand = reuse[k % len(reuse)]
        if cand is not scenes[-1] or len(reuse) == 1:
            scenes.append(cand)
        k += 1
    print(f"[reel] {len(scenes)} scenes ({distinct} different): " + ", ".join(kind for kind, _ in scenes))
    return scenes


def _video_frames(exe, path, duration, n, start=None):
    """Yields n RGB frames of the clip (a file or a URL to stream), cropped to fill 1080x1920
    (last frame held if the clip is short)."""
    if start is None:
        start = 1.0 if duration >= 5 else 0.0  # skip slow fade-ins
    net = ["-rw_timeout", "30000000"] if path.startswith("http") else []  # give up on a stalled stream after 30s
    # NASA videos have their own captions burned in at the bottom: cut that band off so they don't clash with ours
    trim = "crop=iw:ih*0.8:0:0," if "nasa.gov" in path else ""
    cmd = [exe, "-loglevel", "error", *net, "-ss", str(start), "-i", path, "-an",
           "-vf", f"{trim}scale={RW}:{RH}:force_original_aspect_ratio=increase,crop={RW}:{RH},fps={FPS},eq=contrast=1.06:saturation=1.15",
           "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size, last = RW * RH * 3, None
    try:
        for _ in range(n):
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            last = Image.frombytes("RGB", (RW, RH), buf)
            yield last
            n -= 1
    finally:
        proc.stdout.close()
        proc.wait()
    if last is None:
        raise RuntimeError(f"could not decode {os.path.basename(path)}")
    for _ in range(n):
        yield last


MOVES = [(1.00, 1.16, 0.5, 0.35), (1.16, 1.02, 0.5, 0.3), (1.08, 1.18, 0.3, 0.3), (1.08, 1.18, 0.7, 0.35)]
FACE_MOVES = [(1.00, 1.12), (1.12, 1.02), (1.04, 1.16)]  # slow push-in / pull-out, face stays in place


def _face_frames(img, n, face_y, move):
    """Person photo with the face held at FACE_Y: clear of the headline card above and the subtitles below.
    A blurred copy of the photo fills any space above it when the face sits high in the picture."""
    z0, z1 = move
    face_y = 0.33 if face_y is None else face_y  # head-and-shoulders portraits: face in the upper third
    src = _enhance(img).convert("RGB")
    cw0 = int(RW * 1.15)
    s = max(cw0 / src.width, RH / src.height)
    pic = src.resize((int(src.width * s), int(src.height * s)), Image.LANCZOS)
    left0 = (pic.width - cw0) // 2
    pic = pic.crop((left0, 0, left0 + cw0, pic.height))
    pad = RH  # room above the photo, so the face can be pushed down
    canvas = _cover(src, cw0, pic.height + pad).filter(ImageFilter.GaussianBlur(40))
    canvas = Image.blend(canvas, Image.new("RGB", canvas.size, (8, 8, 12)), 0.45)
    canvas.paste(pic, (0, pad))
    fy = pad + face_y * pic.height
    for i in range(n):
        z = z0 + (z1 - z0) * (i / max(1, n - 1))
        cw, ch = RW / z, RH / z
        top = fy - FACE_Y / RH * ch
        top = max(pad - ch * 0.30, min(top, canvas.height - ch))  # never more than 30% blurred band
        left = (canvas.width - cw) / 2
        yield _view(canvas, left, top, cw, ch)



def _photo_frames(img, n, move):
    """Ken Burns move (zoom in/out, drift left/right) over n frames."""
    z0, z1, fx, fy = move
    bw, bh = int(RW * 1.2), int(RH * 1.2)
    img = _enhance(img)
    if img.width / img.height > 0.9:  # landscape photo: show all of it on a blurred copy, not a thin middle strip
        big = Image.blend(_cover(img, bw, bh).filter(ImageFilter.GaussianBlur(40)), Image.new("RGB", (bw, bh), (8, 8, 12)), 0.4)
        s = min(bw * 1.0 / img.width, bh * 0.62 / img.height)
        fg = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
        big.paste(fg, ((bw - fg.width) // 2, int(bh * 0.40) - fg.height // 2))  # centred a bit high, above the subtitles
    else:
        big = _cover(img, bw, bh)
    for i in range(n):
        z = z0 + (z1 - z0) * (i / max(1, n - 1))
        cw, ch = RW * 1.2 / z, RH * 1.2 / z
        yield _view(big, (big.width - cw) * fx, (big.height - ch) * fy, cw, ch)


def _view(img, left, top, w, h):
    """The (left, top, w, h) window of img scaled to the Reel. Sub-pixel position + bicubic: whole-pixel crops made
    slow zooms step and shake, and bilinear looked soft."""
    return img.resize((RW, RH), Image.BICUBIC, box=(left, top, left + w, top + h))


# ------------------------------------------------------------------ overlays
def _header_layer(post):
    layer = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = PAD - 22, 150  # below the Reels top bar
    lg = _logo(96)
    if lg:
        d.ellipse([x - 4, y - 4, x + 100, y + 100], fill=WHITE)
        layer.alpha_composite(lg, (x, y))
    d.text((x + 120, y + 4), "HYPE ATLAS", font=_font("bold", 40), fill=WHITE, stroke_width=2, stroke_fill=(0, 0, 0))
    d.text((x + 122, y + 56), "VIRAL & TRENDING", font=_font("bold", 22), fill=YELLOW, stroke_width=2, stroke_fill=(0, 0, 0))
    bf = _font("bold", 28)
    label = (post.get("badge") or "TRENDING").upper()[:18]
    bw = int(d.textlength(label, font=bf)) + 80
    bx, by, bh = x, y + 130, 60
    _paste_rounded(layer, _hgradient(bw, bh, [PINK, ORANGE]).convert("RGBA"), (bx, by), bh // 2)
    d = ImageDraw.Draw(layer)
    d.ellipse([bx + 24, by + bh // 2 - 8, bx + 40, by + bh // 2 + 8], fill=WHITE)
    d.text((bx + 52, by + 13), label, font=bf, fill=WHITE)
    if post.get("_life_part"):  # owner, 11-11: the series part on top, all through the Reel
        pf = _font("bold", 58)
        text = f"ELON'S LIFE  •  PART {post['_life_part']}"
        pw, ph = int(d.textlength(text, font=pf)) + 90, 96
        px, py = (RW - pw) // 2, by + bh + 34 + (GIVE_EXTRA if config.GIVEAWAY_ON else 0)
        _paste_rounded(layer, _hgradient(pw, ph, [YELLOW, ORANGE]).convert("RGBA"), (px, py), ph // 2)
        d = ImageDraw.Draw(layer)
        d.text((px + 45, py + 17), text, font=pf, fill=(15, 15, 20))
    return layer


def _subtitle_timeline(text, secs):
    """Word timings for the narration (estimated from word length + pauses), grouped in chunks of <= 3 words."""
    words = text.split()
    weights = [len(w) + 3 + (6 if w[-1] in ".,!?;:" else 0) for w in words]
    scale = secs / max(1, sum(weights))
    t, timed = VOICE_AT + 0.05, []
    for w, wt in zip(words, weights):
        timed.append((w, t, t + wt * scale))
        t += wt * scale
    chunks, cur = [], []
    for item in timed:
        cur.append(item)
        if len(cur) == 3 or item[0][-1] in ".,!?;:":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _draw_subtitle(frame, chunk, t, font):
    """Big centred subtitle, the word being spoken in yellow and slightly raised."""
    d = ImageDraw.Draw(frame)
    words = [w.upper() for w, _, _ in chunk]
    space = d.textlength(" ", font=font)
    widths = [d.textlength(w, font=font) for w in words]
    total = sum(widths) + space * (len(words) - 1)
    size = font.size
    while total > RW - 2 * PAD and size > 50:  # long words: shrink to fit
        size -= 6
        font = _font("bold", size)
        space = d.textlength(" ", font=font)
        widths = [d.textlength(w, font=font) for w in words]
        total = sum(widths) + space * (len(words) - 1)
    x = (RW - total) / 2
    for (_, a, b), w, ww in zip(chunk, words, widths):
        active = a <= t < b
        d.text((x, SUB_Y - size // 2 - (8 if active else 0)), w, font=font, fill=YELLOW if active else WHITE,
               stroke_width=7, stroke_fill=(0, 0, 0))
        x += ww + space


GIVE_EXTRA = 56  # giveaway: the follow label has a 2nd line (counter + progress bar), the pills below move down


def _follow_label(post):
    """Small '+ Follow for daily Elon Musk news' pill next to the category badge, on screen from 1s to the end:
    most viewers leave before the end banner, so the reason to follow must show while they watch.
    During the 10K giveaway: '+ Follow & win $100' with the live counter and a progress bar under it."""
    from .poster import giveaway_status
    layer = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = _font("bold", 34)  # owner, 11-24: 'a little more visible' -> bigger, brand yellow, with a shadow
    text = "+ Follow & win $100: see pinned event" if config.GIVEAWAY_ON else f"+ Follow for {FOLLOW_PROMISE}"
    f2, status = _font("bold", 27), giveaway_status(post) if config.GIVEAWAY_ON else ""
    w = int(max(d.textlength(text, font=f), d.textlength(status, font=f2) if status else 0)) + 60
    h = 72 + (GIVE_EXTRA if status else 0)
    x, y = RW - PAD + 22 - w, 150 + 124  # right side, level with the category badge (below the Reels top bar)
    shadow = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([x + 4, y + 6, x + w + 4, y + h + 6], radius=36, fill=(0, 0, 0, 150))
    layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    d.rounded_rectangle([x, y, x + w, y + h], radius=36, fill=YELLOW + (255,), outline=(255, 255, 255, 255), width=3)
    d.text((x + 30, y + 15), text, font=f, fill=(15, 15, 20))
    if status:
        d.text((x + 30, y + 60), status, font=f2, fill=(15, 15, 20))
        done = min(1.0, (post.get("_followers") or 0) / 10000)
        bx0, bx1, by0 = x + 30, x + w - 30, y + h - 24
        d.rounded_rectangle([bx0, by0, bx1, by0 + 12], radius=6, fill=(15, 15, 20, 255))
        if done:
            d.rounded_rectangle([bx0, by0, bx0 + max(12, int((bx1 - bx0) * done)), by0 + 12], radius=6, fill=PINK)
    return layer


def _stars_label(post):
    """'★ Send Stars to support Hype Atlas' pill under the top labels (owner, 11-24: nobody reads captions, so the
    Stars ask has to be in the video). The star is drawn: the font has no emoji."""
    import math
    layer = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = _font("bold", 36)
    part = post.get("_life_part")
    text = f"Send Stars to support Part {part + 1}" if part else "Send Stars to support Hype Atlas"
    w, h = int(d.textlength(text, font=f)) + 130, 76
    x, y = (RW - w) // 2, (150 + 124 + 72 + 28 + ((96 + 22) if part else 0)  # under the badge row (and Part pill)
                           + (GIVE_EXTRA if config.GIVEAWAY_ON else 0))
    _paste_rounded(layer, _hgradient(w, h, [PINK, ORANGE]).convert("RGBA"), (x, y), h // 2)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, outline=(255, 255, 255, 255), width=3)
    cx, cy, r = x + 52, y + h // 2, 24
    star = [(cx + (r if k % 2 == 0 else r * 0.45) * math.sin(k * math.pi / 5),
             cy - (r if k % 2 == 0 else r * 0.45) * math.cos(k * math.pi / 5)) for k in range(10)]
    d.polygon(star, fill=YELLOW, outline=(255, 255, 255))
    d.text((x + 92, y + 16), text, font=f, fill=WHITE)
    return layer


STARS_FROM = 1.0  # seconds: the Stars ask from the start, with the follow label (owner, 11-26), to the end banner


def _follow_banner():
    layer = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([90, 900, RW - 90, 1230], radius=40, fill=(8, 8, 12, 225))
    lg = _logo(150)
    if lg:
        d.ellipse([RW // 2 - 80, 820, RW // 2 + 80, 980], fill=WHITE)
        layer.alpha_composite(lg, (RW // 2 - 75, 825))
    for text, font, fill, yy in (("FOLLOW", _font("bold", 56), WHITE, 1000),
                                 ("HYPE ATLAS", _font("bold", 96), YELLOW, 1062),
                                 (f"for {FOLLOW_PROMISE}", _font("medium", 38), (225, 225, 232), 1172)):
        d.text(((RW - d.textlength(text, font=font)) / 2, yy), text, font=font, fill=fill)
    return layer


def _frames(post, scenes, exe, duration, chunks, follow_at):
    shade = Image.alpha_composite(_vfade(RW, RH, 0, 360, (0, 0, 0), a0=150, a1=0, power=1),
                                  _vfade(RW, RH, 880, RH, (6, 6, 10), a1=200, power=1.1))
    header = _header_layer(post)

    banner = _follow_banner()
    label = _follow_label(post)
    stars = _stars_label(post) if config.STARS_ON else None
    stars_at = STARS_FROM
    sub_font = _font("bold", 86)
    bar = _hgradient(RW, 10, [PINK, ORANGE, YELLOW]).convert("RGBA")
    total = sum(n for _, _, n in scenes)

    def source():
        for k, (kind, item, n) in enumerate(scenes):
            if kind == "video":
                try:
                    yield from _video_frames(exe, item[0], item[1], n, item[2] if len(item) > 2 else None)
                    continue
                except Exception as e:
                    print(f"[reel] {e} -> using the photo instead")
                    item = post["_photo"]
            if kind == "face":
                yield from _face_frames(item[0], n, item[1], FACE_MOVES[k % len(FACE_MOVES)])
                continue
            yield from _photo_frames(item, n, MOVES[k % len(MOVES)])

    for i, base in enumerate(source()):
        t = i / FPS
        # clean hard cuts like a news channel (owner, 11-11: the white flash + zoom punch looked like a template)
        frame = base.convert("RGBA")
        frame.alpha_composite(shade)
        frame.alpha_composite(header)
        if config.GIVEAWAY_ON and t < follow_at:  # giveaway: on screen from the very first frame (owner, 11-26)
            frame.alpha_composite(label)
        elif FOLLOW_FROM <= t < follow_at:  # the reason to follow, while they watch (fades in)
            frame.alpha_composite(_with_alpha(label, _ease(min(1.0, (t - FOLLOW_FROM) / 0.3))))
        if stars is not None and stars_at <= t < follow_at:  # the Stars ask, inside the video (fades in)
            frame.alpha_composite(_with_alpha(stars, _ease(min(1.0, (t - stars_at) / 0.3))))
        if t < follow_at:  # no headline on top (owner's choice): the footage stays clear, the subtitles tell the story
            for chunk in chunks:
                if chunk[0][1] <= t < chunk[-1][2] + 0.15:
                    _draw_subtitle(frame, chunk, t, sub_font)
                    break
        else:
            frame.alpha_composite(_with_alpha(banner, _ease((t - follow_at) / 0.3)))
        frame.alpha_composite(bar.crop((0, 0, max(1, int(RW * (t + 1 / FPS) / duration)), 10)), (0, 0))
        yield frame.convert("RGB").tobytes()
        if i + 1 >= total:
            break


def _music():
    tracks = glob.glob(os.path.join(MUSIC_DIR, "*.mp3")) + glob.glob(os.path.join(MUSIC_DIR, "*.m4a"))
    return random.choice(tracks) if tracks else None


def make_reel(post, photo, path):
    """Renders the Reel to `path` (mp4). Returns the path, or None if rendering failed."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        voice = path.replace(".mp4", "-voice.mp3")
        text = _voice_text(post)
        vsecs = _audio_secs(exe, voice) if _tts(text, voice) else 0.0
        # 15s normally; up to 17s when the full story needs it, so the main point is always said clearly.
        # Life-story series (owner, 11-11): 25s, up to 30s
        reel_len, max_len = (LIFE_LEN, LIFE_MAX) if post.get("_life_part") else (REEL_LEN, MAX_LEN)
        fit15, fit17, tempo = reel_len - VOICE_AT - 0.5, max_len - VOICE_AT - 0.5, 1.1
        for _ in range(3):
            parts = re.split(r"(?<=[.!?])\s+", text.strip())
            if not vsecs or vsecs <= fit15 * tempo:
                break
            follow = bool(parts) and parts[-1].lower().startswith("follow")
            if vsecs > fit17 * tempo and len(parts) > (4 if follow else 3):
                # too long for 17s: drop a story sentence, keep the hook, the twist, the question and the follow line
                parts.pop(len(parts) - (4 if follow else 3))
            elif follow and vsecs > fit17 * tempo:
                parts = parts[:-1]  # still too long: the follow banner on screen says it
            else:
                break  # 15-17s: everything stays, the follow line too (owner: more followers)
            text = " ".join(parts)
            post["scenes"] = [s for s in (post.get("scenes") or []) if isinstance(s, dict)
                              and str(s.get("line", "")).strip()[:25] in text]  # keep scenes in step with the voice
            print(f"[reel] voice {vsecs:.1f}s is too long -> shorter narration")
            vsecs = _audio_secs(exe, voice) if _tts(text, voice) else 0.0
        fit = fit15 if vsecs <= fit15 * tempo else fit17
        if vsecs > fit:  # a little long: speak a touch faster (max 1.1x, still clear)
            fast = voice.replace(".mp3", "-fit.mp3")
            subprocess.run([exe, "-y", "-loglevel", "error", "-i", voice, "-filter:a",
                            f"atempo={min(tempo, vsecs / fit):.3f}", "-b:a", "160k", fast], check=True)
            os.replace(fast, voice)
            vsecs = _audio_secs(exe, voice)
        if vsecs:
            chunks = _subtitle_timeline(text, vsecs)
            follow_at = next((c[0][1] for c in chunks if c[0][0].lower().startswith("follow")), vsecs - 1.5)
            duration = min(max_len, max(reel_len, vsecs + VOICE_AT + 0.5))  # 15s, at most 17s (life: 25-30s)
        else:
            chunks, duration = [], reel_len
            follow_at = duration - END_LEN
        words = [w for c in chunks for w in c]
        scenes = _planned_scenes(post, photo, words, duration)  # visuals that follow what the voice says
        if not scenes:
            count = max(3, min(int(MAX_SCENES * max_len / MAX_LEN), round(duration / SCENE)))
            plain = _scenes(post, photo, count)
            per = int(duration * FPS) // len(plain)
            scenes = [(k, it, per) for k, it in plain]
            scenes[-1] = (scenes[-1][0], scenes[-1][1], int(duration * FPS) - per * (len(plain) - 1))
        track = _music()
        cmd = [exe, "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{RW}x{RH}", "-r", str(FPS), "-i", "-"]
        if track:
            cmd += ["-stream_loop", "-1", "-i", track]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        fades = f"afade=t=in:d=0.3,afade=t=out:st={duration - 1.2:.2f}:d=1.2"
        if vsecs:
            ms = int(VOICE_AT * 1000)
            cmd += ["-i", voice, "-filter_complex",
                    f"[1:a]volume=0.22,{fades}[m];[2:a]adelay={ms}|{ms},volume=1.5[v];"
                    f"[m][v]amix=inputs=2:duration=first:normalize=0[a]", "-map", "0:v", "-map", "[a]"]
        else:
            cmd += ["-af", f"{fades},volume=0.8", "-map", "0:v", "-map", "1:a"]
        cmd += ["-t", f"{duration:.2f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
                "-profile:v", "high", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", path]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for buf in _frames(post, scenes, exe, duration, chunks, follow_at):
            proc.stdin.write(buf)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg exit code {proc.returncode}")
        print(f"[reel] rendered {os.path.basename(path)}, {duration:.0f}s, {'voice + ' if vsecs else ''}"
              f"{'music: ' + os.path.basename(track) if track else 'no music'}")
        return path
    except Exception as e:
        print(f"[reel] failed: {e}")
        return None
