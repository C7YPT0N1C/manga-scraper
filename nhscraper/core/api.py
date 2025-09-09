#!/usr/bin/env python3
# nhscraper/api.py

import os, time, random, cloudscraper, requests, re, json
from flask import Flask, jsonify, request
from datetime import datetime
import urllib.parse
from urllib.parse import urljoin
from threading import Thread, Lock

from nhscraper.core.config import *

################################################################################################################
# HTTP SESSION
################################################################################################################
session = None

def session_builder():
    log_clarification()
    logger.info("Fetcher: Ready.")
    log("Fetcher: Debugging Started.", "debug")

    log("Building HTTP session with cloudscraper", "debug")

    s = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'}
    )

    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://nhentai.net/",
    })
    
    log(f"Built HTTP session with cloudscraper", "debug")
    
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
# GLOBAL VARIABLES
################################################################################################################

# ===============================
# SCRAPER API
# ===============================
app = Flask(__name__)
last_gallery_id = None
running_galleries = []
gallery_metadata = {}  # global state for /status/galleries, key=gallery_id, value={'meta': {...}, 'status': ..., 'last_checked': ...}
state_lock = Lock()

# ===============================
# NHentai API
# ===============================
# NHentai API Endpoints
#
API_BASE = config.get("NHENTAI_API_BASE", DEFAULT_NHENTAI_API_BASE)
# Default base URL: https://nhentai.net/api
#
# 1. Homepage
#    GET /galleries
#    - Returns the most recent galleries
#
# 2. Gallery by ID
#    GET /gallery/{id}
#    - Fetch gallery information for a specific gallery ID
#
# 3. Search
#    GET /galleries/search
#    - Parameters:
#        query=<search terms>
#        page=<page number>
#
# 4. Tag
#    GET /galleries/tag/{tag}
#    - Fetch galleries by a specific tag
#
# 5. Artist
#    GET /galleries/artist/{artist}
#    - Fetch galleries by a specific artist
#
# 6. Group
#    GET /galleries/group/{group}
#    - Fetch galleries by a specific circle/group
#
# 7. Parody
#    GET /galleries/parody/{parody}
#    - Fetch galleries by a specific parody/series
#
# 8. Character
#    GET /galleries/character/{character}
#    - Fetch galleries by a specific character
#
# 9. Popular / Trending (if supported) # ADD SUPPORT FOR THESE # TEST
#    GET /galleries/popular
#    GET /galleries/trending
#
# Notes:
# - Pagination is typically handled via the `page` query parameter.
# - Responses are in JSON format with metadata, tags, images, and media info.
# - Image URLs are usually served via https://i.nhentai.net/galleries/{media_id}/{page}.{ext}
    
################################################################################################################
#  NHentai API Handling
################################################################################################################
def dynamic_sleep(stage, attempt: int = 1): # TEST
    """Adaptive sleep timing based on load and stage"""
    
    BYPASS_SLEEP = config.get("NO_SLEEP", DEFAULT_NO_SLEEP)
    
    if BYPASS_SLEEP == False:
        sleep_min = 0.3 # Minimum time to sleep
        gallery_sleep_min_multiplier = 1.5 # Minimum time for a gallery to sleep (this value x sleep_min)
        
        sleep_max = 0.5 # Maximum time to sleep
        gallery_sleep_max_multiplier = 2 # Maximum time for a gallery to sleep (this value x sleep_max)
        
        # ------------------------------------------------------------
        # Define a base sleep range depending on what stage of scraping we're in
        # ------------------------------------------------------------
        if stage == "api":
            # When calling the API, back off more with each retry attempt
            base_min, base_max = (sleep_min * attempt, sleep_max * attempt)

        elif stage == "metadata":
            # Lightweight requests like fetching metadata (fixed short wait)
            base_min, base_max = (sleep_min, sleep_max)

        elif stage == "gallery":
            # Heavier stage (fetching full galleries), so longer base wait
            base_min, base_max = ((sleep_min * gallery_sleep_min_multiplier), (sleep_max * gallery_sleep_max_multiplier))

        # ------------------------------------------------------------
        # Scaling logic
        # ------------------------------------------------------------
        # Scale grows with number of galleries and total concurrency
        #   - More galleries = more cumulative load
        #   - Cap scaling at ×5 to prevent excessive waiting
        #   - Galleries count capped at 1000 to avoid runaway scaling
        num_galleries = max(1, len(config.get("GALLERIES", DEFAULT_GALLERIES))) # Number of galleries being processed; at least 1 to avoid division by zero
        total_load = ( # Total parallel work = gallery threads × image threads
            config.get("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES)
            * config.get("THREADS_IMAGES", DEFAULT_THREADS_IMAGES)
        )
        capped_galleries = min(num_galleries, 1000)
        load_factor = total_load * capped_galleries / 1000
        scale = min(max(1, load_factor), 5)

        # Choose a random sleep within the scaled range
        sleep_time = random.uniform(base_min * scale, base_max * scale)
        
        # Debug logging for transparency
        log(
            f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Scale: {scale:.1f})",
            "debug"
        )
    
    else:
        sleep_time = 0.3
        
        # Debug logging for transparency
        log(
            f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s",
            "debug"
        )
    
    return sleep_time

