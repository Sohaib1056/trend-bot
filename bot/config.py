"""All settings come from environment variables (GitHub Secrets / Variables)."""
import os
from datetime import datetime


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
# New York winter time (EST, UTC-5) since 2026-11-01: 3,5,6,7 PM, 9,11 PM, midnight ET. When summer time starts again
# (2027-03-14) shift every slot back by -1 h (1,3,4,19,21,22,23 / photos 4,23 / star 19,21) and update cron-job.org.
POST_SLOTS_UTC = sorted({int(h) for h in (os.getenv("POST_SLOTS", "") or "0,2,4,5,20,22,23").split(",") if h.strip().isdigit()})
SLOT_WINDOW_HOURS = _int("SLOT_WINDOW_HOURS", 3)
DRY_RUN = _bool("DRY_RUN", True)            # True = make posters only, do NOT upload
POST_TO_FACEBOOK = _bool("POST_TO_FACEBOOK", True)
POST_TO_INSTAGRAM = _bool("POST_TO_INSTAGRAM", True)
# What each slot posts: "alternate" (Reel, photo, Reel, ...), "reel", "photo" or "both"
POST_FORMAT = (os.getenv("POST_FORMAT", "") or "slots").strip().lower()
# "slots": photo posts in these slots (7 PM and midnight New York), Reels in all others
PHOTO_SLOTS_UTC = {int(h) for h in (os.getenv("PHOTO_SLOTS", "") or "0,5").split(",") if h.strip().isdigit()}
# Elon-style "star" Reels: the best slot (3 PM ET) and a second one 2 hours later
STAR_SLOTS_UTC = {int(h) for h in (os.getenv("STAR_SLOTS", "") or "20,22").split(",") if h.strip().isdigit()}
# "trending" slots: the hottest MONEY / TECH topic, whoever it is about (e.g. TREND_SLOTS=4 = 11 PM New York).
# Owner, 11-11: a 7-day test in the weakest Reel slot (11:05 PM ET, median 584 views on Elon), compared with that
# slot's Elon history; after TREND_UNTIL it is an Elon slot again.
TREND_UNTIL = datetime.fromisoformat(os.getenv("TREND_UNTIL", "") or "2026-11-19T05:00+00:00")
TREND_SLOTS_UTC = {int(h) for h in (os.getenv("TREND_SLOTS", "") or "4").split(",") if h.strip().isdigit()}
if datetime.now(TREND_UNTIL.tzinfo) >= TREND_UNTIL:
    TREND_SLOTS_UTC = set()
# Elon LIFE-STORY Reel slots (owner, 11-11): one dramatic chapter of the star's life per day, never repeated
# (trends.LIFE_CHAPTERS). 2 = 9:05 PM New York, an average slot (median 920 views).
LIFE_SLOTS_UTC = {int(h) for h in (os.getenv("LIFE_SLOTS", "") or "2").split(",") if h.strip().isdigit()}
# until this time every slot (Reels and photos) is about the star. Owner, 10-29: only Elon Musk "until I say stop",
# so no end date; set ALL_STAR_UNTIL (e.g. 2026-12-01T05:00+00:00) or change this line to end it.
ALL_STAR_UNTIL = datetime.fromisoformat(os.getenv("ALL_STAR_UNTIL", "") or "2100-01-01T00:00+00:00")
# Replies to comments: "off", "preview" (only print suggested replies in the log) or "on"
AUTO_REPLY = (os.getenv("AUTO_REPLY", "") or "preview").strip().lower()
FIRST_COMMENT = _bool("FIRST_COMMENT", True)  # page posts a question under each new post
# a short 'support us with Stars' line in captions: only while Stars is switched on for the Page (owner, 11-24:
# "already on"); set STARS_ON=false if Stars is ever turned off, else it confuses viewers
STARS_ON = _bool("STARS_ON", True)
AVOID_POLITICS = _bool("AVOID_POLITICS", True)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-flash-latest"
GEMINI_FALLBACK_MODELS = []  # others are discovered automatically (see ai._gemini_models)
GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b"
GRAPH_VERSION = os.getenv("GRAPH_VERSION", "").strip() or "v23.0"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "state", "posted.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
FONT_DIR = os.path.join(BASE_DIR, "assets", "fonts")
