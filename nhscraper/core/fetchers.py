#!/usr/bin/env python3
# core/fetchers.py

import os, time, random, json, cloudscraper, requests

from nhscraper.core.config import *

# ===============================
# GLOBAL VARIABLES
# ===============================
API_BASE = config.get("NHENTAI_API_BASE", DEFAULT_NHENTAI_API_BASE)
TAG_CACHE_FILE = "/opt/nhentai-scraper/tag_cache.json"

# ===============================
# HTTP SESSION
# ===============================
session = None

def session_builder():
    log_clarification()
    logger.info("Fetcher: Ready.")
    logger.debug("Fetcher: Debugging Started.")

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
    
    logger.debug(f"Built HTTP session with cloudscraper: {s}")
    
    logger.info(f"DEFAULT_USE_TOR = {DEFAULT_USE_TOR}") # TEST
    if config.get("USE_TOR", DEFAULT_USE_TOR):
        proxy = "socks5h://127.0.0.1:9050"
        s.proxies = {"http": proxy, "https": proxy}
        logger.info(f"Using Tor proxy: {proxy}")
    else:
        logger.info("Not using Tor proxy")
    
    return s

def build_session():
    global session
    
    # Ensure session is ready
    # Uses cloudscraper session by default.
    if session is None:
        session = session_builder()
    
################################################################################################################

# ------------------------------
# Load or initialize tag cache
# ------------------------------
if os.path.exists(TAG_CACHE_FILE):
    with open(TAG_CACHE_FILE, "r", encoding="utf-8") as f:
        TAG_CACHE: dict[str, dict[str, int]] = json.load(f)
else:
    TAG_CACHE = {"tag": {}, "artist": {}, "group": {}, "parody": {}}

def preload_tag_cache():
    """
    Fetch all tags from /api/tags and populate the cache in memory and on disk.
    Only fetches if a type is missing a requested tag.
    """

    try:
        resp = session.get(f"{API_BASE}/tags", timeout=30)
        resp.raise_for_status()
        tags = resp.json()

        for tag in tags:
            tag_type = tag["type"]
            tag_name = tag["name"].lower()
            tag_id = tag["id"]

            if tag_type not in TAG_CACHE:
                TAG_CACHE[tag_type] = {}

            TAG_CACHE[tag_type][tag_name] = tag_id

        os.makedirs(os.path.dirname(TAG_CACHE_FILE), exist_ok=True)
        with open(TAG_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(TAG_CACHE, f, ensure_ascii=False, indent=2)

        logger.info(f"Preloaded {sum(len(v) for v in TAG_CACHE.values())} tags into cache")
    except Exception as e:
        logger.warning(f"Failed to preload tags from API: {e}")

# ------------------------------
# Build URL with automatic tag ID resolution
# ------------------------------
def build_url(query: str, page: int) -> str:
    query_lower = query.lower()

    # Homepage
    if query_lower == "homepage":
        return f"{API_BASE}/galleries/all?page={page}&sort=date"

    # Search
    if query_lower.startswith("search:"):
        search_value = query.split(":", 1)[1].strip()
        return f"{API_BASE}/galleries/search?query={search_value}&page={page}&sort=date"

    # Tag-based queries
    for tag_type in ("artist", "group", "tag", "parody"):
        prefix = f"{tag_type}:"
        if query_lower.startswith(prefix):
            tag_name = query[len(prefix):].strip().lower()

            # If tag missing, preload cache
            if tag_type not in TAG_CACHE or tag_name not in TAG_CACHE.get(tag_type, {}):
                logger.debug(f"Tag '{tag_name}' not found in cache, preloading...")
                preload_tag_cache()

            tag_id = TAG_CACHE.get(tag_type, {}).get(tag_name)
            if tag_id is None:
                raise ValueError(f"Tag not found in cache: {tag_type}='{tag_name}'")

            return f"{API_BASE}/galleries/tagged?tag_id={tag_id}&page={page}&sort=date"

    raise ValueError(f"Unknown query format: {query}")

def fetch_gallery_ids(query: str, start_page: int = 1, end_page: int | None = None) -> set[int]:
    ids: set[int] = set()
    page = start_page
    
    try:
        log_clarification()
        logger.info(f"Fetching gallery IDs for query '{query}' (pages {start_page} → {end_page or '∞'})")

        while True:
            if end_page is not None and page > end_page:
                break
            
            url = build_url(query, page) # TEST
            log_clarification()
            logger.debug(f"Requesting {url}")

            resp = None
            for attempt in range(1, config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES) + 1):
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
                    if attempt >= config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES):
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
    url = f"{API_BASE}/gallery/{gallery_id}"
    for attempt in range(1, config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES) + 1):
        try:
            log_clarification()
            logger.debug(f"Fetching metadata for Gallery: {gallery_id} from URL: {url}")

            resp = session.get(url, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"429 rate limit hit for Gallery: {gallery_id}, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            
            #log_clarification()
            #logger.debug(f"Raw API response for Gallery: {gallery_id}: {resp.text}")
            
            data = resp.json()

            # Validate the response
            if not isinstance(data, dict):
                logger.error(f"Unexpected response type for Gallery: {gallery_id}: {type(data)}")
                return None

            log_clarification()
            logger.debug(f"Fetched metadata for Gallery: {gallery_id}: {data}")
            return data
        except requests.HTTPError as e:
            if "404 Client Error: Not Found for url" in str(e):
                logger.warning(f"Gallery: {gallery_id}: Not found (404), skipping retries.")
                return None
            if attempt >= config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES):
                logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                return None
            wait = 2 ** attempt
            log_clarification()
            logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait}s")
            time.sleep(wait)
        except requests.RequestException as e:
            if attempt >= config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES):
                logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                return None
            wait = 2 ** attempt
            log_clarification()
            logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait}s")
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
def get_meta_tags(meta, tag_type):
    """
    Extract all tag names of a given type (artist, group, parody, language, etc.).
    - Splits names on "|".
    - Returns ['Unknown'] if none found.
    """
    if not meta or "tags" not in meta:
        return ["Unknown"]

    names = []
    for tag in meta["tags"]:
        if tag.get("type") == tag_type and tag.get("name"):
            parts = [t.strip() for t in tag["name"].split("|") if t.strip()]
            names.extend(parts)

    return names or ["Unknown"]

def safe_name(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def clean_title(meta):
    title_obj = meta.get("title", {}) or {}
    # Prefer 'pretty', then 'english', then 'japanese', then fallback
    title = title_obj.get("pretty") or title_obj.get("english") or title_obj.get("japanese") or f"Gallery_{meta.get('id')}"
    return safe_name(title)