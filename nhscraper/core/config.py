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
    placeholder_console.setLevel(logging.WARNING)   # Default to WARNING
    placeholder_console.setFormatter(placeholder_formatter)
    logger.addHandler(placeholder_console)

    # Default logger level = WARNING
    logger.setLevel(logging.WARNING)

# ------------------------------------------------------------
# LOG CLARIFICATION
# Prints Blank Line To Make Logs Look Cleaner)
# ------------------------------------------------------------
def log_clarification():
    if logger.getEffectiveLevel == 20:
        print() # Only print new line if log level is INFO
    logger.debug("")

log_clarification()
logger.info("Logger: Ready.")
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

def setup_logger(verbose=False, debug=False):
    """
    Configure the nhscraper logger.
    - Console respects verbose/debug flags with conditional formatting
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
    elif verbose:
        ch.setLevel(logging.INFO)
    else:
        ch.setLevel(logging.WARNING)
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
                "DEBUG" if debug else "INFO" if verbose else "WARNING")
    
    return logger

# --- Placeholder logger so imports don’t crash before setup_logger() runs ---
logger = logging.getLogger("nhscraper")
logger.addHandler(logging.NullHandler())

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

# ------------------------------------------------------------
# Paths & Env
# ------------------------------------------------------------
NHENTAI_DIR = "/opt/nhentai-scraper"
ENV_FILE = os.path.join(NHENTAI_DIR, "nhentai-scraper.env")

# Ensure NHentai directory exists
os.makedirs(NHENTAI_DIR, exist_ok=True)

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

DEFAULT_DOUJIN_TXT_PATH="/root/Doujinshi_IDs.txt"
if not os.path.exists(DEFAULT_DOUJIN_TXT_PATH):
    # Create an empty file with a comment line
    with open(DEFAULT_DOUJIN_TXT_PATH, "w", encoding="utf-8") as f:
        f.write("# Add one nhentai URL or gallery ID per line\n")
    logger.info(f"Created default gallery file: {DEFAULT_DOUJIN_TXT_PATH}")

# Extensions
DEFAULT_EXTENSION="skeleton"
DEFAULT_EXTENSION_DOWNLOAD_PATH="/opt/nhentai-scraper/downloads"

# APIs and Mirrors
DEFAULT_NHENTAI_API_BASE="https://nhentai.net/api"
DEFAULT_NHENTAI_MIRRORS="https://i.nhentai.net"

# Gallery ID selection
DEFAULT_PAGE_RANGE_START=1
DEFAULT_PAGE_RANGE_END=2
DEFAULT_RANGE_START=500000
DEFAULT_RANGE_END=600000
DEFAULT_GALLERIES=""

# Filters
DEFAULT_EXCLUDED_TAGS="snuff,cuntboy,guro,cuntbusting,scat,coprophagia,ai generated"
DEFAULT_LANGUAGE="english"
DEFAULT_TITLE_TYPE="english"

# Threads
DEFAULT_THREADS_GALLERIES=2
DEFAULT_THREADS_IMAGES=10
DEFAULT_MAX_RETRIES=3
DEFAULT_MIN_SLEEP=0.5
DEFAULT_MAX_SLEEP=100
# Download Options
DEFAULT_USE_TOR=True
DEFAULT_DRY_RUN=False
DEFAULT_VERBOSE=False
DEFAULT_DEBUG=False

# ------------------------------------------------------------
# Helper: safe int from env
# ------------------------------------------------------------
def getenv_int(key, default):
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return int(val)

# ------------------------------------------------------------
# Config Dictionary
# ------------------------------------------------------------

# Also change corresponding parser.add_argument in CLI

# NHENTAI_MIRRORS: always a list
MIRRORS_ENV = getenv_int("NHENTAI_MIRRORS", DEFAULT_NHENTAI_MIRRORS)
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
    "HOMEPAGE_RANGE_START": getenv_int("HOMEPAGE_RANGE_START", DEFAULT_PAGE_RANGE_START),
    "HOMEPAGE_RANGE_END": getenv_int("HOMEPAGE_RANGE_END", DEFAULT_PAGE_RANGE_END),
    "RANGE_START": getenv_int("RANGE_START", DEFAULT_RANGE_START),
    "RANGE_END": getenv_int("RANGE_END", DEFAULT_RANGE_END),
    "GALLERIES": os.getenv("GALLERIES", DEFAULT_GALLERIES),
    "ARTIST": os.getenv("ARTIST", ""),
    "GROUP": os.getenv("GROUP", ""),
    "TAG": os.getenv("TAG", ""),
    "PARODY": os.getenv("PARODY", ""),
    "EXCLUDED_TAGS": os.getenv("EXCLUDED_TAGS", DEFAULT_EXCLUDED_TAGS),
    "LANGUAGE": os.getenv("LANGUAGE", DEFAULT_LANGUAGE),
    "TITLE_TYPE": os.getenv("TITLE_TYPE", DEFAULT_TITLE_TYPE),
    "THREADS_GALLERIES": getenv_int("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES),
    "THREADS_IMAGES": getenv_int("THREADS_IMAGES", DEFAULT_THREADS_IMAGES),
    "MAX_RETRIES": getenv_int("MAX_RETRIES", DEFAULT_MAX_RETRIES),
    "MIN_SLEEP": getenv_int("MIN_SLEEP", DEFAULT_MIN_SLEEP),
    "MIN_SLEEP": getenv_int("MAX_SLEEP", DEFAULT_MAX_SLEEP),
    "USE_TOR": str(os.getenv("USE_TOR", DEFAULT_USE_TOR)).lower() == "true",
    "DRY_RUN": str(os.getenv("DRY_RUN", DEFAULT_DRY_RUN)).lower() == "true",
    "VERBOSE": str(os.getenv("VERBOSE", DEFAULT_VERBOSE)).lower() == "true",
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
        "VERBOSE": DEFAULT_VERBOSE,
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