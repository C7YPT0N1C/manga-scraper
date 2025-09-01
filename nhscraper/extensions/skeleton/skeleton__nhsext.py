#!/usr/bin/env python3
# extensions/skeleton/skeleton__nhsext.py
# This is a skeleton/example extension for nhentai-scraper. It is also use as the default extension if none is specified.
# ENSURE THAT THIS FILE IS THE *EXACT SAME* IN BOTH THE NHENTAI-SCRAPER REPO AND THE NHENTAI-SCRAPER-EXTENSIONS REPO.
# PLEASE UPDATE THIS FILE IN THE NHENTAI-SCRAPER REPO FIRST, THEN COPY IT OVER TO THE NHENTAI-SCRAPER-EXTENSIONS REPO.

import os, time, subprocess, json, requests

from nhscraper.core.config import logger, config, log_clarification, update_env
from nhscraper.core.fetchers import get_meta_tags, safe_name, clean_title

####################################################################################################################
# CORE
####################################################################################################################

# Global variables for download path and subfolder strucutre.
EXTENSION_DOWNLOAD_PATH = "/opt/nhentai-scraper/downloads/"
SUBFOLDER_STRUCTURE = ["artist", "title"] # SUBDIR_1, SUBDIR_2, etc

def install_extension():
    os.makedirs(EXTENSION_DOWNLOAD_PATH, exist_ok=True)
    update_env("EXTENSION_DOWNLOAD_PATH", EXTENSION_DOWNLOAD_PATH)
    log_clarification()
    logger.info(f"Extension: Skeleton: Installed.")

def uninstall_extension():
    global EXTENSION_DOWNLOAD_PATH
    try:
        if os.path.exists(EXTENSION_DOWNLOAD_PATH):
            os.rmdir(EXTENSION_DOWNLOAD_PATH)
        EXTENSION_DOWNLOAD_PATH = ""
        update_env("EXTENSION_DOWNLOAD_PATH", "")
        log_clarification()
        logger.info("Extension: Skeleton: Uninstalled")
    except Exception as e:
        log_clarification()
        logger.error(f"Extension: Skeleton: Failed to uninstall: {e}")

def update_extension_download_path():
    log_clarification()
    logger.info("Extension: Skeleton: Ready.")
    logger.debug("Extension: Skeleton: Debugging started.")
    update_env("EXTENSION_DOWNLOAD_PATH", EXTENSION_DOWNLOAD_PATH)

def build_gallery_subfolders(meta):
    """Return a dict of possible variables to use in folder naming."""
    return {
        "artist": (get_meta_tags(meta, "artist") or ["Unknown Artist"])[0],
        "title": clean_title(meta),
        "id": str(meta.get("id", "unknown")),
        "language": (get_meta_tags(meta, "language") or ["Unknown"])[0],
    }

####################################################################################################################
# CUSTOM HOOKS (Create your custom hooks here, add them into the corresponding CORE HOOK)
####################################################################################################################

def remove_empty_directories(RemoveEmptyArtistFolder: bool = False):
    # Remove empty directories - safety check
    if not EXTENSION_DOWNLOAD_PATH or not os.path.isdir(EXTENSION_DOWNLOAD_PATH):
        logger.debug("No valid EXTENSION_DOWNLOAD_PATH set, skipping cleanup.")
        return

    if RemoveEmptyArtistFolder: # Remove empty directories, deepest first, up to EXTENSION_DOWNLOAD_PATH
        for dirpath, dirnames, filenames in os.walk(EXTENSION_DOWNLOAD_PATH, topdown=False):
            try:
                if not os.listdir(dirpath):  # directory is empty (no files, no subdirs)
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
            except Exception as e:
                logger.warning(f"Could not remove empty directory: {dirpath}: {e}")
    else: # Remove empty directories, deepest only.
        for dirpath, dirnames, filenames in os.walk(EXTENSION_DOWNLOAD_PATH, topdown=False):
            if not dirnames and not filenames:
                try:
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
                except Exception as e:
                    logger.warning(f"Could not remove empty directory: {dirpath}: {e}")
    
    

    EXTENSION_DOWNLOAD_PATH = ""  # Reset after download batch
    update_env("EXTENSION_DOWNLOAD_PATH", "")

