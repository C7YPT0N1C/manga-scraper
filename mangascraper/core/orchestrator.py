#!/usr/bin/env python3
# mangascraper/core/orchestrator.py

import os, sys, logging, math, threading, ast, tempfile, sqlite3
from datetime import datetime

##########################################################################################
# DIRECTORIES
##########################################################################################

# Use install-directory defaults by default.
_SOURCE_CHECKOUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

if os.name == "nt":
    _WINDOWS_FALLBACK_DIR = os.path.join(os.path.expanduser("~"), "manga-scraper")
    _USE_SOURCE_CHECKOUT = str(os.getenv("SCRAPER_DIR_USE_SOURCE_CHECKOUT", "")).strip().lower() in ("1", "true", "yes", "on")
    if _USE_SOURCE_CHECKOUT and os.path.isdir(os.path.join(_SOURCE_CHECKOUT_DIR, "mangascraper")):
        _DEFAULT_SCRAPER_DIR = _SOURCE_CHECKOUT_DIR
    else:
        _DEFAULT_SCRAPER_DIR = _WINDOWS_FALLBACK_DIR
else:
    _DEFAULT_SCRAPER_DIR = "/opt/manga-scraper"

_SCRAPER_DIR_ENV = str(os.getenv("SCRAPER_DIR", "")).strip().strip("\"").strip("'")
if os.name == "nt" and (_SCRAPER_DIR_ENV == "" or _SCRAPER_DIR_ENV.replace("\\", "/").lower().startswith("/opt/manga-scraper")):
    SCRAPER_DIR = _DEFAULT_SCRAPER_DIR
else:
    SCRAPER_DIR = _SCRAPER_DIR_ENV or _DEFAULT_SCRAPER_DIR
CORE_DIR = os.path.join(SCRAPER_DIR, "mangascraper", "core")
TEMP_DIR = os.path.join(tempfile.gettempdir(), "manga-scraper")
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(CORE_DIR, exist_ok=True)

##########################################################################################
# LOGGER
##########################################################################################

LOG_DIR = f"{TEMP_DIR}/logs"
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
# Runtime Config Database
# ------------------------------------------------------------
CONFIG_DB_PATH = os.path.join(CORE_DIR, "mangascraper.db")
CONFIG_TABLE = "Config"

# Ensure NHentai directory exists
os.makedirs(SCRAPER_DIR, exist_ok=True)

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
    "tileMinWidthPx": 240,
    "tileAspectRatio": "2 / 3",
    "creatorPageSize": 20,
    "galleryPageSize": 20,
    "galleryTilesPerPage": [7, 14, 28, 56, 70],
}

DASHBOARD_COLLECTION_VIEW_CONFIG = {
    "tileMinWidthPx": 240,
    "tileAspectRatio": "2 / 3",
    "collectionPageSize": 20,
    "galleryTilesPerPage": [14, 28, 42, 56, 70],
}

DASHBOARD_OTHER_VIEWS_CONFIG = {
    "databasePageSize": 25,
}

# ------------------------------------------------------------
# NHentai Scraper Configuration Defaults
# ------------------------------------------------------------

DEFAULT_DOWNLOAD_PATH = os.path.join(SCRAPER_DIR, "downloads")
download_path = DEFAULT_DOWNLOAD_PATH  # public variable

DEFAULT_DOUJIN_TXT_PATH = os.path.join(SCRAPER_DIR, "Doujinshi_IDs.txt")
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

DEFAULT_EXTENSION_DOWNLOAD_PATH = DEFAULT_DOWNLOAD_PATH
extension_download_path = DEFAULT_EXTENSION_DOWNLOAD_PATH


# ------------------------------------------------------------
# APIs and Mirrors
# ------------------------------------------------------------
DEFAULT_NHENTAI_API_BASE = "https://nhentai.net/api/v2"
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
DEFAULT_USE_TOR = (os.name != "nt")
use_tor = DEFAULT_USE_TOR

