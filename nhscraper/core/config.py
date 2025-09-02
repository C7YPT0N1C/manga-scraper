#!/usr/bin/env python3
# core/config.py

import os, logging
from datetime import datetime
from dotenv import load_dotenv, set_key

##########################################################################################
# LOGGER
##########################################################################################

LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Runtime log
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
RUNTIME_LOG_FILE = os.path.join(LOG_DIR, f"runtime-{timestamp}.log")

# --- Placeholder logger so imports don’t crash before setup_logger() runs ---
logger = logging.getLogger("nhscraper")
if not logger.handlers: # Only add default handler if none exist (prevents duplicates on reload)
    placeholder_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    placeholder_console = logging.StreamHandler()
    placeholder_console.setLevel(logging.INFO)   # Default to INFO
    placeholder_console.setFormatter(placeholder_formatter)
    logger.addHandler(placeholder_console)

    # Default logger level = INFO (so modules print their "Ready" messages)
    logger.setLevel(logging.INFO)

# ------------------------------
# LOG CLARIFICATION
# Prints Blank Line To Make Logs Look Cleaner)
# ------------------------------
def log_clarification():
    if logger.getEffectiveLevel == 20:
        print() # Only print new life if log level is INFO
    logger.debug("")

log_clarification()
logger.info("Logger: Ready.")
logger.debug("Logger: Debugging Started.")

def setup_logger(verbose=False):
    """
    Configure the nhscraper logger.
    Ensures no duplicate handlers and sets levels based on flags/config.
    """

    # Always get the same logger
    logger = logging.getLogger("nhscraper")
    logger.handlers.clear()

    # --- Formatter ---
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # --- Console handler ---
    ch = logging.StreamHandler()
    if  verbose:
        logger.setLevel(logging.DEBUG)
        ch.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
        ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Runtime log (new file per run, timestamped)
    fh_runtime = logging.FileHandler(RUNTIME_LOG_FILE, mode="a", encoding="utf-8")
    fh_runtime.setLevel(logging.DEBUG)
    fh_runtime.setFormatter(formatter)
    logger.addHandler(fh_runtime)

    # Announce level
    if verbose:
        logger.info("Log Level Set To DEBUG")
    else:
        logger.info("Log Level Set To INFO")

    return logger

# --- Placeholder logger so imports don’t crash before setup_logger() runs ---
logger = logging.getLogger("nhscraper")
logger.addHandler(logging.NullHandler())

##########################################################################################
# LOGGER
##########################################################################################

# ------------------------------
# Paths & Env
# ------------------------------
NHENTAI_DIR = "/opt/nhentai-scraper"
ENV_FILE = os.path.join(NHENTAI_DIR, "nhentai-scraper.env")

# Ensure NHentai directory exists
os.makedirs(NHENTAI_DIR, exist_ok=True)

# Load environment variables
if os.path.exists(ENV_FILE):
    load_dotenv(dotenv_path=ENV_FILE)

# ------------------------------------------------------------
# NHentai Scraper Configuration Defaults
# ------------------------------------------------------------

# Default Download Path
DEFAULT_DOWNLOAD_PATH="/opt/nhentai-scraper/downloads"

# Extensions
DEFAULT_EXTENSION="skeleton"
DEFAULT_EXTENSION_DOWNLOAD_PATH="/opt/nhentai-scraper/downloads"

# APIs and Mirrors
DEFAULT_NHENTAI_API_BASE="https://nhentai.net/api"
DEFAULT_NHENTAI_MIRRORS="https://i.nhentai.net"

# Gallery ID selection
DEFAULT_HOMEPAGE_RANGE_START=1
DEFAULT_HOMEPAGE_RANGE_END=3
DEFAULT_RANGE_START=500000
DEFAULT_RANGE_END=600000
DEFAULT_GALLERIES=""

# Filters
DEFAULT_EXCLUDED_TAGS=""
DEFAULT_LANGUAGE="english"

