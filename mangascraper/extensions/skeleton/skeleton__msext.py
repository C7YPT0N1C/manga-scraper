#!/usr/bin/env python3
# mangascraper/extensions/skeleton/skeleton__msext.py

import os, time, json, requests, math, shutil, re

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.extensions.extension_manager import (
    build_gallery_metadata_summary,
    calculate_extension_download_path,
    cleanup_download_tree,
    find_latest_cover_id,
    find_latest_gallery_entry,
    parse_gallery_id,
    repair_creator_cover,
    repair_covers_hook,
)
from mangascraper.core.api import (
    get_session,
    make_filesystem_safe,
    dynamic_sleep,
)

# This is a skeleton/example extension for manga-scraper. It is also used as the default extension if none is specified.

# ALL FUNCTIONS MUST BE THREAD SAFE. IF A FUNCTION MANIPULATES A GLOBAL VARIABLE, STORE AND UPDATE IT LOCALLY IF POSSIBLE. 

####################################################################################################################
# Global variables
####################################################################################################################

EXTENSION_NAME = "skeleton" # Must be fully lowercase
EXTENSION_NAME_CAPITALISED = EXTENSION_NAME.capitalize()
EXTENSION_REFERRER = f"{EXTENSION_NAME_CAPITALISED} Extension" # Used for printing the extension's name.

EXTENSION_INSTALL_PATH = "/opt/manga-scraper/downloads/" # Use this if extension installs external programs (like Suwayomi-Server)

DEDICATED_DOWNLOAD_PATH = calculate_extension_download_path(EXTENSION_NAME)

SUBFOLDER_STRUCTURE = ["creator", "title"] # SUBDIR_1, SUBDIR_2, etc

# Used to optionally run stuff in hooks (for example, cleaning the download directory) roughly "RUNS_PER_X_BATCHES" times every "EVERY_X_BATCHES" batches.
# Increase this if the operations in your post batch / run hooks get increasingly demanding the larger the library is.
MAX_X_BATCHES = 1000
EVERY_X_BATCHES = 5
RUNS_PER_X_BATCHES = 2

ARCHIVE_WAIT_SECONDS = 120
ARCHIVE_POLL_INTERVAL = 0.5

####################################################################
# CUSTOM VARIABLES
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
    global DEDICATED_DOWNLOAD_PATH
    
    logger.debug(f"{EXTENSION_REFERRER}: Ready.")
    log(f"{EXTENSION_REFERRER}: Debugging started.", "debug")
    
    orchestrator.refresh_globals()
    DEDICATED_DOWNLOAD_PATH = calculate_extension_download_path(EXTENSION_NAME)
    update_env("EXTENSION_DOWNLOAD_PATH", DEDICATED_DOWNLOAD_PATH) # Update download path in env
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would ensure download path exists: {DEDICATED_DOWNLOAD_PATH}")
        return
    try:
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        logger.debug(f"{EXTENSION_REFERRER}: Download path ready at '{DEDICATED_DOWNLOAD_PATH}'.")
    except Exception as e:
        logger.error(f"{EXTENSION_REFERRER}: Failed to create download path '{DEDICATED_DOWNLOAD_PATH}': {e}")