DEFAULT_SKIP_POST_BATCH = False
skip_post_batch = DEFAULT_SKIP_POST_BATCH

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
    if val is None:
        return default
    text = _clean_env_string(val)
    if text == "":
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _clean_env_string(value):
    """Trim and unquote a scalar env string value."""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1].strip()
    return text


def _is_legacy_linux_download_path(path_text: str) -> bool:
    text = _clean_env_string(path_text).replace("\\", "/").lower().rstrip("/")
    return text.startswith("/opt/manga-scraper")


def _normalise_path_default_for_windows(key: str, value):
    if os.name != "nt":
        return value
    if key in ("DOWNLOAD_PATH", "EXTENSION_DOWNLOAD_PATH", "DOUJIN_TXT_PATH") and _is_legacy_linux_download_path(value):
        if key == "DOWNLOAD_PATH":
            return DEFAULT_DOWNLOAD_PATH
        if key == "EXTENSION_DOWNLOAD_PATH":
            return DEFAULT_EXTENSION_DOWNLOAD_PATH
        if key == "DOUJIN_TXT_PATH":
            return DEFAULT_DOUJIN_TXT_PATH
    return value


def _parse_bool(value, default=False):
    """Normalise bool-like values from env/config payloads."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        text = _clean_env_string(value).lower()
        if text in ("1", "true", "yes", "y", "on"):
            return True
        if text in ("0", "false", "no", "n", "off", ""):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _format_config_store_value(key: str, value) -> str:
    if key in ("EXCLUDED_TAGS", "LANGUAGE", "NHENTAI_MIRRORS", "GALLERIES"):
        if isinstance(value, (list, tuple, set)):
            return ",".join(str(v).strip() for v in value if str(v).strip())
    if key in ("USE_TOR", "SKIP_POST_BATCH", "SKIP_POST_RUN", "DRY_RUN", "CALM", "DEBUG", "VERIFY_SSL", "USE_DAEMON_THREADS"):
        return "true" if _parse_bool(value) else "false"
    return str(value)


def _with_config_db(operation):
    os.makedirs(CORE_DIR, exist_ok=True)
    conn = sqlite3.connect(CONFIG_DB_PATH, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    try:
        return operation(conn)
    finally:
        conn.close()


def _ensure_config_table(conn):
    # One-time migration from old RuntimeConfig table name.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS Config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    if CONFIG_TABLE != "Config":
        raise RuntimeError("CONFIG_TABLE must remain 'Config'.")

    has_legacy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='RuntimeConfig'"
    ).fetchone() is not None

    if has_legacy:
        conn.execute(
            """
            INSERT OR IGNORE INTO Config (key, value, updated_at)
            SELECT key, value, COALESCE(updated_at, ?) FROM RuntimeConfig
            """,
            (datetime.utcnow().isoformat(),),
        )
        conn.execute("DROP TABLE RuntimeConfig")

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CONFIG_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _bootstrap_config_store(seed_config: dict):
    now_iso = datetime.utcnow().isoformat()

    def _op(conn):
        _ensure_config_table(conn)
        for key, value in seed_config.items():
            conn.execute(
                f"INSERT OR IGNORE INTO {CONFIG_TABLE} (key, value, updated_at) VALUES (?, ?, ?)",
                (str(key), _format_config_store_value(str(key), value), now_iso),
            )
        conn.commit()

    with_env_lock(_with_config_db, _op)


def _load_config_store_values() -> dict:
    def _op(conn):
        _ensure_config_table(conn)
        rows = conn.execute(f"SELECT key, value FROM {CONFIG_TABLE}").fetchall()
        return {str(k): v for k, v in rows}

    return with_env_lock(_with_config_db, _op)


def _save_config_value(key: str, value):
    now_iso = datetime.utcnow().isoformat()

    def _op(conn):
        _ensure_config_table(conn)
        conn.execute(
            f"""
            INSERT INTO {CONFIG_TABLE} (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (str(key), _format_config_store_value(str(key), value), now_iso),
        )
        conn.commit()

    with_env_lock(_with_config_db, _op)

