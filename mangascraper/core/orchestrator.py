#!/usr/bin/env python3
# mangascraper/core/orchestrator.py

import os, sys, logging, math, threading, ast
from datetime import datetime
from dotenv import load_dotenv, set_key

##########################################################################################
# DIRECTORIES
##########################################################################################

SCRAPER_DIR = "/opt/manga-scraper"
CORE_DIR = os.path.join(SCRAPER_DIR, "mangascraper", "core")
TEMP_DIR = "/tmp/manga-scraper"
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(CORE_DIR, exist_ok=True)

##########################################################################################
# LOGGER
##########################################################################################

LOG_DIR = f"{TEMP_DIR}/diagnostics"
os.makedirs(LOG_DIR, exist_ok=True)

# Runtime log file used by this process.
RUNTIME_LOG_FILE = os.path.join(
    LOG_DIR,
    f"runtime-{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
)


def _cleanup_empty_runtime_logs():
    """Delete zero-byte files in the runtime log directory left behind by short-lived processes/reloads."""
    try:
        if not os.path.isdir(LOG_DIR):
            return
        for name in os.listdir(LOG_DIR):
            path = os.path.join(LOG_DIR, name)
            if not os.path.isfile(path):
                continue
            # Never remove the active runtime log target.
            if os.path.realpath(path) == os.path.realpath(RUNTIME_LOG_FILE):
                continue
            if os.path.getsize(path) == 0:
                os.remove(path)
    except Exception:
        # Best-effort cleanup only.
        pass

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
logger = logging.getLogger("mangascraper")
if not logger.handlers:  # Only add default handler if none exist (prevents duplicates on reload)
    _cleanup_empty_runtime_logs()
    
    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)  # Default to INFO for imports
    ch.setFormatter(ConditionalFormatter())
    logger.addHandler(ch)

    # File handler: always DEBUG
    try:
        fh = logging.FileHandler(RUNTIME_LOG_FILE, mode="a", encoding="utf-8", delay=True)
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
    logger = logging.getLogger("mangascraper")

    console_handler = next((h for h in logger.handlers if isinstance(h, logging.StreamHandler)), None)
    if console_handler and console_handler.level == logging.INFO and clarification_type != "debug":
        print("") # print direct blank line to console
    logger.debug("") # print blank debug line to file (and console if debug mode)

def setup_logger(calm=False, debug=False):
    """
    Configure the mangascraper logger.
    - Console respects calm/debug flags with conditional formatting
    - File logs always DEBUG with full level info
    """
    logger = logging.getLogger("mangascraper")
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
    os.makedirs(LOG_DIR, exist_ok=True)
    _cleanup_empty_runtime_logs()
    fh = logging.FileHandler(RUNTIME_LOG_FILE, mode="a", encoding="utf-8", delay=True)
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
    
    logger = logging.getLogger("mangascraper")

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

_env_lock = threading.RLock() # Make all necessary operations thread safe.

def with_env_lock(func, *args, **kwargs):
    """
    Execute a function while holding the environment lock.
    Returns the function's result.
    """
    with _env_lock:
        return func(*args, **kwargs)

# ------------------------------------------------------------
# Env
# ------------------------------------------------------------
ENV_FILE = os.path.join(CORE_DIR, "manga-scraper.env")

# Ensure NHentai directory exists
os.makedirs(SCRAPER_DIR, exist_ok=True)

# Load environment variables
if os.path.exists(ENV_FILE):
    load_dotenv(dotenv_path=ENV_FILE)
    
# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------
DEFAULT_DASHBOARD_HOST = "0.0.0.0"
DEFAULT_DASHBOARD_PORT = 6969
DEFAULT_DASHBOARD_DEBUG = True
DEFAULT_DASHBOARD_USE_RELOADER = True

