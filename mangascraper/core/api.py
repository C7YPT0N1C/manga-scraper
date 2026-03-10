#!/usr/bin/env python3
# mangascraper/core/api.py

import os, sqlite3, threading, atexit, json, time, random, cloudscraper, requests, re, socket, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin
from pathlib import Path
from tqdm import tqdm

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.core import database as scraperdb

####################################################################################################################
# GLOBAL VARIABLES
####################################################################################################################

possible_broken_symbols_lock = threading.Lock()

# Pre-compile regex patterns for title cleaning (avoid recompilation on every call)
_BRACKET_PATTERN = re.compile(r"(\[.*?\]|\{.*?\})")
_DASH_PATTERN = re.compile(r"\s*[–—-]\s*")
_UNDERSCORE_PATTERN = re.compile(r"_+")

# Pre-build symbol translation table for faster replacements
_SYMBOL_TRANSLATION_TABLE = None

session = None
session_lock = threading.Lock()

################################################################################################################
# INTERNAL API HELPERS
################################################################################################################

def _build_symbol_translation_table():
    """Build a translation table for symbol replacements (called once at module load)."""
    global _SYMBOL_TRANSLATION_TABLE
    trans_dict = {ord(symbol): replacement for symbol, replacement in BROKEN_SYMBOL_REPLACEMENTS.items()}
    _SYMBOL_TRANSLATION_TABLE = trans_dict

# Initial symbol translation table at module load
_build_symbol_translation_table()

def sanitise_string(meta_or_title):
    """
    Clean a gallery/manga title. Accepts either a meta dict or a raw string.
    Detects broken symbols in the title, updates the persisted broken symbols file,
    and applies them when cleaning.

    Args:
        meta_or_title: Either a dict with metadata containing "title" or a string title.

    Returns:
        str: Sanitised title.
    """
    
    def is_cjk(char: str) -> bool:
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
    possible_broken_symbols = Caching.Load.broken_symbols()

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

    # Detect non-ASCII symbols - cache the known_symbols set to avoid rebuilding
    symbols = {c for c in title if ord(c) > 127 and not is_cjk(c)}
    known_symbols = set(ALLOWED_SYMBOLS).union(
        BROKEN_SYMBOL_REPLACEMENTS.keys(),
        BROKEN_SYMBOL_BLACKLIST,
        possible_broken_symbols.keys()
    )
    new_broken = symbols.difference(known_symbols)

    # Add new symbols to the Database, with logging
    if new_broken:
        for s in new_broken:
            possible_broken_symbols[s] = "_"
        logger.debug(f"[BrokenSymbols] New broken symbols detected: {sorted(new_broken)}. Updating database.")
        Caching.Save.broken_symbols(possible_broken_symbols)
        _build_symbol_translation_table()
    #else:
    #    logger.debug("[BrokenSymbols] No new broken symbols detected. No database update needed.")

    # Remove content inside [] or {} brackets (use pre-compiled regex)
    title = _BRACKET_PATTERN.sub("", title)

    # Apply explicit replacements using optimd translation (faster than loops)
    if _SYMBOL_TRANSLATION_TABLE is None:
        _build_symbol_translation_table()
    title = title.translate(_SYMBOL_TRANSLATION_TABLE)

    # Apply persisted broken symbol replacements
    for symbol, replacement in possible_broken_symbols.items():
        title = title.replace(symbol, replacement)

    # Normalise dashes (use pre-compiled regex)
    title = _DASH_PATTERN.sub("-", title)

    # Replace blacklisted characters
    for symbol in BROKEN_SYMBOL_BLACKLIST:
        title = title.replace(symbol, "_")

    # Collapse multiple underscores/spaces (use pre-compiled regex)
    title = _UNDERSCORE_PATTERN.sub("_", title)
    title = " ".join(title.split())
    title = title.strip(" _")

    if not title:
        title = f"UNTITLED_{meta.get('id', 'UNKNOWN')}" if isinstance(meta_or_title, dict) else "UNTITLED"

    return title.replace("/", "-").replace("\\", "-").strip()

def build_gallery_metadata_summary(meta, referrer: str):
    orchestrator.refresh_globals()

    # Extract and clean creators
    artists = Get.meta_tags(f"{referrer}: Build_gallery_metadata_summary", meta, "artist")
    groups = Get.meta_tags(f"{referrer}: Build_gallery_metadata_summary", meta, "group")
    creators = artists or groups or ["Unknown Creator"]
    creators_clean = [sanitise_string(c) for c in creators]

    # Clean title
    title = sanitise_string(meta)
    id = str(meta.get("id", "Unknown ID"))
    full_title = f"({id}) {title}"

    # Extract and clean language(s)
    gallery_language = Get.meta_tags(
        f"{referrer}: Build_gallery_metadata_summary", meta, "language"
    ) or ["Unknown Language"]
    gallery_language_clean = [sanitise_string(l) for l in gallery_language]

    # Prepare cleaned metadata for DB
    clean_metadata = {
        "clean_title": title,
        "creator_names": creators_clean,
        "language_names": gallery_language_clean,
    }
    
    # Update DB clean_metadata for this gallery if id is valid
    try:
        gid = int(id) if id.isdigit() else id
        scraperdb.upsert_cached_metadata(gid, time.time(), clean_metadata=clean_metadata)
    except Exception as e:
        logger.error(f"Failed to upsert cached metadata for Gallery {id}: {e}")

    return {
        "creator": creators_clean,
        "title": full_title,
        "short_title": title,
        "id": id,
        "language": gallery_language_clean,
    }