# ------------------------------------------------------------
# Config Dictionary
# ------------------------------------------------------------

# Also change corresponding parser.add_argument in CLI

# NHENTAI_MIRRORS: always a list
MIRRORS_ENV = os.getenv("NHENTAI_MIRRORS", DEFAULT_NHENTAI_MIRRORS)
if isinstance(MIRRORS_ENV, str):
    MIRRORS_LIST = [_clean_env_string(m) for m in MIRRORS_ENV.split(",") if _clean_env_string(m)]
else:
    MIRRORS_LIST = list(MIRRORS_ENV)

config = {
    "DOUJIN_TXT_PATH": os.getenv("DOUJIN_TXT_PATH", DEFAULT_DOUJIN_TXT_PATH),
    "DOWNLOAD_PATH": os.getenv("DOWNLOAD_PATH", DEFAULT_DOWNLOAD_PATH),
    "EXTENSION": os.getenv("EXTENSION", DEFAULT_EXTENSION),
    "EXTENSION_DOWNLOAD_PATH": os.getenv("EXTENSION_DOWNLOAD_PATH", DEFAULT_EXTENSION_DOWNLOAD_PATH),
    "NHENTAI_API_BASE": os.getenv("NHENTAI_API_BASE", DEFAULT_NHENTAI_API_BASE),
    "NHENTAI_MIRRORS": MIRRORS_LIST,
    "PAGE_SORT": os.getenv("PAGE_SORT", DEFAULT_PAGE_SORT),
    "PAGE_RANGE_START": getenv_numeric_value("PAGE_RANGE_START", DEFAULT_PAGE_RANGE_START),
    "PAGE_RANGE_END": getenv_numeric_value("PAGE_RANGE_END", DEFAULT_PAGE_RANGE_END),
    "RANGE_START": getenv_numeric_value("RANGE_START", DEFAULT_RANGE_START),
    "RANGE_END": getenv_numeric_value("RANGE_END", DEFAULT_RANGE_END),
    "GALLERIES": os.getenv("GALLERIES", DEFAULT_GALLERIES),
    "EXCLUDED_TAGS": os.getenv("EXCLUDED_TAGS", DEFAULT_EXCLUDED_TAGS),
    "LANGUAGE": os.getenv("LANGUAGE", DEFAULT_LANGUAGE),
    "TITLE_TYPE": os.getenv("TITLE_TYPE", DEFAULT_TITLE_TYPE),
    "GALLERY_FORMAT": os.getenv("GALLERY_FORMAT", DEFAULT_GALLERY_FORMAT),
    "THREADS_GALLERIES": getenv_numeric_value("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES),
    "THREADS_IMAGES": getenv_numeric_value("THREADS_IMAGES", DEFAULT_THREADS_IMAGES),
    "USE_DAEMON_THREADS": _parse_bool(os.getenv("USE_DAEMON_THREADS"), DEFAULT_USE_DAEMON_THREADS),
    "MAX_RETRIES": getenv_numeric_value("MAX_RETRIES", DEFAULT_MAX_RETRIES),
    "VERIFY_SSL": _parse_bool(os.getenv("VERIFY_SSL"), DEFAULT_VERIFY_SSL),
    "USE_TOR": _parse_bool(os.getenv("USE_TOR"), DEFAULT_USE_TOR),
    "SKIP_POST_BATCH": _parse_bool(os.getenv("SKIP_POST_BATCH"), DEFAULT_SKIP_POST_BATCH),
    "SKIP_POST_RUN": _parse_bool(os.getenv("SKIP_POST_RUN"), DEFAULT_SKIP_POST_RUN),
    "DRY_RUN": _parse_bool(os.getenv("DRY_RUN"), DEFAULT_DRY_RUN),
    "CALM": _parse_bool(os.getenv("CALM"), DEFAULT_CALM),
    "DEBUG": _parse_bool(os.getenv("DEBUG"), DEFAULT_DEBUG),
}

# Seed the runtime config store from env-derived values, then load authoritative values from DB.
_bootstrap_config_store(config)
config.update(_load_config_store_values())

##################

# ------------------------------------------------------------
# Runtime Config Sync
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

        config.update(_load_config_store_values())

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
    Normalise config with defaults persisted to Config in SQLite.
    """
    log_clarification("debug")
    log("Populating Config...", "debug")

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
            _save_config_value(key, default_val)

    # Ensure DB has all keys from current runtime config (legacy migration + new keys).
    for key, value in config.items():
        _save_config_value(key, value)
    
    refresh_globals()

def normalise_value(key: str, value):
    """
    Normalise values from config store to consistent runtime types.
    """
    value = _normalise_path_default_for_windows(key, value)
    
    if key == "NHENTAI_MIRRORS":
        if isinstance(value, str):
            mirrors = [_clean_env_string(m) for m in value.split(",") if _clean_env_string(m)]
        elif isinstance(value, list):
            mirrors = [_clean_env_string(v) for v in value if _clean_env_string(v)]
        else:
            mirrors = [DEFAULT_NHENTAI_MIRRORS]
        # Ensure default mirror is first
        return [DEFAULT_NHENTAI_MIRRORS] + [m for m in mirrors if m != DEFAULT_NHENTAI_MIRRORS]
    
    if key in ("EXCLUDED_TAGS", "LANGUAGE"):
        if isinstance(value, str):
            stripped = _clean_env_string(value)
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    parsed = ast.literal_eval(stripped)
                except (ValueError, SyntaxError):
                    parsed = None
                if isinstance(parsed, (list, tuple)):
                    return [str(v).strip().lower() for v in parsed if str(v).strip()]
            return [_clean_env_string(v).lower() for v in value.split(",") if _clean_env_string(v)]
        elif isinstance(value, list):
            return [_clean_env_string(v).lower() for v in value if _clean_env_string(v)]
        else:
            return []

    if key == "GALLERIES":
        if isinstance(value, str):
            stripped = _clean_env_string(value)
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
                v = _clean_env_string(v)
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
        fmt = _clean_env_string(value).lower()
        if fmt not in ("directory", "zip", "cbz"):
            return DEFAULT_GALLERY_FORMAT
        return fmt
    
    if key in ("USE_TOR", "SKIP_POST_BATCH", "SKIP_POST_RUN", "DRY_RUN", "CALM", "DEBUG", "VERIFY_SSL", "USE_DAEMON_THREADS"):
        return _parse_bool(value)

    if key in ("THREADS_GALLERIES", "THREADS_IMAGES", "MAX_RETRIES", "PAGE_RANGE_START", "PAGE_RANGE_END", "RANGE_START", "RANGE_END"):
        defaults = {
            "THREADS_GALLERIES": DEFAULT_THREADS_GALLERIES,
            "THREADS_IMAGES": DEFAULT_THREADS_IMAGES,
            "MAX_RETRIES": DEFAULT_MAX_RETRIES,
            "PAGE_RANGE_START": DEFAULT_PAGE_RANGE_START,
            "PAGE_RANGE_END": DEFAULT_PAGE_RANGE_END,
            "RANGE_START": DEFAULT_RANGE_START,
            "RANGE_END": DEFAULT_RANGE_END,
        }
        try:
            return int(float(_clean_env_string(value)))
        except (TypeError, ValueError):
            return int(defaults[key])

    # Default: return as string
    return _clean_env_string(value)

def ensure_env_file(overwrite: bool = False):
    # Compatibility shim: keep existing call sites functional.
    # Runtime config is database-only and synced via normalise_config().
    normalise_config()

def update_env(key, value):
    """
    Update a single config key in the SQLite Config store.
    Kept as update_env for API compatibility.
    """
    
    global threads_galleries, threads_images, max_retries, min_retry_sleep, max_retry_sleep
    
    _save_config_value(key, value)
    config[key] = normalise_value(key, value)
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