DASHBOARD_HOST = DEFAULT_DASHBOARD_HOST
DASHBOARD_PORT = DEFAULT_DASHBOARD_PORT
DASHBOARD_DEBUG = DEFAULT_DASHBOARD_DEBUG
DASHBOARD_USE_RELOADER = DEFAULT_DASHBOARD_USE_RELOADER

DASHBOARD_GALLERY_VIEW_CONFIG = {
    "searchPlaceholder": "Search creators or galleries...",
    "tileMinWidthPx": 960,
    "tileAspectRatio": "2 / 3",
    "creatorPageSize": 20,
    "galleryPageSize": 20,
    "galleryTilesPerPage": [7, 14, 21, 28, 35],
}

DASHBOARD_COLLECTION_VIEW_CONFIG = {
    "tileMinWidthPx": 960,
    "tileAspectRatio": "2 / 3",
    "galleryTilesPerCollectionPage": [14, 21, 28, 35],
}

DASHBOARD_OTHER_CONFIG = {
    "databasePageSize": 25,
}

# ------------------------------------------------------------
# NHentai Scraper Configuration Defaults
# ------------------------------------------------------------

DEFAULT_DOWNLOAD_PATH = "/opt/manga-scraper/downloads"
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
            "#    Optional page parameter supported (e.g. '?page=2') (fetches pages 1 to 2)\n"
            "# 4) Artist / Group / Tag / Character / Parody URLs: e.g. https://nhentai.net/artist/ARTIST/ or https://nhentai.net/group/GROUP/popular-week, etc\n"
            "#    Optional page parameter supported (e.g. '?page=3') (fetches pages 1 to 3)\n"
            "# 5) Search URLs: e.g. https://nhentai.net/search/?q=QUERY\n"
            "# Lines that do not match these formats will be skipped.\n"
        )
doujin_txt_path = DEFAULT_DOUJIN_TXT_PATH


# ------------------------------------------------------------
# Extensions
# ------------------------------------------------------------
DEFAULT_EXTENSION = "skeleton"
extension = DEFAULT_EXTENSION

DEFAULT_EXTENSION_DOWNLOAD_PATH = "/opt/manga-scraper/downloads/"
extension_download_path = DEFAULT_EXTENSION_DOWNLOAD_PATH

# Metadata cache TTL (seconds) - runtime only, not persisted to env
DEFAULT_METADATA_TTL = 3 * 60 * 60
metadata_ttl = DEFAULT_METADATA_TTL


# ------------------------------------------------------------
# APIs and Mirrors
# ------------------------------------------------------------
DEFAULT_NHENTAI_API_BASE = "https://nhentai.net/api"
nhentai_api_base = DEFAULT_NHENTAI_API_BASE

DEFAULT_NHENTAI_MIRRORS = "https://i.nhentai.net"
# normalised into a list at import
nhentai_mirrors = [DEFAULT_NHENTAI_MIRRORS]

# ------------------------------------------------------------
# SSL/Certificate Verification
# ------------------------------------------------------------
DEFAULT_VERIFY_SSL = True
verify_ssl = DEFAULT_VERIFY_SSL


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

DEFAULT_RANGE_END = 700000
range_end = DEFAULT_RANGE_END

DEFAULT_GALLERIES = ""
galleries = DEFAULT_GALLERIES

total_gallery_images = 0

DEFAULT_ARCHIVING = False
archiving = DEFAULT_RANGE_END


# ------------------------------------------------------------
# Filters
# ------------------------------------------------------------
DEFAULT_EXCLUDED_TAGS = "snuff,cuntboy,guro,cuntbusting,scat,coprophagia,vore,miniguy"
# normalised into a list at import
excluded_tags = [t.strip().lower() for t in DEFAULT_EXCLUDED_TAGS.split(",") if t.strip()]

DEFAULT_LANGUAGE = "english"
# normalised into a list at import
language = [DEFAULT_LANGUAGE.lower()]

