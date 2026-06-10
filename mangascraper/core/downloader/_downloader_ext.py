#!/usr/bin/env python3
# mangascraper/core/downloader/_download_ext.py

"""
This is a skeleton/example extension for manga-scraper. It is also used as the default extension if none is specified.
"""

import os, time, requests, shutil, re, zipfile

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.core.api import api as scraperapi
from mangascraper.core.api.api import *
from dovetail import Dovetail

####################################################################################################################
# Global variables
####################################################################################################################

MODULE_NAME = "downloader" # Must be fully lowercase
MODULE_NAME_CAPITALISED = MODULE_NAME.capitalize()
MODULE_REFERRER = f"{MODULE_NAME_CAPITALISED}"

SUBFOLDER_STRUCTURE = ["creator", "title"] # SUBDIR_1, SUBDIR_2, etc

# Used to optionally run stuff in hooks (for example, cleaning the download directory) roughly "RUNS_PER_X_BATCHES" times every "EVERY_X_BATCHES" batches.
# Increase this if the operations in your post batch / run hooks get increasingly demanding the larger the library is.
MAX_X_BATCHES = 50
EVERY_X_BATCHES = 10
RUNS_PER_X_BATCHES = 1

ARCHIVE_WAIT_SECONDS = 120 # How long the extension should wait for an archive-related operation to finish before treating it as timed out or moving on.
ARCHIVE_POLL_INTERVAL = 0.5 # How often to check the status of an archive operation

####################################################################################################################
# CUSTOM VARIABLES / FUNCTIONS (Create your custom hooks here, add them into the corresponding CORE HOOK. Must be thread-safe.)
####################################################################################################################

def test_hook():
    """
    Hook for testing functionality.
    Use active_extension.test_hook(ARGS) in downloader.
    """
    
    orchestrator.refresh_globals()
    
    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Test Hook Called.", "debug")
    
def parse_gallery_id_from_title(text: str) -> int | None:
    if not text:
        return None
    match = re.search(r"\((\d+)\)", str(text))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


# Image helpers
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

def is_image_name(filename: str) -> bool:
    _, ext = os.path.splitext(filename or "")
    return ext.lower() in IMAGE_EXTS

def find_first_image_file(folder: str) -> str | None:
    """Find the first image file in a folder using numeric-prefix logic.

    Returns the filename (not full path) of the first image, preferring index 1,
    otherwise the smallest numeric index. Returns None if none found.
    """
    if not os.path.isdir(folder):
        return None
    numeric_files = []
    for fn in os.listdir(folder):
        if not is_image_name(fn):
            continue
        m = re.match(r"^(\d+)\.", fn)
        if m:
            try:
                numeric_files.append((int(m.group(1)), fn))
            except Exception:
                continue
    if not numeric_files:
        # Fallback: return first image sorted lexicographically
        images = sorted(f for f in os.listdir(folder) if is_image_name(f))
        return images[0] if images else None
    numeric_files.sort()
    for num, fn in numeric_files:
        if num == 1:
            return fn
    return numeric_files[0][1]

def find_first_image_in_archive(zip_path: str) -> str | None:
    """Return the archive internal name of the first image using numeric-prefix logic, or None."""
    if not os.path.isfile(zip_path):
        return None
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [n for n in zf.namelist() if not n.endswith("/") and is_image_name(n)]
            if not names:
                return None
            numeric_files = []
            for n in names:
                base = os.path.basename(n)
                m = re.match(r"^(\d+)\.", base)
                if m:
                    try:
                        numeric_files.append((int(m.group(1)), n))
                    except Exception:
                        continue
            if not numeric_files:
                return sorted(names)[0]
            numeric_files.sort()
            for num, n in numeric_files:
                if num == 1:
                    return n
            return numeric_files[0][1]
    except Exception:
        return None