def _calculate_thread_load_sleep(stage: str, num_items: int, attempt: int, gallery_cap: int = 3750) -> float:
    """
    Helper function to calculate sleep time based on thread load.
    """
    if orchestrator.threads_galleries is None or orchestrator.threads_images is None:
        gallery_threads = max(2, int(num_items / BATCH_SIZE) + 1) if stage == "gallery" else DEFAULT_THREADS_GALLERIES
        image_threads = gallery_threads * (DEFAULT_THREADS_IMAGES / DEFAULT_THREADS_GALLERIES)
        log(f"→ Optimised Threads: {gallery_threads} Gallery, {image_threads} Image", "debug")
    else:
        gallery_threads = orchestrator.threads_galleries
        image_threads = orchestrator.threads_images
        log(f"→ Threads: {gallery_threads} Gallery, {image_threads} Image", "debug")
        log(f"→ Configured Threads: Gallery = {gallery_threads}, Image = {image_threads}", "debug")

    concurrency = (gallery_threads * image_threads) + gallery_threads
    current_load = (concurrency * attempt) * num_items
    log(f"→ Concurrency = {gallery_threads} Gallery Threads * {image_threads} Image Threads = {concurrency}", "debug")
    log(f"→ Current Load = (Concurrency * Attempt) * Num Of {stage.capitalize()}s = ({concurrency} * {attempt}) * {num_items} = {current_load:.2f} Units Of Work", "debug")

    unit_factor = current_load / gallery_cap
    log_clarification("debug")
    log(f"→ Unit Factor = {current_load} (Current Load) / {gallery_cap} (Gallery Cap) = {unit_factor:.2f} Units Per Capped Gallery", "debug")
    
    BASE_GALLERY_THREADS = 2
    BASE_IMAGE_THREADS = 10
    gallery_thread_damper = 0.9
    image_thread_damper = 0.9

    thread_factor = ((gallery_threads / BASE_GALLERY_THREADS) ** gallery_thread_damper) * ((image_threads / BASE_IMAGE_THREADS) ** image_thread_damper)
    scaled_sleep = max(unit_factor / thread_factor, orchestrator.min_retry_sleep)
    
    log(f"→ Thread factor = (({gallery_threads}/{BASE_GALLERY_THREADS})^{gallery_thread_damper}) * (({image_threads}/{BASE_IMAGE_THREADS})^{image_thread_damper}) = {thread_factor:.2f}", "debug")
    log(f"→ Scaled sleep = Unit Factor / Thread Factor = {unit_factor:.2f} / {thread_factor:.2f} = {scaled_sleep:.2f}s", "debug")
    
    jitter_min, jitter_max = 0.9, 1.1
    sleep_time = min(random.uniform(scaled_sleep * jitter_min, scaled_sleep * jitter_max), orchestrator.max_retry_sleep)
    
    log(f"→ Sleep after jitter (Capped at {orchestrator.max_retry_sleep}s) = Random({scaled_sleep:.2f}*{jitter_min}, {scaled_sleep:.2f}*{jitter_max}) = {sleep_time:.2f}s", "debug")
    
    return sleep_time, current_load, gallery_threads, image_threads, concurrency

def dynamic_sleep(stage, attempt: int = 1):
    """
    Adaptive sleep timing based on load and stage, 
    including dynamic thread optimisation with anchor + units scaling.
    """
    
    gallery_cap = 3750

    log_clarification("debug")
    log("------------------------------", "debug")
    log(f"{stage.capitalize()} Attempt: {attempt}", "debug")
    log_clarification("debug")

    # API stage - simpler calculation
    if stage == "api":
        attempt_scale = attempt ** 2
        base_min, base_max = orchestrator.min_api_sleep * attempt_scale, orchestrator.max_api_sleep * attempt_scale
        sleep_time = random.uniform(base_min, base_max)
        log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s", "debug")
        log("------------------------------", "debug")
        log_clarification()
        return sleep_time

    # Gallery and Image stages - use unified calculation
    if stage in ("gallery", "image"):
        num_items = 1 if stage == "gallery" else max(1, orchestrator.total_gallery_images)
        log(f"→ Number of {stage.capitalize()}s: {num_items} (Capped at {gallery_cap})", "debug")
        sleep_time, current_load, gallery_threads, image_threads, concurrency = _calculate_thread_load_sleep(stage, num_items, attempt, gallery_cap)
        
        log_clarification("debug")
        log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Load: {current_load:.2f} Units)", "debug")
        log("------------------------------", "debug")
        log_clarification()
        return sleep_time

################################################################################################################
# NHentai API Handling / Endpoints
################################################################################################################
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
    
    orchestrator.refresh_globals()
    
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