# Titles
DEFAULT_TITLE_TYPE="english"
DEFAULT_TITLE_SANITISE=True

# Threads
DEFAULT_THREADS_GALLERIES=2
DEFAULT_THREADS_IMAGES=10
DEFAULT_MAX_RETRIES=3

# Download Options
DEFAULT_USE_TOR=True
DEFAULT_DRY_RUN=False
DEFAULT_VERBOSE=False

# ------------------------------------------------------------
# Config Dictionary
# ------------------------------------------------------------

# Also change corresponding parser.add_argument in CLI
config = {
    "DOWNLOAD_PATH": os.getenv("DOWNLOAD_PATH", DEFAULT_DOWNLOAD_PATH),
    "EXTENSION": os.getenv("EXTENSION", DEFAULT_EXTENSION),
    "EXTENSION_DOWNLOAD_PATH": os.getenv("EXTENSION_DOWNLOAD_PATH", DEFAULT_EXTENSION_DOWNLOAD_PATH),
    "NHENTAI_API_BASE": os.getenv("NHENTAI_API_BASE", DEFAULT_NHENTAI_API_BASE),
    "NHENTAI_MIRRORS": os.getenv("NHENTAI_MIRRORS", DEFAULT_NHENTAI_MIRRORS),
    "HOMEPAGE_RANGE_START": int(os.getenv("HOMEPAGE_RANGE_START", DEFAULT_HOMEPAGE_RANGE_START)),
    "HOMEPAGE_RANGE_END": int(os.getenv("HOMEPAGE_RANGE_END", DEFAULT_HOMEPAGE_RANGE_END)),
    "RANGE_START": int(os.getenv("RANGE_START", DEFAULT_RANGE_START)),
    "RANGE_END": int(os.getenv("RANGE_END", DEFAULT_RANGE_END)),
    "GALLERIES": os.getenv("GALLERIES", DEFAULT_GALLERIES),
    "ARTIST": os.getenv("ARTIST", ""),
    "GROUP": os.getenv("GROUP", ""),
    "TAG": os.getenv("TAG", ""),
    "PARODY": os.getenv("PARODY", ""),
    "EXCLUDED_TAGS": os.getenv("EXCLUDED_TAGS", DEFAULT_EXCLUDED_TAGS),
    "LANGUAGE": os.getenv("LANGUAGE", DEFAULT_LANGUAGE),
    "TITLE_TYPE": os.getenv("TITLE_TYPE", DEFAULT_TITLE_TYPE),
    "TITLE_SANITISE": os.getenv("TITLE_SANITISE", DEFAULT_TITLE_SANITISE).lower() == "true",
    "THREADS_GALLERIES": int(os.getenv("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES)),
    "THREADS_IMAGES": int(os.getenv("THREADS_IMAGES", DEFAULT_THREADS_IMAGES)),
    "MAX_RETRIES": int(os.getenv("MAX_RETRIES", DEFAULT_MAX_RETRIES)),
    "USE_TOR": os.getenv("USE_TOR", DEFAULT_USE_TOR).lower() == "true",
    "DRY_RUN": os.getenv("DRY_RUN", DEFAULT_DRY_RUN).lower() == "true",
    "VERBOSE": os.getenv("VERBOSE", DEFAULT_VERBOSE).lower() == "true",
}

# ------------------------------
# Update .env safely
# ------------------------------
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

# ------------------------------
# Dynamic download path
# ------------------------------
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

# ------------------------------
# Get mirrors list
# ------------------------------
def get_mirrors():
    env_mirrors = config.get("NHENTAI_MIRRORS", DEFAULT_NHENTAI_MIRRORS)
    mirrors = []
    if env_mirrors:
        mirrors = [m.strip() for m in env_mirrors.split(",") if m.strip()]
    # Ensure default mirror is first
    mirrors = [DEFAULT_NHENTAI_MIRRORS] + [m for m in mirrors if m != DEFAULT_NHENTAI_MIRRORS]
    return mirrors

MIRRORS = get_mirrors()