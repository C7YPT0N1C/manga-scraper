#!/usr/bin/env python3
# core/fetchers.py

import time, random, cloudscraper, requests
from requests.exceptions import HTTPError

from nhscraper.core.logger import *
from nhscraper.core.config import *

# Delay Settings
min_delay = 1.0
max_delay = 5.0
max_retries = 3 # Max number of download retries.

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
    ids = []
    try:
        logger.info(f"Fetching gallery IDs for query '{query}' (pages {start_page}-{end_page})")

        for page in range(start_page, end_page + 1):
            url = f"{API_BASE}?query={query}&sort=popular&page={page}"
            retries = 0

            while retries < max_retries:
                try:
                    resp = session.get(url, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    batch = [g["id"] for g in data.get("result", [])]
                    logger.debug(f"Page {page}: fetched {len(batch)} gallery IDs")
                    
                    if not batch:
                        logger.info(f"No results on page {page}, stopping early")
                        break

                    ids.extend(batch)
                    break  # exit retry loop on success

                except requests.HTTPError as e:
                    if resp.status_code == 429:
                        wait = (2 ** retries) + random.uniform(0, 1)
                        logger.warning(f"429 received, retrying in {wait:.1f}s (attempt {retries+1})")
                        time.sleep(wait)
                        retries += 1
                    else:
                        raise
            else:
                logger.warning(f"Max retries reached for page {page}, skipping.")

        logger.debug(f"Fetched total {len(ids)} galleries for query '{query}'")
        return ids

    except Exception as e:
        logger.warning(f"Failed to fetch galleries for query '{query}': {e}")
        return []

# ===============================
# FETCH METADATA
# ===============================
def fetch_gallery_metadata(gallery_id: int):
    url = f"https://nhentai.net/api/gallery/{gallery_id}"
    retries = 0

    while retries <= max_retries:
        try:
            start_time = time.time()
            resp = session.get(url, timeout=30)
            elapsed = time.time() - start_time

            if resp.status_code == 429:
                backoff = min(max_delay, 2 ** retries + random.uniform(0, 1))
                logger.warning(f"429 received for gallery {gallery_id}. Backing off {backoff:.2f}s (retry {retries+1}/{max_retries})")
                time.sleep(backoff)
                retries += 1
                continue

            resp.raise_for_status()
            data = resp.json()
            # Dynamic delay after successful request
            delay = min(max_delay, max(min_delay, elapsed * 1.5))
            sleep_time = delay + random.uniform(0, 0.5)
            logger.debug(f"Sleeping for {sleep_time:.2f}s after metadata fetch")
            time.sleep(sleep_time)
            return data

        except requests.RequestException as e:
            logger.warning(f"Failed to fetch metadata for gallery {gallery_id}: {e}. Retrying ({retries+1}/{max_retries})")
            retries += 1
            time.sleep(min(max_delay, 2 ** retries + random.uniform(0, 1)))

    logger.warning(f"Max retries reached for gallery {gallery_id}, returning None")
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