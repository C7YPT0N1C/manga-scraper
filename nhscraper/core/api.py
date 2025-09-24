#!/usr/bin/env python3
# nhscraper/api.py

import os, time, random, cloudscraper, requests, re, json, threading, socket
from flask import Flask, jsonify, request
from datetime import datetime
import urllib.parse
from urllib.parse import urljoin
from threading import Thread, Lock
from pathlib import Path

from nhscraper.core import configurator
from nhscraper.core.configurator import *

################################################################################################################
# GLOBAL VARIABLES
################################################################################################################

file_lock = threading.Lock()

# ===============================
# SCRAPER API
# ===============================
app = Flask(__name__)
last_gallery_id = None
running_galleries = []
gallery_metadata = {}  # global state for /status/galleries, key=gallery_id, value={'meta': {...}, 'status': ..., 'last_checked': ...}
state_lock = Lock()

################################################################################################################
# HTTP SESSION
################################################################################################################
session = None
session_lock = threading.Lock()

def get_session(referrer: str = "Undisclosed Module", status: str = "rebuild"):
    """
    This is one this module's entrypoints.
    
    Ensure and return a ready cloudscraper session.
    - If status="rebuild", rebuilds the session.
    - If return_session=True, returns the current session without rebuilding.
    """

    global session

    log_clarification("debug")
    logger.debug("Fetcher: Ready.")
    log("Fetcher: Debugging Started.", "debug")
    
    fetch_env_vars() # Refresh env vars in case config changed.

    log_clarification("debug")
    # If status is "none", report that Referrer is requesting to only retrieve session, else report build / rebuild.
    # Session is always returned.
    if status == "none":
        logger.debug(f"{referrer}: Requesting to only retrieve session.")
    else:
        logger.debug(f"{referrer}: Requesting to {status} session.")

    with session_lock:
        # Return current session if no build requested
        if status not in ["build", "rebuild"]:
            return session
        
        # Log if building or rebuilding session
        if status == "rebuild":
            log(f"Rebuilding HTTP session with cloudscraper for {referrer}", "debug")
        else:
            log(f"Building HTTP session with cloudscraper for {referrer}", "debug")

        # Random browser profiles (only randomised if flag is True)
        DefaultBrowserProfile = {"browser": "chrome", "platform": "windows", "mobile": False}
        RandomiseBrowserProfile = True
        browsers = [
            {"browser": "chrome", "platform": "windows", "mobile": False},
            {"browser": "chrome", "platform": "windows", "mobile": True},
            {"browser": "chrome", "platform": "linux", "mobile": False},
            {"browser": "chrome", "platform": "linux", "mobile": True},    
            {"browser": "firefox", "platform": "windows", "mobile": False},
            {"browser": "firefox", "platform": "windows", "mobile": True},
            {"browser": "firefox", "platform": "linux", "mobile": False},
            {"browser": "firefox", "platform": "linux", "mobile": True},
        ]
        browser_profile = random.choice(browsers) if RandomiseBrowserProfile else DefaultBrowserProfile

        # Create or rebuild session if needed
        if session is None or status == "rebuild":
            session = cloudscraper.create_scraper(browser=browser_profile)

        # Random User-Agents (only randomised if flag is True)
        DefaultUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        RandomiseUserAgent = True    
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1",
        ]
        ua = random.choice(user_agents) if RandomiseUserAgent else DefaultUserAgent

        # Random Referers (only randomised if flag is True)
        DefaultReferer = "https://nhentai.net/"
        RandomiseReferer = False   
        referers = [
            "https://nhentai.net/",
            "https://google.com/",
            "https://duckduckgo.com/",
            "https://bing.com/",
        ]
        referer = random.choice(referers) if RandomiseReferer else DefaultReferer

        # Update headers
        session.headers.update({
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
        })

        # Update proxies
        if use_tor:
            proxy = "socks5h://127.0.0.1:9050"
            session.proxies = {"http": proxy, "https": proxy}
            logger.info(f"Using Tor proxy: {proxy}")
        else:
            session.proxies = {}
            logger.info("Not using Tor proxy")
            
        # Log completion of building/rebuilding
        if status == "rebuild":
            log("Rebuilt HTTP session.", "debug")
        else:
            log("Built HTTP session.", "debug")
        #logger.debug(f"Session ready: {session}") # DEBUGGING, not really needed.

        return session # Return the current session

