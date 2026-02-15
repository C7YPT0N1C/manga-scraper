#!/usr/bin/env python3
# mangascraper/extensions/skeleton/skeleton__msext.py

import os, time, json, requests, math, shutil, re

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.core.api import (
    get_session,
    get_meta_tags,
    make_filesystem_safe,
    clean_title,
    dynamic_sleep,
    fetch_gallery_metadata,
    fetch_image_urls,
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

LOCAL_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "local_manifest.json"
)

with open(os.path.abspath(LOCAL_MANIFEST_PATH), "r", encoding="utf-8") as f:
    manifest = json.load(f)

DEDICATED_DOWNLOAD_PATH = None
manifest_download_path = None
for ext in manifest.get("extensions", []):
    if ext.get("name") == EXTENSION_NAME:
        manifest_download_path = ext.get("image_download_path")
        break

orchestrator.refresh_globals()
override_download_path = getattr(orchestrator, "extension_download_path", None)
if override_download_path and override_download_path != DEFAULT_EXTENSION_DOWNLOAD_PATH:
    DEDICATED_DOWNLOAD_PATH = override_download_path
elif manifest_download_path:
    DEDICATED_DOWNLOAD_PATH = manifest_download_path
else:
    DEDICATED_DOWNLOAD_PATH = DEFAULT_EXTENSION_DOWNLOAD_PATH

SUBFOLDER_STRUCTURE = ["creator", "title"] # SUBDIR_1, SUBDIR_2, etc

# Used to optionally run stuff in hooks (for example, cleaning the download directory) roughly "RUNS_PER_X_BATCHES" times every "EVERY_X_BATCHES" batches.
# Increase this if the operations in your post batch / run hooks get increasingly demanding the larger the library is.
MAX_X_BATCHES = 1000
EVERY_X_BATCHES = 5
RUNS_PER_X_BATCHES = 2

ARCHIVE_WAIT_SECONDS = 120
ARCHIVE_POLL_INTERVAL = 0.5

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
    
    orchestrator.refresh_globals()
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
    orchestrator.refresh_globals()
    
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

def _extract_gallery_id(text: str) -> int | None:
    if not text:
        return None
    match = re.search(r"\((\d+)\)", str(text))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None

def _get_latest_gallery_entry(creator_folder: str) -> tuple[int | None, str | None, bool]:
    if not os.path.isdir(creator_folder):
        return None, None, False

    entries = []
    for name in os.listdir(creator_folder):
        if not name.startswith("("):
            continue
        full_path = os.path.join(creator_folder, name)
        is_dir = os.path.isdir(full_path)
        is_archive = name.endswith(".cbz") or name.endswith(".zip")
        if not (is_dir or is_archive):
            continue
        entry_id = _extract_gallery_id(name)
        if entry_id is None:
            continue
        entry_name = name
        if is_archive:
            entry_name = os.path.splitext(name)[0]
        entries.append((entry_id, entry_name, is_dir))

    if not entries:
        return None, None, False

    entries.sort(key=lambda item: item[0], reverse=True)
    return entries[0]

def _get_latest_cover_id(covers_folder: str) -> int | None:
    if not os.path.isdir(covers_folder):
        return None
    cover_ids = []
    for name in os.listdir(covers_folder):
        entry_id = _extract_gallery_id(name)
        if entry_id is not None:
            cover_ids.append(entry_id)
    if not cover_ids:
        return None
    return max(cover_ids)

def _find_cover_in_covers_folder(covers_folder: str, entry_name: str, latest_id: int | None) -> str | None:
    if not os.path.isdir(covers_folder):
        return None
    candidates = [f for f in os.listdir(covers_folder) if f.startswith(entry_name)]
    if not candidates and latest_id is not None:
        candidates = [
            f for f in os.listdir(covers_folder)
            if _extract_gallery_id(f) == latest_id
        ]
    if not candidates:
        return None
    candidates.sort()
    return os.path.join(covers_folder, candidates[0])

def _find_cover_in_library(entry_name: str, latest_id: int | None) -> str | None:
    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        return None
    for name in os.listdir(DEDICATED_DOWNLOAD_PATH):
        creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, name)
        if not os.path.isdir(creator_folder):
            continue
        covers_folder = os.path.join(creator_folder, ".covers")
        cover = _find_cover_in_covers_folder(covers_folder, entry_name, latest_id)
        if cover:
            return cover
    return None

