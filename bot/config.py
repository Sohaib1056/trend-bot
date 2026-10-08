"""All settings come from environment variables (GitHub Secrets / Variables)."""
import os


def _bool(name, default=False):
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


# --- Secrets ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()          # optional backup AI
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "").strip()
IG_USER_ID = os.getenv("IG_USER_ID", "").strip()
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()      # optional background photos

# --- Settings (GitHub Variables) ---
PAGE_NAME = os.getenv("PAGE_NAME", "").strip() or "Trending Now"
NICHE = os.getenv("NICHE", "").strip() or "general viral & trending news, entertainment, sports, tech and lifestyle"
COUNTRIES = [c.strip().upper() for c in (os.getenv("COUNTRIES", "") or "US,GB,CA,AU").split(",") if c.strip()]
POSTS_PER_RUN = max(1, _int("POSTS_PER_RUN", 1))
# Posting slots (UTC hours). The workflow wakes up every hour; a scheduled run only posts
# inside a slot window and only once per slot, so GitHub's late/dropped cron runs are covered.
POST_SLOTS_UTC = sorted({int(h) for h in (os.getenv("POST_SLOTS", "") or "1,3,12,16,19,23").split(",") if h.strip().isdigit()})
SLOT_WINDOW_HOURS = _int("SLOT_WINDOW_HOURS", 3)
DRY_RUN = _bool("DRY_RUN", True)            # True = make posters only, do NOT upload
POST_TO_FACEBOOK = _bool("POST_TO_FACEBOOK", True)
POST_TO_INSTAGRAM = _bool("POST_TO_INSTAGRAM", True)
# What each slot posts: "alternate" (Reel, photo, Reel, ...), "reel", "photo" or "both"
POST_FORMAT = (os.getenv("POST_FORMAT", "") or "slots").strip().lower()
# "slots": photo posts in these slots (US morning + lunch scroll), Reels in all others (US afternoon/evening/prime time)
PHOTO_SLOTS_UTC = {int(h) for h in (os.getenv("PHOTO_SLOTS", "") or "12,16").split(",") if h.strip().isdigit()}
# Replies to comments: "off", "preview" (only print suggested replies in the log) or "on"
AUTO_REPLY = (os.getenv("AUTO_REPLY", "") or "preview").strip().lower()
FIRST_COMMENT = _bool("FIRST_COMMENT", True)  # page posts a question under each new post
AVOID_POLITICS = _bool("AVOID_POLITICS", True)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-flash-latest"
GEMINI_FALLBACK_MODELS = []  # others are discovered automatically (see ai._gemini_models)
GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b"
GRAPH_VERSION = os.getenv("GRAPH_VERSION", "").strip() or "v23.0"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "state", "posted.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
FONT_DIR = os.path.join(BASE_DIR, "assets", "fonts")