################################################################################################################

# ===============================
# NHentai API
# ===============================
# NHentai API Endpoints
#
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
# METADATA CLEANING
################################################################################################################

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
    
    #log(f"Fetcher: '{referrer}' Requested Tag Type '{tag_type}', returning {names}", "debug") # DEBUGGING
    return names

def make_filesystem_safe(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def clean_title(meta_or_title):
    """
    Clean a gallery/manga title. Accepts either a meta dict or a raw string.
    Detects broken symbols in the title, updates the persisted broken symbols file,
    and applies them when cleaning.

    Args:
        meta_or_title: Either a dict with metadata containing "title" or a string title.

    Returns:
        str: Sanitised title.
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    # Ensure global broken symbols file path is set
    broken_symbols_file = os.path.join(configurator.extension_download_path, "possible_broken_symbols.json")
    
    log_clarification("debug")
    log(f"Broken Symbols File at {broken_symbols_file}", "debug")

    def load_possible_broken_symbols() -> dict[str, str]:
        """
        Load possible broken symbols as a mapping { "symbol": "_" }.
        Always creates the file if missing.
        """
        if not os.path.exists(broken_symbols_file):
            try:
                os.makedirs(os.path.dirname(broken_symbols_file), exist_ok=True)
                with open(broken_symbols_file, "w", encoding="utf-8") as f:
                    json.dump({}, f, ensure_ascii=False, indent=2)
                logger.debug(f"Created new broken symbols file: {broken_symbols_file}")
            except Exception as e:
                logger.error(f"Could not create broken symbols file: {e}")
            return {}

        try:
            with open(broken_symbols_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning(f"Could not load broken symbols file: {e}")
        return {}

    def save_possible_broken_symbols(symbols: dict[str, str]):
        """
        Save possible broken symbols as a mapping { "symbol": "_" }.
        """
        try:
            os.makedirs(os.path.dirname(broken_symbols_file), exist_ok=True)
            with open(broken_symbols_file, "w", encoding="utf-8") as f:
                json.dump(symbols, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Could not save broken symbols file: {e}")
    
    #log_clarification("debug")
    #logger.debug(f"Broken symbols file: {broken_symbols_file}")
    
    # Load persisted broken symbols (mapping)
    possible_broken_symbols = load_possible_broken_symbols()

    # Determine if input is a dict or string
    if isinstance(meta_or_title, dict):
        meta = meta_or_title
        title_obj = meta.get("title", {}) or {}
        desired_title_type = title_type.lower()
        title = (
            title_obj.get(desired_title_type)
            or title_obj.get("english")
            or title_obj.get("pretty")
            or title_obj.get("japanese")
            or f"Gallery_{meta.get('id', 'UNKNOWN')}"
        )
        if "|" in title:
            title = title.split("|")[-1].strip()
    else:
        title = meta_or_title

    # Detect non-ASCII symbols in the current title
    symbols = {c for c in title if ord(c) > 127}
    known_symbols = set(ALLOWED_SYMBOLS).union(
        BROKEN_SYMBOL_REPLACEMENTS.keys(),
        BROKEN_SYMBOL_BLACKLIST,
        possible_broken_symbols.keys()
    )
    new_broken = symbols.difference(known_symbols)

    # Add new symbols to the mapping
    if new_broken:
        for s in new_broken:
            possible_broken_symbols[s] = "_"
        logger.info(f"New broken symbols detected in '{title}': {new_broken}")

    # Remove content inside [] or {} brackets
    title = re.sub(r"(\[.*?\]|\{.*?\})", "", title)

    # Apply explicit replacements
    for symbol, replacement in BROKEN_SYMBOL_REPLACEMENTS.items():
        title = title.replace(symbol, replacement)

    # Apply persisted broken symbol replacements
    for symbol, replacement in possible_broken_symbols.items():
        title = title.replace(symbol, replacement)

    # Normalise dashes
    title = re.sub(r"\s*[–—-]\s*", "-", title)

    # Replace blacklisted characters
    for symbol in BROKEN_SYMBOL_BLACKLIST:
        title = title.replace(symbol, "_")

    # Collapse multiple underscores/spaces
    title = re.sub(r"_+", "_", title)
    title = " ".join(title.split())
    title = title.strip(" _")

    if not title:
        title = f"UNTITLED_{meta.get('id', 'UNKNOWN')}" if isinstance(meta_or_title, dict) else "UNTITLED"

    # Persist updated broken symbols mapping (always save to guarantee file exists)
    save_possible_broken_symbols(possible_broken_symbols)

    return make_filesystem_safe(title)

################################################################################################################
#  NHentai API Handling
################################################################################################################

def dynamic_sleep(stage, batch_ids = None, attempt: int = 1):
    """
    Adaptive sleep timing based on load and stage, 
    including dynamic thread optimisation with anchor + units scaling.
    """
    
    #debug = True  # Forcefully enable detailed debug logs

    # ------------------------------------------------------------
    # Configurable parameters
    # ------------------------------------------------------------
    gallery_cap = 3750 # Maximum number of galleries considered for scaling (~150 pages)
    # min_sleep = Minimum Gallery sleep time
    # configurator.max_sleep = Maximum Gallery sleep time
    api_min_sleep, api_max_sleep = 0.5, 0.75 # API sleep range

    log_clarification("debug")
    log("------------------------------", "debug")
    log(f"{stage.capitalize()} Attempt: {attempt}", "debug")
    log_clarification("debug")

    # ------------------------------------------------------------
    # API STAGE
    # ------------------------------------------------------------
    if stage == "api":
        attempt_scale = attempt ** 2
        base_min, base_max = api_min_sleep * attempt_scale, api_max_sleep * attempt_scale
        sleep_time = random.uniform(base_min, base_max)
        log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s", "debug")
        log("------------------------------", "debug")
        log_clarification()
        return sleep_time

    # ------------------------------------------------------------
    # GALLERY STAGE
    # ------------------------------------------------------------
    if stage == "gallery":
        # --------------------------------------------------------
        # 1. Calculate Galleries / Threads
        # --------------------------------------------------------
        num_of_galleries = max(1, len(batch_ids))
        
        if debug:
            log(f"→ Number of galleries: {num_of_galleries} (Capped at {gallery_cap})", "debug")

        if threads_galleries is None or threads_images is None:
            # Base gallery threads = 2, scale with number of galleries
            gallery_threads = max(2, int(num_of_galleries / 500) + 1)  # 500 galleries per thread baseline
            image_threads = gallery_threads * 5  # Keep ratio 1:5
            if debug:
                log(f"→ Optimised Threads: {gallery_threads} gallery, {image_threads} image", "debug")
        else:
            gallery_threads = threads_galleries
            image_threads = threads_images
            if debug:
                log(f"→  threads: {gallery_threads} gallery, {image_threads} image", "debug")
                log(f"→ Configured Threads: Gallery = {gallery_threads}, Image = {image_threads}", "debug")

        # --------------------------------------------------------
        # 2. Calculate total load (Units Of Work)
        # --------------------------------------------------------        
        concurrency = gallery_threads * image_threads
        current_load = (concurrency * attempt) * num_of_galleries
        if debug:
            log(f"→ Concurrency = {gallery_threads} Gallery Threads * {image_threads} Image Threads = {concurrency}", "debug")
            log(f"→ Current Load = (Concurrency * Attempt) * Gallery Weight = ({concurrency} * {attempt}) * {num_of_galleries} = {current_load:.2f} Units Of Work", "debug")

        # --------------------------------------------------------
        # 3. Unit-based scaling
        # --------------------------------------------------------
        unit_factor = (current_load) / gallery_cap
        if debug:
            log_clarification("debug")
            log(f"→ Unit Factor = {current_load} (Current Load) / {gallery_cap} (Gallery Cap) = {unit_factor:.2f} Units Per Capped Gallery", "debug")

        # --------------------------------------------------------
        # 4. Thread factor, attempt scaling, and load factor
        # --------------------------------------------------------
        BASE_GALLERY_THREADS = 2
        BASE_IMAGE_THREADS = 10
        
        gallery_thread_damper = 0.9
        image_thread_damper = 0.9

        thread_factor = ((gallery_threads / BASE_GALLERY_THREADS) ** gallery_thread_damper) * ((image_threads / BASE_IMAGE_THREADS) ** image_thread_damper)

        scaled_sleep = unit_factor / thread_factor
        
        # Enforce the minimum sleep time
        scaled_sleep = max(scaled_sleep, min_sleep)
        
        if debug:
            log(f"→ Thread factor = (1 + ({gallery_threads}-2)*0.25)*(1 + ({image_threads}-10)*0.05) = {thread_factor:.2f}", "debug")
            log(f"→ Scaled sleep = Unit Factor / Thread Factor = {unit_factor:.2f} / {thread_factor:.2f} = {scaled_sleep:.2f}s", "debug")

        # --------------------------------------------------------
        # 5. Add jitter to avoid predictable timing
        # --------------------------------------------------------
        jitter_min, jitter_max = 0.9, 1.1
        sleep_time = min(random.uniform(scaled_sleep * jitter_min, scaled_sleep * jitter_max), configurator.max_sleep)
        
        if debug:
            log(f"→ Sleep after jitter (Capped at {configurator.max_sleep}s) = Random({scaled_sleep:.2f}*{jitter_min}, {scaled_sleep:.2f}*{jitter_max}) = {sleep_time:.2f}s", "debug")

        # --------------------------------------------------------
        # 6. Final result
        # --------------------------------------------------------
        log_clarification("debug")
        log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Load: {current_load:.2f} Units)", "debug")
        log("------------------------------", "debug")
        log_clarification()
        return sleep_time

#####################################################################################################################################################################

# ===============================
# BUILD API URLS
# ===============================
def build_url(query_type: str, query_value: str, page: int) -> str:
    query_lower = query_type.lower()
    
    sort = "date" # MOVE TO CLI AT SOME POINT # TEST
    sort_lower = sort.lower()

    # Homepage
    if query_lower == "homepage":
        return f"{nhentai_api_base}/galleries/all?page={page}&sort=date"

    # Tag-based queries (artist, group, tag, parody)
    if query_lower in ("artist", "group", "tag", "parody"):
        search_value = query_value
        if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
            search_value = f'"{search_value}"'
        encoded = urllib.parse.quote(f"{query_type}:{search_value}", safe=':"')
        return f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_lower}"
    
    # Search queries
    if query_lower == "search":
        # Wrap search term in quotes for exact match if it contains spaces
        search_value = query_value
        if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
            search_value = f'"{search_value}"'
        encoded = urllib.parse.quote(search_value, safe='"')
        return f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort=date"

    raise ValueError(f"Unknown query format: {query_type}='{query_value}'")

# ===============================
# FETCH GALLERY IDS
# ===============================
def fetch_gallery_ids(query_type: str, query_value: str, start_page: int = 1, end_page: int | None = None) -> set[int]:
    fetch_env_vars() # Refresh env vars in case config changed.
    
    ids: set[int] = set()
    page = start_page
    
    gallery_ids_session = get_session(referrer="API", status="return")
    
    try:
        log_clarification("debug")
        if query_value is None:
            log(f"Fetching gallery IDs from NHentai Homepages {start_page} → {end_page or '∞'}")
        else:
            log(f"Fetching gallery IDs for query '{query_value}' (pages {start_page} → {end_page or '∞'})")
        
        while True:
            if end_page is not None and page > end_page:
                break
            
            url = build_url(query_type, query_value, page)
            log(f"Fetcher: Requesting URL: {url}", "debug")

            resp = None
            for attempt in range(1, configurator.max_retries + 1):
                try:
                    resp = gallery_ids_session.get(url, timeout=10)
                    if resp.status_code == 429:
                        wait = dynamic_sleep("api", attempt=(attempt))
                        logger.warning(f"Attempt {attempt}: 429 rate limit hit, waiting {wait}s")
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    break
                except requests.RequestException as e:
                    if attempt >= configurator.max_retries:
                        log_clarification("debug")
                        logger.warning(f"Page {page}: Skipped after {attempt} retries: {e}")
                        resp = None
                        # Rebuild session with Tor and try again once
                        if use_tor:
                            logger.info("Rotated Tor IP, retrying page fetch with new session")
                            gallery_ids_session = get_session(referrer="API", status="rebuild")
                            try:
                                resp = gallery_ids_session.get(url, timeout=10)
                                resp.raise_for_status()
                            except Exception as e2:
                                logger.warning(f"Page {page}: Still failed after Tor rotate: {e2}")
                                resp = None
                        break
                    wait = dynamic_sleep("api", attempt=(attempt))
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
    fetch_env_vars() # Refresh env vars in case config changed.

    metadata_session = get_session(referrer="API", status="return")
    
    url = f"{nhentai_api_base}/gallery/{gallery_id}"
    for attempt in range(1, configurator.max_retries + 1):
        try:
            log_clarification("debug")
            log(f"Fetcher: Fetching metadata for Gallery: {gallery_id}, URL: {url}", "debug")

            resp = metadata_session.get(url, timeout=10)
            if resp.status_code == 429:
                wait = dynamic_sleep("api", attempt=(attempt))
                logger.warning(f"429 rate limit hit for Gallery: {gallery_id}, waiting {wait}s")
                time.sleep(wait)
                continue
            
            resp.raise_for_status()
            
            data = resp.json()

            # Validate the response
            if not isinstance(data, dict):
                logger.error(f"Unexpected response type for Gallery: {gallery_id}: {type(data)}")
                return None

            log_clarification("debug")
            log(f"Fetcher: Fetched metadata for Gallery: {gallery_id}", "debug")
            #log(f"Fetcher: Metadata for Gallery: {gallery_id}: {data}", "debug") # DEBUGGING
            return data
        except requests.HTTPError as e:
            if "404 Client Error: Not Found for url" in str(e):
                logger.warning(f"Gallery: {gallery_id}: Not found (404), skipping retries.")
                return None
            if attempt >= configurator.max_retries:
                logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                # Rebuild session with Tor and try again once
                if use_tor:
                    logger.info("Rotated Tor IP, retrying metadata fetch with new session")
                    metadata_session = get_session(referrer="API", status="rebuild")
                    try:
                        resp = metadata_session.get(url, timeout=10)
                        resp.raise_for_status()
                        return resp.json()
                    except Exception as e2:
                        logger.warning(f"Gallery {gallery_id}: Still failed after Tor rotate: {e2}")
                return None
            wait = dynamic_sleep("api", attempt=(attempt))
            logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait}s")
            time.sleep(wait)
        except requests.RequestException as e:
            if attempt >= configurator.max_retries:
                logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                # Rebuild session with Tor and try again once
                if use_tor:
                    logger.info("Rotated Tor IP, retrying metadata fetch with new session")
                    metadata_session = get_session(referrer="API", status="rebuild")
                    try:
                        resp = metadata_session.get(url, timeout=10)
                        resp.raise_for_status()
                        return resp.json()
                    except Exception as e2:
                        logger.warning(f"Gallery {gallery_id}: Still failed after Tor rotate: {e2}")
                return None
            wait = dynamic_sleep("api", attempt=(attempt))
            logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait}s")
            time.sleep(wait)

# ===============================
# FETCH IMAGE URLS
# ===============================
def fetch_image_urls(meta: dict, page: int):
    """
    Returns the full image URL for a gallery page.
    Tries mirrors from NHENTAI_MIRRORS in order until one succeeds.
    Handles missing metadata, unknown types, and defaulting to webp.
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    try:
        #log(f"Fetcher: Building image URLs for Gallery {meta.get('id','?')}: Page {page}", "debug") # DEBUGGING

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
        #nhentai_mirrors = configurator.nhentai_mirrors or DEFAULT_NHENTAI_MIRRORS # Normalised in configurator
        #if isinstance(nhentai_mirrors, str):
        #    nhentai_mirrors = [nhentai_mirrors]
        urls = [
            f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
            for mirror in configurator.nhentai_mirrors
        ]

        log(f"Fetcher: Built image URLs for Gallery {meta.get('id','?')}: Page {page}: {urls}", "debug") # DEBUGGING
        return urls  # return list so downloader can try them in order

    except Exception as e:
        logger.warning(f"Failed to build image URL for Gallery {meta.get('id','?')}: Page {page}: {e}")
        return None

##################################################################################################################################
##################################################################################################################################
##################################################################################################################################
##################################################################################################################################

##################################################################################################################################
# API STATE HELPERS
##################################################################################################################################
def get_tor_ip():
    """
    Fetch current IP, through Tor if enabled.
    """
    try:
        if use_tor:
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
                if entry["download_attempts"] >= configurator.max_retries:
                    logger.error(f"Gallery {gallery_id} download failed after {configurator.max_retries} attempts")

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
    """
    Return live metadata and status for all galleries.
    """
    
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
    """
    This is one this module's entrypoints.
    """
    
    log_clarification("debug")
    logger.debug("API: Ready.")
    log("API: Debugging Started.", "debug")
    
    app.run(
    host="0.0.0.0",
    port=5000,
    debug=debug
)