"""Creative 1080x1350 posters for Facebook + Instagram.

Background image source (first one that works):
  1. FREE AI image - Cloudflare Workers AI, FLUX model (CF_ACCOUNT_ID + CF_API_TOKEN)
  2. AI image      - Pollinations (POLLINATIONS_API_KEY, optional, paid)
  3. Stock photo   - Pixabay (PIXABAY_API_KEY, free) or Pexels (PEXELS_API_KEY)
  4. No image -> bold graphic design

Photo templates rotate so the page never looks repetitive:
  viral    - photo on top, black panel, big white text with yellow keywords
  breaking - full-bleed photo, news ribbon, highlighted word boxes
  frame    - bright colour frame, rounded photo, magazine-style text
"""
import base64
import io
import os
import random
import re
import time
import urllib.parse
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import config

W, H = 1080, 1350
PAD = 70

# (dark colour, deep colour, accent colour)
THEMES = {
    "red":    ((150, 10, 25),  (25, 4, 10),   (255, 214, 0)),
    "blue":   ((15, 50, 150),  (5, 10, 35),   (0, 229, 255)),
    "purple": ((90, 25, 160),  (18, 6, 40),   (255, 95, 205)),
    "orange": ((210, 80, 0),   (40, 10, 0),   (255, 236, 90)),
    "green":  ((0, 110, 75),   (0, 25, 20),   (170, 255, 60)),
    "dark":   ((40, 40, 50),   (5, 5, 10),    (255, 70, 70)),
    "pink":   ((200, 35, 115), (40, 5, 30),   (255, 230, 90)),
    "teal":   ((0, 115, 125),  (0, 25, 35),   (255, 175, 45)),
}
BLACK, WHITE = (12, 12, 14), (255, 255, 255)

# Hype Atlas brand (matches the page logo and cover)
PINK, ORANGE, YELLOW = (255, 31, 107), (255, 122, 0), (255, 212, 0)
LOGO_PATH = os.path.join(config.BASE_DIR, "branding", "profile.png")


# ---------------------------------------------------------------- helpers
def _font(weight, size):
    name = {"bold": "Poppins-Bold.ttf", "medium": "Poppins-Medium.ttf"}.get(weight, "Poppins-Regular.ttf")
    for path in (os.path.join(config.FONT_DIR, name), "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _cover(im, w, h):
    scale = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * scale) + 1, int(im.height * scale) + 1), Image.LANCZOS)
    left, top = (im.width - w) // 2, max(0, (im.height - h) // 3)
    return im.crop((left, top, left + w, top + h))


def _vfade(w, h, start, end, colour, a0=0, a1=255, power=1.2):
    """Vertical fade layer: transparent at `start` to `colour` at `end`."""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(h):
        t = 0 if y < start else 1 if y > end else (y - start) / max(1, end - start)
        d.line([(0, y), (w, y)], fill=colour + (int(a0 + (a1 - a0) * t ** power),))
    return layer


def _gradient(top, bottom):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img.convert("RGBA")


def _tokens(headline, upper=True):
    """'Apple *just dropped* iPhone 18' -> [('APPLE',False),('JUST',True),...]"""
    out, hl = [], False
    for raw in headline.replace("**", "*").split():
        start, end = raw.startswith("*"), raw.endswith("*") and len(raw) > 1
        word = raw.strip("*")
        if start:
            hl = True
        if word:
            out.append((word.upper() if upper else word, hl))
        if end or (start and raw.count("*") >= 2):
            hl = False
    if not any(h for _, h in out) and len(out) > 2:  # AI forgot -> highlight last 2 words
        out = [(w, i >= len(out) - 2) for i, (w, _) in enumerate(out)]
    return out


def _wrap_tokens(d, toks, font, max_w):
    lines, cur = [], []
    space = d.textlength(" ", font=font)
    for t in toks:
        test = cur + [t]
        width = sum(d.textlength(w, font=font) for w, _ in test) + space * (len(test) - 1)
        if width <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = [t]
    if cur:
        lines.append(cur)
    return lines


def _line_w(d, line, font):
    return sum(d.textlength(w, font=font) for w, _ in line) + d.textlength(" ", font=font) * (len(line) - 1)


def _fit(d, toks, max_w, max_h, big=140, small=44, max_lines=5, lh_ratio=1.05):
    for size in range(big, small - 1, -3):
        f = _font("bold", size)
        lines = _wrap_tokens(d, toks, f, max_w)
        lh = int(size * lh_ratio)
        if len(lines) <= max_lines and lh * len(lines) <= max_h and all(_line_w(d, l, f) <= max_w for l in lines):
            return f, lines, lh
    f = _font("bold", small)
    return f, _wrap_tokens(d, toks, f, max_w), int(small * lh_ratio)