def _download_cover_for_gallery(gallery_id: int, covers_folder: str, entry_name: str) -> str | None:
    try:
        meta = fetch_gallery_metadata(gallery_id)
        if not meta:
            return None
        urls = fetch_image_urls(meta, 1)
        if not urls:
            return None
        url = urls[0]
        ext = os.path.splitext(url.split("?")[0])[1]
        if not ext:
            ext = ".jpg"
        target = os.path.join(covers_folder, f"{entry_name}{ext}")
        session = get_session(referrer="Cover Repair", status="return")
        resp = session.get(url, timeout=(60, 60))
        resp.raise_for_status()
        with open(target, "wb") as f:
            f.write(resp.content)
        logger.info(f"Cover repaired (downloaded) for Gallery {gallery_id}: {target}")
        return target
    except Exception as e:
        logger.warning(f"Failed to download missing cover for Gallery {gallery_id}: {e}")
        return None

def _link_cover_to_root(creator_folder: str, cover_source: str):
    _, ext = os.path.splitext(cover_source)
    for f in os.listdir(creator_folder):
        if f.startswith("cover") and f != "covers" and f != ".covers":
            try:
                os.unlink(os.path.join(creator_folder, f))
            except Exception:
                pass
    cover_link = os.path.join(creator_folder, f"cover{ext}")
    os.symlink(cover_source, cover_link)
    logger.info(f"Cover repaired for {creator_folder}: {cover_link} -> {cover_source}")

def _ensure_cover_file(creator_folder: str):
    try:
        if not os.path.isdir(creator_folder):
            return

        latest_id, entry_name, is_dir = _get_latest_gallery_entry(creator_folder)
        if not entry_name:
            return

        logger.debug(f"Cover missing for {creator_folder}; searching for latest gallery cover.")

        covers_folder = os.path.join(creator_folder, ".covers")
        if not os.path.isdir(covers_folder):
            os.makedirs(covers_folder, exist_ok=True)

        cover_source = _find_cover_in_covers_folder(covers_folder, entry_name, latest_id)
        if cover_source:
            logger.debug(f"Cover found in .covers: {cover_source}")
            _link_cover_to_root(creator_folder, cover_source)
            return

        if is_dir:
            gallery_path = os.path.join(creator_folder, entry_name)
            if os.path.isdir(gallery_path):
                logger.debug(f"Latest gallery is a folder; checking page 1 in {gallery_path}")
                candidates = [f for f in os.listdir(gallery_path) if f.startswith("1.")]
                if candidates:
                    page1_file = os.path.join(gallery_path, candidates[0])
                    _, ext = os.path.splitext(page1_file)
                    cover_in_subfolder = os.path.join(covers_folder, f"{entry_name}{ext}")
                    if not os.path.exists(cover_in_subfolder):
                        logger.debug(f"Copying cover into .covers: {cover_in_subfolder}")
                        shutil.copy2(page1_file, cover_in_subfolder)
                    _link_cover_to_root(creator_folder, cover_in_subfolder)
                    return

        cover_source = _find_cover_in_library(entry_name, latest_id)
        if cover_source:
            logger.debug(f"Cover found in another creator .covers: {cover_source}")
            _, ext = os.path.splitext(cover_source)
            cover_in_subfolder = os.path.join(covers_folder, f"{entry_name}{ext}")
            if not os.path.exists(cover_in_subfolder):
                logger.debug(f"Copying cover into .covers: {cover_in_subfolder}")
                shutil.copy2(cover_source, cover_in_subfolder)
            _link_cover_to_root(creator_folder, cover_in_subfolder)
            return

        if latest_id is not None:
            logger.debug(f"Cover not found locally; downloading for Gallery {latest_id}")
            downloaded = _download_cover_for_gallery(latest_id, covers_folder, entry_name)
            if downloaded:
                _link_cover_to_root(creator_folder, downloaded)
    except Exception as e:
        logger.debug(f"Failed to restore cover file in {creator_folder}: {e}")

def install_extension():
    """
    Install the extension and ensure the dedicated image download path exists.
    """
    
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH
    
    orchestrator.refresh_globals()

    if not DEDICATED_DOWNLOAD_PATH:
        # Fallback in case manifest didn't define it
        DEDICATED_DOWNLOAD_PATH = DEFAULT_EXTENSION_DOWNLOAD_PATH
    
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
    
    orchestrator.refresh_globals()
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Test Hook Called.", "debug")

