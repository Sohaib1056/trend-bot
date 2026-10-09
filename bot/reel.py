"""Fast-cut vertical Reel (1080x1920), built like the big viral news pages:

  0.0s  hook: the famous face (or the best footage) + a white headline card at the top, on screen from frame 1
  every ~2s a new scene, never the same picture twice in a row: several real photos of the person (Wikimedia
        Commons, free licences), real moving stock footage (Pixabay videos) and AI images, all checked by the
        AI judge, with a white flash + zoom punch on every cut
  voice: AI narration (Cloudflare text-to-speech) with big word-by-word subtitles in the middle of the screen
  end   "FOLLOW HYPE ATLAS" banner over the footage (no black screen, so the Reel loops smoothly)
  All text stays inside the Reels safe zone (Facebook covers the bottom ~35% with the caption and buttons).
Music: a random track from assets/music/ (royalty-free), quieter under the voice.
"""
import base64
import glob
import os
import random
import re
import subprocess

import requests
from PIL import Image, ImageDraw, ImageFilter

from . import config
from .poster import (COMMONS_SKIP, FREE_LICENSE, MARKER, INK, PAD, PINK, ORANGE, YELLOW, WHITE, WIKI_UA,
                     _cloudflare_image, _cover, _download, _enhance, _fit, _font, _hgradient, _logo,
                     _paste_rounded, _tokens, _vfade)

RW, RH = 1080, 1920
FPS = 30
SCENE = 2.0          # target seconds per scene
MAX_SCENES = 9
VOICE_AT = 0.3       # narration starts after the hook frame
END_LEN = 1.8        # follow banner at the end (when there is no voice to time it)
SUB_Y = 1190         # centre of the subtitles: low in the Reels safe zone, under the face
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
            if not v.get("url"):
                continue
            try:
                judged += 1
                score = judge_image(_download(v["thumbnail"]), headline, broll=True) if v.get("thumbnail") else None
                if score is not None and (score[0] < 5 or score[1] < 5):
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
    return clips


def _person_photos(post, want=3):
    """More real photos of the person in the story (Wikimedia Commons, free licences only), checked by the judge."""
    from .ai import judge_image
    name = (post.get("wiki_title") or "").strip()
    if not name or not post.get("_person") or want <= 0:
        return []
    last = name.split()[-1].lower()
    out, judged = [], 0
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f'"{name}" filetype:bitmap', "gsrlimit": 30,
            "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1600})
        pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    except Exception as e:
        print(f"[reel] Commons search for '{name}' failed: {e}")
        return []
    for p in pages:
        if len(out) >= want or judged >= 5:
            break
        title, ii = p.get("title", ""), (p.get("imageinfo") or [{}])[0]
        if last not in title.lower() or not title.lower().endswith((".jpg", ".jpeg")) or COMMONS_SKIP.search(title):
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
            out.append((im, f"{artist}, {lic}", score[2] if score else None))
            print(f"[reel] extra photo of {name}: {title[:60]}")
        except Exception as e:
            print(f"[reel] photo {title[:40]} failed: {e}")
    return out


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
    return f"{text} Follow Hype Atlas for more!"


def _tts(text, path):
    """Cloudflare Workers AI text-to-speech (same free account as the images). Returns True on success."""
    acc, tok = os.getenv("CF_ACCOUNT_ID", "").strip(), os.getenv("CF_API_TOKEN", "").strip()
    if not acc or not tok:
        return False
    base = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/"
    for model, body in (("@cf/deepgram/aura-1", {"text": text, "speaker": os.getenv("VOICE", "asteria"), "encoding": "mp3"}),
                        ("@cf/myshell-ai/melotts", {"prompt": text, "lang": "en"})):
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
        extra_people = _person_photos(post, want=want)
        people += [(im, face) for im, _, face in extra_people]
        if extra_people:
            post["_reel_credits"] = [c for _, c, _ in extra_people]
    return people


MIN_SCENE, MAX_SCENE = 1.0, 3.6  # seconds
MIN_REEL = 15.0  # every Reel runs at least 15 seconds