# Hook for testing functionality. Use active_extension.test_hook(ARGS) in downloader.
def test_hook():
    log_clarification()
    logger.debug(f"Extension: Skeleton: Test hook called.")

####################################################################################################################
# CORE HOOKS (Please add too the functions, try not to change or remove anything)
####################################################################################################################

# Hook for pre-run functionality. Use active_extension.pre_run_hook(ARGS) in downloader.
def pre_run_hook(config, gallery_list):
    update_extension_download_path()
    
    log_clarification()
    logger.debug(f"Extension: Skeleton: Pre-run hook called.")
    return gallery_list

# Hook for downloading images. Use active_extension.download_images_hook(ARGS) in downloader.
def download_images_hook(gallery, page, url, path, session, pbar=None, artist=None, retries=None):
    """
    Downloads an image from URL to the given path.
    Updates tqdm progress bar with current artist.
    """
    if not url:
        logger.warning(f"Gallery {gallery}: Page {page}: No URL, skipping")
        if pbar and artist:
            pbar.set_postfix_str(f"Skipped artist: {artist}")
        return False

    if retries is None:
        retries = config.get("MAX_RETRIES", 3)

    if os.path.exists(path):
        logger.debug(f"Already exists, skipping: {path}")
        if pbar and artist:
            pbar.set_postfix_str(f"Artist: {artist}")
        return True

    if config.get("DRY_RUN", False):
        logger.info(f"[DRY-RUN] Gallery {gallery}: Would download {url} -> {path}")
        if pbar and artist:
            pbar.set_postfix_str(f"Artist: {artist}")
        return True

    if not isinstance(session, requests.Session):
        session = requests.Session()

    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=30, stream=True)
            if r.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"429 rate limit hit for {url}, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            logger.debug(f"Downloaded Gallery {gallery}: Page {page} -> {path}")
            if pbar and artist:
                pbar.set_postfix_str(f"Artist: {artist}")
            return True

        except Exception as e:
            wait = 2 ** attempt
            log_clarification()
            logger.warning(f"Attempt {attempt} failed for {url}: {e}, retrying in {wait}s")
            time.sleep(wait)

    log_clarification()
    logger.error(f"Gallery {gallery}: Page {page}: Failed to download after {retries} attempts: {url}")
    if pbar and artist:
        pbar.set_postfix_str(f"Failed artist: {artist}")
    return False

# Hook for functionality during download. Use active_extension.during_gallery_download_hook(ARGS) in downloader.
def during_gallery_download_hook(config, gallery_id, gallery_metadata):
    log_clarification()
    logger.debug(f"Extension: Skeleton: During-download hook called: Gallery: {gallery_id}")

# Hook for functionality after each gallery download. Use active_extension.after_gallery_download_hook(ARGS) in downloader.
def after_gallery_download_hook(meta: dict):
    log_clarification()
    logger.debug(f"Extension: Skeleton: Post-Gallery Download hook called: Gallery: {meta['id']}: Downloaded.")

# Hook for post-run functionality. Reset download path. Use active_extension.post_run_hook(ARGS) in downloader.
def post_run_hook(config, completed_galleries):
    global EXTENSION_DOWNLOAD_PATH

    log_clarification()
    logger.debug("Extension: Skeleton: Post-run hook called.")

    log_clarification()
    # Remove empty folders.
    # Set argument to True to remove empty SUBDIR_1's
    # Set argument to False to only remove deepest subdirectory (SUBDIR_2, etc) (refer to SUBFOLDER_STRUCTURE)
    remove_empty_directories(True)