def estimate_gallery_size(meta: dict, use_head_requests: bool = False) -> tuple:
    """
    Estimate total download size for a gallery in bytes.
    Returns (estimated_size_bytes, actual_size_bytes, image_count).
    
    Args:
        meta: Gallery metadata dict with 'images' field
        use_head_requests: If True, make HEAD requests for accurate filesize; if False, estimate by type
    
    Returns:
        (estimated_size_bytes, actual_size_bytes or 0, image_count)
    """
    
    orchestrator.refresh_globals()
    
    pages = meta.get("images", {}).get("pages", [])
    image_count = len(pages)
    
    if image_count == 0:
        return 0, 0, 0
    
    # Type-based size estimation (light, ~85% accurate)
    type_sizes = {"j": 85000, "p": 180000, "g": 520000, "w": 65000}  # bytes
    estimated_total = 0
    actual_total = 0
    fetched_count = 0
    
    for i, page_info in enumerate(pages):
        type_code = page_info.get("t", "w") if page_info else "w"
        estimated_total += type_sizes.get(type_code, 65000)  # default webp
        
        # Optional: use HEAD requests for 100% accuracy (slow)
        if use_head_requests and page_info and meta.get("media_id"):
            try:
                ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
                ext = ext_map.get(type_code, "webp")
                filename = f"{i + 1}.{ext}"
                urls = [
                    f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
                    for mirror in orchestrator.nhentai_mirrors[:1]  # try first mirror only
                ]
                
                if urls:
                    resp = Get.session(referrer="API", status="return").head(urls[0], timeout=(10, 10))
                    if resp.status_code == 200:
                        actual_total += int(resp.headers.get("content-length", type_sizes.get(type_code, 65000)))
                        fetched_count += 1
            except Exception:
                pass  # Fall back to estimate if HEAD fails
    
    # If we got some head request data, interpolate the rest
    if fetched_count > 0 and fetched_count < image_count:
        avg_actual = actual_total / fetched_count
        remaining = image_count - fetched_count
        actual_total += int(avg_actual * remaining)
    elif fetched_count == 0:
        actual_total = estimated_total
    
    return estimated_total, actual_total, image_count

################################################################################################################
# NAMESPACED API
################################################################################################################

class Get:
    """Get a resource, usually generated."""
    
    @staticmethod
    def gallery_status(gallery_id):
        """Get the status of a Gallery keyed by its ID"""
        scraperdb.init_db()
        with scraperdb.lock, scraperdb.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM Galleries WHERE id=?", (gallery_id,))
            row = cursor.fetchone()
            return row[0] if row else None
    
    @staticmethod
    def cache_keys(search_type: str, search_value: str = None) -> str:
        """Generate cache key based on search criteria.
        Args:
            search_type: Type of search (artist, tag, group, character, parody, search, archive, homepage)
            search_value: The query value (artist name, tag name, etc.)
        Returns:
            Cache key suitable for filename (e.g., "artist_john", "tag_schoolgirl")
        """
        if search_value:
            # Split into words/tokens, sort alphanumerically, join with underscores
            terms = [t for t in search_value.lower().split() if t]
            if len(terms) > 1:
                terms = sorted(terms, key=lambda x: (x.isdigit(), x))
            sorted_value = "_".join(terms)
            # Sanitise for use as filename (remove special chars)
            safe_value = "".join(c for c in sorted_value if c.isalnum() or c in ('-', '_')).lower()
            logger.debug(f"[TESTING]: {search_type}:{safe_value}")
            return f"{search_type}:{safe_value}"
        return search_type
    
    ################################################################################################################
    # HTTP SESSION
    ################################################################################################################
    
    @staticmethod
    def session(referrer: str = "Undisclosed Module", status: str = "rebuild"):
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
        
        orchestrator.refresh_globals()

        log_clarification("debug")
        # If status is "none", report that Referrer is requesting to only retrieve session, else report build / rebuild.
        # Session is always returned.
        if status == "none":
            logger.debug(f"{referrer}: Requesting to only retrieve session.")
        else:
            logger.debug(f"{referrer}: Requesting to {status} session.")

        with session_lock:
            # Refresh SSL verification setting on every session access
            if session is not None:
                session.verify = orchestrator.verify_ssl
            
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
            
            # Set SSL certificate verification based on config
            session.verify = orchestrator.verify_ssl

            # Random User-Agents (only randomised if flag is True)
            DefaultUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            RandomiseUserAgent = True    
            user_agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
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

    @staticmethod
    def meta_tags(referrer: str, meta, tag_type):
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
    
    # Utility functions for metadata extraction
    @staticmethod
    def artists(meta):
        return Get.meta_tags("api", meta, "artist") or ["Unknown Artist"]

    @staticmethod
    def groups(meta):
        return Get.meta_tags("api", meta, "group") or ["Unknown Group"]

    @staticmethod
    def tags(meta):
        return Get.meta_tags("api", meta, "tag") or []

    @staticmethod
    def characters(meta):
        return Get.meta_tags("api", meta, "character") or []

    @staticmethod
    def parodies(meta):
        return Get.meta_tags("api", meta, "parody") or []

    @staticmethod
    def languages(meta):
        return Get.meta_tags("api", meta, "language") or ["Unknown Language"]

    @staticmethod
    def page_count(meta):
        return len(meta.get("images", {}).get("pages", []))
    
    ################################################################################################################
    # METADATA SUMMARIES
    ################################################################################################################
    
    def metadata_summary(metadata: dict) -> dict:
        """
        Generate a summary of metadata statistics.

        Returns:
            dict with counts of artists, groups, tags, languages, min/max pages, etc.
        """

        if not metadata:
            return {}

        all_artists = set()
        all_groups = set()
        all_tags = set()
        all_characters = set()
        all_parodies = set()
        all_languages = set()
        pages_list = []

        for meta in metadata.values():
            all_artists.update(meta.get("artists", []))
            all_groups.update(meta.get("groups", []))
            all_tags.update(meta.get("tags", []))
            all_characters.update(meta.get("characters", []))
            all_parodies.update(meta.get("parodies", []))
            all_languages.update(meta.get("languages", []))
            pages_list.append(meta.get("pages", 0))

        return {
            "total_galleries": len(metadata),
            "unique_artists": len(all_artists),
            "unique_groups": len(all_groups),
            "unique_tags": len(all_tags),
            "unique_characters": len(all_characters),
            "unique_parodies": len(all_parodies),
            "unique_languages": len(all_languages),
            "artists": sorted(all_artists),
            "groups": sorted(all_groups),
            "tags": sorted(all_tags),
            "characters": sorted(all_characters),
            "parodies": sorted(all_parodies),
            "languages": sorted(all_languages),
            "min_pages": min(pages_list) if pages_list else 0,
            "max_pages": max(pages_list) if pages_list else 0,
            "avg_pages": sum(pages_list) / len(pages_list) if pages_list else 0,
        }

