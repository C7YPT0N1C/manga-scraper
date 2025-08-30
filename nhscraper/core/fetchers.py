#!/usr/bin/env python3
# core/fetchers.py

import time, random, cloudscraper, requests

from nhscraper.core.logger import *
from nhscraper.core.config import *

# ------------------------------
# LOG CLARIFICATION
# Prints Blank Line To Make Logs Look Cleaner)
# ------------------------------
def log_clarification():  
    print()
    logger.debug("")

# ===============================
# HTTP SESSION
# ===============================

def build_session():
    log_clarification()
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
        log_clarification()
        logger.info(f"Using Tor proxy: {proxy}")
    else:
        log_clarification()
        logger.info("Not using Tor proxy")

    return s

session = build_session()  # cloudscraper session, default.

# ===============================
# FETCH IDs
# ===============================
API_BASE = config["NHENTAI_API_BASE"]

def fetch_galleries_by_artist(artist: str, start_page: int = 1, end_page: int | None = None):
    return _fetch_gallery_ids(f'artist:"{artist}"', start_page, end_page)

def fetch_galleries_by_group(group: str, start_page: int = 1, end_page: int | None = None):
    return _fetch_gallery_ids(f'group:"{group}"', start_page, end_page)

def fetch_galleries_by_tag(tag: str, start_page: int = 1, end_page: int | None = None):
    return _fetch_gallery_ids(f'tag:"{tag}"', start_page, end_page)

def fetch_galleries_by_parody(parody: str, start_page: int = 1, end_page: int | None = None):
    return _fetch_gallery_ids(f'parody:"{parody}"', start_page, end_page)

def _fetch_gallery_ids(query: str, start_page: int, end_page: int | None):
    ids = []
    page = start_page
    try:
        log_clarification()
        logger.info(f"Fetching gallery IDs for query '{query}' (starting page {start_page})")

        while True:
            if end_page is not None and page > end_page:
                break

            url = f"{API_BASE}?query={query}&sort=popular&page={page}"
            log_clarification()
            logger.debug(f"Requesting {url}")

            for attempt in range(1, config.get("MAX_RETRIES", 3) + 1):
                try:
                    resp = session.get(url, timeout=30)
                    if resp.status_code == 429:
                        wait = 2 ** attempt
                        logger.warning(f"429 rate limit hit, waiting {wait}s (attempt {attempt})")
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    break
                except requests.RequestException as e:
                    if attempt >= config.get("MAX_RETRIES", 3):
                        logger.warning(f"Max retries reached for page {page}: {e}")
                        break
                    wait = 2 ** attempt
                    logger.warning(f"Request failed (attempt {attempt}): {e}, retrying in {wait}s")
                    time.sleep(wait)
            else:
                page += 1
                continue  # skip page if all retries failed

            data = resp.json()
            batch = [g["id"] for g in data.get("result", [])]
            log_clarification()
            logger.debug(f"Page {page}: fetched {len(batch)} gallery IDs")

            if not batch:
                logger.info(f"No results on page {page}, stopping early")
                break

            ids.extend(batch)
            page += 1

        logger.info(f"Fetched total {len(ids)} galleries for query '{query}'")
        return ids

    except Exception as e:
        logger.warning(f"Failed to fetch galleries for query '{query}': {e}")
        return []

# ===============================
# FETCH METADATA
# ===============================
def fetch_gallery_metadata(gallery_id: int):
    url = f"https://nhentai.net/api/gallery/{gallery_id}"
    for attempt in range(1, config.get("MAX_RETRIES", 3) + 1):
        try:
            log_clarification()
            logger.debug(f"Fetching metadata for gallery {gallery_id} from {url}")

            resp = session.get(url, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"429 rate limit hit for gallery {gallery_id}, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()

            log_clarification()
            logger.debug(f"Fetched metadata for gallery {gallery_id}")
            return data
        except requests.RequestException as e:
            if attempt >= config.get("MAX_RETRIES", 3):
                log_clarification()
                logger.warning(f"Failed to fetch metadata for gallery {gallery_id} after max retries: {e}")
                return None
            wait = 2 ** attempt
            logger.warning(f"Attempt {attempt} failed for gallery {gallery_id}: {e}, retrying in {wait}s")
            time.sleep(wait)

def fetch_image_url(meta: dict, page: int):
    try:
        log_clarification()
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

        log_clarification()
        logger.debug(f"Built image URL: {url}")
        return url
    except Exception as e:
        log_clarification()
        logger.warning(f"Failed to build image URL for gallery {meta.get('id','?')} page {page}: {e}")
        return None