def _planned_scenes(post, photo, words, duration):
    """Scenes that follow the narration: the AI split the voiceover into lines and said what to show for each
    ('face' = the famous person, otherwise a stock-video search). Returns [(kind, item, frames)] or None."""
    plan = [s for s in (post.get("scenes") or []) if isinstance(s, dict) and str(s.get("line", "")).strip()]
    if len(plan) < 2 or not words:
        return None
    # start time of each line = when its first word is spoken
    starts, idx = [], 0
    for s in plan:
        starts.append(0.0 if not starts else words[min(idx, len(words) - 1)][1])
        idx += len(str(s["line"]).split())
    bounds = [(starts[i], starts[i + 1] if i + 1 < len(plan) else duration) for i in range(len(plan))]
    segs = []  # merge too-short lines into the previous one, split long ones in two
    for s, (a, b) in zip(plan, bounds):
        if segs and b - a < MIN_SCENE:
            segs[-1][2] = b
            continue
        if b - a > MAX_SCENE:
            segs += [[s, a, (a + b) / 2, 0], [s, (a + b) / 2, b, 1]]
        else:
            segs.append([s, a, b, 0])
    people = _people(post, photo, want=3)
    others = [im for im in post.get("_extras", []) if im is not photo] + ([] if post.get("_person") else [photo])
    seen, faces_used, clips_cache, out = set(), 0, {}, []
    for s, a, b, part in segs:
        show = str(s.get("show", "")).lower()
        query = str(s.get("query") or "").strip()
        line = str(s["line"])
        kind = item = None
        repeat = part == 1 and len(people) < 2  # second half of a long line: don't show the same face again
        if show == "face" and people and not repeat:
            kind, item = "face", people[faces_used % len(people)]
            faces_used += 1
        if kind is None and query:
            if query not in clips_cache:
                clips_cache[query] = _pixabay_videos(post, want=2, queries=[query], context=line, seen=seen, max_judge=3)
            got = clips_cache[query]
            if got:
                kind, item = "video", got[min(part, len(got) - 1)]
        if kind is None and not query:  # no search for this line: footage for the story in general
            if "_story" not in clips_cache:
                clips_cache["_story"] = _pixabay_videos(post, want=2, seen=seen, max_judge=4)
            got = clips_cache["_story"]
            if got:
                kind, item = "video", got[len(out) % len(got)]
        if kind is None and s.get("ai"):
            im = _cloudflare_image(str(s["ai"]), abs(hash(line)) % 100000)
            if im is not None:
                kind, item = "photo", im
        if kind is None and people and not repeat:
            kind, item = "face", people[faces_used % len(people)]
            faces_used += 1
        if kind is None:
            kind, item = "photo", (others[len(out) % len(others)] if others else photo)
        out.append([kind, item, int(round((b - a) * FPS))])
        print(f"[reel] {a:4.1f}-{b:4.1f}s {kind:5} for: {line[:60]}")
    if people and out[0][0] != "face":  # a Reel about a famous person always opens on their face
        out[0][0], out[0][1] = "face", people[0]
        print("[reel] first scene -> the face")
    out[-1][2] += int(duration * FPS) - sum(n for _, _, n in out)  # exact total length
    return [tuple(x) for x in out]