# Remove empty folders inside DEDICATED_DOWNLOAD_PATH without deleting the root folder itself.
def clean_directories(RemoveEmptyArtistFolder: bool = True):
    global DEDICATED_DOWNLOAD_PATH
    
    orchestrator.refresh_globals()
    
    log_clarification("debug")

    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        log("No valid DEDICATED_DOWNLOAD_PATH set, skipping cleanup.", "debug")
        return

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would remove empty directories under {DEDICATED_DOWNLOAD_PATH}")
        return

    broken_symlinks_removed = 0
    
    # Combined single walk for both directory cleanup and symlink removal
    for dirpath, dirnames, filenames in os.walk(DEDICATED_DOWNLOAD_PATH, topdown=False):
        if dirpath == DEDICATED_DOWNLOAD_PATH:
            continue
        
        # Remove empty directories
        try:
            if RemoveEmptyArtistFolder:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
            else:
                if not dirnames and not filenames:
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
        except Exception as e:
            logger.warning(f"Could not remove empty directory: {dirpath}: {e}")
        
        # Check and remove broken symlinks
        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            if os.path.islink(full_path) and not os.path.exists(os.readlink(full_path)):
                try:
                    os.unlink(full_path)
                    logger.info(f"Removed broken symlink: {full_path}")
                    broken_symlinks_removed += 1
                except Exception as e:
                    logger.warning(f"Failed to remove broken symlink {full_path}: {e}")

        # Restore missing cover file for creator folders
        if os.path.dirname(dirpath) == DEDICATED_DOWNLOAD_PATH:
            _ensure_cover_file(dirpath)

    if broken_symlinks_removed > 0:
        logger.info(f"Fixed {broken_symlinks_removed} broken symlink(s).")

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
        gallery_format = str(orchestrator.gallery_format).lower() # Check if gallery format is valid, if not, treat as "directory" for safety
        valid_formats = {"directory", "zip", "cbz"}
        if gallery_format not in valid_formats:
            logger.warning(
                f"{EXTENSION_REFERRER}: Unknown GALLERY_FORMAT '{orchestrator.gallery_format}', "
                "treating as 'directory' for safety."
            )
            gallery_format = "directory"

        gallery_meta = return_gallery_metas(meta)
        creators = [make_filesystem_safe(c) for c in gallery_meta.get("creator", [])]
        cover_source = None
        cover_gallery_name = None
        cover_ext = None
        gallery_paths = {}
        cover_gallery_id = None

        for creator_name in creators:
            creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
            if not os.path.isdir(creator_folder):
                continue

            gallery_prefix = f"({gallery_id})"
            gallery_items = [
                f for f in os.listdir(creator_folder)
                if os.path.isdir(os.path.join(creator_folder, f)) and f.startswith(gallery_prefix)
            ]
            if gallery_items:
                gallery_items.sort()
                gallery_path = os.path.join(creator_folder, gallery_items[0])
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
                            cover_gallery_id = _extract_gallery_id(cover_gallery_name)
                else:
                    logger.debug(f"Gallery {gallery_items[0]} is already archived or not a directory, skipping")

        for creator_name in creators:
            creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
            if not os.path.isdir(creator_folder):
                continue

            # Extract cover from the downloaded gallery and store in hidden covers subfolder
            if cover_source and cover_gallery_name and cover_ext:
                covers_folder = os.path.join(creator_folder, ".covers")
                try:
                    os.makedirs(covers_folder, exist_ok=True)
                    latest_cover_id = _get_latest_cover_id(covers_folder)
                    if cover_gallery_id is not None and latest_cover_id is not None:
                        if cover_gallery_id <= latest_cover_id:
                            _ensure_cover_file(creator_folder)
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
                except Exception as e:
                    logger.debug(f"Could not extract cover for Gallery {gallery_id}: {e}")

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

            # Delete original gallery folder
            try:
                shutil.rmtree(gallery_path)
                logger.debug(f"Deleted original gallery folder: {gallery_path}")
            except Exception as e:
                logger.error(f"Failed to delete gallery folder {gallery_path}: {e}")
    
    except Exception as e:
        logger.error(f"Failed in post-download processing for Gallery {gallery_id}: {e}")

# Hook for cleaning after downloads
def cleanup_hook():
    clean_directories(True) # Clean up the download folder / directories

def repair_covers_hook():
    orchestrator.refresh_globals()
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Repair covers hook inactive.")
        return
    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        return
    repaired = 0
    for name in os.listdir(DEDICATED_DOWNLOAD_PATH):
        creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, name)
        if os.path.isdir(creator_folder):
            before = any(
                f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
                for f in os.listdir(creator_folder)
            )
            _ensure_cover_file(creator_folder)
            after = any(
                f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
                for f in os.listdir(creator_folder)
            )
            if not before and after:
                repaired += 1
    logger.debug(f"{EXTENSION_REFERRER}: Cover repair complete. Restored {repaired} cover(s).")

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
        repair_covers_hook()
        cleanup_hook() # Call the cleanup hook
        
        log_clarification("debug")
        log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS