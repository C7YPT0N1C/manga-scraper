#!/usr/bin/env python3
# nhscraper/extensions/skeleton/skeleton__nhsext.py
# ENSURE THAT THIS FILE IS THE *EXACT SAME* IN BOTH THE NHENTAI-SCRAPER REPO AND THE NHENTAI-SCRAPER-EXTENSIONS REPO.
# PLEASE UPDATE THIS FILE IN THE NHENTAI-SCRAPER REPO FIRST, THEN COPY IT OVER TO THE NHENTAI-SCRAPER-EXTENSIONS REPO.

import os, time, json, requests, math

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *
from nhscraper.core.api import get_session, get_meta_tags, make_filesystem_safe, clean_title, dynamic_sleep

# This is a skeleton/example extension for nhentai-scraper. It is also used as the default extension if none is specified.

# ALL FUNCTIONS MUST BE THREAD SAFE. IF A FUNCTION MANIPULATES A GLOBAL VARIABLE, STORE AND UPDATE IT LOCALLY IF POSSIBLE. 

####################################################################################################################
# Global variables
####################################################################################################################

EXTENSION_NAME = "skeleton" # Must be fully lowercase
EXTENSION_NAME_CAPITALISED = EXTENSION_NAME.capitalize()
EXTENSION_REFERRER = f"{EXTENSION_NAME_CAPITALISED} Extension" # Used for printing the extension's name.

EXTENSION_INSTALL_PATH = "/opt/nhentai-scraper/downloads/" # Use this if extension installs external programs (like Suwayomi-Server)
REQUESTED_DOWNLOAD_PATH = "/opt/nhentai-scraper/downloads/"

LOCAL_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "local_manifest.json"
)

with open(os.path.abspath(LOCAL_MANIFEST_PATH), "r", encoding="utf-8") as f:
    manifest = json.load(f)

for ext in manifest.get("extensions", []):
    if ext.get("name") == EXTENSION_NAME:
        DEDICATED_DOWNLOAD_PATH = ext.get("image_download_path")
        break

# Optional fallback
if DEDICATED_DOWNLOAD_PATH is None: # Default download folder here.
    DEDICATED_DOWNLOAD_PATH = REQUESTED_DOWNLOAD_PATH

SUBFOLDER_STRUCTURE = ["creator", "title"] # SUBDIR_1, SUBDIR_2, etc

# Used to optionally run stuff in hooks (for example, cleaning the download directory) roughly "RUNS_PER_X_BATCHES" times every "EVERY_X_BATCHES" batches.
# Increase this if the operations in your post batch / run hooks get increasingly demanding the larger the library is.
EVERY_X_BATCHES = 5
RUNS_PER_X_BATCHES = 2

####################################################################

# PUT YOUR VARIABLES HERE

####################################################################################################################
# CORE
####################################################################################################################

# Hook for pre-run functionality. Use active_extension.pre_run_hook(ARGS) in downloader.
def pre_run_hook():
    """
    This is one this module's entrypoints.
    """
    
    logger.debug(f"{EXTENSION_REFERRER}: Ready.")
    log(f"{EXTENSION_REFERRER}: Debugging started.", "debug")
    
    fetch_env_vars() # Refresh env vars in case config changed.
    update_env("EXTENSION_DOWNLOAD_PATH", DEDICATED_DOWNLOAD_PATH) # Update download path in env
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would ensure download path exists: {DEDICATED_DOWNLOAD_PATH}")
        return
    try:
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        logger.debug(f"{EXTENSION_REFERRER}: Download path ready at '{DEDICATED_DOWNLOAD_PATH}'.")
    except Exception as e:
        logger.error(f"{EXTENSION_REFERRER}: Failed to create download path '{DEDICATED_DOWNLOAD_PATH}': {e}")