class Fetch:
    """Fetch a resource"""
    ################################################################################################################
    # GALLERY ID FETCHING
    ################################################################################################################

    @staticmethod
    def gallery_ids(
        query_type: str,
        query_value: str,
        sort_value: str = DEFAULT_PAGE_SORT,
        start_page: int | None = None,
        end_page: int | None = None,
        file_used: bool = False,
        fetch_as_archival: bool = DEFAULT_ARCHIVING,
    ) -> tuple[str | None, list[int]]:
        """
        Fetches Gallery IDs. Tries cache key(s) first, then falls back to API if needed.
        Returns a tuple (cache_key, list of IDs).
        """
        
        cache_key = Get.cache_keys(query_type, query_value) if query_value else None
        
        # 1. Try cache first
        if cache_key:
            references = Caching.Load.cache()
            cache_entry = references.get(cache_key)
            now = time.time()
            if cache_entry:
                expires_at = cache_entry.get("expires_at")
                
                ids = cache_entry.get("ids", [])
                # Ensure all IDs are integers
                ids = [int(gid) for gid in ids]
                
                if expires_at is None or expires_at > now:
                    logger.debug(f"Using cached gallery IDs for key {cache_key} (count={len(ids)})")
                    return (cache_key, ids)
                else:
                    logger.debug(f"Cache entry for {cache_key} expired (expires_at={expires_at}, now={now}). Will fetch from API.")
            else:
                logger.debug(f"No valid cache entry for {cache_key}. Will fetch from API.")
        else:
            logger.debug(f"No valid cache entry for {cache_key}. Will fetch from API.")

        # 2. If no valid cache, fetch from API
        max_retries = 2
        attempt = 0
        ids = []
        while attempt < max_retries:
            try:
                global archiving
                orchestrator.refresh_globals()
                qt = query_type.capitalize()
                query_str = f" ' {query_value}'" if query_value else ""
                sort_str = f"'{sort_value}'" if sort_value != "date" else "date"
                if start_page is None:
                    start_page = DEFAULT_PAGE_RANGE_START
                if file_used:
                    if end_page is None:
                        end_page = None
                if fetch_as_archival:
                    log_clarification("debug")
                    log(f"SWITCHING TO ARCHIVAL MODE", "debug")
                    orchestrator.archiving = True
                    end_page = None
                else:
                    if end_page is None:
                        end_page = DEFAULT_PAGE_RANGE_END
                ids_set = set()
                page = start_page
                gallery_ids_session = Get.session(referrer="API", status="return")
                log_clarification("debug")
                if query_value is None:
                    log(f"Fetching Gallery IDs from NHentai Homepages {start_page} → {end_page or '∞'}")
                else:
                    log(f"Fetching Gallery IDs for {qt} '{query_value}' (pages {start_page} → {end_page or '∞'}), sorted by {sort_str}")
                while True:
                    if end_page is not None and page > end_page:
                        break
                    url = build_url(qt, query_value, sort_value, page)
                    log(f"Fetcher: Requesting URL: {url}", "debug")
                    resp = None
                    for api_attempt in range(1, orchestrator.max_retries + 1):
                        try:
                            resp = gallery_ids_session.get(url, timeout=(60, 60))
                            if resp.status_code == 429:
                                wait = dynamic_sleep("api", attempt=api_attempt)
                                logger.warning(f"{qt}{query_str}, Page {page}: Attempt {api_attempt}: 429 rate limit, waiting {wait:.2f}s")
                                time.sleep(wait)
                                continue
                            if resp.status_code == 403:
                                wait = dynamic_sleep("api", attempt=api_attempt)
                                logger.warning(f"{qt}{query_str}, Page {page}: Attempt {api_attempt}: 403 forbidden, retrying in {wait:.2f}s")
                                time.sleep(wait)
                                continue
                            resp.raise_for_status()
                            break
                        except requests.RequestException as e:
                            if api_attempt >= orchestrator.max_retries:
                                log_clarification("debug")
                                logger.warning(f"{qt} {f'{query_value}' if query_value == None else ''}, Page {page}: Failed after {api_attempt} retries: {e}")
                                resp = None
                                if use_tor:
                                    wait = dynamic_sleep("api", attempt=api_attempt) * 2
                                    logger.warning(f"{qt}{query_str}, Page {page}: Retrying with new Tor node in {wait:.2f}s")
                                    time.sleep(wait)
                                    gallery_ids_session = Get.session(referrer="API", status="rebuild")
                                    try:
                                        resp = gallery_ids_session.get(url, timeout=(60, 60))
                                        resp.raise_for_status()
                                    except Exception as e2:
                                        logger.warning(f"{qt}{query_str}, Page {page}: Still failed after Tor rotate: {e2}")
                                        resp = None
                                break
                            wait = dynamic_sleep("api", attempt=api_attempt)
                            logger.warning(f"{qt}{query_str}, Page {page}: Attempt {api_attempt}: Request failed: {e}, retrying in {wait:.2f}s")
                            time.sleep(wait)
                    if resp is None:
                        page += 1
                        continue
                    try:
                        data = resp.json()
                    except Exception as e:
                        logger.warning(f"{qt}{query_str}, Page {page}: Failed to decode JSON: {e}")
                        break
                    results = data.get("result", [])
                    batch = []
                    excluded_gallery_tags = [tag.lower() for tag in orchestrator.excluded_tags]
                    allowed_gallery_language = [lang.lower() for lang in orchestrator.language]
                    for g in results:
                        gallery_tags = [
                            t["name"].lower()
                            for t in g.get("tags", [])
                            if t.get("type") == "tag"
                        ]
                        gallery_langs = [
                            t["name"].lower()
                            for t in g.get("tags", [])
                            if t.get("type") == "language"
                        ]
                        blocked_tags = [t for t in gallery_tags if t in excluded_gallery_tags]
                        if blocked_tags:
                            log(f"Skipping Gallery {g['id']} due to excluded tags: {blocked_tags}", "debug")
                            continue
                        if allowed_gallery_language:
                            has_allowed = any(lang in allowed_gallery_language for lang in gallery_langs)
                            has_translated = "translated" in gallery_langs
                            allow_translated = "translated" in allowed_gallery_language
                            if not (has_allowed or (has_translated and allow_translated)):
                                blocked_langs = gallery_langs[:]
                                log(f"Skipping Gallery {g['id']} due to blocked languages: {blocked_langs}", "debug")
                                continue
                        batch.append(int(g["id"]))
                        images = g.get("images", {})
                        num_pages = len(images.get("pages", []))
                        orchestrator.total_gallery_images += num_pages
                    log(f"Fetcher: {qt}{query_str}, Page {page}: Fetched {len(batch)} Gallery IDs", "info")
                    log(f"Current Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")
                    if not results:
                        logger.info(f"Fetcher: {qt}{query_str}, Page {page}: No more results from NHentai, stopping.")
                        break
                    if not batch:
                        logger.debug(f"Fetcher: {qt}{query_str}, Page {page}: All galleries filtered out, continuing to next page.")
                        page += 1
                        continue
                    ids_set.update(batch)
                    page += 1
                log(f"Fetched total {len(ids_set)} Galleries for {qt}{query_str}", "warning")
                log(f"Overall Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")
                ids = list(ids_set)
                return (cache_key, ids)
            except Exception as e:
                attempt += 1
                logger.error(f"Error fetching galleries (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    logger.info("Retrying...")
                    time.sleep(2)
                    continue
                logger.warning(f"Failed to fetch galleries for {query_type}={query_value}. Skipping.")
                return (cache_key, [])
        return (cache_key, ids)

    ################################################################################################################
    # IMAGE URL FETCHING
    ################################################################################################################
    
    @staticmethod
    def image_urls(meta: dict, page: int):
        """
        Returns the full image URL for a gallery page.
        Tries mirrors from NHENTAI_MIRRORS in order until one succeeds.
        Handles missing metadata, unknown types, and defaulting to webp.
        """
        
        orchestrator.refresh_globals()
        
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

    ################################################################################################################
    # GALLERY METADATA FETCHING
    ################################################################################################################
    
    @staticmethod
    def gallery_metadata(gallery_id: int):
        orchestrator.refresh_globals()

        raw_cache = Caching.Load.cached_metadata()
        cached_meta = raw_cache.get(str(gallery_id))
        if cached_meta and isinstance(cached_meta, dict):
            return cached_meta

        metadata_session = Get.session(referrer="API", status="return")
        
        url = f"{nhentai_api_base}/gallery/{gallery_id}"
        for attempt in range(1, orchestrator.max_retries + 1):
            try:
                log_clarification("debug")
                log(f"Fetcher: Fetching metadata for Gallery: {gallery_id}, URL: {url}", "debug")

                resp = metadata_session.get(url, timeout=(60, 60))
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
                
                # Update cache
                cached_entry = Caching.Save.cache(data, gallery_id)
                if cached_entry:
                    general_metadata = Caching.Load.cached_metadata(clean=True)
                    general_metadata[gallery_id] = cached_entry
                    Caching.Save.cached_metadata(general_metadata, clean=True)
                raw_cache = Caching.Load.cached_metadata()
                raw_cache[str(gallery_id)] = data
                Caching.Save.cached_metadata(raw_cache)

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
                        metadata_session = Get.session(referrer="API", status="rebuild")
                        try:
                            resp = metadata_session.get(url, timeout=(60, 60))
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
                        metadata_session = Get.session(referrer="API", status="rebuild")
                        try:
                            resp = metadata_session.get(url, timeout=(60, 60))
                            resp.raise_for_status()
                            return resp.json()
                        except Exception as e2:
                            logger.warning(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}")
                    return None
                wait = dynamic_sleep("api", attempt=(attempt))
                logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s")
                time.sleep(wait)

    ################################################################################################################
    # BATCH METADATA FETCHING (for pre-filtering)
    ################################################################################################################
    
    @staticmethod
    def fetch_metadata_batch(gallery_ids: list) -> dict:
        """
        Fetch metadata for multiple galleries efficiently using threading.
        Used for pre-fetching before filtering/sizing.
        
        Returns:
            dict: {gallery_id: metadata_dict}
        """
        
        if not gallery_ids:
            return {}

        # Ensure all gallery IDs are integers
        gallery_ids = [int(gid) for gid in gallery_ids]

        metadata = {}
        failed_ids = []

        # Use thread pool for parallel fetching
        max_workers = min(10, len(gallery_ids))  # Cap at 10 parallel requests

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(Fetch.gallery_metadata, gid): gid for gid in gallery_ids}

            # Use tqdm for progress
            for future in tqdm(as_completed(futures), total=len(futures), desc="Fetching metadata", unit="gallery"):
                gallery_id = futures[future]
                try:
                    meta = future.result()
                    if meta and isinstance(meta, dict):
                        metadata[gallery_id] = meta
                except Exception as e:
                    logger.debug(f"Failed to fetch metadata for Gallery {gallery_id}: {e}")
                    failed_ids.append(gallery_id)

        if failed_ids:
            logger.warning(f"Failed to fetch metadata for {len(failed_ids)}/{len(gallery_ids)} galleries")

        return metadata

    def all_galleries_metadata(gallery_ids: list, cache_key: str = None) -> dict:
        """
        Fetch metadata for all galleries with caching support.
        Uses caching to avoid repeated API calls for the same search criteria.
        Extracts relevant fields (title, artists, tags, etc.) for display/filtering.
        
        Args:
            gallery_ids: List of gallery IDs to fetch
            cache_key: Optional cache key for storing results by search criteria (e.g., "artist_john")
        
        Returns:
            dict: {gallery_id: metadata_dict with title, artists, tags, language, pages}
        """
        
        if not gallery_ids:
            return {}

        # Ensure all gallery IDs are integers and deduplicate
        normalised_ids = []
        for gid in gallery_ids:
            try:
                gid_int = int(gid)
            except (TypeError, ValueError):
                logger.warning(f"Skipping gallery with invalid ID: {gid}")
                continue
            normalised_ids.append(gid_int)
        gallery_ids = list(dict.fromkeys(normalised_ids))
        
        # Try loading from cache first if cache_key provided
        cached_metadata = {}
        ids_to_fetch = []

        def _is_complete_cached_meta(meta: dict) -> bool:
            if not isinstance(meta, dict):
                return False
            required_keys = {"title", "artists", "tags", "languages", "pages"}
            return required_keys.issubset(meta.keys())

        if cache_key:
            cached_metadata = Caching.Load.cache(cache_key)
        else:
            cached_metadata = Caching.Load.id_metadata(gallery_ids)

        if cached_metadata:
            normalised_cached = {}
            for gid, meta in cached_metadata.items():
                try:
                    gid_int = int(gid)
                except (TypeError, ValueError):
                    continue
                normalised_cached[gid_int] = meta
            cached_metadata = normalised_cached

        if cached_metadata:
            incomplete = [gid for gid, meta in cached_metadata.items() if not _is_complete_cached_meta(meta)]
            if incomplete:
                logger.debug(f"Dropping {len(incomplete)} cached galleries due to incomplete metadata")
            for gid in incomplete:
                cached_metadata.pop(gid, None)
            if incomplete:
                logger.info(f"Refreshing {len(incomplete)} cached galleries with incomplete metadata")

        ids_to_fetch = [gid for gid in gallery_ids if gid not in cached_metadata]
        if cached_metadata:
            logger.info(f"Using {len(cached_metadata)} galleries from cache")
        
        # If all galleries are cached, return immediately
        if not ids_to_fetch:
            logger.info(f"All {len(cached_metadata)} galleries loaded from cache")
            return cached_metadata
        
        logger.info(f"Fetching metadata for {len(ids_to_fetch)} new galleries...")
        if cache_key:
            logger.info(f"({len(cached_metadata)} Cached galleries, fetching {len(ids_to_fetch)} new)")
        log_clarification()
        
        metadata = dict(cached_metadata)  # Start with cached results
        failed_ids = []
        
        # Fetch missing galleries with progress bar
        for gallery_id in tqdm(ids_to_fetch, desc="Fetching gallery metadata", unit="gallery"):
            try:
                meta = Fetch.gallery_metadata(gallery_id)
                if meta and isinstance(meta, dict):
                    meta_entry = Caching.Save.cache(meta, gallery_id)
                    if meta_entry:
                        metadata[gallery_id] = meta_entry
                else:
                    failed_ids.append(gallery_id)
            except Exception as e:
                logger.debug(f"Failed to fetch metadata for Gallery {gallery_id}: {e}")
                failed_ids.append(gallery_id)
        
        if failed_ids:
            logger.warning(f"Failed to fetch metadata for {len(failed_ids)} galleries (they will be skipped)")
        
        logger.debug(f"[TESTING]: all_galleries_metadata cache_key = {cache_key}")
        
        # Save to cache
        if metadata and cache_key:
            Caching.Save.cache(cache_key, gallery_ids)
        elif metadata:
            general_metadata = Caching.Load.cached_metadata(clean=True)
            general_metadata.update(metadata)
            Caching.Save.cached_metadata(general_metadata, clean=True)
        
        return metadata

