#!/usr/bin/env python3
# nhscraper/core/configurator.py

import os, sys, logging, threading

from datetime import datetime
from dotenv import load_dotenv, set_key

from nhscraper.core.cleaning_helper import ALLOWED_SYMBOLS, BROKEN_SYMBOL_BLACKLIST, BROKEN_SYMBOL_REPLACEMENTS

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

def log(message: str, log_type: str = "warning"):
    """
    Unified logging function.
    All logs go to file (DEBUG+), console respects setup_logger flags.

    log_type: "debug", "info", "warning", "error", "critical"
    """
    
    logger = logging.getLogger("nhscraper")

    # Map string to logging function
    log_map = {
        "debug": logger.debug,
        "info": logger.info,
        "warning": logger.warning,
        "error": logger.error,
        "critical": logger.critical,
    }

    log_func = log_map.get(log_type.lower(), logger.info)
    log_func(message)

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
page_range_start = DEFAULT_PAGE_RANGE_START

DEFAULT_PAGE_RANGE_END = 10
page_range_end = DEFAULT_PAGE_RANGE_END

DEFAULT_RANGE_START = 500000
range_start = DEFAULT_RANGE_START

DEFAULT_RANGE_END = 600000
range_end = DEFAULT_RANGE_END

DEFAULT_GALLERIES = ""
galleries = DEFAULT_GALLERIES

total_gallery_images = 0


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

DEFAULT_MIN_RETRY_SLEEP = 0.5
min_api_sleep = 0.5
min_retry_sleep = DEFAULT_MIN_RETRY_SLEEP

DEFAULT_MAX_RETRY_SLEEP = 100
max_api_sleep = 0.75
max_retry_sleep = DEFAULT_MAX_RETRY_SLEEP

BATCH_SIZE = 500 # Splits large scrapes into smaller ones
BATCH_SIZE_SLEEP_MULTIPLIER = 0.05 # Seconds to sleep per gallery in batch
batch_sleep_time = BATCH_SIZE * BATCH_SIZE_SLEEP_MULTIPLIER # Seconds to sleep before starting a new batch


# ------------------------------------------------------------
# Download Options
# ------------------------------------------------------------
DEFAULT_USE_TOR = True
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
    "USE_TOR": str(os.getenv("USE_TOR", DEFAULT_USE_TOR)).lower() == "true",
    "SKIP_POST_RUN": str(os.getenv("SKIP_POST_RUN", DEFAULT_SKIP_POST_RUN)).lower() == "true",
    "DRY_RUN": str(os.getenv("DRY_RUN", DEFAULT_DRY_RUN)).lower() == "true",
    "CALM": str(os.getenv("CALM", DEFAULT_CALM)).lower() == "true",
    "DEBUG": str(os.getenv("DEBUG", DEFAULT_DEBUG)).lower() == "true",
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

def fetch_env_vars():
    """
    Refresh runtime globals from config with normalised values.
    """
    
    def _update_globals():
        global download_path, doujin_txt_path, extension, extension_download_path
        global nhentai_api_base, nhentai_mirrors, page_sort, page_range_start, page_range_end
        global range_start, range_end, galleries, excluded_tags, language, title_type
        global threads_galleries, threads_images, max_retries, min_retry_sleep, max_retry_sleep
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