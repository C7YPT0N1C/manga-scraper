#!/usr/bin/env python3
# core/config.py

import os, logging
from datetime import datetime
from dotenv import load_dotenv, set_key
from nhscraper.core.cleaning_helper import ALLOWED_SYMBOLS, BROKEN_SYMBOL_BLACKLIST, BROKEN_SYMBOL_REPLACEMENTS

##########################################################################################
# LOGGER
##########################################################################################

LOG_DIR = "/opt/nhentai-scraper/logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Runtime log
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
RUNTIME_LOG_FILE = os.path.join(LOG_DIR, f"runtime-{timestamp}.log")

# --- Placeholder logger so imports don’t crash before setup_logger() runs ---
logger = logging.getLogger("nhscraper")
if not logger.handlers: # Only add default handler if none exist (prevents duplicates on reload)
    placeholder_formatter = logging.Formatter("[%(levelname)s] %(message)s")
    placeholder_console = logging.StreamHandler()
    placeholder_console.setLevel(logging.INFO)   # Default to INFO
    placeholder_console.setFormatter(placeholder_formatter)
    logger.addHandler(placeholder_console)

    # Default logger level = INFO
    logger.setLevel(logging.INFO)

# ------------------------------------------------------------
# LOG CLARIFICATION
# Prints Blank Line To Make Logs Look Cleaner)
# ------------------------------------------------------------
def log_clarification():
    if logger.getEffectiveLevel == 20:
        print("") # Only print new line if log level is INFO
    logger.debug("")

log_clarification()
logger.debug("Logger: Ready.")
logger.debug("Logger: Debugging Started.")

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

