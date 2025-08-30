#!/usr/bin/env python3
# extensions/skeleton/skeleton__nhsext.py
# This is a skeleton/example extension for nhentai-scraper. It is also use as the default extension if none is specified.
# ENSURE THAT THIS FILE IS THE *EXACT SAME* IN BOTH THE NHENTAI-SCRAPER REPO AND THE NHENTAI-SCRAPER-EXTENSIONS REPO.
# PLEASE UPDATE THIS FILE IN THE NHENTAI-SCRAPER REPO FIRST, THEN COPY IT OVER TO THE NHENTAI-SCRAPER-EXTENSIONS REPO.

import os, subprocess, json

from nhscraper.core.logger import *
from nhscraper.core.config import *

# ------------------------------
# LOG CLARIFICATION
# Prints Blank Line To Make Logs Look Cleaner)
# ------------------------------
def log_clarification():  
    print()
    logger.debug("")

log_clarification()
logger.info("Extension: Skeleton: Ready.")
logger.debug("Extension: Skeleton: Debugging started.")

# Global variable for download path, update here.
extension_download_path = "/opt/nhentai-scraper/downloads"

def update_extension_download_path():
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)

# Hook for pre-download functionality. Set download path to extension's desired download path.
def pre_download_hook(config, gallery_list):
    update_extension_download_path()
    
    log_clarification()
    logger.debug(f"Skeleton extension: Pre-download hook called.")
    return gallery_list

# Hook for functionality during download
def during_download_hook(config, gallery_id, gallery_metadata):
    log_clarification()
    logger.debug(f"Skeleton extension: During-download hook called: Gallery {gallery_id}")

# Hook for functionality after each gallery download
def after_gallery_download(meta: dict):
    log_clarification()
    logger.debug(f"Skeleton extension: Post-Gallery Download hook called: Gallery {meta['id']} downloaded")

# Hook for functionality after all downloads are complete
def after_all_downloads(all_meta: list):
    log_clarification()
    logger.debug(f"Skeleton extension: Post-Batch hook called: Batch of {len(all_meta)} galleries downloaded")

# Hook for post-download functionality. Reset download path.
def post_download_hook(config, completed_galleries):
    global extension_download_path
    extension_download_path = ""  # Reset after downloads
    update_env("EXTENSION_DOWNLOAD_PATH", "")
    log_clarification()
    logger.debug(f"Skeleton extension: Post-download hook called.")

# ------------------------------
# Install / Uninstall
# ------------------------------
def install_extension():
    global extension_download_path
    extension_download_path = "./downloads_skeleton"
    os.makedirs(extension_download_path, exist_ok=True)
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)
    log_clarification()
    logger.info(f"Skeleton extension installed at {extension_download_path}")

def uninstall_extension():
    global extension_download_path
    try:
        if os.path.exists(extension_download_path):
            os.rmdir(extension_download_path)
        extension_download_path = ""
        update_env("EXTENSION_DOWNLOAD_PATH", "")
        log_clarification()
        logger.info("Skeleton extension uninstalled")
    except Exception as e:
        log_clarification()
        logger.error(f"Failed to uninstall skeleton extension: {e}")