def install_extension():
    """
    Install the extension and ensure the dedicated image download path exists.
    """
    
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH
    
    orchestrator.refresh_globals()
    DEDICATED_DOWNLOAD_PATH = calculate_extension_download_path(EXTENSION_NAME)
    
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
    
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would uninstall extension and remove paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}")
        return
    
    try:
        # Ensure extension install path and image download path is removed.
        if os.path.exists(EXTENSION_INSTALL_PATH):
            os.rmdir(EXTENSION_INSTALL_PATH)
        if os.path.exists(DEDICATED_DOWNLOAD_PATH):
            os.rmdir(DEDICATED_DOWNLOAD_PATH)
        
        logger.info(f"{EXTENSION_REFERRER}: Uninstalled successfully. Your galleries folder will NOT be deleted.")
    
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
    
    orchestrator.refresh_globals()
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Test Hook Called.", "debug")

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

    orchestrator.refresh_globals()

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
                    r = session.get(url, timeout=(60, 60), stream=True)
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
                    wait = dynamic_sleep("image", attempt=attempt)
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
    orchestrator.refresh_globals()
    
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
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Pre-download Hook Inactive.")
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Pre-download Hook Called: Gallery: {gallery_id}", "debug")
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for functionality during a gallery download. Use active_extension.during_gallery_download_hook(ARGS) in downloader.
def during_gallery_download_hook(gallery_id):
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: During-download Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: During-download Hook Called: Gallery: {gallery_id}", "debug")
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for functionality after a completed gallery download. Use active_extension.after_completed_gallery_download_hook(ARGS) in downloader.
def after_completed_gallery_download_hook(meta: dict, gallery_id):
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Post-download Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Post-Completed Gallery Download Hook Called: Gallery: {meta['id']}: Downloaded.", "debug")
    
    # Extract cover and delete original gallery folder after archiving
    try:
        from mangascraper.core import database
        gallery_format = str(orchestrator.gallery_format).lower() # Check if gallery format is valid, if not, treat as "directory" for safety
        valid_formats = {"directory", "zip", "cbz"}
        if gallery_format not in valid_formats:
            logger.warning(
                f"{EXTENSION_REFERRER}: Unknown GALLERY_FORMAT '{orchestrator.gallery_format}', "
                "treating as 'directory' for safety."
            )
            gallery_format = "directory"

        gallery_meta = build_gallery_metadata_summary(meta, EXTENSION_REFERRER)
        creators = [make_filesystem_safe(c) for c in gallery_meta.get("creator", [])]
        tags = gallery_meta.get("tags", [])
        languages = gallery_meta.get("languages", [])

        # --- Consolidated database update call ---
        database.update_gallery_metadata(
            gallery_id=gallery_id,
            raw_title=gallery_meta.get("raw_title"),
            clean_title=gallery_meta.get("clean_title"),
            language=languages,
            tags=tags,
            cover_path=gallery_meta.get("cover_path"),
            creator_name=creators,
            download_path=gallery_meta.get("download_path"),
            extension_used=gallery_meta.get("extension_used"),
            num_pages=gallery_meta.get("num_pages")
        )

        cover_source = None
        cover_gallery_name = None
        cover_ext = None
        gallery_paths = {}
        cover_gallery_id = None

        temp_root = "/tmp/manga-scraper/archive_temp"
        for creator_name in creators:
            creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
            temp_creator_folder = os.path.join(temp_root, creator_name)

            search_folders = []
            if os.path.isdir(creator_folder):
                search_folders.append(creator_folder)
            if os.path.isdir(temp_creator_folder):
                search_folders.append(temp_creator_folder)
            if not search_folders:
                continue

            gallery_prefix = f"({gallery_id})"
            for search_folder in search_folders:
                # Look for both directories and .cbz/.zip files
                gallery_items = [
                    f for f in os.listdir(search_folder)
                    if (os.path.isdir(os.path.join(search_folder, f)) or f.endswith('.cbz') or f.endswith('.zip'))
                    and f.startswith(gallery_prefix)
                ]
                if not gallery_items:
                    continue
                gallery_items.sort()
                gallery_path = os.path.join(search_folder, gallery_items[0])
                if os.path.isdir(gallery_path):
                    gallery_paths[creator_name] = gallery_path

                    if cover_source is None:
                        candidates = [f for f in os.listdir(gallery_path) if f.startswith("1.")]
                        if candidates:
                            page1_file = os.path.join(gallery_path, candidates[0])
                            _, ext = os.path.splitext(page1_file)
                            cover_source = page1_file
                            cover_gallery_name = gallery_items[0]
                            cover_ext = ext
                            cover_gallery_id = parse_gallery_id(cover_gallery_name)
                elif gallery_items[0].endswith('.cbz') or gallery_items[0].endswith('.zip'):
                    # If it's an archive, set the path for later use
                    gallery_paths[creator_name] = gallery_path
                else:
                    logger.debug(f"Gallery {gallery_items[0]} is already archived or not a directory, skipping")

        cover_generated = {}
        for creator_name in creators:
            creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
            if not os.path.isdir(creator_folder):
                continue

            # Extract cover from the downloaded gallery and store in hidden covers subfolder
            if cover_source and cover_gallery_name and cover_ext:
                covers_folder = os.path.join(creator_folder, ".covers")
                try:
                    os.makedirs(covers_folder, exist_ok=True)
                    latest_cover_id = find_latest_cover_id(covers_folder)
                    if cover_gallery_id is not None and latest_cover_id is not None:
                        if cover_gallery_id <= latest_cover_id:
                            continue
                            gallery_path = gallery_paths.get(creator_name)
                            if gallery_format == "directory" or not gallery_path:
                                if gallery_format == "directory" and gallery_path:
                                    logger.debug(
                                        f"Gallery format is 'directory'; keeping original gallery folder: {gallery_path}"
                                    )
                                continue

                    cover_in_subfolder = os.path.join(covers_folder, f"{cover_gallery_name}{cover_ext}")
                    shutil.copy2(cover_source, cover_in_subfolder)
                    logger.debug(f"Extracted cover for {creator_name}: {cover_in_subfolder}")

                    # Remove any existing cover files (regardless of extension)
                    for f in os.listdir(creator_folder):
                        if f.startswith("cover") and f != "covers" and f != ".covers":
                            try:
                                os.unlink(os.path.join(creator_folder, f))
                            except Exception as e:
                                logger.debug(f"Could not remove old cover file {f}: {e}")

                    # Symlink cover into creator root
                    cover_link = os.path.join(creator_folder, f"cover{cover_ext}")
                    os.symlink(cover_in_subfolder, cover_link)
                    logger.debug(f"Updated cover symlink for {creator_name}: {cover_link} -> {cover_in_subfolder}")
                    cover_generated[creator_name] = True
                except Exception as e:
                    logger.debug(f"Could not extract cover for Gallery {gallery_id}: {e}")

            if not cover_generated.get(creator_name):
                logger.debug(
                    f"Skipping delete for {creator_name}; cover not generated for gallery {gallery_id}."
                )
                continue

            gallery_path = gallery_paths.get(creator_name)
            if gallery_format == "directory" or not gallery_path:
                if gallery_format == "directory" and gallery_path:
                    logger.debug(
                        f"Gallery format is 'directory'; keeping original gallery folder: {gallery_path}"
                    )
                continue

            archive_ext = ".cbz" if gallery_format == "cbz" else ".zip"
            gallery_name = os.path.basename(gallery_path)
            expected_archive = os.path.join(creator_folder, f"{gallery_name}{archive_ext}")
            # Archive the gallery if it's a directory and not already archived
            if gallery_format in {"cbz", "zip"} and os.path.isdir(gallery_path):
                import zipfile
                archive_path = os.path.join(creator_folder, f"{gallery_name}{archive_ext}")
                with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for root, _, files in os.walk(gallery_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, gallery_path)
                            archive.write(file_path, arcname)
                logger.info(f"Archived gallery {gallery_path} to {archive_path}")
            # Wait for the archive to exist
            if not os.path.exists(expected_archive):
                max_checks = max(1, int(ARCHIVE_WAIT_SECONDS / ARCHIVE_POLL_INTERVAL))
                for _ in range(max_checks):
                    time.sleep(ARCHIVE_POLL_INTERVAL)
                    if os.path.exists(expected_archive):
                        break
                if not os.path.exists(expected_archive):
                    logger.warning(
                        f"Archive not found for Gallery {gallery_id} after {ARCHIVE_WAIT_SECONDS}s: "
                        f"expected {expected_archive}; leaving folder undeleted"
                    )
                    logger.info(
                        f"Leaving original folder in place: {gallery_path}"
                    )
                    continue

            # Delete original gallery folder if it was archived
            if os.path.isdir(gallery_path) and os.path.exists(expected_archive):
                try:
                    shutil.rmtree(gallery_path)
                    logger.debug(f"Deleted original gallery folder: {gallery_path}")
                except Exception as e:
                    logger.error(f"Failed to delete gallery folder {gallery_path}: {e}")
    
    except Exception as e:
        logger.error(f"Failed in post-download processing for Gallery {gallery_id}: {e}")