def _safe_symlink_or_copy(src: str, dest: str):
    """Try to create a symlink; if not possible, fall back to copying the file."""
    try:
        if os.path.exists(dest) or os.path.islink(dest):
            try:
                os.unlink(dest)
            except Exception:
                pass
        os.symlink(src, dest)
        return True
    except Exception:
        try:
            shutil.copy2(src, dest)
            return True
        except Exception:
            return False

def find_latest_gallery_entry(creator_folder: str) -> tuple[int | None, str | None, bool]:
    if not os.path.isdir(creator_folder):
        return None, None, False

    entries = []
    for name in os.listdir(creator_folder):
        if not name.startswith("("):
            continue
        full_path = os.path.join(creator_folder, name)
        is_dir = os.path.isdir(full_path)
        is_cbz = name.endswith(".cbz") and os.path.isfile(full_path)
        is_zip = name.endswith(".zip") and os.path.isfile(full_path)
        is_archive = is_cbz or is_zip
        if not (is_dir or is_archive):
            continue
        entry_id = parse_gallery_id_from_title(name)
        if entry_id is None:
            continue
        # For archives, strip extension for entry_name, and set is_dir False
        if is_archive:
            entry_name = os.path.splitext(name)[0]
            entries.append((entry_id, entry_name, False))
        else:
            entry_name = name
            entries.append((entry_id, entry_name, True))

    if not entries:
        return None, None, False

    entries.sort(key=lambda item: item[0], reverse=True)
    return entries[0]

def find_latest_cover_id(covers_folder: str) -> int | None:
    if not os.path.isdir(covers_folder):
        return None
    cover_ids = []
    for name in os.listdir(covers_folder):
        entry_id = parse_gallery_id_from_title(name)
        if entry_id is not None:
            cover_ids.append(entry_id)
    if not cover_ids:
        return None
    return max(cover_ids)

def link_creator_cover(creator_folder: str, cover_source: str) -> str | None:
    try:
        _, ext = os.path.splitext(cover_source)
        for f in os.listdir(creator_folder):
            if f.startswith("cover") and f != "covers" and f != ".covers":
                try:
                    os.unlink(os.path.join(creator_folder, f))
                except Exception:
                    pass
        cover_link = os.path.join(creator_folder, f"cover{ext}")
        ok = _safe_symlink_or_copy(cover_source, cover_link)
        if ok:
            logger.info(f"Cover updated for {creator_folder}: {cover_link} -> {cover_source}")
            return cover_link
        return None
    except Exception:
        return None

def find_local_cover_and_link(creator_folder: str, entry_name: str, is_dir: bool) -> str | None:
    covers_folder = os.path.join(creator_folder, ".covers")
    if not os.path.isdir(covers_folder):
        os.makedirs(covers_folder, exist_ok=True)

    candidates = [
        f for f in os.listdir(covers_folder)
        if os.path.splitext(f)[0] == entry_name
    ]
    if candidates:
        candidates.sort()
        cover_source = os.path.join(covers_folder, candidates[0])
        logger.debug(f"Cover found in .covers: {cover_source}")
        return link_creator_cover(creator_folder, cover_source)

    if is_dir:
        gallery_path = os.path.join(creator_folder, entry_name)
        if os.path.isdir(gallery_path):
            logger.debug(f"Latest gallery is a folder; checking page 1 in {gallery_path}")
            # Find files with a leading numeric page index (handles zero-padded names)
            numeric_files = []
            for fn in os.listdir(gallery_path):
                try:
                    m = re.match(r"^(\d+)\.", fn)
                    if m:
                        numeric_files.append((int(m.group(1)), fn))
                except Exception:
                    continue
            if numeric_files:
                numeric_files.sort()
                chosen = None
                for num, fn in numeric_files:
                    if num == 1:
                        chosen = fn
                        break
                if chosen is None:
                    chosen = numeric_files[0][1]
                page1_file = os.path.join(gallery_path, chosen)
                _, ext = os.path.splitext(page1_file)
                cover_in_subfolder = os.path.join(covers_folder, f"{entry_name}{ext}")
                if not os.path.exists(cover_in_subfolder):
                    logger.debug(f"Copying cover into .covers: {cover_in_subfolder}")
                    shutil.copy2(page1_file, cover_in_subfolder)
                return link_creator_cover(creator_folder, cover_in_subfolder)

    return None

def ensure_creator_cover(creator_folder: str):
    try:
        if not os.path.isdir(creator_folder):
            return
        # Use find_latest_gallery_entry, which considers both directories and .cbz/.zip archives
        latest_id, entry_name, is_dir = find_latest_gallery_entry(creator_folder)
        if not entry_name or latest_id is None:
            return

        logger.debug(f"Cover missing for {creator_folder}; checking local sources for gallery {latest_id} (is_dir={is_dir}).")
        find_local_cover_and_link(creator_folder, entry_name, is_dir)
    except Exception as e:
        logger.debug(f"Failed to restore cover file in {creator_folder}: {e}")

def repair_creator_cover(creator_folder: str):
    try:
        logger.debug(f"[Cover Repair] Checking folder: {creator_folder}")
        if not os.path.isdir(creator_folder):
            logger.debug(f"[Cover Repair] Folder does not exist: {creator_folder}")
            return
        if any(
            f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
            for f in os.listdir(creator_folder)
        ):
            logger.debug(f"[Cover Repair] Cover file already exists in: {creator_folder}")
            return

        # Use find_latest_gallery_entry, which considers both directories and .cbz/.zip archives
        latest_id, entry_name, is_dir = find_latest_gallery_entry(creator_folder)
        logger.debug(f"[Cover Repair] Latest gallery entry: id={latest_id}, name={entry_name}, is_dir={is_dir}")
        if not entry_name or latest_id is None:
            logger.debug(f"[Cover Repair] No valid gallery entry found in: {creator_folder}")
            return

        if find_local_cover_and_link(creator_folder, entry_name, is_dir):
            logger.debug(f"[Cover Repair] Cover restored from local sources for: {creator_folder}")
            return

        covers_folder = os.path.join(creator_folder, ".covers")
        if not os.path.isdir(covers_folder):
            logger.debug(f"[Cover Repair] Creating covers folder: {covers_folder}")
            os.makedirs(covers_folder, exist_ok=True)

        logger.debug(f"[Cover Repair] Cover not found locally; attempting download for Gallery {latest_id}")
        try:
            meta = scraperapi.Fetch.gallery_metadata(latest_id)
            logger.debug(f"[Cover Repair] Fetched metadata for Gallery {latest_id}: {meta is not None}")
            if not meta:
                logger.warning(f"[Cover Repair] No metadata found for Gallery {latest_id}")
                return
            urls = scraperapi.Fetch.image_urls(meta, 1)
            logger.debug(f"[Cover Repair] Fetched image URLs for Gallery {latest_id}: {urls}")
            if not urls:
                logger.warning(f"[Cover Repair] No image URLs found for Gallery {latest_id}")
                return
            url = urls[0]
            ext = os.path.splitext(url.split("?")[0])[1]
            if not ext:
                ext = ".jpg"
            target = os.path.join(covers_folder, f"{entry_name}{ext}")
            logger.debug(f"[Cover Repair] Downloading cover from {url} to {target}")
            session = scraperapi.Get.session(referrer="Cover Repair", status="return")
            resp = session.get(url, timeout=(60, 60))
            resp.raise_for_status()
            with open(target, "wb") as f:
                f.write(resp.content)
            logger.info(f"[Cover Repair] Cover updated (downloaded) for Gallery {latest_id}: {target}")
            link_creator_cover(creator_folder, target)
            logger.debug(f"[Cover Repair] Cover linked for {creator_folder}: {target}")
        except Exception as e:
            logger.warning(f"[Cover Repair] Failed to download missing cover for Gallery {latest_id}: {e}")
    except Exception as e:
        logger.debug(f"[Cover Repair] Failed to restore cover file in {creator_folder}: {e}")
        
