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

# Master log
#MASTER_LOG_FILE = os.path.join(LOG_DIR, "master.log")
# Runtime log with timestamp
#timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#RUNTIME_LOG_FILE = os.path.join(LOG_DIR, f"runtime-{timestamp}.log")

# Master log # TEST
MASTER_LOG_FILE = os.path.join(LOG_DIR, "000_master.log")

# Runtime log
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
RUNTIME_LOG_FILE = os.path.join(LOG_DIR, f"100_runtime-{timestamp}.log")

# Logger setup
logger = logging.getLogger("nhscraper")
logger.setLevel(logging.DEBUG)  # Capture everything; handlers decide visibility

# Ensure no duplicate handlers
if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Console handler (default INFO until setup_logger runs)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

# ------------------------------
# LOG CLARIFICATION
# Prints Blank Line To Make Logs Look Cleaner)
# ------------------------------
def log_clarification():  
    print()
    logger.debug("")

log_clarification()
logger.info("Logger: Ready.")
logger.debug("Logger: Debugging Started.")



def setup_logger(dry_run=False, verbose=False, log_file="nhscraper.log"):
    """
    Configure the nhscraper logger.
    Ensures no duplicate handlers and sets levels based on flags/config.
    """
    # LOGGING LEVELS:
    # logging.debug("This is a debug message")    # Not shown because level is INFO (log_level = 10)
    # logging.info("This is info")               # Shown (log_level = 20)
    # logging.warning("This is a warning")       # Shown (log_level = 30)
    # logging.error("This is an error")          # Shown (log_level = 40)
    # logging.critical("This is critical")       # Shown (log_level = 50)
    
    # Always get the same logger
    logger = logging.getLogger("nhscraper")

    # --- Clear existing handlers to prevent duplicates ---
    if logger.handlers:
        logger.handlers.clear()

    # --- Formatter ---
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # --- Console handler ---
    ch = logging.StreamHandler()
    if dry_run or verbose:
        logger.setLevel(logging.DEBUG)
        ch.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
        ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # --- File handler (always logs everything for debugging) ---
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    if dry_run or verbose:
        logger.info("Log Level Set To DEBUG")
    else:
        logger.info("Log Level Set To INFO")

    #return logger

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
config = {
    "DOWNLOAD_PATH": os.getenv("DOWNLOAD_PATH", "/opt/nhentai-scraper/downloads"),
    "EXTENSION_DOWNLOAD_PATH": os.getenv("EXTENSION_DOWNLOAD_PATH", ""),
    "NHENTAI_API_BASE": os.getenv("NHENTAI_API_BASE", "https://nhentai.net/api/galleries/search"),
    "NHENTAI_MIRRORS": os.getenv("NHENTAI_MIRRORS", "https://i.nhentai.net"),
    "RANGE_START": int(os.getenv("RANGE_START", 592000)),
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
    "THREADS_GALLERIES": int(os.getenv("THREADS_GALLERIES", 1)),
    "THREADS_IMAGES": int(os.getenv("THREADS_IMAGES", 4)),
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