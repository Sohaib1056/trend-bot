"""Generates the Hype Atlas profile picture and cover photo (bright pop style)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"
OUT = Path(__file__).resolve().parent

PINK = (255, 31, 107)
ORANGE = (255, 122, 0)
YELLOW = (255, 212, 0)
BLACK = (12, 12, 16)
WHITE = (255, 255, 255)


def font(name, size):
    return ImageFont.truetype(str(FONTS / f"Poppins-{name}.ttf"), size)


def lerp(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def pop_gradient(w, h):
    """Diagonal pink -> orange -> yellow gradient."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    span = w + h
    for i in range(span):
        t = i / span
        c = lerp(PINK, ORANGE, t * 2) if t < 0.5 else lerp(ORANGE, YELLOW, (t - 0.5) * 2)
        d.line([(i, 0), (i - h, h)], fill=c, width=2)
    return img.convert("RGBA")


def overlay(base, draw_fn):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    base.alpha_composite(layer)


def globe_lines(d, cx, cy, r, color, width):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)
    for k in (0.35, 0.7):
        d.ellipse([cx - r * k, cy - r, cx + r * k, cy + r], outline=color, width=width)
    for k in (-0.5, 0, 0.5):
        y = cy + r * k
        half = (r * r - (r * k) ** 2) ** 0.5
        d.line([(cx - half, y), (cx + half, y)], fill=color, width=width)


def sticker_text(base, cx, y, text, f, fill=WHITE, shadow=BLACK, offset=10):
    """Bold text with a hard drop shadow (pop/sticker look), centred on cx."""
    d = ImageDraw.Draw(base)
    bbox = d.textbbox((0, 0), text, font=f)
    x = cx - (bbox[2] - bbox[0]) / 2 - bbox[0]
    d.text((x + offset, y + offset), text, font=f, fill=shadow)
    d.text((x, y), text, font=f, fill=fill)
    return bbox


def pill(base, cx, y, text, f, bg=BLACK, fg=YELLOW, padx=40, pady=14):
    d = ImageDraw.Draw(base)
    bbox = d.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x0 = cx - tw / 2 - padx
    d.rounded_rectangle([x0, y, x0 + tw + padx * 2, y + th + pady * 2], radius=(th + pady * 2) / 2, fill=bg)
    d.text((x0 + padx - bbox[0], y + pady - bbox[1]), text, font=f, fill=fg)


def profile():
    S = 1080
    img = pop_gradient(S, S)
    overlay(img, lambda d: globe_lines(d, S // 2, S // 2, 430, (255, 255, 255, 70), 7))
    sticker_text(img, S / 2, 180, "HA", font("Bold", 470), offset=16)
    pill(img, S / 2, 735, "HYPE ATLAS", font("Bold", 84))
    img.convert("RGB").save(OUT / "profile.png")


def cover():
    # Facebook cover 1640x624; the profile picture overlaps the bottom centre
    # and mobile crops the sides, so content stays in the upper-middle band.
    W, H = 1640, 624
    img = pop_gradient(W, H)
    overlay(img, lambda d: globe_lines(d, 1500, 230, 330, (255, 255, 255, 70), 5))
    overlay(img, lambda d: globe_lines(d, 130, 470, 260, (255, 255, 255, 55), 5))

    cx = W / 2
    sticker_text(img, cx, 10, "HYPE ATLAS", font("Bold", 125), offset=9)

    d = ImageDraw.Draw(img)
    sub = "What the US, UK & the world is talking about"
    f_sub = font("Bold", 44)
    d.text((cx - d.textlength(sub, font=f_sub) / 2, 172), sub, font=f_sub, fill=BLACK)

    chips = ["VIRAL", "POP CULTURE", "TECH", "SPORTS", "ENTERTAINMENT"]
    f_chip = font("Bold", 28)
    pad, gap = 24, 14
    widths = [d.textlength(c, font=f_chip) + pad * 2 for c in chips]
    x = cx - (sum(widths) + gap * (len(chips) - 1)) / 2
    for c, w in zip(chips, widths):
        d.rounded_rectangle([x, 248, x + w, 306], radius=29, fill=BLACK)
        d.text((x + pad, 257), c, font=f_chip, fill=WHITE)
        x += w + gap
    img.convert("RGB").save(OUT / "cover.png")


if __name__ == "__main__":
    profile()
    cover()
    print("saved profile.png and cover.png")
