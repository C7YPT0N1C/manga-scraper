# core/fetchers.py
import cloudscraper, requests
from nhscraper.core.logger import *

# ===============================
# HTTP SESSION
# ===============================

def build_session():
    s = cloudscraper.create_scraper()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://nhentai.net/",
    })

    if config.get("use_tor", True):
        proxy = "socks5h://127.0.0.1:9050"
        s.proxies.update({"http": proxy, "https": proxy})
        logger.info(f"[+] Using Tor proxy: {proxy}")
    else:
        logger.info("[+] Not using Tor proxy")

    return s

session = build_session() # cloudscraper session, default.
#session = requests.Session() # requests session, fallback.

# ===============================
# FETCH FUNCTIONS
# ===============================
def fetch_gallery_metadata(gallery_id: int):
    url = f"https://nhentai.net/api/gallery/{gallery_id}"
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data
    except Exception as e:
        log_clarification("warning")
        logger.warning(f"Failed to fetch metadata for gallery {gallery_id}: {e}")
        return None


def fetch_image_url(meta: dict, page: int):
    try:
        ext_map = {
            "j": "jpg",
            "p": "png",
            "g": "gif",
            "w": "webp"
        }
        ext = ext_map.get(meta["images"]["pages"][page - 1]["t"], "jpg")
        filename = f"{page}.{ext}"
        url = f"https://i.nhentai.net/galleries/{meta['media_id']}/{filename}"
        return url
    except Exception as e:
        log_clarification("warning")
        logger.warning(f"Failed to build image URL for gallery {meta['id']} page {page}: {e}")
        return None