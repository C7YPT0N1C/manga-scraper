#!/usr/bin/env python3
# extensions/skeleton/custom_hooks.py
# ENSURE THAT THIS FILE IS THE *EXACT SAME* IN BOTH THE NHENTAI-SCRAPER REPO AND THE NHENTAI-SCRAPER-EXTENSIONS REPO.
# PLEASE UPDATE THIS FILE IN THE NHENTAI-SCRAPER REPO FIRST, THEN COPY IT OVER TO THE NHENTAI-SCRAPER-EXTENSIONS REPO.

import os, threading

from nhscraper.core.config import *
from nhscraper.core.api import get_meta_tags, safe_name, clean_title

####################################################################################################################
# Global variables
####################################################################################################################
EXTENSION_NAME = "skeleton"
dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)

# Thread lock for file operations
_file_lock = threading.Lock()

####################################################################################################################
# CORE
####################################################################################################################
def return_gallery_metas(meta):
    artists = get_meta_tags(f"{EXTENSION_NAME}: Return_gallery_metas", meta, "artist")
    groups = get_meta_tags(f"{EXTENSION_NAME}: Return_gallery_metas", meta, "group")
    creators = artists or groups or ["Unknown Creator"]
    
    title = clean_title(meta)
    id = str(meta.get("id", "Unknown ID"))
    full_title = f"({id}) {title}"
    
    language = get_meta_tags(f"{EXTENSION_NAME}: Return_gallery_metas", meta, "language") or ["Unknown Language"]
    
    log_clarification()
    return {
        "creator": creators,
        "title": full_title,
        "short_title": title,
        "id": id,
        "language": language,
    }

####################################################################################################################
# CUSTOM HOOKS (Create your custom hooks here, add them into the corresponding CORE HOOK)
####################################################################################################################

# Hook for testing functionality. Use active_extension.test_hook(ARGS) in downloader.
def test_hook():
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Test hook called.", "debug")
    log_clarification()

# Remove empty folders inside DEDICATED_DOWNLOAD_PATH without deleting the root folder itself.
def remove_empty_directories(RemoveEmptyArtistFolder: bool = True):
    global DEDICATED_DOWNLOAD_PATH

    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        log("No valid DEDICATED_DOWNLOAD_PATH set, skipping cleanup.", "debug")
        return

    if dry_run:
        logger.info(f"[DRY-RUN] Would remove empty directories under {DEDICATED_DOWNLOAD_PATH}")
        return

    if RemoveEmptyArtistFolder:
        for dirpath, dirnames, filenames in os.walk(DEDICATED_DOWNLOAD_PATH, topdown=False):
            if dirpath == DEDICATED_DOWNLOAD_PATH:
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
            except Exception as e:
                logger.warning(f"Could not remove empty directory: {dirpath}: {e}")
    else:
        for dirpath, dirnames, filenames in os.walk(DEDICATED_DOWNLOAD_PATH, topdown=False):
            if dirpath == DEDICATED_DOWNLOAD_PATH:
                continue
            if not dirnames and not filenames:
                try:
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
                except Exception as e:
                    logger.warning(f"Could not remove empty directory: {dirpath}: {e}")

    logger.info(f"Removed empty directories.")
    DEDICATED_DOWNLOAD_PATH = ""
    update_env("EXTENSION_DOWNLOAD_PATH", DEDICATED_DOWNLOAD_PATH)