# Hook for cleaning after downloads
def cleanup_hook():
    repair_covers_hook(DEDICATED_DOWNLOAD_PATH, referrer=EXTENSION_REFERRER)
    cleanup_download_tree(DEDICATED_DOWNLOAD_PATH, remove_empty_artist_folder=True)

# Hook for post-batch functionality. Use active_extension.post_batch_hook(ARGS) in downloader.
def post_batch_hook(current_batch_number: int, total_batch_numbers: int):
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Post-batch Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Post-batch Hook Called.", "debug")
    
    def _should_run_post_batch():
        # --- If Total Batches higher than MAX_X_BATCHES, do not run ---
        if total_batch_numbers > MAX_X_BATCHES:
            return False
        
        # --- Calculate when to trigger cleanup ---
        interval = max(1, round(RUNS_PER_X_BATCHES * total_batch_numbers / EVERY_X_BATCHES))
        is_last_batch = current_batch_number == total_batch_numbers
        
        # --- Only run if conditions are met ---
        return (
            not orchestrator.skip_post_batch # If NOT skipping post batch
            and not orchestrator.archiving # If NOT in archival mode
            and not is_last_batch # If not last batch
            and (current_batch_number % interval == 0) # If current batch hits interval
        )
    
    if _should_run_post_batch():
        cleanup_hook() # Call the cleanup hook
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

# Hook for post-run functionality. Use active_extension.post_run_hook(ARGS) in downloader.
def post_run_hook():
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Post-run Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Post-run Hook Called.", "debug")
    
    if orchestrator.skip_post_run:
        log_clarification("debug")
        log(f"{EXTENSION_REFERRER}: Post-run Hook Skipped.", "debug")
    else:
        cleanup_hook() # Call the cleanup hook
        
        log_clarification("debug")
        log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS