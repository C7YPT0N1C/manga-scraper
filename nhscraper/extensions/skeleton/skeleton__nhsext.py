#!/usr/bin/env python3
# extensions/skeleton/skeleton__nhsext.py
# ENSURE THAT THIS FILE IS THE *EXACT SAME* IN BOTH THE NHENTAI-SCRAPER REPO AND THE NHENTAI-SCRAPER-EXTENSIONS REPO.
# PLEASE UPDATE THIS FILE IN THE NHENTAI-SCRAPER REPO FIRST, THEN COPY IT OVER TO THE NHENTAI-SCRAPER-EXTENSIONS REPO.

import os, time, json, requests, threading

from nhscraper.core.config import *
from nhscraper.core.api import get_meta_tags, safe_name, clean_title

# This is a skeleton/example extension for nhentai-scraper. It is also used as the default extension if none is specified.

# ALL FUNCTIONS MUST BE THREAD SAFE. IF A FUNCTION MANIPULATES A GLOBAL VARIABLE, STORE AND UPDATE IT LOCALLY IF POSSIBLE. 

####################################################################################################################
# Global variables
####################################################################################################################
EXTENSION_NAME = "skeleton" # Must be fully lowercase
EXTENSION_INSTALL_PATH = "/opt/nhentai-scraper/downloads/" # Use this if extension installs external programs (like Suwayomi-Server)
REQUESTED_DOWNLOAD_PATH = "/opt/nhentai-scraper/downloads/"
#DEDICATED_DOWNLOAD_PATH = None # In case it tweaks out.

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

############################################

# Thread locks for file operations
_file_lock = threading.Lock()

####################################################################################################################
# CORE
####################################################################################################################
def update_extension_download_path():
    log_clarification()
    logger.info(f"Extension: {EXTENSION_NAME}: Ready.")
    log(f"Extension: {EXTENSION_NAME}: Debugging started.", "debug")
    update_env("EXTENSION_DOWNLOAD_PATH", DEDICATED_DOWNLOAD_PATH)
    
    if global_dry_run:
        logger.info(f"[DRY-RUN] Would ensure download path exists: {DEDICATED_DOWNLOAD_PATH}")
        return
    try:
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        logger.info(f"Extension: {EXTENSION_NAME}: Download path ready at '{DEDICATED_DOWNLOAD_PATH}'.")
    except Exception as e:
        logger.error(f"Extension: {EXTENSION_NAME}: Failed to create download path '{DEDICATED_DOWNLOAD_PATH}': {e}")

def return_gallery_metas(meta):
    artists = get_meta_tags(f"Extension: {EXTENSION_NAME}: Return_gallery_metas", meta, "artist")
    groups = get_meta_tags(f"Extension: {EXTENSION_NAME}: Return_gallery_metas", meta, "group")
    creators = artists or groups or ["Unknown Creator"]
    
    title = clean_title(meta)
    id = str(meta.get("id", "Unknown ID"))
    full_title = f"({id}) {title}"
    
    language = get_meta_tags(f"Extension: {EXTENSION_NAME}: Return_gallery_metas", meta, "language") or ["Unknown Language"]
    
    log_clarification()
    return {
        "creator": creators,
        "title": full_title,
        "short_title": title,
        "id": id,
        "language": language,
    }

def install_extension():
    """
    Install the extension and ensure the dedicated image download path exists.
    """
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH

    if not DEDICATED_DOWNLOAD_PATH:
        # Fallback in case manifest didn't define it
        DEDICATED_DOWNLOAD_PATH = REQUESTED_DOWNLOAD_PATH
    
    if global_dry_run:
        logger.info(f"[DRY-RUN] Would install extension and create paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}")
        return

    try:
        # Ensure extension install path and image download path exists.
        os.makedirs(EXTENSION_INSTALL_PATH, exist_ok=True)
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        
        update_extension_download_path()
        
        logger.info(f"Extension: {EXTENSION_NAME}: Installed.")
    
    except Exception as e:
        logger.error(f"Extension: {EXTENSION_NAME}: Failed to install: {e}")

def uninstall_extension():
    """
    Remove the extension and related paths.
    """
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH
    
    if global_dry_run:
        logger.info(f"[DRY-RUN] Would uninstall extension and remove paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}")
        return
    
    try:
        # Ensure extension install path and image download path is removed.
        if os.path.exists(EXTENSION_INSTALL_PATH):
            os.rmdir(EXTENSION_INSTALL_PATH)
        if os.path.exists(DEDICATED_DOWNLOAD_PATH):
            os.rmdir(DEDICATED_DOWNLOAD_PATH)
        
        logger.info(f"Extension: {EXTENSION_NAME}: Uninstalled")
    
    except Exception as e:
        logger.error(f"Extension: {EXTENSION_NAME}: Failed to uninstall: {e}")