def repair_covers_hook(download_path, referrer="Extension Manager"):
    orchestrator.refresh_globals()
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {referrer}: Repair covers hook inactive.")
        return
    if not download_path or not os.path.isdir(download_path):
        logger.debug(f"[Cover Repair] No valid download path: {download_path}")
        return
    repaired = 0
    for name in os.listdir(download_path):
        creator_folder = os.path.join(download_path, name)
        if os.path.isdir(creator_folder):
            before = any(
                f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
                for f in os.listdir(creator_folder)
            )
            repair_creator_cover(creator_folder)
            after = any(
                f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
                for f in os.listdir(creator_folder)
            )
            if not before and after:
                repaired += 1
    logger.debug(f"{referrer}: Cover update pass complete. Restored {repaired} cover(s).")

def cleanup_download_tree(
    download_path: str,
    remove_empty_artist_folder: bool = True,
    log_scan_summary: bool = False,
):
    orchestrator.refresh_globals()

    log_clarification("debug")

    if not download_path or not os.path.isdir(download_path):
        log("No valid download path set, skipping cleanup.", "debug")
        return

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would remove empty directories under {download_path}")
        return

    broken_symlinks_removed = 0

    # Combined single walk for both directory cleanup and symlink removal
    for dirpath, dirnames, filenames in os.walk(download_path, topdown=False):
        if dirpath == download_path:
            continue

        # Remove empty directories
        try:
            if remove_empty_artist_folder:
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
        if os.path.dirname(dirpath) == download_path:
            ensure_creator_cover(dirpath)

    if log_scan_summary:
        logger.info("Removed empty directories.")
        log_clarification()

    if broken_symlinks_removed > 0:
        logger.info(f"Fixed {broken_symlinks_removed} broken symlink(s).")

    if log_scan_summary:
        logger.info("Scan complete.")

def _create_cover_link_or_copy(cover_source: str, cover_link: str, creator_name: str) -> bool:
    """
    Create a symlink to the cover; fall back to copying for Windows/non-privileged environments.
    """
    
    try:
        os.symlink(cover_source, cover_link)
        logger.debug(f"[COVERS] Updated cover symlink for {creator_name}: {cover_link} -> {cover_source}")
        return True
    except Exception as symlink_error:
        try:
            shutil.copy2(cover_source, cover_link)
            logger.debug(
                f"[COVERS] Symlink unavailable for {creator_name}; copied cover instead: {cover_source} -> {cover_link} ({symlink_error})"
            )
            return True
        except Exception as copy_error:
            logger.debug(
                f"[COVERS] Failed to create cover link/copy for {creator_name}: symlink={symlink_error}; copy={copy_error}"
            )
            return False