def _draw_rich(d, lines, font, lh, x, y, colour, hl_colour, align="left", box=None, shadow=True, width=W, stroke=0):
    """Draw wrapped headline. box=(bg,fg) paints highlighted words as coloured boxes."""
    space = d.textlength(" ", font=font)
    asc, desc = font.getmetrics()
    for line in lines:
        lw = _line_w(d, line, font)
        cx = x if align == "left" else (width - lw) / 2
        pos = []
        for word, hl in line:
            ww = d.textlength(word, font=font)
            pos.append((cx, ww, word, hl))
            cx += ww + space
        if box:  # one solid box per run of highlighted words
            i = 0
            while i < len(pos):
                if pos[i][3]:
                    j = i
                    while j + 1 < len(pos) and pos[j + 1][3]:
                        j += 1
                    d.rectangle([pos[i][0] - 12, y + asc * 0.12, pos[j][0] + pos[j][1] + 12, y + asc + desc * 0.35],
                                fill=box[0])
                    i = j + 1
                else:
                    i += 1
        for px, ww, word, hl in pos:
            if hl and box:
                d.text((px, y), word, font=font, fill=box[1])
            else:
                if shadow:
                    d.text((px + 3, y + 4), word, font=font, fill=(0, 0, 0, 150))
                d.text((px, y), word, font=font, fill=hl_colour if hl else colour,
                       stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += lh
    return y


def _wrap(d, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if d.textlength(test, font=font) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _pill(d, x, y, text, font, bg, fg, pad_x=24, pad_y=10, dot=False):
    tw = d.textlength(text, font=font)
    asc, desc = font.getmetrics()
    h = asc + desc + pad_y * 2
    extra = 30 if dot else 0
    d.rounded_rectangle([x, y, x + tw + pad_x * 2 + extra, y + h], radius=h // 2, fill=bg)
    if dot:
        cy = y + h // 2
        d.ellipse([x + pad_x, cy - 8, x + pad_x + 16, cy + 8], fill=fg)
    d.text((x + pad_x + extra, y + pad_y), text, font=font, fill=fg)
    return x + tw + pad_x * 2 + extra, y + h


def _handle():
    return config.PAGE_NAME.upper()


# ---------------------------------------------------------- image sources
def _download(url, headers=None, timeout=60):
    for attempt in range(3):  # Wikimedia answers 429 'too many requests' at busy times: wait and retry
        r = requests.get(url, headers=headers or {}, timeout=timeout)
        if r.status_code in (429, 503) and attempt < 2:
            wait = min(int(r.headers.get("Retry-After", "0") or 0) or 3 * (attempt + 1), 8)
            print(f"[poster] {r.status_code} from {url.split('/')[2]}, retrying in {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")


def _ai_image(prompt, seed):
    key = os.getenv("POLLINATIONS_API_KEY", "").strip()
    if not key or not prompt:
        return None
    try:
        full = (f"{prompt}, dramatic cinematic lighting, vibrant colors, high detail, "
                f"professional editorial photo, no text, no letters, no watermark")
        url = (f"https://gen.pollinations.ai/image/{urllib.parse.quote(full)}"
               f"?width=1080&height=1350&seed={seed}&nologo=true&model="
               f"{os.getenv('POLLINATIONS_MODEL', 'flux')}")
        im = _download(url, {"Authorization": f"Bearer {key}"}, timeout=120)
        print("[poster] background: AI image")
        return im
    except Exception as e:
        print(f"[poster] AI image failed: {e}")
        return None


def _stock_photo(query):
    if not config.PEXELS_API_KEY or not query:
        return None
    try:
        for q in (query, " ".join(query.split()[:2])):
            r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": config.PEXELS_API_KEY},
                             params={"query": q, "orientation": "portrait", "per_page": 12}, timeout=20)
            photos = r.json().get("photos", [])
            if photos:
                im = _download(random.choice(photos[:6])["src"]["large2x"])
                print(f"[poster] background: Pexels photo for '{q}'")
                return im
    except Exception as e:
        print(f"[poster] Pexels failed: {e}")
    return None


def _cloudflare_image(prompt, seed):
    """Free: ~170 FLUX images/day on Cloudflare's free Workers AI plan."""
    acc, tok = os.getenv("CF_ACCOUNT_ID", "").strip(), os.getenv("CF_API_TOKEN", "").strip()
    if not acc or not tok or not prompt:
        return None
    full = (f"{prompt}. Vertical portrait composition, main subject large and in the upper half of the frame, "
            f"cinematic editorial photograph, dramatic lighting, rich vibrant colors, sharp focus, high detail, "
            f"professional magazine cover quality. No text, no letters, no words, no watermark, no logo.")
    # FLUX schnell: cheap enough for ~170 images/day on the free plan (bigger models used up the quota in 2-3 images)
    try:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/black-forest-labs/flux-1-schnell",
            headers={"Authorization": f"Bearer {tok}"},
            json={"prompt": full[:2000], "steps": 8}, timeout=120)
        data = r.json()
        if r.status_code != 200 or not data.get("success", True):
            raise RuntimeError(f"HTTP {r.status_code}: {str(data)[:300]}")
        img_b64 = (data.get("result") or {}).get("image")
        if not img_b64:
            raise RuntimeError(f"no image in response: {str(data)[:200]}")
        im = Image.open(io.BytesIO(base64.b64decode(img_b64))).convert("RGB")
        print("[poster] background: Cloudflare FLUX AI image")
        return im
    except Exception as e:
        print(f"[poster] Cloudflare AI image failed: {e}")
        return None


def _pixabay_photos(query, limit=3):
    """Up to `limit` professional stock photos from Pixabay (most popular first)."""
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key or not query:
        return []
    out = []
    try:
        for q in (query, " ".join(query.split()[:2])):
            r = requests.get("https://pixabay.com/api/", params={
                "key": key, "q": q[:100], "image_type": "photo", "safesearch": "true",
                "order": "popular", "per_page": 20, "min_width": 1200}, timeout=20)
            for h in r.json().get("hits", [])[:limit]:
                out.append(_download(h["largeImageURL"]))
            if out:
                print(f"[poster] Pixabay: {len(out)} photo(s) for '{q}'")
                return out
    except Exception as e:
        print(f"[poster] Pixabay failed: {e}")
    return out


BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0 Safari/537.36"}
WIKI_UA = {"User-Agent": "HypeAtlasBot/1.0 (facebook.com/hypeatlasnews)"}
FREE_LICENSE = ("cc by", "cc0", "public domain", "pd", "cc-by", "ogl")  # OGL = UK Open Government Licence


def _is_person(qid):
    """Wikidata check: instance of human (Q5)."""
    if not qid:
        return False
    try:
        r = requests.get("https://www.wikidata.org/w/api.php", headers=WIKI_UA, timeout=20, params={
            "action": "wbgetclaims", "entity": qid, "property": "P31", "format": "json"})
        claims = r.json().get("claims", {}).get("P31", [])
        return any(c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") == "Q5" for c in claims)
    except Exception:
        return False


def _wikimedia_photo(title):
    """Real photo of the person/team/film from Wikipedia, only if freely licensed. Returns (img, credit)."""
    if not title:
        return None, None
    try:
        r = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_")),
                         headers=WIKI_UA, timeout=20)
        info = r.json()
        src = ((info.get("originalimage") or {}).get("source") or "").split("?", 1)[0]
        if r.status_code != 200 or not src or info.get("type") == "disambiguation":
            raise RuntimeError("no page image")
        oi = info.get("originalimage") or {}
        if oi.get("width", 0) < 700:
            raise RuntimeError("image too small")
        fname = urllib.parse.unquote(src.rsplit("/", 1)[-1])
        # real photos of people only: shows, films, teams and brands usually lead with a logo/title card
        if not fname.lower().endswith((".jpg", ".jpeg")) or re.search(r"logo|title|card|poster|wordmark|banner", fname, re.I):
            raise RuntimeError(f"looks like a logo/poster, not a photo: {fname}")
        if not oi.get("height") or oi["width"] / oi["height"] > 1.8:
            raise RuntimeError("very wide image (likely a banner)")
        if not _is_person(info.get("wikibase_item")):
            raise RuntimeError("not a person (only real photos of people are used)")
        q = requests.get("https://en.wikipedia.org/w/api.php", headers=WIKI_UA, timeout=20, params={
            "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "extmetadata|url",
            "iiurlwidth": 1280, "titles": "File:" + fname}).json()
        ii = next(iter(q["query"]["pages"].values()))["imageinfo"][0]
        meta = ii["extmetadata"]
        src = ii.get("thumburl") or src  # a 1280px copy: lighter, and far less often rate-limited than the original
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if not lic.lower().startswith(FREE_LICENSE) or "non-free" in lic.lower() or "fair use" in lic.lower():
            raise RuntimeError(f"license not free: {lic}")
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()[:40] or "Wikimedia Commons"
        im = _download(src, WIKI_UA)
        print(f"[poster] background: Wikimedia photo of '{title}' ({lic})")
        return im, f"📷 Photo: {artist}, {lic}, via Wikimedia Commons"
    except Exception as e:
        print(f"[poster] Wikimedia photo for '{title}' not used: {e}")
        return None, None


def _wm_thumb(url, width):
    """A resized copy of a Wikimedia Commons file (lighter and less often rate-limited than the original)."""
    m = re.match(r"(https://upload\.wikimedia\.org/wikipedia/commons)/(\w)/(\w\w)/([^/?]+)$", url)
    if not m or width < 200:
        return url
    base, a, ab, name = m.groups()
    return f"{base}/thumb/{a}/{ab}/{name}/{width}px-{name}"


def openverse_person(name, headline, want=1, skip=()):
    """Free-licensed photos of a famous person from Openverse (Flickr, Wikimedia and more; no key needed).
    Only photos whose title/tags name the person and where the judge sees a face. Returns [(img, credit, face_y)]."""
    from .ai import judge_image
    if not name:
        return []
    try:
        r = requests.get("https://api.openverse.org/v1/images/", headers=WIKI_UA, timeout=30, params={
            "q": name, "license_type": "commercial", "page_size": 20})
        results = r.json().get("results", [])
    except Exception as e:
        print(f"[poster] Openverse search for '{name}' failed: {e}")
        return []
    last = name.split()[-1].lower()
    good = [x for x in results if last in (x.get("title", "") + " " + " ".join(t.get("name", "") for t in x.get("tags") or [])).lower()
            and min(x.get("width") or 0, x.get("height") or 0) >= 600 and x.get("url") not in skip]
    good.sort(key=lambda x: (x.get("source") == "wikimedia", (x.get("width") or 0) > (x.get("height") or 0)))
    out, judged = [], 0
    for x in good:
        if len(out) >= want or judged >= 5:
            break
        url = x["url"]
        if x.get("source") == "wikimedia":
            if (x.get("width") or 0) > 1280:  # Wikimedia serves only standard thumbnail sizes
                url = _wm_thumb(url, 1280)
        try:
            # Wikimedia wants a descriptive bot User-Agent; Flickr refuses those and wants a browser one
            im = _download(url, WIKI_UA if x.get("source") == "wikimedia" else BROWSER_UA)
            judged += 1
            score = judge_image(im, headline)
            if score is not None and (score[0] < 5 or score[1] < 5 or (len(score) > 2 and score[2] is None)):
                print(f"[poster] Openverse photo '{x.get('title', '')[:40]}' rejected (no clear face / off-topic)")
                continue
            lic = x.get("license", "")
            lic = "public domain" if lic in ("cc0", "pdm") else f"CC {lic.upper()} {x.get('license_version', '')}".strip()
            credit = f"📷 Photo: {(x.get('creator') or 'unknown')[:40]}, {lic}, via {x.get('source', 'Openverse').title()}"
            out.append((im, credit, score[2] if score and len(score) > 2 else None))
            print(f"[poster] Openverse photo of {name}: {x.get('title', '')[:50]} ({x.get('source')}, {lic})")
        except Exception as e:
            print(f"[poster] Openverse photo failed: {e}")
    return out


COMMONS_SKIP = re.compile(r"logo|map|flag|diagram|chart|icon|seal|coat.of.arms|screenshot|signature|poster|banner|svg", re.I)


def _commons_photo(query):
    """Free-licensed real photo for a topic (e.g. 'football stadium night') from Wikimedia Commons."""
    if not query:
        return None, None
    try:
        short = " ".join(query.split()[:2])
        # only photos reviewed as high quality: random amateur shots look worse than the brand backdrop
        searches = [f'{query} incategory:"Quality_images"', f'{short} incategory:"Quality_images"']
        for q in searches:
            r = requests.get("https://commons.wikimedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
                "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                "gsrsearch": f"{q} filetype:bitmap", "gsrlimit": 25,
                "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1280})
            pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
            for p in pages:
                ii = (p.get("imageinfo") or [{}])[0]
                title = p.get("title", "")
                if not title.lower().endswith((".jpg", ".jpeg")) or COMMONS_SKIP.search(title):
                    continue
                if ii.get("width", 0) < 1200 or ii.get("height", 0) < 800:
                    continue
                meta = ii.get("extmetadata", {})
                lic = meta.get("LicenseShortName", {}).get("value", "")
                if not lic.lower().startswith(FREE_LICENSE) or "non-free" in lic.lower():
                    continue
                artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()[:40] or "Wikimedia Commons"
                im = _download(ii.get("thumburl") or ii["url"], WIKI_UA)
                print(f"[poster] background: Wikimedia Commons photo for '{q}' ({title})")
                return im, f"📷 Photo: {artist}, {lic}, via Wikimedia Commons"
    except Exception as e:
        print(f"[poster] Commons search failed: {e}")
    return None, None


def _candidates(post):
    """Background candidates in order of preference: (image, credit, source)."""
    seed = abs(hash(post["key"])) % 100000
    prompt = post.get("image_prompt") or post.get("image_query")
    im = _cloudflare_image(prompt, seed) or _ai_image(prompt, seed)
    if im is not None:
        yield im, None, "AI image"
    for im in _pixabay_photos(post.get("image_query")):
        yield im, None, "Pixabay"
    im = _stock_photo(post.get("image_query"))
    if im is not None:
        yield im, None, "Pexels"
    im, credit = _commons_photo(post.get("image_query"))
    if im is not None:
        yield im, credit, "Wikimedia Commons"


def get_background(post):
    """Real photo of the person if the story is about one; otherwise the best candidate as scored
    by the AI image judge (relevance to the headline + photo quality)."""
    from .ai import judge_image
    post["_extras"] = []  # other good images, reused as extra scenes in the Reel
    real, credit = _wikimedia_photo(post.get("wiki_title"))
    if real is None and post.get("wiki_title"):  # Wikipedia had no usable photo (or was busy): try Openverse
        got = openverse_person(post["wiki_title"], post["headline"].replace("*", ""))
        if got:
            real, credit, _ = got[0]
    if real is not None:
        post["_credit"] = credit
        post["_person"] = True
        return real
    best, best_score = None, -1
    for im, credit, source in _candidates(post):
        score = judge_image(im, post["headline"].replace("*", ""))
        if score is None:  # judge unavailable: keep the best judged one so far, else take this one
            if best is not None:
                break
            post["_credit"] = credit
            print(f"[poster] background: {source} (judge unavailable)")
            return im
        rel, qual = score[:2]
        total = rel * 2 + qual
        print(f"[poster] candidate from {source}: score {total:.0f}/30")
        if rel >= 6 and qual >= 6 and credit is None:  # no-attribution images only
            post["_extras"].append(im)
        if rel >= 7 and qual >= 6:
            post["_credit"] = credit
            print(f"[poster] background: {source} (passed the judge)")
            return im
        if total > best_score:
            best, best_score, best_credit, best_source = im, total, credit, source
    if best is not None and best_score >= 12:
        post["_credit"] = best_credit
        print(f"[poster] background: {best_source} (best available, score {best_score:.0f}/30)")
        return best
    return None


# --------------------------------------------------------------- templates
def tpl_viral(post, photo, theme):
    _, _, accent = theme
    hl = (255, 221, 0)
    img = Image.new("RGBA", (W, H), BLACK + (255,))
    ph_h = 780
    img.paste(_cover(photo, W, ph_h), (0, 0))
    img = Image.alpha_composite(img, _vfade(W, H, ph_h - 260, ph_h, BLACK))
    img = Image.alpha_composite(img, _vfade(W, H, 0, 200, (0, 0, 0), a0=110, a1=0, power=1))  # top shade
    d = ImageDraw.Draw(img)

    _pill(d, PAD - 20, 50, (post.get("badge") or "TRENDING").upper(), _font("bold", 28), (230, 30, 40), WHITE, dot=True)

    # centred page name tag on the seam
    tag_f = _font("bold", 30)
    tw = d.textlength(_handle(), font=tag_f)
    x0 = (W - tw) / 2 - 30
    d.rounded_rectangle([x0, ph_h - 60, x0 + tw + 60, ph_h - 4], radius=28, fill=accent)
    d.text((x0 + 30, ph_h - 52), _handle(), font=tag_f, fill=BLACK)

    toks = _tokens(post["headline"])
    f, lines, lh = _fit(d, toks, W - 110, 400, big=100, small=46, max_lines=5)
    block = lh * len(lines)
    y = ph_h + 20 + (440 - block) / 2
    _draw_rich(d, lines, f, lh, 0, y, WHITE, hl, align="center", shadow=False)

    foot = _font("medium", 28)
    txt = "Comment your thoughts  |  Follow for more"
    d.text(((W - d.textlength(txt, font=foot)) / 2, H - 70), txt, font=foot, fill=(150, 150, 155))
    return img


def tpl_breaking(post, photo, theme):
    dark, _, accent = theme
    img = _cover(photo, W, H).convert("RGBA")
    img = Image.alpha_composite(img, _vfade(W, H, 380, H, (dark[0] // 5, dark[1] // 5, dark[2] // 5), a1=250))
    img = Image.alpha_composite(img, _vfade(W, H, 0, 220, (0, 0, 0), a0=120, a1=0, power=1))
    d = ImageDraw.Draw(img)

    # slanted ribbon
    rf = _font("bold", 34)
    label = (post.get("badge") or "BREAKING").upper()
    rw = d.textlength(label, font=rf) + 110
    d.polygon([(0, 60), (rw + 30, 60), (rw, 128), (0, 128)], fill=accent)
    d.text((PAD - 20, 70), label, font=rf, fill=BLACK)
    d.polygon([(rw + 42, 60), (rw + 330, 60), (rw + 300, 128), (rw + 12, 128)], fill=WHITE)
    d.text((rw + 60, 76), _handle()[:14], font=_font("bold", 28), fill=BLACK)

    toks = _tokens(post["headline"])
    f, lines, lh = _fit(d, toks, W - PAD * 2, 520, big=118, small=50, max_lines=5, lh_ratio=1.12)
    sub_f = _font("medium", 38)
    sub = _wrap(d, post.get("subtext", ""), sub_f, W - PAD * 2 - 30)[:3]
    y = H - 120 - len(sub) * 52 - 40 - lh * len(lines)
    y = _draw_rich(d, lines, f, lh, PAD, y, WHITE, BLACK, box=(accent, BLACK))
    y += 20
    d.rectangle([PAD, y, PAD + 8, y + len(sub) * 52 - 8], fill=accent)
    for s in sub:
        d.text((PAD + 30, y), s, font=sub_f, fill=(235, 235, 235))
        y += 52

    date = datetime.now(timezone.utc).strftime("%d %B %Y").upper()
    df = _font("medium", 26)
    d.text((PAD, H - 70), date, font=df, fill=(200, 200, 200))
    txt = "FOLLOW FOR MORE"
    d.text((W - PAD - d.textlength(txt, font=df), H - 70), txt, font=df, fill=accent)
    return img


def tpl_frame(post, photo, theme):
    dark, deep, accent = theme
    img = Image.new("RGBA", (W, H), accent + (255,))
    d = ImageDraw.Draw(img)
    # halftone dots for texture
    for yy in range(0, H, 34):
        for xx in range((yy // 34) % 2 * 17, W, 34):
            d.ellipse([xx, yy, xx + 5, yy + 5], fill=tuple(max(0, c - 22) for c in accent))
    box = (45, 130, W - 45, 800)
    ph = _cover(photo, box[2] - box[0], box[3] - box[1])
    mask = Image.new("L", ph.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, ph.width, ph.height], radius=36, fill=255)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([box[0] + 14, box[1] + 18, box[2] + 14, box[3] + 18], 36, fill=(0, 0, 0, 110))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(10)))
    img.paste(ph, box[:2], mask)
    d = ImageDraw.Draw(img)

    _pill(d, 45, 45, _handle(), _font("bold", 30), BLACK, WHITE)
    date = datetime.now(timezone.utc).strftime("%d %b %Y").upper()
    df = _font("bold", 28)
    d.text((W - 45 - d.textlength(date, font=df), 58), date, font=df, fill=BLACK)
    _pill(d, 75, box[3] - 80, (post.get("badge") or "TRENDING").upper(), _font("bold", 28), dark, WHITE, dot=True)

    toks = _tokens(post["headline"])
    sub_f = _font("medium", 34)
    sub = _wrap(d, post.get("subtext", ""), sub_f, W - 120)[:2]
    avail = H - 110 - (box[3] + 40) - 20 - len(sub) * 46
    f, lines, lh = _fit(d, toks, W - 120, avail, big=104, small=44, max_lines=4, lh_ratio=1.12)
    y = box[3] + 40 + (avail - lh * len(lines)) / 3
    y = _draw_rich(d, lines, f, lh, 60, y, BLACK, WHITE, box=(BLACK, accent), shadow=False)
    y += 18
    for s in sub:
        d.text((60, y), s, font=sub_f, fill=(35, 35, 35))
        y += 46
    d.rectangle([0, H - 90, W, H], fill=BLACK)
    cta = f"FOLLOW {_handle()} FOR MORE"
    cf = _font("bold", 28)
    d.text(((W - d.textlength(cta, font=cf)) / 2, H - 64), cta, font=cf, fill=accent)
    return img


def tpl_graphic(post, theme):
    """No photo available: bold typographic design."""
    dark, deep, accent = theme
    img = _gradient(dark, deep)
    rnd = random.Random(post["key"])
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for _ in range(3):
        r = rnd.randint(250, 450)
        x, y = rnd.randint(0, W), rnd.randint(0, H // 2)
        g.ellipse([x - r, y - r, x + r, y + r], fill=accent + (55,))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(120)))

    # giant outlined topic word in the background
    word = (post.get("topic") or post["headline"]).split()[0].upper()[:10]
    big = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(big)
    bf = _font("bold", 420)
    bw = bd.textlength(word, font=bf)
    if bw > W * 1.6:
        bf = _font("bold", int(420 * W * 1.6 / bw))
    bd.text((-20, 120), word, font=bf, fill=(0, 0, 0, 0), stroke_width=4, stroke_fill=(255, 255, 255, 45))
    img = Image.alpha_composite(img, big)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=accent)

    _pill(d, PAD, PAD, _handle(), _font("bold", 30), WHITE, BLACK)
    date = datetime.now(timezone.utc).strftime("%d %b %Y").upper()
    df = _font("medium", 28)
    d.text((W - PAD - d.textlength(date, font=df), PAD + 12), date, font=df, fill=WHITE)

    toks = _tokens(post["headline"])
    f, lines, lh = _fit(d, toks, W - PAD * 2, 600, big=140, small=56, max_lines=5)
    sub_f = _font("medium", 40)
    sub = _wrap(d, post.get("subtext", ""), sub_f, W - PAD * 2)[:3]
    y = max(300, H - 170 - (90 + lh * len(lines) + 40 + len(sub) * 54))
    _, y2 = _pill(d, PAD, y, (post.get("badge") or "TRENDING").upper(), _font("bold", 32), accent, BLACK, dot=True)
    y = _draw_rich(d, lines, f, lh, PAD, y2 + 26, WHITE, accent)
    y += 18
    d.rectangle([PAD, y, PAD + 140, y + 8], fill=accent)
    y += 30
    for s in sub:
        d.text((PAD, y), s, font=sub_f, fill=(235, 235, 235))
        y += 54
    d.rectangle([0, H - 110, W, H], fill=BLACK)
    d.rectangle([0, H - 110, W, H - 104], fill=accent)
    cta = f"FOLLOW {_handle()} FOR MORE"
    cf = _font("bold", 30)
    d.text(((W - d.textlength(cta, font=cf)) / 2, H - 76), cta, font=cf, fill=WHITE)
    return img



# ------------------------------------------------- signature brand template
def _hgradient(w, h, stops):
    """Horizontal gradient image through the given colour stops."""
    g = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(g)
    n = len(stops) - 1
    for x in range(w):
        t = x / max(1, w - 1) * n
        i = min(int(t), n - 1)
        f = t - i
        d.line([(x, 0), (x, h)], fill=tuple(int(stops[i][k] + (stops[i + 1][k] - stops[i][k]) * f) for k in range(3)))
    return g


def _paste_rounded(img, src, xy, radius):
    mask = Image.new("L", src.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, src.width - 1, src.height - 1], radius=radius, fill=255)
    img.paste(src, xy, mask)


def _logo(size):
    try:
        lg = Image.open(LOGO_PATH).convert("RGB").resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(lg, (0, 0), mask)
        return out
    except Exception:
        return None


def _enhance(photo):
    photo = ImageEnhance.Color(photo).enhance(1.15)
    photo = ImageEnhance.Contrast(photo).enhance(1.08)
    return photo.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))