DEFAULT_TITLE_TYPE = "english"
title_type = DEFAULT_TITLE_TYPE.lower()

# ------------------------------------------------------------
# Gallery Format
# ------------------------------------------------------------
DEFAULT_GALLERY_FORMAT = "directory"
gallery_format = DEFAULT_GALLERY_FORMAT

# ------------------------------------------------------------
# Threads
# ------------------------------------------------------------
BATCH_SIZE = 500 # Splits large scrapes into smaller ones
BATCH_SIZE_SLEEP_MULTIPLIER = 0.05 # Seconds to sleep per gallery in batch
batch_sleep_time = BATCH_SIZE * BATCH_SIZE_SLEEP_MULTIPLIER # Seconds to sleep before starting a new batch

# --- API hits (pages + galleries) ---
#                      FETCHING IDS                 GET METADATA  IMAGE DOWNLOADING (ESTIMATE)
MAX_ALLOWED_API_HITS = math.ceil(BATCH_SIZE / 25) + BATCH_SIZE + (BATCH_SIZE * 20)

MIN_THREADS_GALLERIES = 1
MAX_THREADS_GALLERIES = 1000
DEFAULT_THREADS_GALLERIES = 2
threads_galleries = min(max(MIN_THREADS_GALLERIES, DEFAULT_THREADS_GALLERIES), MAX_THREADS_GALLERIES)

MIN_THREADS_IMAGES = 1
MAX_THREADS_IMAGES = 1000
DEFAULT_THREADS_IMAGES = 10
calculated_threads_images = round(((MAX_ALLOWED_API_HITS / BATCH_SIZE) - threads_galleries) / threads_galleries)
threads_images = min(max(MIN_THREADS_IMAGES, calculated_threads_images), MAX_THREADS_IMAGES)

# ------------------------------------------------------------
# Thread Management
# ------------------------------------------------------------
# Allow Background Processing: If True (default - daemon mode), program exits immediately even if downloads continue
# If False (safe mode), program waits for all downloads to complete before exiting
DEFAULT_USE_DAEMON_THREADS = True
use_daemon_threads = DEFAULT_USE_DAEMON_THREADS

DEFAULT_MAX_RETRIES = 5
max_retries = DEFAULT_MAX_RETRIES

DEFAULT_MIN_RETRY_SLEEP = 0.5
min_api_sleep = 0.5
min_retry_sleep = DEFAULT_MIN_RETRY_SLEEP

DEFAULT_MAX_RETRY_SLEEP = (DEFAULT_THREADS_GALLERIES * DEFAULT_THREADS_IMAGES * 5) / 2
max_api_sleep = 0.75
max_retry_sleep = DEFAULT_MAX_RETRY_SLEEP


# ------------------------------------------------------------
# Download Options
# ------------------------------------------------------------
DEFAULT_USE_TOR = True
use_tor = DEFAULT_USE_TOR

DEFAULT_SKIP_POST_BATCH = False
skip_post_batch = DEFAULT_SKIP_POST_BATCH

DEFAULT_SKIP_POST_RUN = False
skip_post_run = DEFAULT_SKIP_POST_RUN

DEFAULT_DRY_RUN = False
dry_run = DEFAULT_DRY_RUN