def maintain_gallery_covers():
    """
    Maintains cover images for galleries.
    
    Structure:
    - CreatorFolder/cover.ext → symlink to latest gallery cover
    - CreatorFolder/.covers/(GalleryID) GalleryTitle.ext → cover images
    """
    
    if not os.path.isdir(orchestrator.download_path):
        return
    
    try:
        for creator_name in os.listdir(orchestrator.download_path):
            if creator_name.startswith("."):
                continue
            
            creator_path = os.path.join(orchestrator.download_path, creator_name)
            if not os.path.isdir(creator_path):
                continue
            
            # Create .covers subfolder
            covers_folder = os.path.join(creator_path, ".covers")
            os.makedirs(covers_folder, exist_ok=True)
            
            latest_gallery_id = None
            latest_gallery_name = None
            
            # Process all galleries in the creator folder
            for entry_name in os.listdir(creator_path):
                if entry_name.startswith("."):
                    continue
                
                entry_path = os.path.join(creator_path, entry_name)
                
                # Extract gallery ID and title
                match = re.search(r"\((\d+)\)", entry_name)
                if not match:
                    continue
                
                try:
                    gallery_id = int(match.group(1))
                except ValueError:
                    continue
                
                # Track the latest gallery
                if latest_gallery_id is None or gallery_id > latest_gallery_id:
                    latest_gallery_id = gallery_id
                    latest_gallery_name = entry_name
                
                # Get the base name without extension for directories
                if os.path.isdir(entry_path):
                    gallery_base = entry_name
                    # Look for first image page
                    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    pages = sorted(f for f in os.listdir(entry_path) 
                                 if os.path.splitext(f)[1].lower() in IMAGE_EXTS)
                    if pages:
                        first_page = os.path.join(entry_path, pages[0])
                        _, ext = os.path.splitext(first_page)
                        cover_dest = os.path.join(covers_folder, f"{gallery_base}{ext}")
                        # Copy first page as cover if it doesn't exist
                        if not os.path.exists(cover_dest):
                            try:
                                shutil.copy2(first_page, cover_dest)
                                logger.debug(f"[COVERS] Created cover for {creator_name}/{gallery_base}")
                            except Exception as e:
                                logger.debug(f"[COVERS] Failed to copy cover for {gallery_base}: {e}")
                
                # Handle archive files (.cbz, .zip)
                elif entry_path.endswith((".cbz", ".zip")) and os.path.isfile(entry_path):
                    gallery_base = os.path.splitext(entry_name)[0]
                    # Try to extract first image from archive
                    try:
                        with zipfile.ZipFile(entry_path, "r") as zf:
                            IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                            pages = sorted(f for f in zf.namelist()
                                         if not f.endswith("/") and 
                                         os.path.splitext(f)[1].lower() in IMAGE_EXTS)
                            if pages:
                                first_page_data = zf.read(pages[0])
                                _, ext = os.path.splitext(pages[0])
                                cover_dest = os.path.join(covers_folder, f"{gallery_base}{ext}")
                                if not os.path.exists(cover_dest):
                                    with open(cover_dest, "wb") as f:
                                        f.write(first_page_data)
                                    logger.debug(f"[COVERS] Created cover from archive {creator_name}/{gallery_base}")
                    except Exception as e:
                        logger.debug(f"[COVERS] Failed to extract cover from {entry_name}: {e}")
            
            # Create/update creator cover (symlink to latest gallery cover)
            if latest_gallery_name:
                gallery_base = os.path.splitext(latest_gallery_name)[0] if latest_gallery_name.endswith((".cbz", ".zip")) else latest_gallery_name
                
                # Find the cover file for the latest gallery
                for ext in ("jpg", "jpeg", "png", "gif", "webp"):
                    cover_source = os.path.join(covers_folder, f"{gallery_base}.{ext}")
                    if os.path.exists(cover_source):
                        # Remove old cover links
                        for old_ext in ("jpg", "jpeg", "png", "gif", "webp"):
                            old_cover = os.path.join(creator_path, f"cover.{old_ext}")
                            if os.path.islink(old_cover) or os.path.isfile(old_cover):
                                try:
                                    os.unlink(old_cover)
                                except Exception:
                                    pass
                        
                        # Create new symlink
                        cover_link = os.path.join(creator_path, f"cover.{ext}")
                        _create_cover_link_or_copy(cover_source, cover_link, creator_name)
                        break
    
    except Exception as e:
        logger.debug(f"[COVERS] Error in maintain_gallery_covers: {e}")
        
def cleanup_hook():
    """
    Does housekeeping / maintenance tasks, for example, pruning temp files, removing stale artifacts, or normalising folders.
    """
    
    maintain_gallery_covers()
    repair_covers_hook(orchestrator.download_path, referrer=MODULE_REFERRER)
    cleanup_download_tree(orchestrator.download_path, remove_empty_artist_folder=True, log_scan_summary=True)

# PUT YOUR FUNCTIONS / VARIABLES HERE

####################################################################################################################
# CORE HOOKS (Please add to the functions, try not to change or remove anything. Must be thread-safe.)
####################################################################################################################