def setup_logger(calm=False, debug=False):
    """
    Configure the nhscraper logger.
    - Console respects calm/debug flags with conditional formatting
    - File logs always DEBUG with full level info
    """
    
    logger = logging.getLogger("nhscraper")
    logger.handlers.clear()  # Remove previous handlers

    # ----------------------------
    # Console handler
    # ----------------------------
    ch = logging.StreamHandler()
    if debug:
        ch.setLevel(logging.DEBUG)
    elif calm:
        ch.setLevel(logging.WARNING)
    else:
        ch.setLevel(logging.INFO)
    ch.setFormatter(ConditionalFormatter())
    logger.addHandler(ch)

    # ----------------------------
    # File handler: always DEBUG
    # ----------------------------
    fh = logging.FileHandler(RUNTIME_LOG_FILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    # Logger level: DEBUG ensures all messages reach file handler
    logger.setLevel(logging.DEBUG)

    # Initialisation summary
    logger.info("Logger initialised. Console level: %s",
                "DEBUG" if debug else "WARNING" if calm else "INFO")
    
    return logger

# --- Placeholder logger so imports don’t crash before setup_logger() runs ---
logger = logging.getLogger("nhscraper")
logger.addHandler(logging.NullHandler())

def log(message: str, log_type: str = "info"):
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

# ------------------------------------------------------------
# NHentai Scraper Configuration Defaults
# ------------------------------------------------------------

# Default Paths
DEFAULT_DOWNLOAD_PATH="/opt/nhentai-scraper/downloads"
download_path = DEFAULT_DOWNLOAD_PATH # Load initial value to public variable on import


DEFAULT_DOUJIN_TXT_PATH="/root/Doujinshi_IDs.txt"
if not os.path.exists(DEFAULT_DOUJIN_TXT_PATH):
    # Create an empty file with a comment line
    with open(DEFAULT_DOUJIN_TXT_PATH, "w", encoding="utf-8") as f:
        f.write("# Add one nhentai URL or gallery ID per line\n")
    logger.info(f"Created default gallery file: {DEFAULT_DOUJIN_TXT_PATH}")
doujin_txt_path = DEFAULT_DOUJIN_TXT_PATH # Load initial value to public variable on import


# Extensions
DEFAULT_EXTENSION="skeleton"
extension = DEFAULT_EXTENSION # Load initial value to public variable on import

DEFAULT_EXTENSION_DOWNLOAD_PATH="/opt/nhentai-scraper/downloads"
extension_download_path = DEFAULT_EXTENSION_DOWNLOAD_PATH # Load initial value to public variable on import


# APIs and Mirrors
DEFAULT_NHENTAI_API_BASE="https://nhentai.net/api"
nhentai_api_base = DEFAULT_NHENTAI_API_BASE # Load initial value to public variable on import

DEFAULT_NHENTAI_MIRRORS="https://i.nhentai.net"
nhentai_mirrors = DEFAULT_NHENTAI_MIRRORS # Load initial value to public variable on import


# Gallery ID selection
DEFAULT_PAGE_RANGE_START=1
homepage_range_start = DEFAULT_PAGE_RANGE_START # Load initial value to public variable on import

DEFAULT_PAGE_RANGE_END=2
homepage_range_end = DEFAULT_PAGE_RANGE_END # Load initial value to public variable on import

DEFAULT_RANGE_START=500000
range_start = DEFAULT_RANGE_START # Load initial value to public variable on import

DEFAULT_RANGE_END=600000
range_end = DEFAULT_RANGE_END # Load initial value to public variable on import

DEFAULT_GALLERIES=""
galleries = DEFAULT_GALLERIES # Load initial value to public variable on import


# Filters
DEFAULT_EXCLUDED_TAGS="snuff,cuntboy,guro,cuntbusting,scat,coprophagia,ai generated"
excluded_tags = DEFAULT_EXCLUDED_TAGS # Load initial value to public variable on import

DEFAULT_LANGUAGE="english"
language = DEFAULT_LANGUAGE # Load initial value to public variable on import

DEFAULT_TITLE_TYPE="english"
title_type = DEFAULT_TITLE_TYPE # Load initial value to public variable on import


# Threads
DEFAULT_THREADS_GALLERIES=2
threads_galleries = DEFAULT_THREADS_GALLERIES # Load initial value to public variable on import

DEFAULT_THREADS_IMAGES=10
threads_images = DEFAULT_THREADS_IMAGES # Load initial value to public variable on import

DEFAULT_MAX_RETRIES=3
max_retries = DEFAULT_MAX_RETRIES # Load initial value to public variable on import

DEFAULT_MIN_SLEEP=0.5
min_sleep = DEFAULT_MIN_SLEEP # Load initial value to public variable on import

DEFAULT_MAX_SLEEP=100
max_sleep = DEFAULT_MAX_SLEEP # Load initial value to public variable on import


# Download Options
DEFAULT_USE_TOR=True
use_tor = DEFAULT_USE_TOR # Load initial value to public variable on import

DEFAULT_DRY_RUN=False
dry_run = DEFAULT_DRY_RUN # Load initial value to public variable on import


DEFAULT_CALM=False
calm = DEFAULT_CALM # Load initial value to public variable on import

DEFAULT_DEBUG=False
debug = DEFAULT_DEBUG # Load initial value to public variable on import


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
MIRRORS_ENV = getenv_numeric_value("NHENTAI_MIRRORS", DEFAULT_NHENTAI_MIRRORS)
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
    "HOMEPAGE_RANGE_START": getenv_numeric_value("HOMEPAGE_RANGE_START", DEFAULT_PAGE_RANGE_START),
    "HOMEPAGE_RANGE_END": getenv_numeric_value("HOMEPAGE_RANGE_END", DEFAULT_PAGE_RANGE_END),
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
    "USE_TOR": str(os.getenv("USE_TOR", DEFAULT_USE_TOR)).lower() == "true",
    "DRY_RUN": str(os.getenv("DRY_RUN", DEFAULT_DRY_RUN)).lower() == "true",
    "CALM": str(os.getenv("CALM", DEFAULT_CALM)).lower() == "true",
    "DEBUG": str(os.getenv("DEBUG", DEFAULT_DEBUG)).lower() == "true",
}

# ------------------------------------------------------------
# Update .env safely
# ------------------------------------------------------------
def update_env(key, value):
    """
    Update a single variable in the .env file.
    If the key doesn't exist, append it.
    """
    
    if not os.path.exists(ENV_FILE):
        # create empty file if missing
        with open(ENV_FILE, "w") as f:
            f.write("")

    # Use set_key from dotenv to safely update
    set_key(ENV_FILE, key, str(value))

    # Update runtime config
    config[key] = value