DEFAULT_CALM = True
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
    "GALLERY_FORMAT": os.getenv("GALLERY_FORMAT", DEFAULT_GALLERY_FORMAT),
    "THREADS_GALLERIES": getenv_numeric_value("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES),
    "THREADS_IMAGES": getenv_numeric_value("THREADS_IMAGES", DEFAULT_THREADS_IMAGES),
    "USE_DAEMON_THREADS": str(os.getenv("USE_DAEMON_THREADS", DEFAULT_USE_DAEMON_THREADS)).lower() == "true",
    "MAX_RETRIES": getenv_numeric_value("MAX_RETRIES", DEFAULT_MAX_RETRIES),
    "VERIFY_SSL": str(os.getenv("VERIFY_SSL", DEFAULT_VERIFY_SSL)).lower() == "true",
    "USE_TOR": str(os.getenv("USE_TOR", DEFAULT_USE_TOR)).lower() == "true",
    "SKIP_POST_BATCH": str(os.getenv("SKIP_POST_BATCH", DEFAULT_SKIP_POST_BATCH)).lower() == "true",
    "SKIP_POST_RUN": str(os.getenv("SKIP_POST_RUN", DEFAULT_SKIP_POST_RUN)).lower() == "true",
    "DRY_RUN": str(os.getenv("DRY_RUN", DEFAULT_DRY_RUN)).lower() == "true",
    "CALM": str(os.getenv("CALM", DEFAULT_CALM)).lower() == "true",
    "DEBUG": str(os.getenv("DEBUG", DEFAULT_DEBUG)).lower() == "true",
}

##################

# ------------------------------------------------------------
# Update .env safely
# ------------------------------------------------------------
def refresh_globals():
    """
    Refresh runtime globals from config with the normalised default values.
    """
    
    def _update_globals():
        global download_path, doujin_txt_path, extension, extension_download_path
        global nhentai_api_base, nhentai_mirrors, page_sort, page_range_start, page_range_end
        global range_start, range_end, galleries, excluded_tags, language, title_type, gallery_format
        global threads_galleries, threads_images, max_retries, min_retry_sleep, max_retry_sleep
        global use_tor, skip_post_batch, skip_post_run, dry_run, calm, debug, verify_ssl, use_daemon_threads

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
            "GALLERY_FORMAT": DEFAULT_GALLERY_FORMAT,
            "THREADS_GALLERIES": DEFAULT_THREADS_GALLERIES,
            "THREADS_IMAGES": DEFAULT_THREADS_IMAGES,
            "USE_DAEMON_THREADS": DEFAULT_USE_DAEMON_THREADS,
            "MAX_RETRIES": DEFAULT_MAX_RETRIES,
            "VERIFY_SSL": DEFAULT_VERIFY_SSL,
            "USE_TOR": DEFAULT_USE_TOR,
            "SKIP_POST_BATCH": DEFAULT_SKIP_POST_BATCH,
            "SKIP_POST_RUN": DEFAULT_SKIP_POST_RUN,
            "DRY_RUN": DEFAULT_DRY_RUN,
            "CALM": DEFAULT_CALM,
            "DEBUG": DEFAULT_DEBUG,
        }.items():
            globals()[key.lower()] = normalise_value(key, config.get(key, default))
    
    # Execute the update under the lock
    with_env_lock(_update_globals)