def build_url(query_type: str, query_value: str, page: int) -> str:
    query_lower = query_type.lower()
    
    sort = "date" # MOVE TO CLI AT SOME POINT # TEST
    sort_lower = sort.lower()

    # Homepage
    if query_lower == "homepage":
        return f"{API_BASE}/galleries/all?page={page}&sort=date"

    # Tag-based queries (artist, group, tag, parody)
    if query_lower in ("artist", "group", "tag", "parody"):
        search_value = query_value
        if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
            search_value = f'"{search_value}"'
        encoded = urllib.parse.quote(f"{query_type}:{search_value}", safe=':"')
        return f"{API_BASE}/galleries/search?query={encoded}&page={page}&sort={sort_lower}"
    
    # Search queries
    if query_lower == "search":
        # Wrap search term in quotes for exact match if it contains spaces
        search_value = query_value
        if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
            search_value = f'"{search_value}"'
        encoded = urllib.parse.quote(search_value, safe='"')
        return f"{API_BASE}/galleries/search?query={encoded}&page={page}&sort=date"

    raise ValueError(f"Unknown query format: {query_type}='{query_value}'")

def fetch_gallery_ids(query_type: str, query_value: str, start_page: int = 1, end_page: int | None = None) -> set[int]:
    ids: set[int] = set()
    page = start_page
    
    try:
        log_clarification()
        logger.info(f"Fetching gallery IDs for query '{query_value}' (pages {start_page} → {end_page or '∞'})")

        while True:
            if end_page is not None and page > end_page:
                break
            
            url = build_url(query_type, query_value, page)
            log(f"Fetcher: Requesting URL: {url}", "debug")

            resp = None
            for attempt in range(1, config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES) + 1):
                try:
                    resp = session.get(url, timeout=30)
                    if resp.status_code == 429:
                        wait = dynamic_sleep("api", attempt)
                        logger.warning(f"Attempt {attempt}: 429 rate limit hit, waiting {wait}s")
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    break
                except requests.RequestException as e:
                    if attempt >= config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES):
                        logger.warning(f"Page {page}: Skipped after {attempt} retries: {e}")
                        resp = None
                        break
                    wait = dynamic_sleep("api", attempt)
                    logger.warning(f"Fetcher: Page {page}: Attempt {attempt}: Request failed: {e}, retrying in {wait}s")
                    time.sleep(wait)

            if resp is None:
                page += 1
                continue  # skip this page if no success

            data = resp.json()
            batch = [g["id"] for g in data.get("result", [])]
            log(f"Fetcher: Page {page}: Fetched {len(batch)} gallery IDs", "debug")

            if not batch:
                logger.info(f"Page {page}: No results, stopping early")
                break

            ids.update(batch)
            page += 1

        logger.info(f"Fetched total {len(ids)} galleries for query '{query_value}'")
        return ids

    except Exception as e:
        logger.warning(f"Failed to fetch galleries for query '{query_value}': {e}")
        return set()