def _scenes(post, photo, count):
    """Ordered list of ('photo', img) / ('video', (path, duration)) scenes, no picture shown twice in a row."""
    people = _people(post, photo, want=min(3, count // 2))
    others =([] if post.get("_person") else [photo]) + [im for im in post.get("_extras", []) if im is not photo]
    videos = _pixabay_videos(post, want=min(4, count - len(people)))
    short = count - len(people) - len(videos) - len(others)
    if short > 0:
        others += _ai_images(post, min(3, short))
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
    k = 0
    while len(scenes) < count:  # still short: re-use pictures, never next to themselves
        cand = scenes[k % distinct]
        if cand is not scenes[-1] or distinct == 1:
            scenes.append(cand)
        k += 1
    print(f"[reel] {len(scenes)} scenes ({distinct} different): " + ", ".join(kind for kind, _ in scenes))
    return scenes


def _video_frames(exe, path, duration, n):
    """Yields n RGB frames of the clip, cropped to fill 1080x1920 (last frame held if the clip is short)."""
    start = 1.0 if duration >= 5 else 0.0  # skip slow fade-ins
    cmd = [exe, "-loglevel", "error", "-ss", str(start), "-i", path, "-an",
           "-vf", f"scale={RW}:{RH}:force_original_aspect_ratio=increase,crop={RW}:{RH},fps={FPS},eq=contrast=1.06:saturation=1.15",
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
        cw, ch = int(RW / z * 1.0), int(RH / z)
        top = int(fy - FACE_Y / RH * ch)
        top = max(pad - int(ch * 0.30), min(top, canvas.height - ch))  # never more than 30% blurred band
        left = (canvas.width - cw) // 2
        yield canvas.crop((left, top, left + cw, top + ch)).resize((RW, RH), Image.BILINEAR)



def _photo_frames(img, n, move):
    """Ken Burns move (zoom in/out, drift left/right) over n frames."""
    z0, z1, fx, fy = move
    big = _cover(_enhance(img), int(RW * 1.2), int(RH * 1.2))
    for i in range(n):
        z = z0 + (z1 - z0) * (i / max(1, n - 1))
        cw, ch = int(RW * 1.2 / z), int(RH * 1.2 / z)
        left, top = int((big.width - cw) * fx), int((big.height - ch) * fy)
        yield big.crop((left, top, left + cw, top + ch)).resize((RW, RH), Image.BILINEAR)


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
    return layer


def _title_layer(post):
    """White headline card at the top (brand look of the posters), keywords marked with the highlighter."""
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    x0, top = PAD - 30, 372
    max_w = RW - 2 * x0 - 60
    f, lines, lh = _fit(probe, _tokens(post["headline"], upper=False), max_w, 400,
                        big=76, small=48, max_lines=4, lh_ratio=1.2)
    h = lh * len(lines) + 56
    layer = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([x0 + 6, top + 8, RW - x0 + 6, top + h + 8], radius=26, fill=(0, 0, 0, 110))
    d.rounded_rectangle([x0, top, RW - x0, top + h], radius=26, fill=WHITE)
    space = d.textlength(" ", font=f)
    asc, desc = f.getmetrics()
    y = top + 26
    for line in lines:
        pos, x = [], x0 + 30
        for word, hl in line:
            ww = d.textlength(word, font=f)
            pos.append((x, ww, word, hl))
            x += ww + space
        i = 0
        while i < len(pos):  # one highlighter stroke per run of keywords
            if pos[i][3]:
                j = i
                while j + 1 < len(pos) and pos[j + 1][3]:
                    j += 1
                d.rounded_rectangle([pos[i][0] - 8, y + asc * 0.18, pos[j][0] + pos[j][1] + 8, y + asc + desc * 0.25],
                                    radius=8, fill=MARKER)
                i = j + 1
            else:
                i += 1
        for px, ww, word, hl in pos:
            d.text((px, y), word, font=f, fill=INK)
        y += lh
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
                                 ("for daily viral news", _font("medium", 38), (225, 225, 232), 1172)):
        d.text(((RW - d.textlength(text, font=font)) / 2, yy), text, font=font, fill=fill)
    return layer


def _frames(post, scenes, exe, duration, chunks, follow_at):
    shade = Image.alpha_composite(_vfade(RW, RH, 0, 360, (0, 0, 0), a0=150, a1=0, power=1),
                                  _vfade(RW, RH, 880, RH, (6, 6, 10), a1=200, power=1.1))
    header = _header_layer(post)

    banner = _follow_banner()
    sub_font = _font("bold", 86)
    flash = Image.new("RGBA", (RW, RH), (255, 255, 255, 255))
    bar = _hgradient(RW, 10, [PINK, ORANGE, YELLOW]).convert("RGBA")
    total = sum(n for _, _, n in scenes)
    cuts, at = [], 0  # frame index where each scene starts
    for _, _, n in scenes:
        cuts.append(at)
        at += n

    def source():
        for k, (kind, item, n) in enumerate(scenes):
            if kind == "video":
                try:
                    yield from _video_frames(exe, item[0], item[1], n)
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
        since_cut = (i - max(c for c in cuts if c <= i)) / FPS
        first = i < cuts[1] if len(cuts) > 1 else True
        frame = base
        if not first and since_cut < 0.25:  # zoom punch on each cut
            z = 1.0 + 0.08 * (1 - since_cut / 0.25)
            cw, ch = int(RW / z), int(RH / z)
            frame = frame.crop(((RW - cw) // 2, (RH - ch) // 2, (RW + cw) // 2, (RH + ch) // 2)).resize((RW, RH), Image.BILINEAR)
        frame = frame.convert("RGBA")
        frame.alpha_composite(shade)
        if not first and since_cut < 0.15:  # white flash on each cut
            frame.alpha_composite(_with_alpha(flash, 0.5 * (1 - since_cut / 0.15)))
        frame.alpha_composite(header)
        if t < follow_at:  # no headline card: the footage stays clear, the subtitles tell the story
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
        if vsecs:
            chunks = _subtitle_timeline(text, vsecs)
            follow_at = next((c[0][1] for c in chunks if c[0][0].lower().startswith("follow")), vsecs - 1.5)
            duration = max(MIN_REEL, vsecs + VOICE_AT + 0.6)
        else:
            chunks, duration = [], MIN_REEL
            follow_at = duration - END_LEN
        words = [w for c in chunks for w in c]
        scenes = _planned_scenes(post, photo, words, duration)  # visuals that follow what the voice says
        if not scenes:
            count = max(3, min(MAX_SCENES, round(duration / SCENE)))
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
                "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
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