def _brand_backdrop(post):
    """Used when no photo is available: dark canvas with brand-coloured glows."""
    img = _gradient((28, 10, 40), (8, 4, 14))
    rnd = random.Random(post["key"])
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for colour in (PINK, ORANGE, YELLOW):
        r = rnd.randint(260, 420)
        x, y = rnd.randint(0, W), rnd.randint(0, H // 2)
        g.ellipse([x - r, y - r, x + r, y + r], fill=colour + (90,))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(140)))
    word = (post.get("topic") or post["headline"]).split()[0].upper()[:10]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bf = _font("bold", 380)
    bw = d.textlength(word, font=bf)
    if bw > W * 1.5:
        bf = _font("bold", int(380 * W * 1.5 / bw))
    d.text((-20, 180), word, font=bf, fill=(0, 0, 0, 0), stroke_width=4, stroke_fill=(255, 255, 255, 45))
    return Image.alpha_composite(img, layer).convert("RGB")


def tpl_hype(post, photo, theme=None):
    """Hype Atlas signature design: one consistent look so the page grid reads like a magazine.
    Full-bleed photo, brand header, big white headline with yellow keywords, brand gradient footer."""
    img = _cover(_enhance(photo), W, H).convert("RGBA")
    img = Image.alpha_composite(img, _vfade(W, H, 0, 260, (0, 0, 0), a0=150, a1=0, power=1))
    img = Image.alpha_composite(img, _vfade(W, H, 560, H - 120, (6, 6, 10), a1=252, power=1.25))
    d = ImageDraw.Draw(img)

    # header: logo + page name (left), brand badge (right)
    lg = _logo(88)
    x = PAD - 22
    if lg:
        d.ellipse([x - 4, 42, x + 92, 138], fill=WHITE)
        img.alpha_composite(lg, (x, 46))
        x += 110
    d.text((x, 50), "HYPE ATLAS", font=_font("bold", 36), fill=WHITE)
    d.text((x + 2, 98), "VIRAL & TRENDING", font=_font("bold", 20), fill=YELLOW)

    bf = _font("bold", 26)
    label = (post.get("badge") or "TRENDING").upper()[:18]
    bw = int(d.textlength(label, font=bf)) + 76
    bx, by, bh = W - PAD + 22 - bw, 62, 56
    _paste_rounded(img, _hgradient(bw, bh, [PINK, ORANGE]), (bx, by), bh // 2)
    d = ImageDraw.Draw(img)
    d.ellipse([bx + 24, by + bh // 2 - 7, bx + 38, by + bh // 2 + 7], fill=WHITE)
    d.text((bx + 50, by + 13), label, font=bf, fill=WHITE)

    # headline + subtext, anchored to the bottom
    toks = _tokens(post["headline"])
    f, lines, lh = _fit(d, toks, W - PAD * 2, 330, big=100, small=50, max_lines=3, lh_ratio=1.08)
    sub_f = _font("medium", 36)
    sub = _wrap(d, post.get("subtext", ""), sub_f, W - PAD * 2)[:2]
    foot_top = H - 96
    y = foot_top - 40 - len(sub) * 50 - (26 if sub else 0) - lh * len(lines) - 36
    img.paste(_hgradient(150, 10, [PINK, ORANGE, YELLOW]), (PAD, int(y)))
    d = ImageDraw.Draw(img)
    y = _draw_rich(d, lines, f, lh, PAD, y + 30, WHITE, YELLOW, shadow=True, stroke=3)
    if sub:
        y += 26
        for line in sub:
            d.text((PAD, y), line, font=sub_f, fill=(225, 225, 232))
            y += 50

    # footer
    ff = _font("bold", 26)
    d.text((PAD, H - 72), "FOLLOW FOR MORE", font=ff, fill=YELLOW)
    date = datetime.now(timezone.utc).strftime("%d %b %Y").upper()
    df = _font("medium", 24)
    d.text((W - PAD - d.textlength(date, font=df), H - 70), date, font=df, fill=(170, 170, 178))
    img.paste(_hgradient(W, 12, [PINK, ORANGE, YELLOW]), (0, H - 12))
    return img



# ------------------------------------------------ news-card template (default)
INK = (17, 17, 20)
MARKER = (255, 214, 10)


def tpl_card(post, photo, theme=None):
    """'News card' layout used by the big viral pages (Pubity, Pop Base style):
    clean white headline panel on top (readable even as a small feed thumbnail),
    big photo below, keywords marked with a yellow highlighter, small brand watermark."""
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    toks = _tokens(post["headline"], upper=False)
    max_w = W - 2 * 64
    f, lines, lh = _fit(probe, toks, max_w, 300, big=74, small=46, max_lines=4, lh_ratio=1.22)
    label_f = _font("bold", 24)
    panel_h = max(300, 64 + 40 + lh * len(lines) + 46)

    img = Image.new("RGBA", (W, H), WHITE + (255,))
    ph = _cover(_enhance(photo), W, H - panel_h)
    img.paste(ph, (0, panel_h))
    img = Image.alpha_composite(img, _vfade(W, H, H - 260, H, (0, 0, 0), a1=150, power=1.2))
    img.paste(_hgradient(W, 10, [PINK, ORANGE, YELLOW]), (0, panel_h - 5))
    d = ImageDraw.Draw(img)

    # small category label
    label = (post.get("badge") or "TRENDING").upper()[:22]
    d.ellipse([64, 52, 80, 68], fill=PINK)
    d.text((92, 44), label, font=label_f, fill=PINK)

    # headline with highlighter marks on the keywords
    y = 64 + 40
    space = d.textlength(" ", font=f)
    asc, desc = f.getmetrics()
    for line in lines:
        pos, x = [], 64
        for word, hl in line:
            ww = d.textlength(word, font=f)
            pos.append((x, ww, word, hl))
            x += ww + space
        i = 0
        while i < len(pos):  # one continuous highlighter stroke per run of keywords
            if pos[i][3]:
                j = i
                while j + 1 < len(pos) and pos[j + 1][3]:
                    j += 1
                d.rounded_rectangle([pos[i][0] - 10, y + asc * 0.18, pos[j][0] + pos[j][1] + 10, y + asc + desc * 0.25],
                                    radius=8, fill=MARKER)
                i = j + 1
            else:
                i += 1
        for px, ww, word, hl in pos:
            d.text((px, y), word, font=f, fill=INK)
        y += lh

    # brand watermark on the photo (bottom-left), like the big pages do
    lg = _logo(70)
    if lg:
        d.ellipse([46, H - 120, 122, H - 44], fill=WHITE)
        img.alpha_composite(lg, (49, H - 117))
    d = ImageDraw.Draw(img)
    for dx, dy, col in ((2, 2, (0, 0, 0, 160)), (0, 0, WHITE)):
        d.text((138 + dx, H - 106 + dy), "HYPE ATLAS", font=_font("bold", 30), fill=col)
        d.text((139 + dx, H - 70 + dy), "Viral & Trending", font=_font("medium", 20), fill=col)
    return img


PHOTO_TEMPLATES = {"card": tpl_card, "hype": tpl_hype, "viral": tpl_viral, "breaking": tpl_breaking, "frame": tpl_frame}
DEFAULT_TEMPLATE = os.getenv("POSTER_TEMPLATE", "card").strip() or "card"


def make_poster(post, path, photo=None, template=None):
    """Signature 'hype' design for every post (consistent page grid).
    Other templates only when asked for explicitly (template arg or POSTER_TEMPLATE env)."""
    theme = THEMES.get(post.get("theme"), THEMES["red"])
    if photo is None:
        photo = get_background(post)
    if photo is None:
        print("[poster] no background image (add CF_ACCOUNT_ID + CF_API_TOKEN or PIXABAY_API_KEY) -> brand backdrop")
        photo = _brand_backdrop(post)
    post["_photo"] = photo  # reused by the Reel
    name = template or DEFAULT_TEMPLATE
    if name not in PHOTO_TEMPLATES:
        name = "card"
    print(f"[poster] template: {name}")
    img = PHOTO_TEMPLATES[name](post, photo, theme)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=95, subsampling=0)
    return path
