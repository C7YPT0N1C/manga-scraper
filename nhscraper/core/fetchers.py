#!/usr/bin/env python3
# core/fetchers.py

import cloudscraper, requests

from nhscraper.core.logger import *
from nhscraper.core.config import *

# ===============================
# HTTP SESSION
# ===============================

def build_session():
    log_clarification("debug")
    logger.debug("Building HTTP session with cloudscraper")

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
        log_clarification("info")
        logger.info(f"[+] Using Tor proxy: {proxy}")
    else:
        log_clarification("info")
        logger.info("[+] Not using Tor proxy")

    return s

session = build_session() # cloudscraper session, default.
#session = requests.Session() # requests session, fallback.

# ===============================
# FETCH IDs
# ===============================
API_BASE = "https://nhentai.net/api/galleries/search"

def fetch_galleries_by_artist(artist: str, start_page: int = 1, end_page: int = 1):
    return _fetch_gallery_ids(f'artist:"{artist}"', start_page, end_page)

def fetch_galleries_by_group(group: str, start_page: int = 1, end_page: int = 1):
    return _fetch_gallery_ids(f'group:"{group}"', start_page, end_page)

def fetch_galleries_by_tag(tag: str, start_page: int = 1, end_page: int = 1):
    return _fetch_gallery_ids(f'tag:"{tag}"', start_page, end_page)

def fetch_galleries_by_parody(parody: str, start_page: int = 1, end_page: int = 1):
    return _fetch_gallery_ids(f'parody:"{parody}"', start_page, end_page)


def _fetch_gallery_ids(query: str, start_page: int, end_page: int):
    """
    Fetch gallery IDs from nhentai search API.
    Iterates from start_page to end_page (inclusive).
    """
    ids = []
    try:
        log_clarification("info")
        logger.info(f"Fetching gallery IDs for query '{query}' (pages {start_page}-{end_page})")

        for page in range(start_page, end_page + 1):
            url = f"{API_BASE}?query={query}&sort=popular&page={page}"
            log_clarification("debug")
            logger.debug(f"Requesting {url}")

            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            batch = [g["id"] for g in data.get("result", [])]
            log_clarification("debug")
            logger.debug(f"Page {page}: fetched {len(batch)} gallery IDs")

            if not batch:  # stop if page is empty
                log_clarification("info")
                logger.info(f"No results on page {page}, stopping early")
                break

            ids.extend(batch)

        log_clarification("debug")
        logger.debug(f"Fetched total {len(ids)} galleries for query '{query}'")
        return ids

    except Exception as e:
        log_clarification("warning")
        logger.warning(f"Failed to fetch galleries for query '{query}': {e}")
        return []

# ===============================
# FETCH METADATA
# ===============================
def fetch_gallery_metadata(gallery_id: int):
    url = f"https://nhentai.net/api/gallery/{gallery_id}"
    try:
        log_clarification("debug")
        logger.debug(f"Fetching metadata for gallery {gallery_id} from {url}")

        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        log_clarification("debug")
        logger.debug(f"Fetched metadata for gallery {gallery_id}")
        return data
    except Exception as e:
        log_clarification("warning")
        logger.warning(f"Failed to fetch metadata for gallery {gallery_id}: {e}")
        return None


def fetch_image_url(meta: dict, page: int):
    try:
        log_clarification("debug")
        logger.debug(f"Building image URL for gallery {meta['id']} page {page}")

        ext_map = {
            "j": "jpg",
            "p": "png",
            "g": "gif",
            "w": "webp"
        }
        ext = ext_map.get(meta["images"]["pages"][page - 1]["t"], "jpg")
        filename = f"{page}.{ext}"
        url = f"https://i.nhentai.net/galleries/{meta['media_id']}/{filename}"

        log_clarification("debug")
        logger.debug(f"Built image URL: {url}")
        return url
    except Exception as e:
        log_clarification("warning")
        logger.warning(f"Failed to build image URL for gallery {meta.get('id','?')} page {page}: {e}")
        return None