#!/usr/bin/env python3
# nhscraper/core/orchestrator.py
import os, sys, time, random, argparse, re, subprocess, urllib.parse, json # 'Default' imports

import threading, asyncio, aiohttp, aiohttp_socks, logging, inspect # Module-specific imports

from datetime import datetime
from dotenv import load_dotenv, set_key

from nhscraper.core.cleaning_helper import *

"""
Central coordinator for download workflows.
Manages task scheduling, concurrency, retries, and
the overall sequencing of gallery and image downloads.

Refer to 'Docs.txt' for a guide to using the executor functions.
"""

_module_referrer=f"Orchestrator" # Used in executor.* calls
DEFAULT_REFERRER = "Undisclosed Module"

##########################################################################################
# LOGGER
##########################################################################################

LOG_DIR = "/tmp/nhentai-scraper/logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Runtime log
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
RUNTIME_LOG_FILE = os.path.join(LOG_DIR, f"runtime-{timestamp}.log")

class ConditionalFormatter(logging.Formatter):
    """
    Custom formatter:
    - INFO: only show message
    - Other levels: include [LEVEL] prefix
    """
    def format(self, record):
        if record.levelno == logging.INFO:
            self._style._fmt = "%(message)s"
        else:
            self._style._fmt = "[%(levelname)s] %(message)s"
        return super().format(record)