def fetch_env_vars():
    """
    Update environment variables used by this module.
    Any module that uses any these variables can call this function to ensure they are up to date.
    """
    
    global download_path, doujin_txt_path, extension, extension_download_path, nhentai_api_base, nhentai_mirrors
    global homepage_range_start, homepage_range_end, range_start, range_end, galleries, excluded_tags
    global language, title_type, threads_galleries, threads_images, max_retries, min_sleep, max_sleep
    global use_tor, dry_run, calm, debug
    
    # Update variables from config
    download_path = config.get("DOWNLOAD_PATH", DEFAULT_DOWNLOAD_PATH)
    doujin_txt_path = config.get("DOUJIN_TXT_PATH", DEFAULT_DOUJIN_TXT_PATH)
    extension = config.get("EXTENSION", DEFAULT_EXTENSION)
    extension_download_path = config.get("EXTENSION_DOWNLOAD_PATH", DEFAULT_EXTENSION_DOWNLOAD_PATH)
    nhentai_api_base = config.get("NHENTAI_API_BASE", DEFAULT_NHENTAI_API_BASE)
    nhentai_mirrors = config.get("NHENTAI_MIRRORS", DEFAULT_NHENTAI_MIRRORS)
    homepage_range_start = config.get("HOMEPAGE_RANGE_START", DEFAULT_PAGE_RANGE_START)
    homepage_range_end = config.get("HOMEPAGE_RANGE_END", DEFAULT_PAGE_RANGE_END)
    range_start = config.get("RANGE_START", DEFAULT_RANGE_START)
    range_end = config.get("RANGE_END", DEFAULT_RANGE_END)
    galleries = config.get("GALLERIES", DEFAULT_GALLERIES)
    excluded_tags = config.get("EXCLUDED_TAGS", DEFAULT_EXCLUDED_TAGS)
    language = config.get("LANGUAGE", DEFAULT_LANGUAGE)
    title_type = config.get("TITLE_TYPE", DEFAULT_TITLE_TYPE)
    threads_galleries = config.get("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES)
    threads_images = config.get("THREADS_IMAGES", DEFAULT_THREADS_IMAGES)
    max_retries = config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES)
    min_sleep = config.get("MIN_SLEEP", DEFAULT_MIN_SLEEP)
    max_sleep = config.get("MAX_SLEEP", DEFAULT_MAX_SLEEP)
    use_tor = config.get("USE_TOR", DEFAULT_USE_TOR)
    dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)
    calm = config.get("CALM", DEFAULT_CALM)
    debug = config.get("DEBUG", DEFAULT_DEBUG)

# ------------------------------------------------------------
# Normalise config with defaults
# ------------------------------------------------------------
def normalise_config():
    log_clarification()
    log("Populating Config...", "debug")
    
    defaults = {
        "DOUJIN_TXT_PATH": DEFAULT_DOUJIN_TXT_PATH,
        "DOWNLOAD_PATH": DEFAULT_DOWNLOAD_PATH,
        "EXTENSION": DEFAULT_EXTENSION,
        "EXTENSION_DOWNLOAD_PATH": DEFAULT_EXTENSION_DOWNLOAD_PATH,
        "NHENTAI_API_BASE": DEFAULT_NHENTAI_API_BASE,
        "NHENTAI_MIRRORS": DEFAULT_NHENTAI_MIRRORS,
        "HOMEPAGE_RANGE_START": DEFAULT_PAGE_RANGE_START,
        "HOMEPAGE_RANGE_END": DEFAULT_PAGE_RANGE_END,
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
        "DRY_RUN": DEFAULT_DRY_RUN,
        "CALM": DEFAULT_CALM,
        "DEBUG": DEFAULT_DEBUG,
    }

    for key, default_val in defaults.items():
        val = config.get(key)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            config[key] = default_val
            update_env(key, default_val)

# Run normalisation immediately so .env is populated
normalise_config()

# ------------------------------------------------------------
# Dynamic download path
# ------------------------------------------------------------
def get_download_path():
    """
    Returns the path to use for downloads.
    Priority:
        1. EXTENSION_DOWNLOAD_PATH if set and valid
        2. Default DOWNLOAD_PATH
    """
    
    ext_path = config.get("EXTENSION_DOWNLOAD_PATH", DEFAULT_EXTENSION_DOWNLOAD_PATH).strip()
    if ext_path and os.path.isdir(ext_path):
        return ext_path
    return config.get("DOWNLOAD_PATH", DEFAULT_DOWNLOAD_PATH)

# ------------------------------------------------------------
# Get mirrors list
# ------------------------------------------------------------
def get_mirrors():
    env_mirrors = config.get("NHENTAI_MIRRORS", DEFAULT_NHENTAI_MIRRORS)
    mirrors = []

    if isinstance(env_mirrors, str):
        # comma-separated string from environment
        mirrors = [m.strip() for m in env_mirrors.split(",") if m.strip()]
    elif isinstance(env_mirrors, list):
        # already a list
        mirrors = env_mirrors
    else:
        # fallback
        mirrors = [DEFAULT_NHENTAI_MIRRORS]

    # Ensure default mirror is first
    mirrors = [DEFAULT_NHENTAI_MIRRORS] + [m for m in mirrors if m != DEFAULT_NHENTAI_MIRRORS]
    return mirrors

MIRRORS = get_mirrors()

# ------------------------------------------------------------
# Fetch latest env vars
# ------------------------------------------------------------
