def return_gallery_metas(meta):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    artists = get_meta_tags(f"{EXTENSION_REFERRER}: Return_gallery_metas", meta, "artist")
    groups = get_meta_tags(f"{EXTENSION_REFERRER}: Return_gallery_metas", meta, "group")
    creators = artists or groups or ["Unknown Creator"]
    
    title = clean_title(meta)
    id = str(meta.get("id", "Unknown ID"))
    full_title = f"({id}) {title}"
    
    gallery_language = get_meta_tags(f"{EXTENSION_REFERRER}: Return_gallery_metas", meta, "language") or ["Unknown Language"]
    
    return {
        "creator": creators,
        "title": full_title,
        "short_title": title,
        "id": id,
        "language": gallery_language,
    }

def install_extension():
    """
    Install the extension and ensure the dedicated image download path exists.
    """
    
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH
    
    fetch_env_vars() # Refresh env vars in case config changed.

    if not DEDICATED_DOWNLOAD_PATH:
        # Fallback in case manifest didn't define it
        DEDICATED_DOWNLOAD_PATH = REQUESTED_DOWNLOAD_PATH
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would install extension and create paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}")
        return

    try:
        # Ensure extension install path and image download path exists.
        os.makedirs(EXTENSION_INSTALL_PATH, exist_ok=True)
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        
        pre_run_hook()
        
        logger.info(f"{EXTENSION_REFERRER}: Installed.")
    
    except Exception as e:
        logger.error(f"{EXTENSION_REFERRER}: Failed to install: {e}")

def uninstall_extension():
    """
    Remove the extension and related paths.
    """
    
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would uninstall extension and remove paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}")
        return
    
    try:
        # Ensure extension install path and image download path is removed.
        if os.path.exists(EXTENSION_INSTALL_PATH):
            os.rmdir(EXTENSION_INSTALL_PATH)
        if os.path.exists(DEDICATED_DOWNLOAD_PATH):
            os.rmdir(DEDICATED_DOWNLOAD_PATH)
        
        logger.info(f"{EXTENSION_REFERRER}: Uninstalled")
    
    except Exception as e:
        logger.error(f"{EXTENSION_REFERRER}: Failed to uninstall: {e}")

####################################################################################################################
# CUSTOM HOOKS (Create your custom hooks here, add them into the corresponding CORE HOOK)
####################################################################################################################

