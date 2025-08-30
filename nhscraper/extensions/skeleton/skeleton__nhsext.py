#!/usr/bin/env python3
# extensions/skeleton/skeleton__nhsext.py
# This is a skeleton/example extension for nhentai-scraper. It is also use as the default extension if none is specified.
# ENSURE THAT THIS FILE IS THE *EXACT SAME* IN BOTH THE NHENTAI-SCRAPER REPO AND THE NHENTAI-SCRAPER-EXTENSIONS REPO.
# PLEASE UPDATE THIS FILE IN THE NHENTAI-SCRAPER REPO FIRST, THEN COPY IT OVER TO THE NHENTAI-SCRAPER-EXTENSIONS REPO.

import os, time, subprocess, json, requests

from nhscraper.core.config import logger, config, log_clarification, update_env
from nhscraper.core.fetchers import get_meta_tag_names, safe_name, clean_title

# Global variables for download path and subfolder strucutre.
extension_download_path = "/opt/nhentai-scraper/downloads/default"
SUBFOLDER_STRUCTURE = ["artist", "title"]

def update_extension_download_path():
    log_clarification()
    logger.info("Extension: Skeleton: Ready.")
    logger.debug("Extension: Skeleton: Debugging started.")
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)

def build_gallery_subfolders(meta):
    """Return a dict of possible variables to use in folder naming."""
    return {
        "artist": (get_meta_tag_names(meta, "artist") or ["Unknown Artist"])[0],
        "title": clean_title(meta),
        "id": str(meta.get("id", "unknown")),
        "language": (get_meta_tag_names(meta, "language") or ["Unknown"])[0],
    }

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
        log_clarification()
        logger.info(f"[DRY-RUN] Would download {url} -> {path}")
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
                log_clarification()
                logger.warning(f"429 rate limit hit for {url}, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            log_clarification()
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
    
####################################################################################################################

# Hook for testing functionality. Use active_extension.test_hook(ARGS)
def test_hook(config, gallery_list):
    log_clarification()
    logger.debug(f"Extension: Skeleton: Test hook called.")

# Hook for pre-run functionality. Use active_extension.pre_run_hook(ARGS)
def pre_run_hook(config, gallery_list):
    update_extension_download_path()
    
    log_clarification()
    logger.debug(f"Extension: Skeleton: Pre-run hook called.")
    return gallery_list

# Hook for functionality during download. Use active_extension.during_gallery_download_hook(ARGS)
def during_gallery_download_hook(config, gallery_id, gallery_metadata):
    log_clarification()
    logger.debug(f"Extension: Skeleton: During-download hook called: Gallery: {gallery_id}")

# Hook for functionality after each gallery download. Use active_extension.after_gallery_download_hook(ARGS)
def after_gallery_download_hook(meta: dict):
    log_clarification()
    logger.debug(f"Extension: Skeleton: Post-Gallery Download hook called: Gallery: {meta['id']}: Downloaded.")

# Hook for functionality after all downloads are complete. Use active_extension.after_all_galleries_download_hook(ARGS)
def after_all_galleries_download_hook(all_meta: list):
    log_clarification()
    logger.debug(f"Extension: Skeleton: Post-Batch hook called: Batch of {len(all_meta)} galleries downloaded")

# Hook for post-run functionality. Reset download path. Use active_extension.post_run_hook(ARGS)
def post_run_hook(config, completed_galleries):
    global extension_download_path
    extension_download_path = ""  # Reset after downloads
    update_env("EXTENSION_DOWNLOAD_PATH", "")
    log_clarification()
    logger.debug(f"Extension: Skeleton: Post-run hook called.")

# ------------------------------
# Install / Uninstall
# ------------------------------
def install_extension():
    extension_install_download_path = extension_download_path
    os.makedirs(extension_install_download_path, exist_ok=True)
    update_env("EXTENSION_DOWNLOAD_PATH", extension_install_download_path)
    log_clarification()
    logger.info(f"Extension: Skeleton: Installed at {extension_install_download_path}")

def uninstall_extension():
    global extension_download_path
    try:
        if os.path.exists(extension_download_path):
            os.rmdir(extension_download_path)
        extension_download_path = ""
        update_env("EXTENSION_DOWNLOAD_PATH", "")
        log_clarification()
        logger.info("Extension: Skeleton: Uninstalled")
    except Exception as e:
        log_clarification()
        logger.error(f"Failed to uninstall skeleton extension: {e}")