def normalise_config():
    """
    Normalise config with defaults.
    normalise_config() is called by CLI to normalise and populate the .env file
    """
    log_clarification("debug")
    log("Populating Config...", "debug")

    ensure_env_file()
    
    defaults = {
        "DOUJIN_TXT_PATH": DEFAULT_DOUJIN_TXT_PATH,
        "DOWNLOAD_PATH": DEFAULT_DOWNLOAD_PATH,
        "EXTENSION": DEFAULT_EXTENSION,
        "EXTENSION_DOWNLOAD_PATH": DEFAULT_EXTENSION_DOWNLOAD_PATH,
        "NHENTAI_API_BASE": DEFAULT_NHENTAI_API_BASE,
        "NHENTAI_MIRRORS": DEFAULT_NHENTAI_MIRRORS,
        "VERIFY_SSL": DEFAULT_VERIFY_SSL,
        "PAGE_SORT": DEFAULT_PAGE_SORT,
        "PAGE_RANGE_START": DEFAULT_PAGE_RANGE_START,
        "PAGE_RANGE_END": DEFAULT_PAGE_RANGE_END,
        "RANGE_START": DEFAULT_RANGE_START,
        "RANGE_END": DEFAULT_RANGE_END,
        "GALLERIES": DEFAULT_GALLERIES,
        "EXCLUDED_TAGS": DEFAULT_EXCLUDED_TAGS,
        "LANGUAGE": DEFAULT_LANGUAGE,
        "TITLE_TYPE": DEFAULT_TITLE_TYPE,
        "GALLERY_FORMAT": DEFAULT_GALLERY_FORMAT,
        "THREADS_GALLERIES": DEFAULT_THREADS_GALLERIES,
        "THREADS_IMAGES": DEFAULT_THREADS_IMAGES,
        "USE_DAEMON_THREADS": DEFAULT_USE_DAEMON_THREADS,
        "MAX_RETRIES": DEFAULT_MAX_RETRIES,
        "USE_TOR": DEFAULT_USE_TOR,
        "SKIP_POST_BATCH": DEFAULT_SKIP_POST_BATCH,
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
    
    refresh_globals()

def normalise_value(key: str, value):
    """
    Normalise values from .env/config to consistent runtime types.
    """
    
    if key == "NHENTAI_MIRRORS":
        if isinstance(value, str):
            mirrors = [m.strip() for m in value.split(",") if m.strip()]
        elif isinstance(value, list):
            mirrors = value
        else:
            mirrors = [DEFAULT_NHENTAI_MIRRORS]
        # Ensure default mirror is first
        return [DEFAULT_NHENTAI_MIRRORS] + [m for m in mirrors if m != DEFAULT_NHENTAI_MIRRORS]
    
    if key in ("EXCLUDED_TAGS", "LANGUAGE"):
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    parsed = ast.literal_eval(stripped)
                except (ValueError, SyntaxError):
                    parsed = None
                if isinstance(parsed, (list, tuple)):
                    return [str(v).strip().lower() for v in parsed if str(v).strip()]
            return [v.strip().lower() for v in value.split(",") if v.strip()]
        elif isinstance(value, list):
            return [str(v).lower() for v in value]
        else:
            return []

    if key == "GALLERIES":
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    parsed = ast.literal_eval(stripped)
                except (ValueError, SyntaxError):
                    parsed = None
                if isinstance(parsed, (list, tuple)):
                    ids = []
                    for v in parsed:
                        try:
                            ids.append(int(v))
                        except (TypeError, ValueError):
                            continue
                    return ids
            ids = []
            for v in stripped.split(","):
                v = v.strip()
                if not v:
                    continue
                try:
                    ids.append(int(v))
                except (TypeError, ValueError):
                    continue
            return ids
        if isinstance(value, (list, tuple)):
            ids = []
            for v in value:
                try:
                    ids.append(int(v))
                except (TypeError, ValueError):
                    continue
            return ids
        return []

    if key == "GALLERY_FORMAT":
        fmt = str(value).lower()
        if fmt not in ("directory", "zip", "cbz"):
            return DEFAULT_GALLERY_FORMAT
        return fmt
    
    if key in ("USE_TOR", "SKIP_POST_RUN", "DRY_RUN", "CALM", "DEBUG", "VERIFY_SSL", "USE_DAEMON_THREADS"):
        return str(value).lower() == "true"

    if key in ("THREADS_GALLERIES", "THREADS_IMAGES", "MAX_RETRIES"):
        return int(value)

    # Default: return as string
    return str(value)

def _format_env_value(key: str, value) -> str:
    if key in ("EXCLUDED_TAGS", "LANGUAGE", "NHENTAI_MIRRORS", "GALLERIES"):
        if isinstance(value, (list, tuple, set)):
            return ",".join(str(v).strip() for v in value if str(v).strip())
    if key in ("USE_TOR", "SKIP_POST_RUN", "DRY_RUN", "CALM", "DEBUG", "VERIFY_SSL", "USE_DAEMON_THREADS"):
        return "true" if str(value).lower() == "true" else "false"
    return str(value)

def _build_env_template() -> str:
    return (
        "# Manga Scraper Configuration\n\n"
        "# Custom (Username and Password must be manually set for now)\n"
        "AUTH_USERNAME=\n"
        "AUTH_PASSWORD=\n\n"
        "# Directories\n"
        f"SCRAPER_DIR={SCRAPER_DIR}\n\n"
        "# Default Paths\n"
        f"DOWNLOAD_PATH={_format_env_value('DOWNLOAD_PATH', DEFAULT_DOWNLOAD_PATH)}\n"
        f"DOUJIN_TXT_PATH={_format_env_value('DOUJIN_TXT_PATH', DEFAULT_DOUJIN_TXT_PATH)}\n\n"
        "# Extensions\n"
        f"EXTENSION={_format_env_value('EXTENSION', DEFAULT_EXTENSION)}\n"
        f"EXTENSION_DOWNLOAD_PATH={_format_env_value('EXTENSION_DOWNLOAD_PATH', DEFAULT_EXTENSION_DOWNLOAD_PATH)}\n\n"
        "# APIs and Mirrors\n"
        f"NHENTAI_API_BASE={_format_env_value('NHENTAI_API_BASE', DEFAULT_NHENTAI_API_BASE)}\n"
        f"NHENTAI_MIRRORS={_format_env_value('NHENTAI_MIRRORS', DEFAULT_NHENTAI_MIRRORS)}\n\n"
        "# Gallery ID selection\n"
        f"PAGE_SORT={_format_env_value('PAGE_SORT', DEFAULT_PAGE_SORT)}\n"
        f"PAGE_RANGE_START={_format_env_value('PAGE_RANGE_START', DEFAULT_PAGE_RANGE_START)}\n"
        f"PAGE_RANGE_END={_format_env_value('PAGE_RANGE_END', DEFAULT_PAGE_RANGE_END)}\n"
        f"RANGE_START={_format_env_value('RANGE_START', DEFAULT_RANGE_START)}\n"
        f"RANGE_END={_format_env_value('RANGE_END', DEFAULT_RANGE_END)}\n"
        f"GALLERIES={_format_env_value('GALLERIES', DEFAULT_GALLERIES)}\n\n"
        "# Filters\n"
        f"EXCLUDED_TAGS={_format_env_value('EXCLUDED_TAGS', DEFAULT_EXCLUDED_TAGS)}\n"
        f"LANGUAGE={_format_env_value('LANGUAGE', DEFAULT_LANGUAGE)}\n"
        f"TITLE_TYPE={_format_env_value('TITLE_TYPE', DEFAULT_TITLE_TYPE)}\n\n"
        "# Threads\n"
        f"THREADS_GALLERIES={_format_env_value('THREADS_GALLERIES', DEFAULT_THREADS_GALLERIES)}\n"
        f"THREADS_IMAGES={_format_env_value('THREADS_IMAGES', DEFAULT_THREADS_IMAGES)}\n"
        f"MAX_RETRIES={_format_env_value('MAX_RETRIES', DEFAULT_MAX_RETRIES)}\n"
        f"USE_DAEMON_THREADS={_format_env_value('USE_DAEMON_THREADS', DEFAULT_USE_DAEMON_THREADS)}\n\n"
        "# Download Options\n"
        f"USE_TOR={_format_env_value('USE_TOR', DEFAULT_USE_TOR)}\n"
        f"SKIP_POST_BATCH={_format_env_value('SKIP_POST_BATCH', DEFAULT_SKIP_POST_BATCH)}\n"
        f"SKIP_POST_RUN={_format_env_value('SKIP_POST_RUN', DEFAULT_SKIP_POST_RUN)}\n"
        f"DRY_RUN={_format_env_value('DRY_RUN', DEFAULT_DRY_RUN)}\n"
        f"CALM={_format_env_value('CALM', DEFAULT_CALM)}\n"
        f"DEBUG={_format_env_value('DEBUG', DEFAULT_DEBUG)}\n"
        f"GALLERY_FORMAT={_format_env_value('GALLERY_FORMAT', DEFAULT_GALLERY_FORMAT)}\n"
        f"VERIFY_SSL={_format_env_value('VERIFY_SSL', DEFAULT_VERIFY_SSL)}\n"
    )

def ensure_env_file(overwrite: bool = False):
    def _ensure():
        if overwrite or not os.path.exists(ENV_FILE):
            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.write(_build_env_template())
    with_env_lock(_ensure)

def update_env(key, value):
    """
    Update a single variable in the .env file safely under lock.
    """
    
    global threads_galleries, threads_images, max_retries, min_retry_sleep, max_retry_sleep
    
    def _update():
        if not os.path.exists(ENV_FILE):
            with open(ENV_FILE, "w") as f:
                f.write("")

        # Safely update .env
        set_key(ENV_FILE, key, _format_env_value(key, value))
        
        # Update runtime config
        config[key] = normalise_value(key, value)

    with_env_lock(_update)
    refresh_globals()

def get_valid_sort_value(sort_value):
    refresh_globals()
    
    valid_sort_value = DEFAULT_PAGE_SORT # Set to default.
    
    if sort_value in ("date", "recent", "1"):
        valid_sort_value = "date"       
    
    elif sort_value in ("popular-today", "popular_today", "today", "2"):
        valid_sort_value = "popular-today"
    
    elif sort_value in ("popular-week", "popular_week", "week", "3"):
        valid_sort_value = "popular-week"       
    
    elif sort_value in ("popular", "all_time", "all-time", "4"):
        valid_sort_value = "popular"
    
    else:
        valid_sort_value = DEFAULT_PAGE_SORT # Fallback to default.
    
    return valid_sort_value

##########################################################################################
# SYMBOL CLEANING CONSTANTS (formerly cleaning_helper.py)
##########################################################################################

# Symbols that are filesystem safe and should not be removed or replaced
ALLOWED_SYMBOLS = [ "!", "#", "&", "'", "(", ")", "\"", ",", ".", ":", "?", "_"]

# Fallback blacklist (these always become "_")
BROKEN_SYMBOL_BLACKLIST = [
    "↑", "↓", "→", "←",
    "♡", "♥", "★", "☆", "♪", "◆", "◇", "※", "✔", "✖",
    "◦", "∙", "•", "°", "●", "‣", "®", "©",
    "…", "@", "¬", "<", ">", "^", "¤", "¢",
    "♂", "♀", "⚥", "⚢", "⚣", "⚤", "⚦", "⚧", "⚨", "⚩", "♂", "♀",
    "£", "$", "¥",
    "ð", "§", "¶", "†", "‡", "‰", "µ", "¦", "~"
]

# Define explicit replacements for certain symbols
BROKEN_SYMBOL_REPLACEMENTS = {
    # Miscellaneous
    "ā": "a", "Ā": "A", "ē": "e", "Ē": "E",
    "ī": "i", "Ī": "I", "ō": "o", "Ō": "O",
    "ū": "u", "Ū": "U","ŕ": "r", "Ŕ": "R",
    "ś": "s", "Ś": "S", "ź": "z", "Ź": "Z", "ż": "z", "Ż": "Z",
    
    # Accented Latin vowels
    "à": "a", "À": "A", "á": "a", "Á": "A", "â": "a", "Â": "A",
    "ã": "a", "Ã": "A", "ä": "a", "Ä": "A", "å": "a", "Å": "A",
    "è": "e", "È": "E", "é": "e", "É": "E", "ê": "e", "Ê": "E",
    "ë": "e", "Ë": "E",
    "ì": "i", "Ì": "I", "í": "i", "Í": "I", "î": "i", "Î": "I",
    "ï": "i", "Ï": "I",
    "ò": "o", "Ò": "O", "ó": "o", "Ó": "O", "ô": "o", "Ô": "O",
    "õ": "o", "Õ": "O", "ö": "o", "Ö": "O", "ø": "o", "Ø": "O",
    "ù": "u", "Ù": "U", "ú": "u", "Ú": "U", "û": "u", "Û": "U",
    "ü": "u", "Ü": "U",
    "ý": "y", "Ý": "Y", "ÿ": "y", "Ÿ": "Y",

    # Special Latin ligatures & consonants
    "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ç": "c", "Ç": "C",
    "ñ": "n", "Ñ": "N",
    "ß": "ss",
    "Ð": "D",

    # Punctuation & misc symbols
    "'": "'", "¿": "?", "¡": "!",
    "ー": "-", "×": "X",

    # Greek letters
    "α": "a", "Α": "A",
    "β": "b", "Β": "B",
    "γ": "g", "Γ": "G",
    "δ": "d", "Δ": "D",
    "ε": "e", "Ε": "E",
    "ζ": "z", "Ζ": "Z",
    "η": "e", "Η": "E",
    "θ": "th", "Θ": "Th",
    "ι": "i", "Ι": "I",
    "κ": "k", "Κ": "K",
    "λ": "l", "Λ": "L",
    "μ": "m", "Μ": "M",
    "ν": "n", "Ν": "N",
    "ξ": "x", "Ξ": "X",
    "ο": "o", "Ο": "O",
    "π": "p", "Π": "P",
    "ρ": "r", "Ρ": "R",
    "σ": "s", "Σ": "S", "ς": "s",
    "τ": "t", "Τ": "T",
    "υ": "y", "Υ": "Y",
    "φ": "f", "Φ": "F",
    "χ": "ch", "Χ": "Ch",
    "ψ": "ps", "Ψ": "Ps",
    "ω": "o", "Ω": "O",

    # Cyrillic letters
    "а": "a", "А": "A",
    "б": "b", "Б": "B",
    "в": "v", "В": "V",
    "г": "g", "Г": "G",
    "д": "d", "Д": "D",
    "е": "e", "Е": "E",
    "ё": "e", "Ё": "E",
    "ж": "zh", "Ж": "Zh",
    "з": "z", "З": "Z",
    "и": "i", "И": "I",
    "й": "i", "Й": "I",
    "к": "k", "К": "K",
    "л": "l", "Л": "L",
    "м": "m", "М": "M",
    "н": "n", "Н": "N",
    "о": "o", "О": "O",
    "п": "p", "П": "P",
    "р": "r", "Р": "R",
    "с": "s", "С": "S",
    "т": "t", "Т": "T",
    "у": "u", "У": "U",
    "ф": "f", "Ф": "F",
    "х": "h", "Х": "H",
    "ц": "ts", "Ц": "Ts",
    "ч": "ch", "Ч": "Ch",
    "ш": "sh", "Ш": "Sh",
    "щ": "shch", "Щ": "Shch",
    "ъ": "", "Ъ": "",
    "ы": "y", "Ы": "Y",
    "ь": "", "Ь": "",
    "э": "e", "Э": "E",
    "ю": "yu", "Ю": "Yu",
    "я": "ya", "Я": "Ya",
    
    # Possible Broken Symbols
    "²": "_",
    "―": "_",
    "'": "_",
    "\"": "_",
    "\"": "_",
    "‼": "_",
    "↔": "_",
    "①": "1",
    "②": "2",
    "③": "3",
    "④": "4",
    "⑤": "5",
    "█": "_",
    "□": "_",
    "△": "_",
    "▶": "_",
    "❤": "_",
    "〇": "_",
    "「": "_",
    "」": "_",
    "【": "_",
    "】": "_",
    "〜": "_",
    "３": "_",
    "？": "_",
    "｜": "_",
    "～": "_",
    "💅": "_"
}