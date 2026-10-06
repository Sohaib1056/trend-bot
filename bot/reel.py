"""Fast-cut vertical Reel (1080x1920), built like the big viral news pages:

  0.0s  hook: headline is on screen from the very first second (muted autoplay decides in ~1s)
  every ~2s a new scene: real moving stock footage (Pixabay videos, checked by the AI judge)
        mixed with the post's photos, with a white flash + zoom punch on every cut
  ~3s   subtext fades in
  end   card: logo + "FOLLOW HYPE ATLAS"
  brand-gradient progress bar along the top the whole time
Voice: the AI's narration read by Cloudflare text-to-speech (Deepgram Aura, MeloTTS backup); the Reel is as long
       as the narration. Music: a random track from assets/music/ (royalty-free), quieter under the voice.
"""
import base64
import glob
import math
import os
import random
import re
import subprocess

import requests
from PIL import Image, ImageDraw

from . import config
from .poster import (PAD, PINK, ORANGE, YELLOW, WHITE, _cover, _download, _enhance, _fit, _font, _hgradient,
                     _logo, _paste_rounded, _tokens, _vfade, _wrap)

RW, RH = 1080, 1920
FPS = 30
SCENE = 2.0          # seconds per scene
MAX_SCENES = 6
VOICE_AT = 0.3       # narration starts after the hook frame
END_LEN = 2.2        # end card
MUSIC_DIR = os.path.join(config.BASE_DIR, "assets", "music")
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
def _pixabay_videos(post, want=3):
    """Real stock video clips for the story, each checked by the AI judge (via its thumbnail)."""
    from .ai import judge_image
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    queries = [q for q in (post.get("video_queries") or []) if isinstance(q, str) and q.strip()]
    queries += [post.get("image_query") or ""]
    if not key:
        return []
    headline = post["headline"].replace("*", "")
    os.makedirs(CLIP_DIR, exist_ok=True)
    clips, seen, judged = [], set(), 0
    for q in queries:
        if len(clips) >= want or judged >= 7 or not q.strip():
            continue
        try:
            r = requests.get("https://pixabay.com/api/videos/", timeout=20, params={
                "key": key, "q": q[:100], "safesearch": "true", "order": "popular", "per_page": 12})
            hits = [h for h in r.json().get("hits", []) if h.get("duration", 0) >= 3 and h["id"] not in seen]
        except Exception as e:
            print(f"[reel] Pixabay video search failed for '{q}': {e}")
            continue
        hits.sort(key=lambda h: (h["videos"]["large"].get("height", 0) <= h["videos"]["large"].get("width", 1)))
        taken = 0
        for h in hits[:4]:
            if len(clips) >= want or judged >= 7 or taken >= 2:
                break
            seen.add(h["id"])
            v = h["videos"].get("large") or {}
            if not v.get("url") or v.get("width", 0) < 1280:
                v = h["videos"].get("medium") or {}
            if not v.get("url"):
                continue
            try:
                judged += 1
                score = judge_image(_download(v["thumbnail"]), headline) if v.get("thumbnail") else None
                if score is not None and (score[0] < 6 or score[1] < 5):
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


# ------------------------------------------------------------------ voice
def _voice_text(post):
    text = post.get("voiceover") or (post["headline"] + ". " + post.get("subtext", ""))
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


def _scenes(post, photo, count=4):
    """Ordered list of ('photo', img) / ('video', (path, duration)) scenes."""
    photos = [photo] + [im for im in post.get("_extras", []) if im is not photo]
    videos = _pixabay_videos(post, want=min(4, count - 1))
    scenes = []
    if post.get("_person") or not videos:  # a real face is the strongest hook
        scenes.append(("photo", photos.pop(0)))
    while len(scenes) < count and (videos or photos):
        if videos:
            scenes.append(("video", videos.pop(0)))
        if photos and len(scenes) < count:
            scenes.append(("photo", photos.pop(0)))
    while len(scenes) < count:  # too little footage: re-use the photos with a different move
        scenes.append(scenes[len(scenes) % max(1, len(scenes))] if scenes[-1][0] == "video" else ("photo", photo))
    print(f"[reel] {len(scenes)} scenes: " + ", ".join(k for k, _ in scenes))
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


def _frames(post, scenes, exe, duration):
    shade = Image.alpha_composite(_vfade(RW, RH, 0, 420, (0, 0, 0), a0=170, a1=0, power=1),
                                  _vfade(RW, RH, 820, RH, (6, 6, 10), a1=245, power=1.3))
    header = _header_layer(post)
    end = _end_card()
    flash = Image.new("RGBA", (RW, RH), (255, 255, 255, 255))
    f, words, sub_f, sub, sub_y, _ = _layout_headline(post)
    bar = _hgradient(RW, 10, [PINK, ORANGE, YELLOW]).convert("RGBA")
    per = int(SCENE * FPS)
    end_at = duration - END_LEN
    total = int(duration * FPS)
    word_gap = min(0.12, 1.0 / max(1, len(words)))  # whole headline is up within ~1s
    sub_at = word_gap * len(words) + 0.8

    def source():
        for k, (kind, item) in enumerate(scenes):
            n = per + (total - per * len(scenes) if k == len(scenes) - 1 else 0)  # last scene runs under the end card
            if kind == "video":
                try:
                    yield from _video_frames(exe, item[0], item[1], n)
                    continue
                except Exception as e:
                    print(f"[reel] {e} -> using the photo instead")
                    item = scenes[0][1] if scenes[0][0] == "photo" else post["_photo"]
            yield from _photo_frames(item, n, MOVES[k % len(MOVES)])

    for i, base in enumerate(source()):
        t = i / FPS
        since_cut = t % SCENE if t < len(scenes) * SCENE else 99
        frame = base
        if i >= per and since_cut < 0.25:  # zoom punch on each cut
            z = 1.0 + 0.08 * (1 - since_cut / 0.25)
            cw, ch = int(RW / z), int(RH / z)
            frame = frame.crop(((RW - cw) // 2, (RH - ch) // 2, (RW + cw) // 2, (RH + ch) // 2)).resize((RW, RH), Image.BILINEAR)
        frame = frame.convert("RGBA")
        frame.alpha_composite(shade)
        if i >= per and since_cut < 0.15:  # white flash on each cut
            frame.alpha_composite(_with_alpha(flash, 0.55 * (1 - since_cut / 0.15)))
        frame.alpha_composite(_with_alpha(header, _ease(t / 0.3)))

        d = ImageDraw.Draw(frame)
        for k, (x, y, w, hl) in enumerate(words):
            p = 1.0 if k == 0 else _ease((t - k * word_gap) / 0.15)  # first word is there on frame 1
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

        if t >= end_at:
            frame.alpha_composite(_with_alpha(end, _ease((t - end_at) / 0.35)))
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
        vsecs = _audio_secs(exe, voice) if _tts(_voice_text(post), voice) else 0.0
        # the narration (minus the "follow" line, which plays over the end card) decides the length
        count = max(3, min(MAX_SCENES, math.ceil((vsecs + VOICE_AT - 1.6) / SCENE))) if vsecs else 4
        scenes = _scenes(post, photo, count)
        duration = max(len(scenes) * SCENE + END_LEN, vsecs + VOICE_AT + 0.5)
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
        for buf in _frames(post, scenes, exe, duration):
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