# ===============================
# FETCH DOUJINSHI METADATA
# ===============================
def fetch_gallery_metadata(gallery_id: int):
    url = f"{API_BASE}/gallery/{gallery_id}"
    for attempt in range(1, config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES) + 1):
        try:
            log_clarification()
            log(f"Fetcher: Fetching metadata for Gallery: {gallery_id} from URL: {url}", "debug")

            resp = session.get(url, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"429 rate limit hit for Gallery: {gallery_id}, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            
            #log_clarification()
            #log(f"Fetcher: Raw API response for Gallery: {gallery_id}: {resp.text}", "debug")
            
            data = resp.json()

            # Validate the response
            if not isinstance(data, dict):
                logger.error(f"Unexpected response type for Gallery: {gallery_id}: {type(data)}")
                return None

            log_clarification()
            log(f"Fetcher: Fetched metadata for Gallery: {gallery_id}: {data}", "debug")
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

def fetch_image_urls(meta: dict, page: int):
    """
    Returns the full image URL for a gallery page.
    Tries mirrors from NHENTAI_MIRRORS in order until one succeeds.
    Handles missing metadata, unknown types, and defaulting to webp.
    """
    try:
        log(f"Fetcher: Building image URLs for Gallery {meta.get('id','?')}: Page {page}", "debug")

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

        # Try each mirror in order
        urls = [
            f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
            for mirror in config.get("NHENTAI_MIRRORS", [])
        ]

        log(f"Fetcher: Built image URLs for Gallery {meta.get('id','?')}: Page {page}: {urls}", "debug")
        return urls  # return list so downloader can try them in order

    except Exception as e:
        logger.warning(f"Failed to build image URL for Gallery {meta.get('id','?')}: Page {page}: {e}")
        return None

# ===============================
# METADATA CLEANING
# ===============================
def get_meta_tags(referrer: str, meta, tag_type):
    """
    Extract all tag names of a given type (artist, group, parody, language, etc.).
    - Splits names on "|".
    - Returns [] if none found.
    """
    if not meta or "tags" not in meta:
        return []

    names = []
    for tag in meta["tags"]:
        if tag.get("type") == tag_type and tag.get("name"):
            parts = [t.strip() for t in tag["name"].split("|") if t.strip()]
            names.extend(parts)
    
    #log(f"Fetcher: '{referrer}' Requested Tag Type '{tag_type}', returning {names}", "debug")
    return names

def safe_name(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def clean_title(meta):
    title_obj = meta.get("title", {}) or {}
    title_type = config.get("TITLE_TYPE", DEFAULT_TITLE_TYPE).lower()
    title = (
        title_obj.get(title_type)
        or title_obj.get("english")
        or title_obj.get("pretty")
        or title_obj.get("japanese")
        or f"Gallery_{meta.get('id')}"
    )

    # If there's a |, take the last part
    if "|" in title:
        title = title.split("|")[-1].strip()

    # Remove all content inside [] or {} brackets, including the brackets themselves
    title = re.sub(r"(\[.*?\]|\{.*?\})", "", title)


    # Replace blacklisted symbols with underscores
    for symbol in DODGY_SYMBOL_BLACKLIST:
        title = re.sub(rf"\s*{re.escape(symbol)}", "_", title)

    # Collapse multiple underscores/spaces
    title = re.sub(r"_+", "_", title)   # collapse consecutive underscores
    title = " ".join(title.split())     # collapse consecutive spaces
    title = title.strip(" _")           # trim leading/trailing underscores/spaces

    # If title is empty after cleanup, fallback to safe placeholder
    if not title:
        title = f"UNTITLED_{meta.get('id', 'UNKNOWN')}"

    return safe_name(title)

##################################################################################################################################
##################################################################################################################################
##################################################################################################################################
##################################################################################################################################

##################################################################################################################################
# API STATE HELPERS
##################################################################################################################################
def get_tor_ip():
    """Fetch current IP, through Tor if enabled."""
    try:
        if config.get("USE_TOR", DEFAULT_USE_TOR):
            r = requests.get("https://httpbin.org/ip",
                             proxies={
                                 "http": "socks5h://127.0.0.1:9050",
                                 "https": "socks5h://127.0.0.1:9050"
                             },
                             timeout=10)
        else:
            r = requests.get("https://httpbin.org/ip", timeout=10)
        r.raise_for_status()
        return r.json().get("origin")
    except Exception as e:
        return f"Error: {e}"

def get_last_gallery_id():
    with state_lock:
        return last_gallery_id

def get_running_galleries():
    with state_lock:
        return list(running_galleries)
        
def update_gallery_state(gallery_id: int, stage="download", success=True):
    # Unified function to update gallery state per stage.
    # stage: 'download' or 'graphql'
    # success: True if stage completed, False if failed
    max_attempts = config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES)
    
    with state_lock:
        entry = gallery_metadata.setdefault(gallery_id, {"meta": None})
        entry.setdefault("download_attempts", 0)
        entry.setdefault("graphql_attempts", 0)

        if stage == "download":
            if success:
                entry["download_status"] = "completed"
            else:
                entry["download_attempts"] += 1
                entry["download_status"] = "failed"
                if entry["download_attempts"] >= max_attempts:
                    logger.error(f"Gallery {gallery_id} download failed after {max_attempts} attempts")

        entry["last_checked"] = datetime.now().isoformat()
        logger.info(f"Gallery {gallery_id} {stage} stage updated: {'success' if success else 'failure'}")

##################################################################################################################################
# FLASK ENDPOINTS
##################################################################################################################################
@app.route("/status", methods=["GET"])
def status_endpoint():
    return jsonify({
        "errors": [],
        "last_checked": datetime.now().isoformat(),
        "last_gallery": get_last_gallery_id(),
        "running_galleries": get_running_galleries(),
        "tor_ip": get_tor_ip()
    })

@app.route("/status/galleries", methods=["GET"])
def all_galleries_status():
    """Return live metadata and status for all galleries."""
    with state_lock:
        for gid in running_galleries:
            if gid in gallery_metadata:
                gallery_metadata[gid]["status"] = "running"
                gallery_metadata[gid]["last_checked"] = datetime.now().isoformat()

        result = {
            gid: {
                "gallery_id": gid,
                "status": info.get("status"),
                "last_checked": info.get("last_checked"),
                "meta": info.get("meta")
            }
            for gid, info in gallery_metadata.items()
        }

    return jsonify({
        "last_checked": datetime.now().isoformat(),
        "galleries": result
    })

##################################################################################################################################
# MAIN ENTRYPOINT
##################################################################################################################################
if __name__ == "__main__":
    log_clarification()
    logger.info("API: Ready.")
    log("API: Debugging Started.", "debug")
    
    app.run(
    host="0.0.0.0",
    port=5000,
    debug=config.get("DEBUG", DEFAULT_DEBUG)
)