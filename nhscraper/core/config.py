#!/usr/bin/env python3
# core/config.py

import os
from dotenv import load_dotenv

from nhscraper.core.logger import *

ENV_FILE = "/opt/nhentai-scraper/nhentai-scraper.env"

# Load the environment file
if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE)

# Config dictionary
config = {
    "DOWNLOAD_PATH": os.getenv("DOWNLOAD_PATH", "/opt/nhentai-scraper/downloads"),
    "EXTENSION_DOWNLOAD_PATH": os.getenv("EXTENSION_DOWNLOAD_PATH", ""),
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
    "USE_TOR": os.getenv("USE_TOR", "false").lower() == "true",
    "DRY_RUN": os.getenv("DRY_RUN", "false").lower() == "true",
    "VERBOSE": os.getenv("VERBOSE", "false").lower() == "true",
}


# ------------------------------
# Helper function to update .env
# ------------------------------
def update_env(key, value):
    """
    Update a single variable in the .env file.
    If the key doesn't exist, append it.
    """
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            lines = f.readlines()

    key_found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            key_found = True
            break

    if not key_found:
        lines.append(f"{key}={value}\n")

    with open(ENV_FILE, "w") as f:
        f.writelines(lines)

    # Update the runtime config dict
    if key in config:
        config[key] = value


# ------------------------------
# Get dynamic download path
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
    log_clarification("info")
    logger.info(f"DOWNLOAD PATH = {ext_path}")
    return config.get("DOWNLOAD_PATH", "/opt/nhentai-scraper/downloads")