def pre_run_hook():
    """
    Hook for pre-run functionality.
    Use active_extension.pre_run_hook(ARGS) in downloader.
    \nThis is the extension's entrypoint.
    """
    
    logger.debug(f"[{MODULE_REFERRER}] Ready.")
    log(f"[{MODULE_REFERRER}] Debugging started.", "debug")
    
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would ensure download path exists: {orchestrator.download_path}")
        return
    try:
        os.makedirs(orchestrator.download_path, exist_ok=True)
        logger.debug(f"[{MODULE_REFERRER}] Download path ready at '{orchestrator.download_path}'.")
    except Exception as e:
        logger.error(f"[{MODULE_REFERRER}] Failed to create download path '{orchestrator.download_path}': {e}")

def download_images_hook(gallery, page, urls, path, downloader_session, pbar=None, creator=None, page_update_hook=None):
    """
    Hook for downloading images.
    Use active_extension.download_images_hook(ARGS) in downloader.
    \nDownloads an image from one of the provided URLs to the given path.
    \nTries mirrors in order until one succeeds, with retries per mirror.
    \nUpdates tqdm progress bar with current creator.
    """

    orchestrator.refresh_globals()
    
    # Abort early if shutdown requested
    try:
        from mangascraper.core.downloader.download_manager import _shutdown_event
    except ImportError:
        _shutdown_event = None
    if _shutdown_event and _shutdown_event.is_set():
        logger.warning("Shutdown event detected, aborting image download.")
        return False

    if not urls: # If no URLs are returned for the Gallery.
        logger.warning(f"Gallery {gallery}: Page {page}: No URLs, skipping")
        if pbar and creator:
            pbar.set_postfix_str(f"Skipped Creator: {creator}")
        return False

    if os.path.exists(path): # If Gallery already exists, skip downloading it.
        log(f"Already exists, skipping: {path}", "debug")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if orchestrator.dry_run: # Dry Run
        logger.info(f"[DRY RUN] Gallery {gallery}: Would download {urls[0]} -> {path}")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    # Create a new downloader session if one doesn't exist.
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
                    if os.path.exists(path):
                        try:
                            os.unlink(path)
                        except Exception:
                            pass
                    wait = scraperapi.Sleep.dynamic("image", attempt=attempt)
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
        downloader_session = scraperapi.Get.session(referrer=f"{MODULE_REFERRER}", status="rebuild")
        success = try_download(downloader_session, urls, 1, tor_rotate=True)
    
    # Explicitly call page_update_hook after each page download if provided
    if success and page_update_hook:
        try:
            page_update_hook()
        except Exception as e:
            logger.warning(f"page_update_hook failed: {e}")

    if not success:
        log_clarification()
        logger.error(
            f"Gallery {gallery}: Page {page}: All mirrors failed after Tor rotate too: {urls}"
        )
        if pbar and creator:
            pbar.set_postfix_str(f"Failed Creator: {creator}")

    return success

def pre_batch_hook(gallery_list):
    """
    Hook for pre-batch functionality.
    Use active_extension.pre_batch_hook(ARGS) in downloader.
    """
    
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: Pre-batch Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Pre-batch Hook Called.", "debug")
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS
    
    return gallery_list

def pre_gallery_download_hook(gallery_id):
    """
    Hook for functionality before a gallery download.
    Use active_extension.pre_gallery_download_hook(ARGS) in downloader.
    """
    
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: Pre-download Hook Inactive.")
    
    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Pre-download Hook Called: Gallery: {gallery_id}", "debug")
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

def during_gallery_download_hook(gallery_id):
    """
    Hook for functionality during a gallery download.
    Use active_extension.during_gallery_download_hook(ARGS) in downloader.
    """
    
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: During-download Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] During-download Hook Called: Gallery: {gallery_id}", "debug")
    
    #log_clarification("debug")
    #log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS

