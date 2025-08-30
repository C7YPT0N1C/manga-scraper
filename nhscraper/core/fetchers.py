#!/usr/bin/env python3
# core/fetchers.py

import time, random, cloudscraper, requests

from nhscraper.core.config import logger, config, log_clarification

# ===============================
# HTTP SESSION
# ===============================
session = None

def session_builder():
    log_clarification()
    logger.info("Fetcher: Ready.")
    logger.debug("Fetcher: Debugging Started.")

    log_clarification()
    logger.debug("Building HTTP session with cloudscraper")

    s = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'}
    )

    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://nhentai.net/",
    })

    if config.get("USE_TOR", False):
        proxy = "socks5h://127.0.0.1:9050"
        s.proxies = {"http": proxy, "https": proxy}
        logger.info(f"Using Tor proxy: {proxy}")
    else:
        logger.info("Not using Tor proxy")

    return s

def build_session():
    global session
    session = session_builder() # cloudscraper session, default.

# ===============================
# FETCH IDs
# ===============================
API_BASE = config["NHENTAI_API_BASE"]

def fetch_gallery_ids(query: str, start_page: int = 1, end_page: int | None = None) -> set[int]:
    ids: set[int] = set()
    page = start_page
    try:
        log_clarification()
        logger.info(f"Fetching gallery IDs for query '{query}' (pages {start_page} → {end_page or '∞'})")

        while True:
            if end_page is not None and page > end_page:
                break

            url = f"{API_BASE}?query={query}&sort=date&page={page}"
            log_clarification()
            logger.debug(f"Requesting {url}")

            resp = None
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
                        logger.warning(f"Page {page}: Skipped after {attempt} retries: {e}")
                        resp = None
                        break
                    wait = 2 ** attempt
                    logger.warning(f"Request failed (attempt {attempt}): {e}, retrying in {wait}s")
                    time.sleep(wait)

            if resp is None:
                page += 1
                continue  # skip this page if no success

            data = resp.json()
            batch = [g["id"] for g in data.get("result", [])]
            log_clarification()
            logger.debug(f"Page {page}: Fetched {len(batch)} gallery IDs")

            if not batch:
                logger.info(f"Page {page}: No results, stopping early")
                break

            ids.update(batch)
            page += 1

        logger.info(f"Fetched total {len(ids)} galleries for query '{query}'")
        return ids

    except Exception as e:
        logger.warning(f"Failed to fetch galleries for query '{query}': {e}")
        return set()

# ===============================
# FETCH METADATA
# ===============================
def fetch_gallery_metadata(gallery_id: int):
    url = f"https://nhentai.net/api/gallery/{gallery_id}"
    for attempt in range(1, config.get("MAX_RETRIES", 3) + 1):
        try:
            log_clarification()
            logger.debug(f"Fetching metadata for Gallery {gallery_id} from {url}")

            resp = session.get(url, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"429 rate limit hit for Gallery {gallery_id}, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            
            log_clarification()
            logger.debug(f"Raw API response for Gallery {gallery_id}: {resp.text}")
            
            data = resp.json()

            # Validate the response # TEST
            if not isinstance(data, dict):
                logger.error(f"Unexpected response type for Gallery {gallery_id}: {type(data)}")
                return None

            log_clarification()
            logger.debug(f"Fetched metadata for Gallery {gallery_id}: {data}")
            return data
        except requests.HTTPError as e:
            if "404 Client Error: Not Found for url" in str(e):
                logger.warning(f"Gallery {gallery_id} not found (404), skipping retries.")
                return None
            if attempt >= config.get("MAX_RETRIES", 3):
                logger.warning(f"Failed to fetch metadata for Gallery {gallery_id} after max retries: {e}")
                return None
            wait = 2 ** attempt
            log_clarification()
            logger.warning(f"Attempt {attempt} failed for Gallery {gallery_id}: {e}, retrying in {wait}s")
            time.sleep(wait)
        except requests.RequestException as e:
            if attempt >= config.get("MAX_RETRIES", 3):
                logger.warning(f"Failed to fetch metadata for Gallery {gallery_id} after max retries: {e}")
                return None
            wait = 2 ** attempt
            log_clarification()
            logger.warning(f"Attempt {attempt} failed for Gallery {gallery_id}: {e}, retrying in {wait}s")
            time.sleep(wait)

def fetch_image_url(meta: dict, page: int):
    """
    Returns the full image URL for a gallery page.
    Handles missing metadata, unknown types, and defaulting to webp.
    """
    try:
        logger.debug(f"Building image URL for Gallery {meta.get('id','?')}: Page {page}")

        pages = meta.get("images", {}).get("pages", [])
        if page - 1 >= len(pages):
            logger.warning(f"Gallery {meta.get('id','?')}: Page {page}: Not in metadata")
            return None

        page_info = pages[page - 1]
        if not page_info:
            logger.warning(f"Gallery {meta.get('id','?')}: Page {page}: Metadata is None")
            return None

        # Map type codes to extensions
        ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
        type_code = page_info.get("t", "w")  # default to webp
        if type_code not in ext_map:
            log_clarification()
            logger.warning(
                f"Unknown image type '{type_code}' for Gallery {meta.get('id','?')}: Page {page}: Defaulting to webp"
            )

        ext = ext_map.get(type_code, "webp")
        filename = f"{page}.{ext}"
        url = f"https://i.nhentai.net/galleries/{meta.get('media_id','')}/{filename}"

        logger.debug(f"Built image URL: {url}")
        return url

    except Exception as e:
        logger.warning(f"Failed to build image URL for Gallery {meta.get('id','?')}: Page {page}: {e}")
        return None

# ===============================
# METADATA CLEANING
# ===============================
def get_tag_names(meta, tag_type):
    """
    Return a list of tag names for a given type (artist, group, etc).
    Handles splitting cases like 'Artist A | Artist B'.
    """
    tags = meta.get("tags", {}).get(tag_type, [])
    normalized = []
    for tag in tags:
        # split on | if present, strip whitespace
        parts = [t.strip() for t in tag.split("|") if t.strip()]
        normalized.extend(parts)
    return normalized or None

def safe_name(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def clean_title(meta):
    title_obj = meta.get("title", {}) or {}
    # Prefer 'pretty', then 'english', then 'japanese', then fallback
    title = title_obj.get("pretty") or title_obj.get("english") or title_obj.get("japanese") or f"Gallery_{meta.get('id')}"
    return safe_name(title)