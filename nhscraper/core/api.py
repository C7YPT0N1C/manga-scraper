#!/usr/bin/env python3
# nhscraper/core/api.py
import os, sys, time, random, argparse, re, subprocess, urllib.parse # 'Default' imports

import threading, asyncio, aiohttp, aiohttp_socks, socket, cloudscraper, json # Module-specific imports

from aiohttp_socks import ProxyConnector
from flask import Flask, jsonify, request
from datetime import datetime
from urllib.parse import urljoin
from pathlib import Path

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *

"""
Programmatic API for interacting with the downloader.
Provides functions for metadata fetching, downloads,
and integration with other modules.
"""

################################################################################################################
# GLOBAL VARIABLES
################################################################################################################

_module_referrer=f"API" # Used in executor.* calls

# This lock guards access to the broken symbols file on disk.
possible_broken_symbols_lock = threading.Lock()

# ===============================
# SCRAPER API (Flask)
# ===============================
app = Flask(__name__)
last_gallery_id = None
running_galleries = []
gallery_metadata = {}  # global state for /status/galleries, key=gallery_id, value={'meta': {...}, 'status': ..., 'last_checked': ...}
state_lock = threading.Lock()

################################################################################################################
# HTTP SESSION (cloudscraper wrapped for async)
################################################################################################################

# Global cloudscraper session (synchronous object). We will call its methods via executor.run_blocking() (Is this even right bruh)
session = None
# Use an asyncio.Lock here because session creation is now async-aware.
session_lock = asyncio.Lock()

async def get_session(referrer = None, status: str = "rebuild", backend: str = "cloudscraper"):
    """
    Ensure and return a ready session.

    Parameters:
        referrer: str
            Label for logging where this session is being requested from.
        status: str
            - "build" → create a new session if none exists.
            - "rebuild" → force rebuild the session.
            - "none" → return the current session without creating/rebuilding.
        backend: str
            - "cloudscraper" → use cloudscraper session (existing behavior)
            - "aiohttp" → use aiohttp session (new backend)

    Notes on executor usage:
        - Blocking operations (e.g., `cloudscraper.create_scraper`) are executed
          via `executor.call_appropriately(func(*args), referrer=_module_referrer)` to avoid blocking the event loop.
        - execute-and-forget operations (e.g., updating session headers) are executed
          via `executor.call_appropriately(func(*args), referrer=_module_referrer)`.

    Returns:
        The ready session object.
    """
    
    log_clarification("debug")
    log("Fetcher: Debugging Started.", "debug")

    global session
    fetch_env_vars()  # Refresh env vars in case config changed.
    
    if referrer is None:
    # Try module-level _module_referrer variable first
       referrer = globals().get("_module_referrer", __name__) or DEFAULT_REFERRER
    
    # Log intent
    if status == "none":
        log(f"{referrer}: Requesting to only retrieve session.", "debug")
    else:
        log(f"{referrer}: Requesting to {status} session.", "debug")

    async with session_lock:
        # Return current session if no build/rebuild requested
        if status not in ["build", "rebuild"]:
            return session

        # Log if building or rebuilding
        log_msg = "Rebuilding" if status == "rebuild" else "Building"

        # Random browser profiles
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

        # Create or rebuild session if needed (blocking)
        if session is None or status == "rebuild":
            if backend == "aiohttp": # aiohttp / aiohttp_socks session for get_tor_ip()
                log(f"{log_msg} HTTP session with aiohttp for {referrer}", "debug")
                connector = None
                if use_tor:
                    # Use aiohttp_socks for Tor
                    connector = ProxyConnector.from_url("socks5h://127.0.0.1:9050")
                
                session = aiohttp.ClientSession(connector=connector)
            
            else:  # Default to cloudscraper session
                log(f"{log_msg} HTTP session with cloudscraper for {referrer}", "debug")
                session = executor.run_blocking(
                    cloudscraper.create_scraper(browser_profile),
                    referrer=_module_referrer
                )

        # Random User-Agents
        DefaultUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        RandomiseUserAgent = True
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1",
        ]
        ua = random.choice(user_agents) if RandomiseUserAgent else DefaultUserAgent

        # Random Referers
        DefaultReferer = "https://nhentai.net/"
        RandomiseReferer = False
        referers = [
            "https://nhentai.net/",
            "https://google.com/",
            "https://duckduckgo.com/",
            "https://bing.com/",
        ]
        referer = random.choice(referers) if RandomiseReferer else DefaultReferer

        # Update headers (execute-and-forget)
        def _update_session_headers(s, headers):
            if backend == "aiohttp":
                s._default_headers.update(headers)
            else:
                s.headers.update(headers)

        headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
        }
        executor.call_appropriately(
            _update_session_headers(session, headers),
            referrer=_module_referrer
        )

        # Update proxies (blocking; need result immediately)
        def _update_proxies(s, use_tor_flag):
            if backend == "cloudscraper":
                if use_tor_flag:
                    proxy = "socks5h://127.0.0.1:9050"
                    s.proxies = {"http": proxy, "https": proxy}
                    return proxy
                else:
                    s.proxies = {}
                    return None
            return None # aiohttp proxies handled via connector

        proxy_used = executor.call_appropriately(
            _update_proxies(session, use_tor),
            referrer=_module_referrer
        )
        if proxy_used:
            log(f"Using Tor proxy: {proxy_used}", "info")
        else:
            log("Not using Tor proxy", "info")

        # Log completion
        log(f"{log_msg} HTTP session completed.", "debug")

        return session

