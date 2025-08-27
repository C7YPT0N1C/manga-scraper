#!/usr/bin/env python3
# nhscraper/config.py
# DESCRIPTION: Holds global configuration and helper functions.
# Called by: cli.py, downloader.py, nhscraper_api.py
# Calls: None directly, reads/writes .env
# FUNCTION: Load, update, and persist scraper configuration including CLI flags, env variables, and dashboard settings

import os
from dotenv import load_dotenv, set_key

ENV_FILE = "/opt/nhentai-scraper/config.env"

# Load .env
if not os.path.exists(ENV_FILE):
    open(ENV_FILE, "a").close()
load_dotenv(ENV_FILE)

# Global config dictionary
config = {
    "galleries": [],
    "range": None,
    "artist": None,
    "group": None,
    "category": None,
    "excluded_tags": [],
    "language": ["english"],
    "title_type": "pretty",
    "threads_galleries": 1,
    "threads_images": 4,
    "use_tor": False,
    "dry_run": False,
    "verbose": False,
    "dashboard_pass_hash": os.getenv("DASHBOARD_PASS_HASH", ""),
}

def update_config(new_values: dict):
    """
    Merge new values (from CLI or settings) into global config.
    Write changes to .env for persistence.
    """
    for key, value in new_values.items():
        if value is not None:
            config[key] = value
            set_env_var(key.upper(), value)

def set_env_var(key, value):
    """
    Write a single config variable to the .env file.
    Converts list to comma-separated string.
    """
    val_str = ",".join(value) if isinstance(value, list) else str(value)
    set_key(ENV_FILE, key, val_str)
    os.environ[key] = val_str

def update_dashboard_password(new_hash):
    """
    Update the DASHBOARD_PASS_HASH in config, .env, and environment
    """
    config["dashboard_pass_hash"] = new_hash
    set_env_var("DASHBOARD_PASS_HASH", new_hash)