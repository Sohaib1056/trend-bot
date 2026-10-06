"""10-second vertical Reel (1080x1920) built from the post's background photo.

Timeline:
  0.0s  photo with slow Ken Burns zoom, brand header fades in
  0.4s  headline words pop in one by one (keywords in yellow)
  ~3s   subtext fades in
  7.6s  end card: logo + "FOLLOW HYPE ATLAS"
  brand-gradient progress bar along the top the whole time
Music: a random track from assets/music/ if present (use only royalty-free tracks), else silent audio.
"""
import glob
import os
import random
import subprocess

from PIL import Image, ImageDraw

from . import config
from .poster import (PAD, PINK, ORANGE, YELLOW, WHITE, _cover, _enhance, _fit, _font, _hgradient,
                     _logo, _paste_rounded, _tokens, _vfade, _wrap)

RW, RH = 1080, 1920
FPS = 30
DURATION = 10.0
END_AT = 7.6
MUSIC_DIR = os.path.join(config.BASE_DIR, "assets", "music")


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _with_alpha(layer, a):
    if a >= 1:
        return layer
    out = layer.copy()
    out.putalpha(layer.getchannel("A").point(lambda v: int(v * max(0.0, a))))
    return out


def _header_layer(post):
    layer = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = PAD - 22, 150  # below the Reels top bar
    lg = _logo(96)
    if lg:
        d.ellipse([x - 4, y - 4, x + 100, y + 100], fill=WHITE)
        layer.alpha_composite(lg, (x, y))
    d.text((x + 120, y + 4), "HYPE ATLAS", font=_font("bold", 40), fill=WHITE)
    d.text((x + 122, y + 56), "VIRAL & TRENDING", font=_font("bold", 22), fill=YELLOW)
    bf = _font("bold", 28)
    label = (post.get("badge") or "TRENDING").upper()[:18]
    bw = int(d.textlength(label, font=bf)) + 80
    bx, by, bh = x, y + 130, 60
    _paste_rounded(layer, _hgradient(bw, bh, [PINK, ORANGE]).convert("RGBA"), (bx, by), bh // 2)
    d = ImageDraw.Draw(layer)
    d.ellipse([bx + 24, by + bh // 2 - 8, bx + 40, by + bh // 2 + 8], fill=WHITE)
    d.text((bx + 52, by + 13), label, font=bf, fill=WHITE)
    return layer


def _layout_headline(post):
    """Positions of every headline word, computed once."""
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    toks = _tokens(post["headline"])
    max_w = RW - PAD * 2 - 60  # keep clear of the Reels side buttons
    f, lines, lh = _fit(probe, toks, max_w, 520, big=112, small=56, max_lines=4, lh_ratio=1.1)
    sub_f = _font("medium", 38)
    sub = _wrap(probe, post.get("subtext", ""), sub_f, max_w)[:3]
    block = lh * len(lines) + (30 + len(sub) * 52 if sub else 0)
    y0 = 1500 - block  # bottom of text block sits above the Reels caption area
    space = probe.textlength(" ", font=f)
    words, y = [], y0
    for line in lines:
        x = PAD
        for w, hl in line:
            words.append((x, y, w, hl))
            x += probe.textlength(w, font=f) + space
        y += lh
    return f, words, sub_f, sub, y + 30, y0


def _end_card():
    layer = Image.new("RGBA", (RW, RH), (6, 6, 10, 246))
    d = ImageDraw.Draw(layer)
    lg = _logo(240)
    cy = 640
    if lg:
        d.ellipse([RW // 2 - 128, cy - 8, RW // 2 + 128, cy + 248], fill=WHITE)
        layer.alpha_composite(lg, (RW // 2 - 120, cy))
    for text, font, fill, yy in (("FOLLOW", _font("bold", 64), WHITE, 960),
                                 ("HYPE ATLAS", _font("bold", 110), YELLOW, 1040),
                                 ("Daily viral & trending news", _font("medium", 42), (225, 225, 232), 1190),
                                 ("from the US, UK & the world", _font("medium", 42), (225, 225, 232), 1245)):
        d.text(((RW - d.textlength(text, font=font)) / 2, yy), text, font=font, fill=fill)
    _paste_rounded(layer, _hgradient(420, 12, [PINK, ORANGE, YELLOW]).convert("RGBA"), ((RW - 420) // 2, 1340), 6)
    return layer


def _frames(post, photo):
    big = _cover(_enhance(photo), int(RW * 1.14), int(RH * 1.14))
    shade = Image.alpha_composite(_vfade(RW, RH, 0, 420, (0, 0, 0), a0=170, a1=0, power=1),
                                  _vfade(RW, RH, 820, RH, (6, 6, 10), a1=245, power=1.3))
    header = _header_layer(post)
    end = _end_card()
    f, words, sub_f, sub, sub_y, _ = _layout_headline(post)
    bar = _hgradient(RW, 10, [PINK, ORANGE, YELLOW]).convert("RGBA")
    n = int(DURATION * FPS)
    word_gap = min(0.28, 2.4 / max(1, len(words)))
    sub_at = 0.4 + word_gap * len(words) + 0.3

    for i in range(n):
        t = i / FPS
        z = 1.0 + 0.12 * (t / DURATION)  # Ken Burns: slow push-in
        cw, ch = int(big.width / 1.14 * (1.14 / z)), int(big.height / 1.14 * (1.14 / z))
        left, top = (big.width - cw) // 2, (big.height - ch) // 3
        frame = big.crop((left, top, left + cw, top + ch)).resize((RW, RH), Image.BILINEAR).convert("RGBA")
        frame.alpha_composite(shade)
        frame.alpha_composite(_with_alpha(header, _ease(t / 0.5)))

        d = ImageDraw.Draw(frame)
        for k, (x, y, w, hl) in enumerate(words):
            p = _ease((t - 0.4 - k * word_gap) / 0.18)
            if p <= 0:
                continue
            dy = int((1 - p) * 40)
            col = YELLOW if hl else WHITE
            d.text((x + 3, y + dy + 4), w, font=f, fill=(0, 0, 0, int(150 * p)))
            d.text((x, y + dy), w, font=f, fill=col + (int(255 * p),), stroke_width=3,
                   stroke_fill=(0, 0, 0, int(255 * p)))
        sp = _ease((t - sub_at) / 0.4)
        if sp > 0:
            yy = sub_y
            for line in sub:
                d.text((PAD, yy), line, font=sub_f, fill=(225, 225, 232, int(255 * sp)))
                yy += 52

        if t >= END_AT:
            frame.alpha_composite(_with_alpha(end, _ease((t - END_AT) / 0.45)))
        frame.alpha_composite(bar.crop((0, 0, max(1, int(RW * (t + 1 / FPS) / DURATION)), 10)), (0, 0))
        yield frame.convert("RGB").tobytes()


def _music():
    tracks = glob.glob(os.path.join(MUSIC_DIR, "*.mp3")) + glob.glob(os.path.join(MUSIC_DIR, "*.m4a"))
    return random.choice(tracks) if tracks else None


def make_reel(post, photo, path):
    """Renders the Reel to `path` (mp4). Returns the path, or None if rendering failed."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        track = _music()
        cmd = [exe, "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{RW}x{RH}", "-r", str(FPS), "-i", "-"]
        if track:
            cmd += ["-stream_loop", "-1", "-i", track,
                    "-af", f"afade=t=in:d=0.6,afade=t=out:st={DURATION - 1.2}:d=1.2,volume=0.8"]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        cmd += ["-map", "0:v", "-map", "1:a", "-t", str(DURATION),
                "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                "-profile:v", "high", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", path]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for buf in _frames(post, photo):
            proc.stdin.write(buf)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg exit code {proc.returncode}")
        print(f"[reel] rendered {os.path.basename(path)} ({'music: ' + os.path.basename(track) if track else 'no music'})")
        return path
    except Exception as e:
        print(f"[reel] failed: {e}")
        return None