################################################################################################################
# METADATA CLEANING
################################################################################################################

def get_meta_tags(meta, tag_type, referrer = None):
    """
    Extract all tag names of a given type (artist, group, parody, language, etc.).
    - Splits names on "|".
    - Returns [] if none found.
    """
    
    if referrer is None:
    # Try module-level _module_referrer variable first
       referrer = globals().get("_module_referrer", __name__) or DEFAULT_REFERRER
    
    if not meta or "tags" not in meta:
        return []

    names = []
    for tag in meta["tags"]:
        if tag.get("type") == tag_type and tag.get("name"):
            parts = [t.strip() for t in tag["name"].split("|") if t.strip()]
            names.extend(parts)

    return names

def make_filesystem_safe(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

async def clean_title(meta_or_title):
    """
    Clean a gallery/manga title. Accepts either a meta dict or a raw string.
    This is async because it performs file IO for the broken symbols mapping (which runs
    in threads internally to avoid blocking the event loop).
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.

    # Ensure global broken symbols file path is set
    broken_symbols_file = os.path.join(orchestrator.extension_download_path, "possible_broken_symbols.json")

    # Helpers (synchronous file IO) run in threadpool to avoid blocking event loop
    def _load_file(path):
        with possible_broken_symbols_lock:
            if not os.path.exists(path):
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump({}, f, ensure_ascii=False, indent=2)
                    log(f"Created new broken symbols file: {path}", "debug")
                except Exception as e:
                    log(f"Could not create broken symbols file: {e}", "error")
                return {}
            
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        # Attempt minimal recovery by truncating after last closing brace
                        if "}" in content:
                            fixed = content[: content.rfind("}") + 1]
                            try:
                                return json.loads(fixed)
                            except Exception:
                                pass
            
            except Exception as e:
                log(f"Could not load broken symbols file: {e}", "warning")
            return {}

    def _save_file(path, symbols):
        with possible_broken_symbols_lock:
            try:
                cleaned = {s: "_" for s in symbols if s.strip()}
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cleaned, f, ensure_ascii=False, indent=2)
            
            except Exception as e:
                log(f"Could not save broken symbols: {e}", "error")

    def is_cjk(char: str) -> bool:
        code = ord(char)
        return (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x20000 <= code <= 0x2A6DF
            or 0x2A700 <= code <= 0x2B73F
            or 0x2B740 <= code <= 0x2B81F
            or 0x2B820 <= code <= 0x2CEAF
            or 0x2CEB0 <= code <= 0x2EBEF
            or 0x3000 <= code <= 0x303F
            or 0x3040 <= code <= 0x309F
            or 0x30A0 <= code <= 0x30FF
            or 0x31F0 <= code <= 0x31FF
            or 0xFF65 <= code <= 0xFF9F
        )

    # Load persisted broken symbols mapping via thread
    possible_broken_symbols = executor.call_appropriately(
        _load_file(broken_symbols_file),
        referrer=_module_referrer
    )

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

    # Detect non-ASCII symbols in the current title that aren't CJK or allowed
    symbols = {c for c in title if ord(c) > 127 and not is_cjk(c)}
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
        log(f"New broken symbols detected in '{title}': {new_broken}", "info")

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

    # Persist updated broken symbols mapping via thread
    executor.call_appropriately(
        _save_file(broken_symbols_file, possible_broken_symbols),
        referrer=_module_referrer
    )

    return make_filesystem_safe(title)

################################################################################################################
#  NHentai API Handling (async wrappers for blocking cloudscraper calls)
################################################################################################################

# ===============================
# BUILD API URLS
# ===============================
def build_url(query_type: str, query_value: str, sort_value: str, page: int) -> str:
    fetch_env_vars() # Refresh env vars in case config changed.
    query_lower = query_type.lower()

    if query_lower == "homepage":
        if sort_value == "date":
            built_url = f"{nhentai_api_base}/galleries/all?page={page}"
        else:
            built_url = f"{nhentai_api_base}/galleries/all?page={page}&sort={sort_value}"
        return built_url

    if query_lower in ("artist", "group", "tag", "character", "parody"):
        search_value = query_value
        if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
            search_value = f'"{search_value}"'
        encoded = urllib.parse.quote(f"{query_type}:{search_value}", safe=':"')
        if sort_value == "date":
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}"
        else:
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"
        return built_url

    if query_lower == "search":
        search_value = query_value
        if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
            search_value = f'"{search_value}"'
        encoded = urllib.parse.quote(search_value, safe='"')
        if sort_value == "date":
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}"
        else:
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"
        return built_url

    raise ValueError(f"Unknown query format: {query_type}='{query_value}'")

# ===============================
# FETCH GALLERY IDS (async)
# ===============================
async def fetch_gallery_ids(query_type: str, query_value: str, sort_value: str = DEFAULT_PAGE_SORT, start_page: int = 1, end_page: int | None = None) -> set[int]:
    """
    Fetch gallery IDs from NHentai asynchronously, but using the synchronous cloudscraper session executed in threads.
    """
    fetch_env_vars() # Refresh env vars in case config changed.

    ids: set[int] = set()
    page = start_page

    gallery_ids_session = await get_session(referrer=_module_referrer)

    try:
        log_clarification("debug")
        if query_value is None:
            log(f"Fetching gallery IDs from NHentai Homepages {start_page} → {end_page or '∞'}", "debug")
        else:
            log(f"Fetching gallery IDs for query '{query_value}' (pages {start_page} → {end_page or '∞'})", "debug")

        while True:
            if end_page is not None and page > end_page:
                break

            url = build_url(query_type, query_value, sort_value, page)
            log(f"Fetcher: Requesting URL: {url}", "debug")

            resp = None
            for attempt in range(1, orchestrator.max_retries + 1):
                try:
                    # Execute async request in thread (capped by number of gallery threads)
                    resp = executor.call_appropriately(
                        gallery_ids_session.get(url, timeout=10),
                        referrer=_module_referrer
                    )
                    
                    status_code = getattr(resp, "status_code", None)
                    if status_code == 429:
                        wait = dynamic_sleep("api", attempt=(attempt), perform_sleep=False)
                        log(f"Query '{query_value}': Attempt {attempt}: 429 rate limit hit, waiting {wait}s", "warning")
                        executor.thread_sleep(wait, referrer=_module_referrer)
                        continue
                    if status_code == 403:
                        dynamic_sleep("api", attempt=(attempt))
                        continue

                    # Raise for status is synchronous
                    try:
                        resp.raise_for_status()
                    except Exception as e:
                        raise

                    break

                except Exception as e:
                    if attempt >= orchestrator.max_retries:
                        log_clarification("debug")
                        log(f"Page {page}: Skipped after {attempt} retries: {e}", "warning")
                        resp = None
                        # Rebuild session with Tor and try again once
                        if use_tor:
                            wait = dynamic_sleep("api", attempt=(attempt), perform_sleep=False) * 2
                            log(f"Query '{query_value}', Page {page}: Attempt {attempt}: Request failed: {e}, retrying with new Tor Node in {wait:.2f}s", "warning")
                            executor.thread_sleep(wait, referrer=_module_referrer)
                            gallery_ids_session = await get_session(referrer=_module_referrer, status="rebuild")
                            
                            # Execute async request in thread (capped by number of gallery threads)
                            try:
                                resp = executor.call_appropriately(
                                    gallery_ids_session.get(url, timeout=10),
                                    referrer=_module_referrer
                                )
                                resp.raise_for_status()
                            except Exception as e2:
                                log(f"Page {page}: Still failed after Tor rotate: {e2}", "warning")
                                resp = None
                        break

                    wait = dynamic_sleep("api", attempt=(attempt), perform_sleep=False)
                    log(f"Query '{query_value}', Page {page}: Attempt {attempt}: Request failed: {e}, retrying in {wait:.2f}s", "warning")
                    executor.thread_sleep(wait, referrer=_module_referrer)

            if resp is None:
                page += 1
                continue  # skip this page if no success

            # resp.json() is synchronous and potentially blocking; run / parse in thread
            data = executor.call_appropriately(
                lambda: resp.json(),
                referrer=_module_referrer
            )
            log(f"Fetcher: HTTP Response JSON: {data}", "debug")
            batch = [g["id"] for g in data.get("result", [])]
            log(f"Fetcher: Page {page}: Fetched {len(batch)} gallery IDs", "debug")

            if not batch:
                log(f"Page {page}: No results, stopping early", "info")
                break

            ids.update(batch)
            page += 1

        log(f"Fetched total {len(ids)} galleries for query '{query_value}'", "info")
        return ids

    except Exception as e:
        log(f"Failed to fetch galleries for query '{query_value}': {e}", "warning")
        return set()

# ===============================
# FETCH IMAGE URLS (sync - builds URLs)
# ===============================
def fetch_image_urls(meta: dict, page: int):
    """
    Returns the full image URL list for a gallery page. No network I/O — kept synchronous.
    """
    fetch_env_vars() # Refresh env vars in case config changed.

    try:
        pages = meta.get("images", {}).get("pages", [])
        if page - 1 >= len(pages):
            log(f"Gallery {meta.get('id','?')}: Page {page}: Not in metadata", "warning")
            return None

        page_info = pages[page - 1]
        if not page_info:
            log(f"Gallery {meta.get('id','?')}: Page {page}: Metadata is None", "warning")
            return None

        ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
        type_code = page_info.get("t", "w")
        if type_code not in ext_map:
            log_clarification()
            log(f"Unknown image type '{type_code}' for Gallery {meta.get('id','?')}: Page {page}: Defaulting to webp", "warning")

        ext = ext_map.get(type_code, "webp")
        filename = f"{page}.{ext}"

        urls = [
            f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
            for mirror in orchestrator.nhentai_mirrors
        ]

        log(f"Fetcher: Built image URLs for Gallery {meta.get('id','?')}: Page {page}: {urls}", "debug")
        return urls

    except Exception as e:
        log(f"Failed to build image URL for Gallery {meta.get('id','?')}: Page {page}: {e}", "warning")
        return None

# ===============================
# FETCH GALLERY METADATA (async)
# ===============================
async def fetch_gallery_metadata(gallery_id: int):
    """
    Fetch gallery metadata using the synchronous cloudscraper session executed in a thread.
    Returns dict or None.
    """
    fetch_env_vars() # Refresh env vars in case config changed.

    metadata_session = await get_session(referrer=_module_referrer, status="return")

    url = f"{nhentai_api_base}/gallery/{gallery_id}"
    for attempt in range(1, orchestrator.max_retries + 1):
        try:
            log_clarification("debug")
            log(f"Fetcher: Fetching metadata for Gallery: {gallery_id}, URL: {url}", "debug")

            # Execute async request in thread (capped by number of gallery threads)
            resp = executor.call_appropriately(
                metadata_session.get(url, timeout=10),
                referrer=_module_referrer
            )
            status_code = getattr(resp, "status_code", None)

            if status_code == 429:
                wait = dynamic_sleep("api", attempt=(attempt), perform_sleep=False)
                log(f"Gallery: {gallery_id}: Attempt {attempt}: 429 rate limit hit, waiting {wait}s", "warning")
                executor.thread_sleep(wait, referrer=_module_referrer)
                continue
            if status_code == 403:
                dynamic_sleep("api", attempt=(attempt))
                continue

            # Raise for status (blocking) — run in thread
            try:
                executor.call_appropriately(
                    resp.raise_for_status,
                    referrer=_module_referrer
                )
            
            except Exception as e:
                raise

            # resp.json() is synchronous and potentially blocking; run / parse in thread
            data = executor.call_appropriately(
                lambda: resp.json(),
                referrer=_module_referrer
            )

            if not isinstance(data, dict):
                log(f"Unexpected response type for Gallery: {gallery_id}: {type(data)}", "error")
                return None

            log_clarification("debug")
            log(f"Fetcher: Fetched metadata for Gallery: {gallery_id}", "debug")
            return data

        except Exception as e:
            # Distinguish HTTPError-like messages when possible but be conservative
            if attempt >= orchestrator.max_retries:
                log(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}", "warning")
                # Rebuild session with Tor and try again once
                if use_tor:
                    wait = dynamic_sleep("api", attempt=(attempt), perform_sleep=False) * 2
                    log(f"Gallery: {gallery_id}: Attempt {attempt}: Metadata fetch failed: {e}, retrying with new Tor Node in {wait:.2f}s", "warning")
                    executor.thread_sleep(wait, referrer=_module_referrer)
                    metadata_session = await get_session(referrer=_module_referrer, status="rebuild")
                    
                    # Execute async request in thread (capped by number of gallery threads)
                    try:
                        resp = executor.call_appropriately(
                            metadata_session.get(url, timeout=10),
                            referrer=_module_referrer
                        )
                        
                        executor.call_appropriately(
                            resp.raise_for_status,
                            referrer=_module_referrer
                        )
                        return executor.call_appropriately(
                            lambda: resp.json(),
                            referrer=_module_referrer
                        )
                    
                    except Exception as e2:
                        log(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}", "warning")
                return None

            wait = dynamic_sleep("api", attempt=(attempt), perform_sleep=False)
            log(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s", "warning")
            executor.thread_sleep(wait, referrer=_module_referrer)

##################################################################################################################################
# API STATE HELPERS (sync - used by Flask endpoints)
##################################################################################################################################

async def get_tor_ip(backend: str = "aiohttp") -> str | None:
    """
    Fetch current IP, through Tor if enabled.
    backend options:
      - "aiohttp": use aiohttp + aiohttp_socks (default)
      - "cloudscraper": use cloudscraper (blocking in executor)
    """
    url = "https://httpbin.org/ip"
    timeout = 10  # seconds

    if backend == "aiohttp":
        try:
            if use_tor:
                connector = ProxyConnector.from_url("socks5h://127.0.0.1:9050")
                async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                    async with session.get(url) as r:
                        r.raise_for_status()
                        data = await r.json()
            else:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                    async with session.get(url) as r:
                        r.raise_for_status()
                        data = await r.json()
            return data.get("origin")
        except Exception as e:
            return f"Error: {e}"

    else: # backend == "cloudscraper"
        def _cloudscraper_check():
            scraper = cloudscraper.create_scraper()
            proxies = {"http": "socks5h://127.0.0.1:9050",
                       "https": "socks5h://127.0.0.1:9050"} if use_tor else None
            r = scraper.get(url, timeout=timeout, proxies=proxies)
            r.raise_for_status()
            return r.json().get("origin")

        try:
            return await executor.run_in_executor(None, _cloudscraper_check)
        except Exception as e:
            return f"Error: {e}"

def get_last_gallery_id():
    with state_lock:
        return last_gallery_id

def get_running_galleries():
    with state_lock:
        return list(running_galleries)

def update_gallery_state(gallery_id: int, stage="download", success=True):
    """
    Unified function to update gallery state per stage.
    This function is synchronous and uses threading.Lock to protect shared state.
    """
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
                if entry["download_attempts"] >= orchestrator.max_retries:
                    log(f"Gallery {gallery_id}: Download failed after {orchestrator.max_retries} attempts", "error")

        entry["last_checked"] = datetime.now().isoformat()
        log(f"Gallery {gallery_id}: {stage} stage updated: {'success' if success else 'failure'}", "info")

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
        "tor_ip": executor.call_appropriately(
            get_tor_ip,
            referrer=_module_referrer
        )
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
    This is one of this module's entrypoints.
    """
    log_clarification("debug")
    log("API: Debugging Started.", "debug")
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=debug
    )