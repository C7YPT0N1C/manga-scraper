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

# ------------------------------
# Config dictionary
# ------------------------------
# Also change corresponding parser.add_argument in CLI
API_BASE = "https://nhentai.net/api" # Set NHentai API Base here. Overrides .env .

config = {
    "DOWNLOAD_PATH": os.getenv("DOWNLOAD_PATH", "/opt/nhentai-scraper/downloads"),
    "EXTENSION_DOWNLOAD_PATH": os.getenv("EXTENSION_DOWNLOAD_PATH", ""),
    "NHENTAI_API_BASE": os.getenv(f"NHENTAI_API_BASE", {API_BASE}),
    "NHENTAI_MIRRORS": os.getenv("NHENTAI_MIRRORS", "https://i.nhentai.net"),
    "HOMEPAGE_RANGE_START": int(os.getenv("HOMEPAGE_RANGE_START", 1)),
    "HOMEPAGE_RANGE_END": int(os.getenv("HOMEPAGE_RANGE_END", 3)),
    "RANGE_START": int(os.getenv("RANGE_START", 500000)),
    "RANGE_END": int(os.getenv("RANGE_END", 600000)),
    "GALLERIES": os.getenv("GALLERIES", ""),
    "ARTIST": os.getenv("ARTIST", ""),
    "GROUP": os.getenv("GROUP", ""),
    "TAG": os.getenv("TAG", ""),
    "PARODY": os.getenv("PARODY", ""),
    "EXCLUDED_TAGS": os.getenv("EXCLUDED_TAGS", ""),
    "LANGUAGE": os.getenv("LANGUAGE", "english"),
    "TITLE_TYPE": os.getenv("TITLE_TYPE", "english"),
    "TITLE_SANITISE": os.getenv("TITLE_SANITISE", "false").lower() == "true",
    "THREADS_GALLERIES": int(os.getenv("THREADS_GALLERIES", 2)),
    "THREADS_IMAGES": int(os.getenv("THREADS_IMAGES", 10)),
    "MAX_RETRIES": int(os.getenv("MAX_RETRIES", 3)),
    "USE_TOR": os.getenv("USE_TOR", "false").lower() == "true",
    "DRY_RUN": os.getenv("DRY_RUN", "false").lower() == "true",
    "VERBOSE": os.getenv("VERBOSE", "false").lower() == "true",
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
    ext_path = config.get("EXTENSION_DOWNLOAD_PATH", "").strip()
    if ext_path and os.path.isdir(ext_path):
        return ext_path
    return config.get("DOWNLOAD_PATH")

# ------------------------------
# Get mirrors list
# ------------------------------
def get_mirrors():
    env_mirrors = config.get("NHENTAI_MIRRORS", "")
    mirrors = []
    if env_mirrors:
        mirrors = [m.strip() for m in env_mirrors.split(",") if m.strip()]
    # Ensure default mirror is first
    mirrors = ["https://i.nhentai.net"] + [m for m in mirrors if m != "https://i.nhentai.net"]
    return mirrors

MIRRORS = get_mirrors()