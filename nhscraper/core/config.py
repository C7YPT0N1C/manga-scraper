#!/usr/bin/env python3
# core/config.py

import os, logging
from datetime import datetime
from dotenv import load_dotenv, set_key

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
    placeholder_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
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

def setup_logger(verbose=False, debug=False):
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
    if debug:
        logger.setLevel(logging.DEBUG)
        ch.setLevel(logging.DEBUG)
    elif verbose:
        logger.setLevel(logging.INFO)
        ch.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)
        ch.setLevel(logging.WARNING)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Runtime log (new file per run, timestamped)
    fh_runtime = logging.FileHandler(RUNTIME_LOG_FILE, mode="a", encoding="utf-8")
    fh_runtime.setLevel(logging.DEBUG)
    fh_runtime.setFormatter(formatter)
    logger.addHandler(fh_runtime)

    # Announce level
    if debug:
        logger.info("Log Level Set To DEBUG")
    elif verbose:
        logger.info("Log Level Set To INFO")
    else:
        logger.info("Log Level Set To WARNING")

    return logger

# --- Placeholder logger so imports don’t crash before setup_logger() runs ---
logger = logging.getLogger("nhscraper")
logger.addHandler(logging.NullHandler())

def log(message: str, log_type: str = None):
    """Unified logging for CLI depending on verbose/debug flags."""
    debug_mode = config.get("DEBUG")
    verbose_mode = config.get("VERBOSE")

    if log_type == None:
        if logger.getEffectiveLevel == 10: # Only log if log level is DEBUG
            logger.debug(message)  # Log as debug
        if logger.getEffectiveLevel == 20: # Only log if log level is INFO
            logger.info(message)  # Log as debug
        else:
            print(message)         # Always print to terminal
    
    elif log_type == "debug":
        logger.debug(message)  # Always log debug to file if DEBUG or VERBOSE
    
    elif log_type == "info":
        logger.info(message)   # Log info to file and terminal
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
DEFAULT_EXCLUDED_TAGS="snuff,cuntboy,guro,cuntbusting,ai generated"
DEFAULT_LANGUAGE="english"
DEFAULT_TITLE_TYPE="english"
DODGY_SYMBOL_BLACKLIST = ["↑", "↓", "→", "←", "★", "☆", "♥", "♪", "◆", "◇", "※", "✔", "✖", "•", "●", "…", "@",
                          "¤", "¢", "£", "¥", "§", "¶", "†", "‡", "‰", "µ", "¦", "°", "¬", "<", ">", "$", "^"]

# Threads
DEFAULT_THREADS_GALLERIES=2
DEFAULT_THREADS_IMAGES=8
DEFAULT_MAX_RETRIES=3
DEFAULT_NO_SLEEP=False

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
    "NO_SLEEP": str(os.getenv("USE_TOR", DEFAULT_NO_SLEEP)).lower() == "true",
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
        "NO_SLEEP": DEFAULT_NO_SLEEP,
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

global_dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)