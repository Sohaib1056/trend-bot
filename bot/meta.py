"""Upload posts to a Facebook Page and Instagram via Meta's official Graph API."""
import time

import requests

from . import config


def _url(path):
    return f"https://graph.facebook.com/{config.GRAPH_VERSION}/{path}"


def _check(r, what):
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:300]}
    if r.status_code != 200 or "error" in data:
        raise RuntimeError(f"{what} failed (HTTP {r.status_code}): {data}")
    return data


def check_connection():
    """Read-only check that the Page token works and belongs to FB_PAGE_ID. Never posts."""
    if not (config.FB_PAGE_ID and config.FB_PAGE_TOKEN):
        print("[meta] FB_PAGE_ID / FB_PAGE_TOKEN not set -> skipping Facebook check")
        return False
    try:
        r = requests.get(_url("me"), params={"fields": "id,name", "access_token": config.FB_PAGE_TOKEN}, timeout=30)
        me = _check(r, "Facebook token check")
        if me.get("id") != config.FB_PAGE_ID:
            print(f"[meta] WARNING: token belongs to '{me.get('name')}' ({me.get('id')}), "
                  f"but FB_PAGE_ID is {config.FB_PAGE_ID}. Use the Page token, not the user token.")
            return False
        print(f"[meta] Facebook connected OK: page '{me.get('name')}'")
        return True
    except Exception as e:
        print(f"[meta] {e}")
        return False


def post_facebook_photo(image_path, message):
    """Publishes the photo on the Page. Returns (photo_id, post_id)."""
    with open(image_path, "rb") as f:
        r = requests.post(_url(f"{config.FB_PAGE_ID}/photos"),
                          data={"message": message, "access_token": config.FB_PAGE_TOKEN},
                          files={"source": ("poster.jpg", f, "image/jpeg")}, timeout=120)
    data = _check(r, "Facebook photo upload")
    return data.get("id"), data.get("post_id")


def upload_hidden_facebook_photo(image_path):
    """Uploads an unpublished photo (only to get a public image URL for Instagram)."""
    with open(image_path, "rb") as f:
        r = requests.post(_url(f"{config.FB_PAGE_ID}/photos"),
                          data={"published": "false", "access_token": config.FB_PAGE_TOKEN},
                          files={"source": ("poster.jpg", f, "image/jpeg")}, timeout=120)
    return _check(r, "Hidden Facebook upload")["id"]


def photo_public_url(photo_id):
    r = requests.get(_url(photo_id), params={"fields": "images", "access_token": config.FB_PAGE_TOKEN}, timeout=30)
    images = _check(r, "Get photo URL").get("images", [])
    if not images:
        raise RuntimeError("Facebook did not return an image URL")
    return max(images, key=lambda i: i.get("width", 0))["source"]


def _ig_publish(data, what, tries=20, gap=5):
    """Instagram: create a media container, wait until Instagram has processed it, publish. Returns media id."""
    r = requests.post(_url(f"{config.IG_USER_ID}/media"), data={**data, "access_token": config.FB_PAGE_TOKEN},
                      timeout=60)
    creation_id = _check(r, f"Instagram {what} container")["id"]
    for _ in range(tries):
        s = requests.get(_url(creation_id), params={"fields": "status_code", "access_token": config.FB_PAGE_TOKEN},
                         timeout=30).json()
        status = s.get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"Instagram processing error: {s}")
        time.sleep(gap)
    r = requests.post(_url(f"{config.IG_USER_ID}/media_publish"),
                      data={"creation_id": creation_id, "access_token": config.FB_PAGE_TOKEN}, timeout=60)
    return _check(r, f"Instagram {what} publish")["id"]


def post_instagram(image_url, caption):
    return _ig_publish({"image_url": image_url, "caption": caption}, "photo")


def post_instagram_reel(video_url, caption):
    """The same Reel on Instagram (from the public mp4 of the Facebook Reel); also shown in the profile grid."""
    return _ig_publish({"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true"},
                       "Reel", tries=40, gap=8)


def video_source(video_id):
    """Public mp4 URL of an uploaded video (used to cross-post Reels to Threads)."""
    for _ in range(12):
        r = requests.get(_url(video_id), params={"fields": "source", "access_token": config.FB_PAGE_TOKEN}, timeout=30)
        src = r.json().get("source")
        if src:
            return src
        time.sleep(10)
    return None


def publish_reel(video_path, caption):
    """Facebook Page Reel: start upload session -> upload bytes -> finish & publish. Returns video id."""
    r = requests.post(_url(f"{config.FB_PAGE_ID}/video_reels"),
                      data={"upload_phase": "start", "access_token": config.FB_PAGE_TOKEN}, timeout=60)
    start = _check(r, "Reel start")
    video_id, upload_url = start["video_id"], start["upload_url"]
    with open(video_path, "rb") as f:
        body = f.read()
    r = requests.post(upload_url, data=body, timeout=300, headers={
        "Authorization": f"OAuth {config.FB_PAGE_TOKEN}", "offset": "0", "file_size": str(len(body))})
    _check(r, "Reel upload")
    r = requests.post(_url(f"{config.FB_PAGE_ID}/video_reels"), timeout=60, data={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "description": caption, "access_token": config.FB_PAGE_TOKEN})
    _check(r, "Reel publish")
    print(f"[meta] Facebook Reel posted: {video_id}")
    return video_id


def publish(image_path, caption):
    """Post to both platforms. Returns dict of results; one failing does not stop the other."""
    result = {}
    fb_photo_id = None
    if config.POST_TO_FACEBOOK:
        try:
            fb_photo_id, post_id = post_facebook_photo(image_path, caption)
            result["facebook"] = post_id or fb_photo_id
            result["facebook_photo_id"] = fb_photo_id
            print(f"[meta] Facebook posted: {result['facebook']}")
        except Exception as e:
            result["facebook_error"] = str(e)
            print(f"[meta] {e}")
    if config.POST_TO_INSTAGRAM and config.IG_USER_ID:
        try:
            pid = fb_photo_id or upload_hidden_facebook_photo(image_path)
            url = photo_public_url(pid)
            result["instagram"] = post_instagram(url, caption)
            print(f"[meta] Instagram posted: {result['instagram']}")
        except Exception as e:
            result["instagram_error"] = str(e)
            print(f"[meta] {e}")
    return result