# --- Placeholder logger so logging during module imports don't crash before setup_logger() runs ---
logger = logging.getLogger("nhscraper")
if not logger.handlers:  # Only add default handler if none exist (prevents duplicates on reload)
    
    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)  # Default to INFO for imports
    ch.setFormatter(ConditionalFormatter())
    logger.addHandler(ch)

    # File handler: always DEBUG
    try:
        fh = logging.FileHandler(RUNTIME_LOG_FILE, mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(fh)
    except Exception as e:
        # Silently ignore file handler errors in placeholder
        pass

    # Logger level: DEBUG ensures all messages reach file handler
    logger.setLevel(logging.DEBUG)

def log_clarification(clarification_type: str = "info"):
    """
    Prints a blank line in the terminal if the console handler is at INFO,
    or adds a blank debug line otherwise.
    """
    logger = logging.getLogger("nhscraper")

    console_handler = next((h for h in logger.handlers if isinstance(h, logging.StreamHandler)), None)
    if console_handler and console_handler.level == logging.INFO and clarification_type != "debug":
        print("") # print direct blank line to console
    logger.debug("") # print blank debug line to file (and console if debug mode)

def setup_logger(calm=False, debug=False):
    """
    Configure the nhscraper logger.
    - Console respects calm/debug flags with conditional formatting
    - File logs always DEBUG with full level info
    """
    logger = logging.getLogger("nhscraper")
    logger.handlers.clear()  # Remove previous handlers

    # Console handler
    ch = logging.StreamHandler()
    if debug:
        ch.setLevel(logging.DEBUG)
    elif calm:
        ch.setLevel(logging.WARNING)
    else:
        ch.setLevel(logging.INFO)
    ch.setFormatter(ConditionalFormatter())
    logger.addHandler(ch)

    # File handler: always DEBUG
    fh = logging.FileHandler(RUNTIME_LOG_FILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    # Logger level: DEBUG ensures all messages reach file handler
    logger.setLevel(logging.DEBUG)

    # Initialisation summary
    log_clarification("debug")
    logger.debug("Logger initialised. Console level: %s",
                 "DEBUG" if debug else "WARNING" if calm else "INFO")
    
    log_clarification("debug")
    logger.debug("Logger: Ready.")
    logger.debug("Logger: Debugging Started.")

    return logger

def _log_backend(message: str, log_type: str = "warning"):
    """
    Unified logging function.
    All logs go to file (DEBUG+), console respects setup_logger flags.
    """
    logger = logging.getLogger("nhscraper")

    log_map = {
        "debug": logger.debug,
        "info": logger.info,
        "warning": logger.warning,
        "error": logger.error,
        "critical": logger.critical,
    }

    log_func = log_map.get(log_type.lower(), logger.info)
    log_func(message)


def log(message: str, log_type: str = "warning"):
    """
    Unified logging function for both sync and async functions.
    Works transparently whether called inside or outside async code.
    """

    try:
        loop = asyncio.get_running_loop()
        # We are in async → schedule in background thread
        return asyncio.ensure_future(asyncio.to_thread(_log_backend, message, log_type))
    except RuntimeError:
        # No running loop → normal sync call
        return _log_backend(message, log_type)

##########################################################################################
# CONFIGS
##########################################################################################

_env_lock = threading.Lock() # Make all necessary operations thread safe.

def with_env_lock(func, *args, **kwargs):
    """
    Execute a function while holding the environment lock.
    Returns the function's result.
    """
    with _env_lock:
        return func(*args, **kwargs)

# ------------------------------------------------------------
# Paths & Env
# ------------------------------------------------------------
SCRAPER_DIR = "/opt/nhentai-scraper"
ENV_FILE = os.path.join(SCRAPER_DIR, "nhentai-scraper.env")

# Ensure NHentai directory exists
os.makedirs(SCRAPER_DIR, exist_ok=True)

# Load environment variables
if os.path.exists(ENV_FILE):
    load_dotenv(dotenv_path=ENV_FILE)
    
BATCH_SIZE = 500 # Splits large scrapes into smaller ones
BATCH_SIZE_SLEEP_MULTIPLIER = 0.05 # Seconds to sleep per gallery in batch
batch_sleep_time = BATCH_SIZE * BATCH_SIZE_SLEEP_MULTIPLIER

# ------------------------------------------------------------
# NHentai Scraper Configuration Defaults
# ------------------------------------------------------------

DEFAULT_DOWNLOAD_PATH = "/opt/nhentai-scraper/downloads"
download_path = DEFAULT_DOWNLOAD_PATH  # public variable

DEFAULT_DOUJIN_TXT_PATH = "/root/Doujinshi_IDs.txt"
if not os.path.exists(DEFAULT_DOUJIN_TXT_PATH):
    # Create an empty file with instructions for the user
    with open(DEFAULT_DOUJIN_TXT_PATH, "w", encoding="utf-8") as f:
        f.write(
            "# Add one NHentai gallery per line. Supported formats:\n"
            "# 1) Plain gallery ID: e.g. 123456\n"
            "# 2) Full gallery URL: e.g. https://nhentai.net/g/123456/\n"
            "# 3) NHentai Homepage URL: e.g. https://nhentai.net/ or https://nhentai.net/?page=2/\n"
            "#    Optional page parameter supported (e.g.) ?page=2 (fetches pages 1 to 2)\n"
            "# 4) Artist / Group / Tag / Character / Parody URLs: e.g. https://nhentai.net/artist/ARTIST/ or https://nhentai.net/group/GROUP/popular-week, etc\n"
            "#    Optional page parameter supported (e.g.) ?page=3 (fetches pages 1 to 3)\n"
            "# 5) Search URLs: e.g. https://nhentai.net/search/?q=QUERY\n"
            "# Lines that do not match these formats will be skipped.\n"
        )
doujin_txt_path = DEFAULT_DOUJIN_TXT_PATH


# ------------------------------------------------------------
# Extensions
# ------------------------------------------------------------
DEFAULT_EXTENSION = "skeleton"
extension = DEFAULT_EXTENSION

DEFAULT_EXTENSION_DOWNLOAD_PATH = "/opt/nhentai-scraper/downloads"
extension_download_path = DEFAULT_EXTENSION_DOWNLOAD_PATH


# ------------------------------------------------------------
# APIs and Mirrors
# ------------------------------------------------------------
DEFAULT_NHENTAI_API_BASE = "https://nhentai.net/api"
nhentai_api_base = DEFAULT_NHENTAI_API_BASE

DEFAULT_NHENTAI_MIRRORS = "https://i.nhentai.net"
# normalised into a list at import
nhentai_mirrors = [DEFAULT_NHENTAI_MIRRORS]


# ------------------------------------------------------------
# Gallery ID selection
# ------------------------------------------------------------
DEFAULT_PAGE_SORT = "date"
page_sort = DEFAULT_PAGE_SORT

DEFAULT_PAGE_RANGE_START = 1
homepage_range_start = DEFAULT_PAGE_RANGE_START

DEFAULT_PAGE_RANGE_END = 2
homepage_range_end = DEFAULT_PAGE_RANGE_END

DEFAULT_RANGE_START = 500000
range_start = DEFAULT_RANGE_START

DEFAULT_RANGE_END = 600000
range_end = DEFAULT_RANGE_END

DEFAULT_GALLERIES = ""
galleries = DEFAULT_GALLERIES


# ------------------------------------------------------------
# Filters
# ------------------------------------------------------------
DEFAULT_EXCLUDED_TAGS = "snuff,cuntboy,guro,cuntbusting,scat,coprophagia,ai generated,vore"
# normalised into a list at import
excluded_tags = [t.strip().lower() for t in DEFAULT_EXCLUDED_TAGS.split(",") if t.strip()]

DEFAULT_LANGUAGE = "english"
# normalised into a list at import
language = [DEFAULT_LANGUAGE.lower()]

DEFAULT_TITLE_TYPE = "english"
title_type = DEFAULT_TITLE_TYPE.lower()


# ------------------------------------------------------------
# Threads
# ------------------------------------------------------------
DEFAULT_THREADS_GALLERIES = 2
threads_galleries = DEFAULT_THREADS_GALLERIES

DEFAULT_THREADS_IMAGES = 10
threads_images = DEFAULT_THREADS_IMAGES

DEFAULT_MAX_RETRIES = 3
max_retries = DEFAULT_MAX_RETRIES

DEFAULT_MIN_SLEEP = 0.5
min_sleep = DEFAULT_MIN_SLEEP

DEFAULT_MAX_SLEEP = 100
max_sleep = DEFAULT_MAX_SLEEP


# ------------------------------------------------------------
# Download Options
# ------------------------------------------------------------
DEFAULT_USE_TOR = False
use_tor = DEFAULT_USE_TOR

DEFAULT_SKIP_POST_RUN = False
skip_post_run = DEFAULT_SKIP_POST_RUN

DEFAULT_DRY_RUN = False
dry_run = DEFAULT_DRY_RUN

DEFAULT_CALM = False
calm = DEFAULT_CALM

DEFAULT_DEBUG = False
debug = DEFAULT_DEBUG

# ------------------------------------------------------------
# Helper: safe int from env
# ------------------------------------------------------------
def getenv_numeric_value(key, default):
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return float(val)

# ------------------------------------------------------------
# Config Dictionary
# ------------------------------------------------------------

# Also change corresponding parser.add_argument in CLI

# NHENTAI_MIRRORS: always a list
MIRRORS_ENV = os.getenv("NHENTAI_MIRRORS", DEFAULT_NHENTAI_MIRRORS)
if isinstance(MIRRORS_ENV, str):
    MIRRORS_LIST = [m.strip() for m in MIRRORS_ENV.split(",") if m.strip()]
else:
    MIRRORS_LIST = list(MIRRORS_ENV)

config = {
    "DOUJIN_TXT_PATH": os.getenv("DOUJIN_TXT_PATH", DEFAULT_DOUJIN_TXT_PATH),
    "DOWNLOAD_PATH": os.getenv("DOWNLOAD_PATH", DEFAULT_DOWNLOAD_PATH),
    "EXTENSION": os.getenv("EXTENSION", DEFAULT_EXTENSION),
    "EXTENSION_DOWNLOAD_PATH": os.getenv("EXTENSION_DOWNLOAD_PATH", DEFAULT_EXTENSION_DOWNLOAD_PATH),
    "NHENTAI_API_BASE": os.getenv("NHENTAI_API_BASE", DEFAULT_NHENTAI_API_BASE),
    "NHENTAI_MIRRORS": MIRRORS_LIST,
    "PAGE_SORT": os.getenv("PAGE_RANGE_START", DEFAULT_PAGE_RANGE_START),
    "PAGE_RANGE_START": getenv_numeric_value("PAGE_RANGE_START", DEFAULT_PAGE_RANGE_START),
    "PAGE_RANGE_END": getenv_numeric_value("PAGE_RANGE_END", DEFAULT_PAGE_RANGE_END),
    "RANGE_START": getenv_numeric_value("RANGE_START", DEFAULT_RANGE_START),
    "RANGE_END": getenv_numeric_value("RANGE_END", DEFAULT_RANGE_END),
    "GALLERIES": os.getenv("GALLERIES", DEFAULT_GALLERIES),
    "ARTIST": os.getenv("ARTIST", ""),
    "GROUP": os.getenv("GROUP", ""),
    "TAG": os.getenv("TAG", ""),
    "PARODY": os.getenv("PARODY", ""),
    "EXCLUDED_TAGS": os.getenv("EXCLUDED_TAGS", DEFAULT_EXCLUDED_TAGS),
    "LANGUAGE": os.getenv("LANGUAGE", DEFAULT_LANGUAGE),
    "TITLE_TYPE": os.getenv("TITLE_TYPE", DEFAULT_TITLE_TYPE),
    "THREADS_GALLERIES": getenv_numeric_value("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES),
    "THREADS_IMAGES": getenv_numeric_value("THREADS_IMAGES", DEFAULT_THREADS_IMAGES),
    "MAX_RETRIES": getenv_numeric_value("MAX_RETRIES", DEFAULT_MAX_RETRIES),
    "MIN_SLEEP": getenv_numeric_value("MIN_SLEEP", DEFAULT_MIN_SLEEP),
    "MAX_SLEEP": getenv_numeric_value("MAX_SLEEP", DEFAULT_MAX_SLEEP),
    "USE_TOR": str(os.getenv("USE_TOR", DEFAULT_USE_TOR)).lower() == "false",
    "SKIP_POST_RUN": str(os.getenv("SKIP_POST_RUN", DEFAULT_SKIP_POST_RUN)).lower() == "false",
    "DRY_RUN": str(os.getenv("DRY_RUN", DEFAULT_DRY_RUN)).lower() == "false",
    "CALM": str(os.getenv("CALM", DEFAULT_CALM)).lower() == "false",
    "DEBUG": str(os.getenv("DEBUG", DEFAULT_DEBUG)).lower() == "false",
}

##################

# ------------------------------------------------------------
# Normalise config with defaults
# ------------------------------------------------------------
def normalise_config():
    log_clarification("debug")
    log("Populating Config...", "debug")
    
    defaults = {
        "DOUJIN_TXT_PATH": DEFAULT_DOUJIN_TXT_PATH,
        "DOWNLOAD_PATH": DEFAULT_DOWNLOAD_PATH,
        "EXTENSION": DEFAULT_EXTENSION,
        "EXTENSION_DOWNLOAD_PATH": DEFAULT_EXTENSION_DOWNLOAD_PATH,
        "NHENTAI_API_BASE": DEFAULT_NHENTAI_API_BASE,
        "NHENTAI_MIRRORS": DEFAULT_NHENTAI_MIRRORS,
        "PAGE_SORT": DEFAULT_PAGE_SORT,
        "PAGE_RANGE_START": DEFAULT_PAGE_RANGE_START,
        "PAGE_RANGE_END": DEFAULT_PAGE_RANGE_END,
        "RANGE_START": DEFAULT_RANGE_START,
        "RANGE_END": DEFAULT_RANGE_END,
        "GALLERIES": DEFAULT_GALLERIES,
        "EXCLUDED_TAGS": DEFAULT_EXCLUDED_TAGS,
        "LANGUAGE": DEFAULT_LANGUAGE,
        "TITLE_TYPE": DEFAULT_TITLE_TYPE,
        "THREADS_GALLERIES": DEFAULT_THREADS_GALLERIES,
        "THREADS_IMAGES": DEFAULT_THREADS_IMAGES,
        "MAX_RETRIES": DEFAULT_MAX_RETRIES,
        "MIN_SLEEP": DEFAULT_MIN_SLEEP,
        "MAX_SLEEP": DEFAULT_MAX_SLEEP,
        "USE_TOR": DEFAULT_USE_TOR,
        "SKIP_POST_RUN": DEFAULT_SKIP_POST_RUN,
        "DRY_RUN": DEFAULT_DRY_RUN,
        "CALM": DEFAULT_CALM,
        "DEBUG": DEFAULT_DEBUG,
    }

    for key, default_val in defaults.items():
        val = config.get(key)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            config[key] = default_val
            update_env(key, default_val)

# normalise_config() is called by CLI to normalise and populate .env

# ------------------------------------------------------------
# Update .env safely
# ------------------------------------------------------------

def normalise_value(key: str, value):
    """
    Normalise values from .env/config to consistent runtime types.
    """
    if key in ("EXCLUDED_TAGS", "LANGUAGE"):
        if isinstance(value, str):
            return [v.strip().lower() for v in value.split(",") if v.strip()]
        elif isinstance(value, list):
            return [str(v).lower() for v in value]
        else:
            return []
    
    if key == "NHENTAI_MIRRORS":
        if isinstance(value, str):
            mirrors = [m.strip() for m in value.split(",") if m.strip()]
        elif isinstance(value, list):
            mirrors = value
        else:
            mirrors = [DEFAULT_NHENTAI_MIRRORS]
        # Ensure default mirror is first
        return [DEFAULT_NHENTAI_MIRRORS] + [m for m in mirrors if m != DEFAULT_NHENTAI_MIRRORS]

    if key in ("USE_TOR", "SKIP_POST_RUN", "DRY_RUN", "CALM", "DEBUG"):
        return str(value).lower() == "true"

    if key in ("THREADS_GALLERIES", "THREADS_IMAGES", "MAX_RETRIES"):
        return int(value)

    if key in ("MIN_SLEEP", "MAX_SLEEP"):
        return float(value)

    # Default: return as string
    return str(value)

def update_env(key, value):
    """
    Update a single variable in the .env file safely under lock.
    """
    def _update():
        if not os.path.exists(ENV_FILE):
            with open(ENV_FILE, "w") as f:
                f.write("")

        # Safely update .env
        set_key(ENV_FILE, key, str(value))
        
        # Update runtime config
        config[key] = normalise_value(key, value)

    with_env_lock(_update)
    
def init_scraper(gallery_list):
    """
    Updates Config with Built Gallery List
    Automatically sets a reasonable amount of threads to use 
    """
    
    global threads_galleries, threads_images
    
    update_env("GALLERIES", gallery_list) # Immediately update config with built gallery list
    
    if threads_galleries is None or threads_images is None:
        threads_galleries = max(DEFAULT_THREADS_GALLERIES, int(gallery_list / 500) + 1)
        threads_images = max(DEFAULT_THREADS_IMAGES, threads_galleries * 5)
        
        if debug:
            log(f"→ Optimised Threads: {threads_galleries} gallery, {threads_images} image", "debug")
        
        update_env()

def fetch_env_vars():
    """
    Refresh runtime globals from config with normalised values.
    """
    
    def _update_globals():
        global download_path, doujin_txt_path, extension, extension_download_path
        global nhentai_api_base, nhentai_mirrors, page_sort, homepage_range_start, homepage_range_end
        global range_start, range_end, galleries, excluded_tags, language, title_type
        global threads_galleries, threads_images, max_retries, min_sleep, max_sleep
        global use_tor, skip_post_run, dry_run, calm, debug

        for key, default in {
            "DOWNLOAD_PATH": DEFAULT_DOWNLOAD_PATH,
            "DOUJIN_TXT_PATH": DEFAULT_DOUJIN_TXT_PATH,
            "EXTENSION": DEFAULT_EXTENSION,
            "EXTENSION_DOWNLOAD_PATH": DEFAULT_EXTENSION_DOWNLOAD_PATH,
            "NHENTAI_API_BASE": DEFAULT_NHENTAI_API_BASE,
            "NHENTAI_MIRRORS": DEFAULT_NHENTAI_MIRRORS,
            "PAGE_SORT": DEFAULT_PAGE_SORT,
            "PAGE_RANGE_START": DEFAULT_PAGE_RANGE_START,
            "PAGE_RANGE_END": DEFAULT_PAGE_RANGE_END,
            "RANGE_START": DEFAULT_RANGE_START,
            "RANGE_END": DEFAULT_RANGE_END,
            "GALLERIES": DEFAULT_GALLERIES,
            "EXCLUDED_TAGS": DEFAULT_EXCLUDED_TAGS,
            "LANGUAGE": DEFAULT_LANGUAGE,
            "TITLE_TYPE": DEFAULT_TITLE_TYPE,
            "THREADS_GALLERIES": DEFAULT_THREADS_GALLERIES,
            "THREADS_IMAGES": DEFAULT_THREADS_IMAGES,
            "MAX_RETRIES": DEFAULT_MAX_RETRIES,
            "MIN_SLEEP": DEFAULT_MIN_SLEEP,
            "MAX_SLEEP": DEFAULT_MAX_SLEEP,
            "USE_TOR": DEFAULT_USE_TOR,
            "SKIP_POST_RUN": DEFAULT_SKIP_POST_RUN,
            "DRY_RUN": DEFAULT_DRY_RUN,
            "CALM": DEFAULT_CALM,
            "DEBUG": DEFAULT_DEBUG,
        }.items():
            globals()[key.lower()] = normalise_value(key, config.get(key, default))
    
    # Execute the update under the lock
    with_env_lock(_update_globals)

def get_valid_sort_value(sort_value):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    valid_sort_value = DEFAULT_PAGE_SORT # Set to default.
    
    if sort_value in ("date", "recent"):
        valid_sort_value = "date"       
    
    elif sort_value in ("popular-today", "popular_today", "today"):
        valid_sort_value = "popular-today"
    
    elif sort_value in ("popular-week", "popular_week", "week"):
        valid_sort_value = "popular-week"       
    
    elif sort_value in ("popular", "all_time"):
        valid_sort_value = "popular"
    
    else:
        valid_sort_value = DEFAULT_PAGE_SORT # Fallback to default.
    
    return valid_sort_value

def get_caller_module_name(frame_num: int = 1, default=DEFAULT_REFERRER):
    """
    When called by a function (FunctionA), retrieves the calling function's (FunctionB)
    module's '_module_referrer' variable, or the clean module name.

    Strips any 'nhscraper.*.' prefix, leaving only the final part of the module path.
    """
    frame = inspect.currentframe()
    try:
        # Step back through frames
        caller_frame = frame
        for _ in range(frame_num):
            if caller_frame.f_back is None:
                break
            caller_frame = caller_frame.f_back

        module = inspect.getmodule(caller_frame)
        if module:
            # Prefer explicit _module_referrer if defined
            ref = getattr(module, "_module_referrer", module.__name__)
            # Strip nhscraper.*. → keep last part only
            if ref.startswith("nhscraper."):
                ref = ref.split(".")[-1]
            return ref
        return default
    finally:
        del frame  # avoid reference cycles

##########################################################################################
# EXECUTOR
##########################################################################################

# Used when no specific concurrency limit is required; essentially a single-slot semaphore for generic tasks.
DEFAULT_SEMAPHORE = max(1, (DEFAULT_THREADS_GALLERIES or 0) + (DEFAULT_THREADS_IMAGES or 0)) # Seems good lmfao

class Executor:
    """
    Executor Utility: executor.call_appropriately() / executor.run_blocking() / executor.spawn_task() / executor.safe_get()

    Provides a unified interface for running synchronous and asynchronous functions
    in both sync and async contexts, with optional concurrency control.

    ---
    General Rules:
    1. If Step B depends on Step A finishing → **await** the executor call.
    2. If order/result doesn't matter → call without `await` (fire-and-forget).
    3. Sync functions: only `executor.run_blocking()` (always blocks).
    ⚠️ Can run async coroutines here; it will block until completion.
    4. Async functions: use `executor.spawn_task()`, `executor.call_appropriately()`, or `executor.run_blocking()` depending on need.

    For a decision tree for choosing the right executor function, refer to `Docs.txt`

    Lambda Usage Notes:
    - Fire-and-forget **sync functions in async context** must be wrapped in `lambda` for `executor.spawn_task()`.
    - Async coroutines must always be passed as coroutine objects, **never pre-called**.
    
    ---
    Important Notes:
    - Always pass the function itself + arguments, not pre-called results.
    - `executor.spawn_task()` requires a coroutine object.
    - `executor.call_appropriately()` auto-detects async vs sync context.
    - `executor.run_blocking()` blocks immediately; only use in sync or rare async scenarios.
    """
    
    def __init__(self, max_gallery: int = None, max_image: int = None):
        fetch_env_vars() # Refresh env vars in case config changed.  # Refresh env vars in case config changed.

        # Semaphores for limiting concurrency
        self.default_semaphore = asyncio.Semaphore(DEFAULT_SEMAPHORE)
        self.gallery_semaphore = asyncio.Semaphore(max_gallery or threads_galleries)
        self.image_semaphore = asyncio.Semaphore(max_image or threads_images)

        self.tasks: list[asyncio.Task] = []

    async def _wrap(self, coro, task: str, module_name: str, semaphore: asyncio.Semaphore = None):
        """
        Internal wrapper for coroutine execution.
        - Respects semaphore limits
        - Logs errors
        - Returns result or None on failure
        """
        
        try:
            if semaphore:
                async with semaphore:
                    return await coro
            return await coro
        except Exception as e:
            log(f"Executor: executor.{task} in {module_name} failed: {e}", "error")
            return None

    async def gather(self):
        """
        Wait for all tracked tasks to complete, return results, and clear the task list.
        """
        
        if not self.tasks:
            return []
        results = await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        return results

    async def cancel_all(self):
        """
        Cancel all tracked tasks and clear the task list.
        """
        
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    def run_blocking(self, func, *args, **kwargs):
        """
        Run a synchronous function or coroutine in a **synchronous context**.
        Accepts the function + arguments directly; wraps in lambda internally.
        """
        
        referrer_blocking = get_caller_module_name(frame_num=3) # Retrieve calling module's '_module_referrer' variable
        
        # Wrap everything in a zero-arg callable
        if inspect.iscoroutinefunction(func):
            coro = lambda: func(*args, **kwargs)
        else:
            coro = lambda: func(*args, **kwargs)

        async def wrapper():
            return await self._wrap(coro(), "run_blocking", module_name=referrer_blocking)

        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(wrapper())
        except RuntimeError:
            return asyncio.run(wrapper())
    
    def spawn_task(self, coro, type: str = "default"):
        """
        Schedule an **asynchronous function** (coroutine) for execution in an async context.

        Two usage patterns:

        1. **Awaited** → pauses until task completes (use when result is needed):
               result = await executor.spawn_task(coro(...))

        2. **Fire-and-forget** → background execution (use when result is not needed):
               executor.spawn_task(coro(...))

        Arguments:
            coro: a coroutine object (async function called with parentheses)
            type: 'default', 'gallery', or 'image' (controls semaphore)

        Notes:
        - Must pass a **coroutine object**, not a sync function or pre-called result.
        - For synchronous functions in async context, use `call_appropriately`.
        """
        
        referrer_async = get_caller_module_name() # Retrieve calling module's '_module_referrer' variable
        
        sem = self.default_semaphore # Set default.
        if type == "gallery":
            referrer_async = referrer_async or "Gallery Download"
            sem = self.gallery_semaphore
        elif type == "image":
            referrer_async = referrer_async or "Image Download"
            sem = self.image_semaphore
        else:
            referrer_async = referrer_async or "General I/O"
            sem = self.default_semaphore

        task = asyncio.create_task(self._wrap(coro, "spawn_task", module_name=referrer_async, semaphore=sem))
        self.tasks.append(task)
        return task
    
    async def call_appropriately(self, func, *args, type: str = "default", referrer: str = None, **kwargs):
        """
        Safely run a function in the correct context (async vs sync) without blocking improperly.
        Accepts function + args directly; internally wraps with lambda where needed.
        """
        
        if referrer is None:
            referrer = get_caller_module_name() # Retrieve calling module's '_module_referrer' variable

        try:
            asyncio.get_running_loop()
            in_async = True
        except RuntimeError:
            in_async = False
            
        # Internal wrapper
        wrapped = lambda: func(*args, **kwargs)

        # --- Async context ---
        if in_async:
            if inspect.iscoroutinefunction(func):
                # async → spawn_task and await
                return await self.spawn_task(func(*args, **kwargs), type=type)
            else:
                # sync → run in thread
                return await asyncio.to_thread(wrapped)
        
        # --- Sync context ---
        else:
            if inspect.iscoroutinefunction(func):
                # async in sync → run_blocking
                return self.run_blocking(func, *args, **kwargs)
            else:
                # sync in sync → run_blocking
                return self.run_blocking(func, *args, **kwargs)
    
    def sleep_sync(self, seconds: float):
        """Block the current thread for `seconds` (sync sleep)."""
        
        referrer_blocking = get_caller_module_name() # Retrieve calling module's '_module_referrer' variable
        
        log(f"{referrer_blocking}: Sleeping for {seconds}s")
        time.sleep(seconds)
    
    async def sleep_async(self, seconds: float):
        """Async sleep for `seconds`, yielding to event loop."""
        
        referrer_async = get_caller_module_name() # Retrieve calling module's '_module_referrer' variable
        
        log(f"{referrer_async}: Sleeping for {seconds}s")
        await asyncio.sleep(seconds)
    
    async def io_to_thread(self, func, *args, **kwargs):
        """
        Run a synchronous I/O-bound function in a background thread inside an async context.

        Usage:
            result = await executor.io_to_thread(sync_io_func, arg1, arg2)

        Notes:
        - Equivalent to `asyncio.to_thread`.
        - Useful for blocking I/O (file, network) inside async code.
        """
        
        return await asyncio.to_thread(func, *args, **kwargs)

    async def read_json(self, path):
        """
        Read JSON from disk in a non-blocking async-safe way.

        Usage:
            data = await executor.read_json("file.json")

        Notes:
        - Runs file I/O in a background thread to avoid blocking the event loop.
        """
        
        def _read():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        
        return await self.io_to_thread(_read)

    async def write_json(self, path, data):
        """
        Write JSON to disk in a non-blocking async-safe way.

        Usage:
            await executor.write_json("file.json", data)

        Notes:
        - Runs file I/O in a background thread to avoid blocking the event loop.
        - Creates parent directories automatically.
        """
        
        def _write():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        
        return await self.io_to_thread(_write)

# Global executor instance
executor = Executor()

##########################################################################################
# EXECUTOR HELPERS
##########################################################################################

async def safe_session_get(session, url, **kwargs):
    """
    Unified GET request that works for both sync and async sessions.
    Handles both requests/Cloudscraper (sync) and aiohttp (async).
    """
    
    assert not callable(session), f"safe_session_get got a function instead of a session: {session}"
    
    method = session.get
    if inspect.iscoroutinefunction(method):
        return await method(url, **kwargs)
    else:
        return await executor.call_appropriately(method, url, **kwargs)

async def dynamic_sleep(stage=None, batch_ids=None, attempt: int = 1, wait: float = 0.5, perform_sleep: bool = True, dynamic: bool = True, dynamic_sleep_requester: str = None):
    """
    Unified Adaptive sleep  function based on load and stage for both sync and async contexts.
    Returns the sleep duration (float) and performs an asyncio.sleep for that duration.
    
    Parameters:
        stage: str | None
            "api" or "gallery"; used for dynamic calculation. Ignored if dynamic=False.
        batch_ids: list | None
            Used when stage="gallery" to calculate adaptive sleep.
        attempt: int
            Attempt number, used in scaling.
        wait: float
            Base sleep time if dynamic=False.
        perform_sleep: bool
            Whether to actually sleep or just return the calculated sleep time.
        dynamic: bool
            If True, calculates adaptive sleep based on stage and load.
            If False, sleeps for `wait` seconds.
            
    Returns:
        float: The sleep duration actually used.
    """
    
    if stage == None:
        stage = get_caller_module_name() # Retrieve calling module's '_module_referrer' variable
    
    try:
        asyncio.get_running_loop()
        in_async = True
    except RuntimeError:
        in_async = False

    sleep_time = wait
    
    debug = False # NOTE: DEBUGGING
    
    # ------------------------------------------------------------
    # Configurable parameters
    # ------------------------------------------------------------
    gallery_cap = 3750 # Maximum number of galleries considered for scaling (~150 pages)
    # min_sleep = Minimum Gallery sleep time
    # max_sleep = Maximum Gallery sleep time
    api_min_sleep, api_max_sleep = 0.5, 0.75 # API sleep range

    log_clarification("debug")
    log("------------------------------", "debug")
    log(f"{stage.capitalize()} Attempt: {attempt}", "debug")
    log_clarification("debug")

    if dynamic:
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
            sleep_time = min(random.uniform(scaled_sleep * jitter_min, scaled_sleep * jitter_max), max_sleep)
            
            if debug:
                log(f"→ Sleep after jitter (Capped at {max_sleep}s) = Random({scaled_sleep:.2f}*{jitter_min}, {scaled_sleep:.2f}*{jitter_max}) = {sleep_time:.2f}s", "debug")

            # --------------------------------------------------------
            # 6. Final result
            # --------------------------------------------------------
            log_clarification("debug")
            log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Load: {current_load:.2f} Units)", "debug")
            log("------------------------------", "debug")
            log_clarification()

    # --- Perform the sleep ---
    if perform_sleep:
        if in_async:
            await executor.sleep_async(sleep_time)
        else:
            await asyncio.to_thread(executor.sleep_sync, sleep_time)

    return sleep_time