# Hook for testing functionality. Use active_extension.test_hook(ARGS) in downloader.
def test_hook():
    """
    Update environment variables used by this module.
    Call this function at the start of any function that uses any these variables to ensure they are up to date.
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Test Hook Called.", "debug")

# Remove empty folders inside DEDICATED_DOWNLOAD_PATH without deleting the root folder itself.
def clean_directories(RemoveEmptyArtistFolder: bool = True):
    global DEDICATED_DOWNLOAD_PATH
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    log_clarification("debug")

    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        log("No valid DEDICATED_DOWNLOAD_PATH set, skipping cleanup.", "debug")
        return

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would remove empty directories under {DEDICATED_DOWNLOAD_PATH}")
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
    
    log_clarification()
    
    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        logger.warning("No valid DEDICATED_DOWNLOAD_PATH for symlink check.")
        return

    removed = 0
    for dirpath, _, filenames in os.walk(DEDICATED_DOWNLOAD_PATH):
        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            if os.path.islink(full_path) and not os.path.exists(os.readlink(full_path)):
                try:
                    os.unlink(full_path)
                    logger.info(f"Removed broken symlink: {full_path}")
                    removed += 1
                except Exception as e:
                    logger.warning(f"Failed to remove broken symlink {full_path}: {e}")
    
    logger.info(f"Fixed {removed} broken symlink(s).")

####################################################################################################################
# CORE HOOKS (Please add to the functions, try not to change or remove anything)
####################################################################################################################

# Hook for downloading images. Use active_extension.download_images_hook(ARGS) in downloader.
def download_images_hook(gallery, page, urls, path, downloader_session, pbar=None, creator=None):
    """
    Downloads an image from one of the provided URLs to the given path.
    Tries mirrors in order until one succeeds, with retries per mirror.
    Updates tqdm progress bar with current creator.
    """

    fetch_env_vars()  # Refresh env vars in case config changed.

    if not urls:
        logger.warning(f"Gallery {gallery}: Page {page}: No URLs, skipping")
        if pbar and creator:
            pbar.set_postfix_str(f"Skipped Creator: {creator}")
        return False

    if os.path.exists(path):
        log(f"Already exists, skipping: {path}", "debug")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Gallery {gallery}: Would download {urls[0]} -> {path}")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if not isinstance(downloader_session, requests.Session):
        downloader_session = requests.Session()

    def try_download(session, mirrors, retries, tor_rotate=False):
        """Try downloading with a given session and retry count."""
        for url in mirrors:
            for attempt in range(1, retries + 1):
                try:
                    r = session.get(url, timeout=10, stream=True)
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

                    log(f"Downloaded Gallery {gallery}: Page {page} -> {path}", "debug")
                    if pbar and creator:
                        pbar.set_postfix_str(f"Creator: {creator}")
                    return True

                except Exception as e:
                    wait = dynamic_sleep("gallery", attempt=attempt)
                    log_clarification()
                    logger.warning(
                        f"Gallery {gallery}: Page {page}: Mirror {url}, attempt {attempt} failed: {e}, retrying in {wait:.2f}s"
                    )
                    time.sleep(wait)

            logger.warning(
                f"Gallery {gallery}: Page {page}: Mirror {url} failed after {retries} attempts, trying next mirror"
            )
        return False

    # First attempt: normal retries
    success = try_download(downloader_session, urls, orchestrator.max_retries)

    # If still failed, rebuild Tor session once and retry
    if not success and orchestrator.use_tor:
        logger.warning(
            f"Gallery {gallery}: Page {page}: All retries failed, rotating Tor node and retrying once more..."
        )
        downloader_session = get_session(referrer=f"{EXTENSION_NAME}", status="rebuild")
        success = try_download(downloader_session, urls, 1, tor_rotate=True)

    if not success:
        log_clarification()
        logger.error(
            f"Gallery {gallery}: Page {page}: All mirrors failed after Tor rotate too: {urls}"
        )
        if pbar and creator:
            pbar.set_postfix_str(f"Failed Creator: {creator}")

    return success

# Hook for pre-batch functionality. Use active_extension.pre_batch_hook(ARGS) in downloader.
def pre_batch_hook(gallery_list):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Pre-batch Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Pre-batch Hook Called.", "debug")
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS
    
    return gallery_list

# Hook for functionality before a gallery download. Use active_extension.pre_gallery_download_hook(ARGS) in downloader.
def pre_gallery_download_hook(gallery_id):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Pre-download Hook Inactive.")
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Pre-download Hook Called: Gallery: {gallery_id}", "debug")
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for functionality during a gallery download. Use active_extension.during_gallery_download_hook(ARGS) in downloader.
def during_gallery_download_hook(gallery_id):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: During-download Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: During-download Hook Called: Gallery: {gallery_id}", "debug")
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for functionality after a completed gallery download. Use active_extension.after_completed_gallery_download_hook(ARGS) in downloader.
def after_completed_gallery_download_hook(meta: dict, gallery_id):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Post-download Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Post-Completed Gallery Download Hook Called: Gallery: {meta['id']}: Downloaded.", "debug")
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for cleaning after downloads
def cleanup_hook():
    clean_directories(True) # Clean up the download folder / directories

# Hook for post-batch functionality. Use active_extension.post_batch_hook(ARGS) in downloader.
def post_batch_hook(current_batch_number: int, total_batch_numbers: int):
    fetch_env_vars()  # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Post-batch Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Post-batch Hook Called.", "debug")

    # --- Run if current batch hits interval or last batch ---
    interval = max(1, round(RUNS_PER_X_BATCHES * total_batch_numbers / EVERY_X_BATCHES))
    is_last_batch = current_batch_number == total_batch_numbers
    if (current_batch_number % interval == 0) or is_last_batch:
        cleanup_hook() # Call the cleanup hook
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for post-run functionality. Use active_extension.post_run_hook(ARGS) in downloader.
def post_run_hook():
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Post-run Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Post-run Hook Called.", "debug")
    
    if orchestrator.skip_post_run == True:
        log_clarification("debug")
        log(f"{EXTENSION_REFERRER}: Post-run Hook Skipped.", "debug")
    else:
        cleanup_hook() # Call the cleanup hook
        
        log_clarification("debug")
        log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS