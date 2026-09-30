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


def post_instagram(image_url, caption):
    r = requests.post(_url(f"{config.IG_USER_ID}/media"),
                      data={"image_url": image_url, "caption": caption, "access_token": config.FB_PAGE_TOKEN},
                      timeout=60)
    creation_id = _check(r, "Instagram container")["id"]
    for _ in range(20):  # wait until Instagram has processed the image
        s = requests.get(_url(creation_id), params={"fields": "status_code", "access_token": config.FB_PAGE_TOKEN},
                         timeout=30).json()
        status = s.get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"Instagram processing error: {s}")
        time.sleep(5)
    r = requests.post(_url(f"{config.IG_USER_ID}/media_publish"),
                      data={"creation_id": creation_id, "access_token": config.FB_PAGE_TOKEN}, timeout=60)
    return _check(r, "Instagram publish")["id"]


def publish(image_path, caption):
    """Post to both platforms. Returns dict of results; one failing does not stop the other."""
    result = {}
    fb_photo_id = None
    if config.POST_TO_FACEBOOK:
        try:
            fb_photo_id, post_id = post_facebook_photo(image_path, caption)
            result["facebook"] = post_id or fb_photo_id
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