def after_completed_gallery_download_hook(meta: dict, gallery_id):
    """
    Hook for functionality after a completed gallery download.
    Use active_extension.after_completed_gallery_download_hook(ARGS) in downloader.
    """
    
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: Post-download Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Post-Completed Gallery Download Hook Called: Gallery: {meta['id']}: Downloaded.", "debug")
    
    # Extract cover and delete original gallery folder after archiving
    
    try:
        gallery_format = str(orchestrator.gallery_format).lower() # Check if gallery format is valid, if not, treat as "directory" for safety
        valid_formats = {"directory", "zip", "cbz"}
        if gallery_format not in valid_formats:
            logger.warning(
                f"[{MODULE_REFERRER}] Unknown GALLERY_FORMAT '{orchestrator.gallery_format}', "
                "treating as 'directory' for safety."
            )
            gallery_format = "directory"

        gallery_meta = scraperapi.Helpers.summary(meta, MODULE_REFERRER)
        creator_entries = scraperapi.Helpers.resolve_creator_entries(meta, orchestrator.download_path)
        creators = [entry["folder_name"] for entry in creator_entries]

        cover_source = None
        cover_gallery_name = None
        cover_ext = None
        gallery_paths = {}
        cover_gallery_id = None
        
        temp_root = f"/opt/manga-scraper/mangascraper/core/archive_temp/"
        for creator_name in creators:
            creator_folder = os.path.join(orchestrator.download_path, creator_name)
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
                        # Accept both non-padded and zero-padded first-page names (1.jpg, 01.jpg, 001.jpg, ...)
                        candidates = [
                            f for f in os.listdir(gallery_path)
                            if re.match(r"^0*1\.[^.]+$", f, re.IGNORECASE)
                        ]
                        if candidates:
                            candidates.sort()
                            page1_file = os.path.join(gallery_path, candidates[0])
                            _, ext = os.path.splitext(page1_file)
                            cover_source = page1_file
                            cover_gallery_name = gallery_items[0]
                            cover_ext = ext
                            cover_gallery_id = parse_gallery_id_from_title(cover_gallery_name)
                elif gallery_items[0].endswith('.cbz') or gallery_items[0].endswith('.zip'):
                    # If it's an archive, set the path for later use
                    gallery_paths[creator_name] = gallery_path
                else:
                    logger.debug(f"Gallery {gallery_items[0]} is already archived or not a directory, skipping")

        cover_generated = {}
        for creator_name in creators:
            creator_folder = os.path.join(orchestrator.download_path, creator_name)
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
                    cover_generated[creator_name] = _create_cover_link_or_copy(
                        cover_in_subfolder,
                        cover_link,
                        creator_name,
                    )
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
            archive_name = scraperapi.Helpers.archive_filename(gallery_name, archive_ext)
            expected_archive = os.path.join(creator_folder, archive_name)
            
            # Archive the gallery if it's a directory and not already archived
            if gallery_format in {"cbz", "zip"} and os.path.isdir(gallery_path):
                archive_path = os.path.join(creator_folder, archive_name)
                with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for root, _, files in os.walk(gallery_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, gallery_path)
                            archive.write(file_path, arcname)
                logger.debug(f"[{MODULE_REFERRER}] Archived gallery {gallery_path} to {archive_path}")
            
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

def post_batch_hook(current_batch_number: int, total_batch_numbers: int):
    """
    Hook for post-batch functionality.
    Use active_extension.post_batch_hook(ARGS) in downloader.
    """
    
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: Post-batch Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Post-batch Hook Called.", "debug")
    
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

def post_run_hook():
    """
    Hook for post-run functionality.
    Use active_extension.post_run_hook(ARGS) in downloader.
    """
    
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: Post-run Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Post-run Hook Called.", "debug")
    
    if orchestrator.skip_post_run:
        log_clarification("debug")
        log(f"[{MODULE_REFERRER}] Post-run Hook Skipped.", "debug")
    else:
        cleanup_hook() # Call the cleanup hook
        
        log_clarification("debug")
        log("", "debug") # <-------- ADD STUFF IN PLACE OF THIS