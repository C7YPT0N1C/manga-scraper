#!/usr/bin/env python3
# nhscraper/core/api.py

import os, time, random, cloudscraper, requests, re, json, threading, socket, urllib.parse

from datetime import datetime
from urllib.parse import urljoin
from pathlib import Path

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *
from nhscraper.core import database

################################################################################################################
# GLOBAL VARIABLES
################################################################################################################

possible_broken_symbols_lock = threading.Lock()

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

    log_clarification("debug")
    logger.debug("Fetcher: Ready.")
    log("Fetcher: Debugging Started.", "debug")
    
    global session
    
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
        #logger.debug(f"Session ready: {session}") # NOTE: DEBUGGING, not really needed.

        return session # Return the current session
    
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
    
    #log(f"Fetcher: '{referrer}' Requested Tag Type '{tag_type}', returning {names}", "debug") # NOTE: DEBUGGING
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
    #broken_symbols_file = os.path.join(orchestrator.extension_download_path, "possible_broken_symbols.json")
    #log_clarification("debug")
    #log(f"Broken Symbols File at {broken_symbols_file}", "debug")

    def is_cjk(char: str) -> bool: # NOTE: TEST: Add a flag for this?
        """Return True if char is a Chinese/Japanese/Korean character."""
        code = ord(char)
        return (
            0x4E00 <= code <= 0x9FFF   # CJK Unified Ideographs
            or 0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
            or 0x20000 <= code <= 0x2A6DF  # Extension B
            or 0x2A700 <= code <= 0x2B73F  # Extension C
            or 0x2B740 <= code <= 0x2B81F  # Extension D
            or 0x2B820 <= code <= 0x2CEAF  # Extension E
            or 0x2CEB0 <= code <= 0x2EBEF  # Extension F
            or 0x3000 <= code <= 0x303F  # CJK Symbols and Punctuation
            or 0x3040 <= code <= 0x309F  # Hiragana
            or 0x30A0 <= code <= 0x30FF  # Katakana
            or 0x31F0 <= code <= 0x31FF  # Katakana Phonetic Extensions
            or 0xFF65 <= code <= 0xFF9F  # Half-width Katakana
        )
    
    # Load persisted broken symbols (mapping)
    possible_broken_symbols = database.load_broken_symbols()

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

    # Detect non-ASCII symbols in the current title that aren't Japanese Characters, Chinese Characters, Korean Characters
    # or in ALLOWED_SYMBOLS, BROKEN_SYMBOL_BLACKLIST, BROKEN_SYMBOL_REPLACEMENTS
    symbols = {c for c in title if ord(c) > 127 and not is_cjk(c)}
    known_symbols = set(ALLOWED_SYMBOLS).union(
        BROKEN_SYMBOL_REPLACEMENTS.keys(),
        BROKEN_SYMBOL_BLACKLIST,
        possible_broken_symbols.keys()
    )
    new_broken = symbols.difference(known_symbols)

    # Add new symbols to the Database
    if new_broken:
        for s in new_broken:
            possible_broken_symbols[s] = "_"  # maintain the mapping for this session
        database.save_broken_symbols(possible_broken_symbols)  # persist mapping to DB
        #log(f"New broken symbols detected in '{title}': {new_broken}", "info")

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
    
    # orchestrator.min_api_sleep = Minimum API sleep time
    # orchestrator.max_api_sleep = Maximum API sleep time
    
    # orchestrator.min_retry_sleep = Minimum Gallery sleep time
    # orchestrator.max_retry_sleep = Maximum Gallery sleep time

    log_clarification("debug")
    log("------------------------------", "debug")
    log(f"{stage.capitalize()} Attempt: {attempt}", "debug")
    log_clarification("debug")

    # ------------------------------------------------------------
    # API STAGE
    # ------------------------------------------------------------
    if stage == "api":
        attempt_scale = attempt ** 2
        base_min, base_max = orchestrator.min_api_sleep * attempt_scale, orchestrator.max_api_sleep * attempt_scale
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
        scaled_sleep = max(scaled_sleep, orchestrator.min_retry_sleep)
        
        if debug:
            log(f"→ Thread factor = (1 + ({gallery_threads}-2)*0.25)*(1 + ({image_threads}-10)*0.05) = {thread_factor:.2f}", "debug")
            log(f"→ Scaled sleep = Unit Factor / Thread Factor = {unit_factor:.2f} / {thread_factor:.2f} = {scaled_sleep:.2f}s", "debug")

        # --------------------------------------------------------
        # 5. Add jitter to avoid predictable timing
        # --------------------------------------------------------
        jitter_min, jitter_max = 0.9, 1.1
        sleep_time = min(random.uniform(scaled_sleep * jitter_min, scaled_sleep * jitter_max), orchestrator.max_retry_sleep)
        
        if debug:
            log(f"→ Sleep after jitter (Capped at {orchestrator.max_retry_sleep}s) = Random({scaled_sleep:.2f}*{jitter_min}, {scaled_sleep:.2f}*{jitter_max}) = {sleep_time:.2f}s", "debug")

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
#        sort=<date / popular-today / popular-week / popular>
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
# 9. Popular / Trending (if supported)
#    GET /galleries/popular
#    GET /galleries/trending
#
# Notes:
# - Pagination is typically handled via the `page` query parameter.
# - Responses are in JSON format with metadata, tags, images, and media info.
# - Image URLs are usually served via https://i.nhentai.net/galleries/{media_id}/{page}.{ext}

# ===============================
# BUILD API URLS
# ===============================
def build_url(query_type: str, query_value: str, sort_value: str, page: int) -> str:
    """
    Build the NHentai API URL for a given query type, value, sort type, and page number.

    query_type: homepage, artist, group, tag, character, parody, search
    query_value: string value of query (None for homepage)
    sort_value: date / recent / today / week / popular / all_time
    page: page number (1-based)
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    query_lower = query_type.lower()

    # Homepage
    if query_lower == "homepage":
        if sort_value == "date":
            built_url = f"{nhentai_api_base}/galleries/all?page={page}"
        else:
            built_url = f"{nhentai_api_base}/galleries/all?page={page}&sort={sort_value}"
        return built_url

    # Artist / Group / Tag / Character / Parody
    if query_lower in ("artist", "group", "tag", "character", "parody"):
        search_value = query_value
        
        # Only wrap in quotes if user didn't already do so
        if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
            search_value = f'"{search_value}"'
        
        # Use urllib.parse.quote so spaces become '%20'
        encoded = urllib.parse.quote(f"{query_type}:{search_value}", safe=':"')
        
        if sort_value == "date":
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}"
        else:
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"
        return built_url

    # Search queries
    if query_lower == "search":
        # Strip surrounding quotes if present
        search_value = query_value.strip('"').strip("'")

        # Use urllib.parse.quote_plus so spaces become '+', not '%20'
        encoded = urllib.parse.quote_plus(search_value)

        if sort_value == "date":
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}"
        else:
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"
        return built_url

    raise ValueError(f"Unknown query format: {query_type}='{query_value}'")

# ===============================
# FETCH GALLERY IDS
# ===============================
def fetch_gallery_ids(
    query_type: str,
    query_value: str,
    sort_value: str = DEFAULT_PAGE_SORT,
    start_page: int | None = None,
    end_page: int | None = None,
    archival: bool = False,
) -> set[int]:
    """
    Fetch gallery IDs from NHentai based on query type, value, and optional sort type.

    query_type: homepage, artist, group, tag, character, parody, search
    query_value: string query value (None for homepage)
    sort_value: date / recent / today / week / popular / all_time (defaults to 'date')
    start_page, end_page: pagination (auto-defaults depend on archival flag)
    archival: if True, crawl until NHentai returns no more results (ignores end_page)
    """

    fetch_env_vars()  # Refresh env vars in case config changed.

    # Apply default ranges depending on archival mode
    if archival:
        if start_page is None:
            start_page = DEFAULT_PAGE_RANGE_START
        end_page = None  # always unlimited in archival
    else:
        if start_page is None:
            start_page = DEFAULT_PAGE_RANGE_START
        if end_page is None:
            end_page = DEFAULT_PAGE_RANGE_END

    ids: set[int] = set()
    page = start_page

    gallery_ids_session = get_session(referrer="API", status="return")

    try:
        log_clarification("debug")
        if query_value is None:
            log(f"Fetching Gallery IDs from NHentai Homepages {start_page} → {end_page or '∞'}")
        else:
            log(f"Fetching Gallery IDs for {query_type} '{query_value}' (pages {start_page} → {end_page or '∞'})")
        query_str = f" '{query_value}'" if query_value else ""

        while True:
            # Stop at configured end_page (non-archival only)
            if end_page is not None and page > end_page:
                break

            url = build_url(query_type, query_value, sort_value, page)
            log(f"Fetcher: Requesting URL: {url}", "debug")

            resp = None
            for attempt in range(1, orchestrator.max_retries + 1):
                try:
                    resp = gallery_ids_session.get(url, timeout=10)

                    if resp.status_code == 429:
                        wait = dynamic_sleep("api", attempt=attempt)
                        logger.warning(f"{query_type}{query_str}, Page {page}: Attempt {attempt}: 429 rate limit, waiting {wait:.2f}s")
                        time.sleep(wait)
                        continue

                    if resp.status_code == 403:
                        wait = dynamic_sleep("api", attempt=attempt)
                        logger.warning(f"{query_type}{query_str}, Page {page}: Attempt {attempt}: 403 forbidden, retrying in {wait:.2f}s")
                        time.sleep(wait)
                        continue

                    resp.raise_for_status()
                    break  # success

                except requests.RequestException as e:
                    if attempt >= orchestrator.max_retries:
                        log_clarification("debug")
                        logger.warning(f"{query_type} {f'{query_value}' if query_value == None else ''}, Page {page}: Failed after {attempt} retries: {e}")
                        resp = None

                        # Tor fallback
                        if use_tor:
                            wait = dynamic_sleep("api", attempt=attempt) * 2
                            logger.warning(f"{query_type}{query_str}, Page {page}: Retrying with new Tor node in {wait:.2f}s")
                            time.sleep(wait)
                            gallery_ids_session = get_session(referrer="API", status="rebuild")
                            try:
                                resp = gallery_ids_session.get(url, timeout=10)
                                resp.raise_for_status()
                            except Exception as e2:
                                logger.warning(f"{query_type}{query_str}, Page {page}: Still failed after Tor rotate: {e2}")
                                resp = None
                        break

                    wait = dynamic_sleep("api", attempt=attempt)
                    logger.warning(f"{query_type}{query_str}, Page {page}: Attempt {attempt}: Request failed: {e}, retrying in {wait:.2f}s")
                    time.sleep(wait)

            if resp is None:
                page += 1
                continue  # skip this page

            try:
                data = resp.json()
            except Exception as e:
                logger.warning(f"{query_type}{query_str}, Page {page}: Failed to decode JSON: {e}")
                break
            
            # ------------------------------------
            # Filtering
            # ------------------------------------
            results = data.get("result", [])
            batch = []

            # --- Excluded Tags ---
            excluded_gallery_tags = [tag.lower() for tag in orchestrator.excluded_tags]
            
            # --- Allowed Languages ---
            allowed_gallery_language = [lang.lower() for lang in orchestrator.language]

            for g in results:
                # Extract gallery tags
                gallery_tags = [
                    t["name"].lower()
                    for t in g.get("tags", [])
                    if t.get("type") == "tag"
                ]

                # Extract gallery languages
                gallery_langs = [
                    t["name"].lower()
                    for t in g.get("tags", [])
                    if t.get("type") == "language"
                ]

                # --- Tag filter ---
                blocked_tags = [t for t in gallery_tags if t in excluded_gallery_tags]
                if blocked_tags:
                    log(
                        f"Skipping Gallery {g['id']} due to excluded tags: {blocked_tags}",
                        "debug"
                    )
                    continue

                # --- Language filter ---
                if allowed_gallery_language:
                    has_allowed = any(lang in allowed_gallery_language for lang in gallery_langs)
                    has_translated = ("translated" in gallery_langs) and has_allowed

                    if not (has_allowed or has_translated):
                        blocked_langs = gallery_langs[:]  # NOTE: debugging
                        log(
                            f"Skipping Gallery {g['id']} due to blocked languages: {blocked_langs}",
                            "debug"
                        )
                        continue

                # If passed filters → keep
                batch.append(int(g["id"]))
                
                # --- Track total pages ---
                images = g.get("images", {})
                num_pages = len(images.get("pages", []))
                orchestrator.total_gallery_images += num_pages

            log(f"Fetcher: {query_type}{query_str}, Page {page}: Fetched {len(batch)} Gallery IDs", "debug")
            log(f"Current Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")
            log(f"Excluded tags: {excluded_gallery_tags})", "debug")
            log(f"Langs allowed: {allowed_gallery_language}", "debug")

            if not batch:
                logger.info(f"Fetcher: {query_type}{query_str}, Page {page}: No more results, stopping.")
                break

            ids.update(batch)
            page += 1

        log(f"Fetched total {len(ids)} Galleries for {query_type}{query_str}", "info")
        log(f"Overall Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")
        return ids

    except Exception as e:
        logger.warning(f"Failed to fetch Galleries for {query_type}{query_str}: {e}")
        return set()

# ===============================
# FETCH IMAGE URLS AND DOUJINSHI METADATA
# ===============================
def fetch_image_urls(meta: dict, page: int):
    """
    Returns the full image URL for a gallery page.
    Tries mirrors from NHENTAI_MIRRORS in order until one succeeds.
    Handles missing metadata, unknown types, and defaulting to webp.
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    try:
        #log(f"Fetcher: Building image URLs for Gallery {meta.get('id','?')}: Page {page}", "debug") # NOTE: DEBUGGING

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
        #nhentai_mirrors = orchestrator.nhentai_mirrors or DEFAULT_NHENTAI_MIRRORS # Normalised in configurator
        #if isinstance(nhentai_mirrors, str):
        #    nhentai_mirrors = [nhentai_mirrors]
        urls = [
            f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
            for mirror in orchestrator.nhentai_mirrors
        ]

        log(f"Fetcher: Built image URLs for Gallery {meta.get('id','?')}: Page {page}: {urls}", "debug") # NOTE: DEBUGGING
        return urls  # return list so downloader can try them in order

    except Exception as e:
        logger.warning(f"Failed to build image URL for Gallery {meta.get('id','?')}: Page {page}: {e}")
        return None

def fetch_gallery_metadata(gallery_id: int):
    fetch_env_vars() # Refresh env vars in case config changed.

    metadata_session = get_session(referrer="API", status="return")
    
    url = f"{nhentai_api_base}/gallery/{gallery_id}"
    for attempt in range(1, orchestrator.max_retries + 1):
        try:
            log_clarification("debug")
            log(f"Fetcher: Fetching metadata for Gallery: {gallery_id}, URL: {url}", "debug")

            resp = metadata_session.get(url, timeout=10)
            if resp.status_code == 429:
                wait = dynamic_sleep("api", attempt=(attempt))
                logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: 429 rate limit hit, waiting {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code == 403:
                wait = dynamic_sleep("api", attempt=(attempt))
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
            #log(f"Fetcher: Metadata for Gallery: {gallery_id}: {data}", "debug") # NOTE: DEBUGGING
            return data
        except requests.HTTPError as e:
            if "404 Client Error: Not Found for url" in str(e):
                logger.warning(f"Gallery: {gallery_id}: Not found (404), skipping retries.")
                return None
            if attempt >= orchestrator.max_retries:
                logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                # Rebuild session with Tor and try again once
                if use_tor:
                    wait = dynamic_sleep("api", attempt=(attempt)) * 2
                    logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: Metadata fetch failed: {e}, retrying with new Tor Node in {wait:.2f}s")
                    time.sleep(wait)
                    metadata_session = get_session(referrer="API", status="rebuild")
                    try:
                        resp = metadata_session.get(url, timeout=10)
                        resp.raise_for_status()
                        return resp.json()
                    except Exception as e2:
                        logger.warning(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}")
                return None
            wait = dynamic_sleep("api", attempt=(attempt))
            logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s")
            time.sleep(wait)
        except requests.RequestException as e:
            if attempt >= orchestrator.max_retries:
                logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                # Rebuild session with Tor and try again once
                if use_tor:
                    wait = dynamic_sleep("api", attempt=(attempt)) * 2
                    logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: Metadata fetch failed: {e}, retrying with new Tor Node in {wait:.2f}s")
                    time.sleep(wait)
                    metadata_session = get_session(referrer="API", status="rebuild")
                    try:
                        resp = metadata_session.get(url, timeout=10)
                        resp.raise_for_status()
                        return resp.json()
                    except Exception as e2:
                        logger.warning(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}")
                return None
            wait = dynamic_sleep("api", attempt=(attempt))
            logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s")
            time.sleep(wait)