class Caching:
    @staticmethod
    def clear_cache(cache_key: str = None):
        """Clear / prune the cache"""
        # Prune references to cache files by their own TTL
        scraperdb.prune_all_caches()
    
    @staticmethod
    def _read_cache() -> dict:
        cutoff = time.time() - scraperdb.TTL
        try:
            cache_entry = scraperdb.read_cached_metadata_entry(cutoff=cutoff)
            
            # Remove expired entries from cache_entry (in-memory prune)
            if isinstance(cache_entry, dict):
                expired_keys = []
                for gid, entry in cache_entry.items():
                    if not isinstance(entry, dict):
                        continue
                    timestamp = entry.get("timestamp") or 0
                    if (time.time() - timestamp) >= scraperdb.TTL:
                        expired_keys.append(gid)
                for gid in expired_keys:
                    cache_entry.pop(gid, None)
            references = scraperdb.read_cached_metadata_entry()
            logger.debug(f"[TESTING]: cache_entry = {cache_entry}, references = {references}")
            return {"references": references, "metadata": cache_entry}
        except Exception:
            return {"references": {}, "metadata": {}}
    
    class Load:
        @staticmethod
        def cache(cache_key: str = None, gallery_id: int = None):
            """
            Load from cache by either cache_key or gallery_id.
            - If cache_key is provided, return the list of IDs for that key (from CacheReferences).
            - If gallery_id is provided, return the clean metadata for that ID (from CachedMetadata).
            - If no arguments are provided, return all cache references as a dict.
            """
            
            # Return the list of Gallery IDs for a specific cache_key
            if cache_key is not None:
                references_entry = scraperdb.read_cached_metadata_entry(cache_key=cache_key)
                if not references_entry or not isinstance(references_entry, dict):
                    return []
                ids = references_entry.get("ids")
                if not ids or not isinstance(ids, list):
                    return []
                normalised_ids = []
                for gid in ids:
                    try:
                        normalised_ids.append(int(gid))
                    except (TypeError, ValueError):
                        continue
                return normalised_ids
            
            # Return clean metadata for a specific Gallery ID
            elif gallery_id is not None:
                cache_entry = scraperdb.read_cached_metadata_entry(gallery_id=gallery_id)
                if not cache_entry:
                    return None
                return cache_entry.get("clean_metadata")
            
            # Return all cache references as a dict
            else:
                return scraperdb.read_cached_metadata_entry()
        
        @staticmethod
        def cached_metadata(clean: bool = False) -> dict:
            """
            Returns cached metadata for all galleries.
            By default returns raw metadata. If clean=True, returns clean metadata.
            """
            
            data = Caching._read_cache()
            metadata_block = data.get("metadata", {})
            if not isinstance(metadata_block, dict):
                return {}
            result = {}
            
            for gid, entry in metadata_block.items():
                if not isinstance(entry, dict):
                    continue
                
                timestamp = entry.get("timestamp") or 0
                if (time.time() - timestamp) >= scraperdb.TTL:
                    continue
                
                if clean:
                    clean_meta = entry.get("clean_metadata")
                    if isinstance(clean_meta, dict):
                        result[gid] = clean_meta
                
                else:
                    if "raw_metadata" in entry:
                        result[gid] = entry.get("raw_metadata")
            
            return result
        
        @staticmethod
        def broken_symbols() -> dict[str, str]:
            """Load all detected broken symbols as { symbol: '_' }."""
            scraperdb.init_db()
            with scraperdb.lock, scraperdb.dbconnect() as conn:
                c = conn.cursor()
                c.execute("SELECT symbol FROM BrokenSymbols WHERE fixed=0")
                rows = c.fetchall()
                return {row[0]: "_" for row in rows if row[0].strip()}
        
        @staticmethod
        def queued_galleries() -> list:
            """Fetch queued galleries from GalleriesQueue table in the database."""
            scraperdb.init_db()
            with scraperdb.lock, scraperdb.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM GalleriesQueue")
                rows = cursor.fetchall()
                return sorted({int(row[0]) for row in rows})

        @staticmethod
        def all_metadata() -> dict:
            """Load all cached metadata entries from the CachedMetadata table."""
            # Just return all clean_metadata from CachedMetadata
            all_entries = scraperdb.Caching.Load.cache()
            merged = {}
            for gid, entry in all_entries.items():
                clean = entry.get("clean_metadata")
                if isinstance(clean, dict):
                    merged[gid] = clean
            return merged

        @staticmethod
        def id_metadata(ids: list[int]) -> dict:
            """Load each metadata entry from the CachedMetadata table corresponding to the IDs in a given list."""
            if not ids:
                return {}
            # Fetch metadata for all IDs from CachedMetadata
            meta_dict = scraperdb.read_cached_metadata_entry(ids=ids)
            result = {}
            for gid, entry in meta_dict.items():
                clean = entry.get("clean_metadata")
                if isinstance(clean, dict):
                    result[gid] = clean
            return result

    class Save:
        @staticmethod
        def cache(cache_key: str = None, gallery_ids: list = None, meta: dict = None):
            """
            Canonical function for updating CachedMetadata and/or CacheReferences.
            - If only meta is provided, updates CachedMetadata for the ID extracted from meta.
            - If cache_key and gallery_ids are provided, updates CacheReferences for that key with the given IDs.
            - If all are provided, updates both.
            Returns the clean metadata entry if metadata is updated, else None.
            """
            now = time.time()
            entry = None

            # If meta is provided, extract gallery_id from meta
            if meta is not None:
                gid = meta.get("id")
                if gid is not None:
                    gid = int(gid)
                    entry = {
                        "id": gid,
                        "title": meta.get("title", {}).get("english", f"Gallery {gid}"),
                        "artists": Get.artists(meta),
                        "groups": Get.groups(meta),
                        "tags": Get.tags(meta),
                        "characters": Get.characters(meta),
                        "parodies": Get.parodies(meta),
                        "languages": Get.languages(meta),
                        "pages": Get.page_count(meta),
                    }
                    scraperdb.upsert_cached_metadata(
                        gallery_id=gid,
                        timestamp=now,
                        clean_metadata=entry,
                        raw_metadata=meta,
                    )
            
            # If cache_key and gallery_ids are provided, update CacheReferences
            if cache_key and gallery_ids is not None:
                cache_type, cache_target = scraperdb.split_cache_key(cache_key)
                ids = []
                for gid in gallery_ids:
                    try:
                        ids.append(int(gid))
                    except (TypeError, ValueError):
                        continue
                ids = list(sorted(set(ids)))
                ttl_default = 10800
                expires_at = now + ttl_default
                entry_ref = {
                    "cache_key": cache_key,
                    "cache_type": cache_type,
                    "cache_target": cache_target,
                    "ids": ids,
                    "ttl": ttl_default,
                    "expires_at": expires_at,
                }
                scraperdb.upsert_cache_reference(cache_key, entry_ref)
            
            return entry
        
        @staticmethod
        def cached_metadata(metadata: dict, clean: bool = False):
            """
            Updates cached metadata for all galleries.
            If clean=True, updates clean_metadata. Otherwise, updates raw_metadata.
            """
            
            now = time.time()
            if not isinstance(metadata, dict):
                return
            
            for gid, entry in metadata.items():
                if not isinstance(entry, dict):
                    continue
                
                if clean:
                    scraperdb.upsert_cached_metadata(
                        str(gid),
                        now,
                        clean_metadata=entry,
                        raw_metadata=None,
                    )
                
                else:
                    scraperdb.upsert_cached_metadata(
                        str(gid),
                        now,
                        clean_metadata=None,
                        raw_metadata=entry,
                    )
        
        @staticmethod
        def broken_symbols(symbol_map: dict[str, str]):
            """Insert or update broken symbols into the database, keeping the mapping (symbol -> replacement)."""
            if not symbol_map:
                return
            scraperdb.init_db()
            now = datetime.now(timezone.utc).isoformat()
            with scraperdb.lock, scraperdb.dbconnect() as conn:
                c = conn.cursor()
                for symbol in symbol_map.keys():
                    c.execute("""
                        INSERT INTO BrokenSymbols (symbol, date_detected, fixed)
                        VALUES (?, ?, 0)
                        ON CONFLICT(symbol) DO UPDATE SET
                            fixed=0,
                            date_detected=excluded.date_detected
                    """, (symbol, now))
                conn.commit()

################################################################################################################
# EXPORTED API
################################################################################################################

cache = Caching()
get = Get()
fetch = Fetch()