####################################################################################################################
# CUSTOM HOOKS (Create your custom hooks here, add them into the corresponding CORE HOOK)
####################################################################################################################

# Hook for testing functionality. Use active_extension.test_hook(ARGS) in downloader.
def test_hook():
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Test Hook Called.", "debug")
    log_clarification()

# Remove empty folders inside DEDICATED_DOWNLOAD_PATH without deleting the root folder itself.
def remove_empty_directories(RemoveEmptyArtistFolder: bool = True):
    global DEDICATED_DOWNLOAD_PATH

    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        log("No valid DEDICATED_DOWNLOAD_PATH set, skipping cleanup.", "debug")
        return

    if global_dry_run:
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

####################################################################################################################
# CORE HOOKS (Please add to the functions, try not to change or remove anything)
####################################################################################################################

# Hook for downloading images. Use active_extension.download_images_hook(ARGS) in downloader.
def download_images_hook(gallery, page, urls, path, session, pbar=None, creator=None, retries=None):
    """
    Downloads an image from one of the provided URLs to the given path.
    Tries mirrors in order until one succeeds, with retries per mirror.
    Updates tqdm progress bar with current creator.
    """
    #log_clarification()
    #log(f"Extension: {EXTENSION_NAME}: Image Download Hook Called.", "debug")
    
    if not urls:
        logger.warning(f"Gallery {gallery}: Page {page}: No URLs, skipping")
        if pbar and creator:
            pbar.set_postfix_str(f"Skipped Creator: {creator}")
        return False

    if retries is None:
        retries = config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES)

    if os.path.exists(path):
        log(f"Already exists, skipping: {path}", "debug")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if global_dry_run:
        logger.info(f"[DRY-RUN] Gallery {gallery}: Would download {urls[0]} -> {path}")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if not isinstance(session, requests.Session):
        session = requests.Session()

    # Loop through mirrors
    for url in urls:
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

                log(f"Downloaded Gallery {gallery}: Page {page} -> {path}", "debug")
                if pbar and creator:
                    pbar.set_postfix_str(f"Creator: {creator}")
                return True

            except Exception as e:
                wait = 2 ** attempt
                log_clarification()
                logger.warning(f"Gallery {gallery}: Page {page}: Mirror {url}, attempt {attempt} failed: {e}, retrying in {wait}s")
                time.sleep(wait)

        # If all retries for this mirror failed, move to next mirror
        logger.warning(f"Gallery {gallery}: Page {page}: Mirror {url} failed after {retries} attempts, trying next mirror")

    # If no mirrors succeeded
    log_clarification()
    logger.error(f"Gallery {gallery}: Page {page}: All mirrors failed after {retries} retries each: {urls}")
    
    if pbar and creator:
        pbar.set_postfix_str(f"Failed Creator: {creator}")
    
    return False

# Hook for pre-run functionality. Use active_extension.pre_run_hook(ARGS) in downloader.
def pre_run_hook(gallery_list):
    update_extension_download_path()
    
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Pre-run Hook Called.", "debug")
    
    #log_clarification()
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS
    
    return gallery_list

# Hook for functionality before a gallery download. Use active_extension.pre_gallery_download_hook(ARGS) in downloader.
def pre_gallery_download_hook(gallery_id):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Pre-download Hook Called: Gallery: {gallery_id}", "debug")
    
    #log_clarification()
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for functionality during a gallery download. Use active_extension.during_gallery_download_hook(ARGS) in downloader.
def during_gallery_download_hook(gallery_id):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: During-download Hook Called: Gallery: {gallery_id}", "debug")
    
    #log_clarification()
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for functionality after a completed gallery download. Use active_extension.after_completed_gallery_download_hook(ARGS) in downloader.
def after_completed_gallery_download_hook(meta: dict, gallery_id):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Post-Completed Gallery Download Hook Called: Gallery: {meta['id']}: Downloaded.", "debug")
    
    #log_clarification()
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for post-run functionality. Reset download path. Use active_extension.post_run_hook(ARGS) in downloader.
def post_run_hook():
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Post-run Hook Called.", "debug")
    
    #log_clarification()
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

    log_clarification()
    remove_empty_directories(True)