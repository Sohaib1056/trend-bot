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
import urllib.parse
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


def _tokens(headline):
    """'Apple *just dropped* iPhone 18' -> [('APPLE',False),('JUST',True),...]"""
    out, hl = [], False
    for raw in headline.replace("**", "*").split():
        start, end = raw.startswith("*"), raw.endswith("*") and len(raw) > 1
        word = raw.strip("*")
        if start:
            hl = True
        if word:
            out.append((word.upper(), hl))
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


def _draw_rich(d, lines, font, lh, x, y, colour, hl_colour, align="left", box=None, shadow=True, width=W):
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
                d.text((px, y), word, font=font, fill=hl_colour if hl else colour)
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
    r = requests.get(url, headers=headers or {}, timeout=timeout)
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
    full = (f"{prompt}. Cinematic editorial photograph, dramatic lighting, vibrant colors, sharp focus, "
            f"high detail, professional magazine quality. No text, no letters, no words, no watermark, no logo.")
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


def _pixabay_photo(query):
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key or not query:
        return None
    try:
        for q in (query, " ".join(query.split()[:2])):
            r = requests.get("https://pixabay.com/api/", params={
                "key": key, "q": q[:100], "image_type": "photo", "orientation": "vertical",
                "safesearch": "true", "order": "popular", "per_page": 20, "min_width": 900}, timeout=20)
            hits = r.json().get("hits", [])
            if hits:
                im = _download(random.choice(hits[:8])["largeImageURL"])
                print(f"[poster] background: Pixabay photo for '{q}'")
                return im
    except Exception as e:
        print(f"[poster] Pixabay failed: {e}")
    return None


def get_background(post):
    seed = abs(hash(post["key"])) % 100000
    prompt = post.get("image_prompt") or post.get("image_query")
    return (_cloudflare_image(prompt, seed) or _ai_image(prompt, seed)
            or _pixabay_photo(post.get("image_query")) or _stock_photo(post.get("image_query")))


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


PHOTO_TEMPLATES = {"viral": tpl_viral, "breaking": tpl_breaking, "frame": tpl_frame}


def make_poster(post, path, photo=None, template=None):
    theme = THEMES.get(post.get("theme"), THEMES["red"])
    if photo is None:
        photo = get_background(post)
    if photo is not None:
        name = template or post.get("template")
        if name not in PHOTO_TEMPLATES:
            name = random.choice(list(PHOTO_TEMPLATES))
        print(f"[poster] template: {name}")
        img = PHOTO_TEMPLATES[name](post, photo, theme)
    else:
        print("[poster] template: graphic (add PEXELS_API_KEY for photo posters)")
        img = tpl_graphic(post, theme)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=93)
    return path
