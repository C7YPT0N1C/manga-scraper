#!/usr/bin/env python3
# mangascraper/core/downloader/_downloader_ext.py

"""
Extension file for manga-scraper. Also used as the default extension if none is specified.
Contains cover management, archive helpers, filesystem helpers, and download lifecycle hooks.
"""

import os, re, time, shutil, zipfile, requests

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.core.api import api as scraperapi
from mangascraper.core.api.api import *
from dovetail import Dovetail

####################################################################################################################
# Global Variables
####################################################################################################################

MODULE_NAME = "downloader"
MODULE_NAME_CAPITALISED = MODULE_NAME.capitalize()
MODULE_REFERRER = f"{MODULE_NAME_CAPITALISED}"

SUBFOLDER_STRUCTURE = ["creator", "title"]

# Controls how often post-batch cleanup runs.
MAX_X_BATCHES = 50
EVERY_X_BATCHES = 10
RUNS_PER_X_BATCHES = 1

ARCHIVE_WAIT_SECONDS = 120
ARCHIVE_POLL_INTERVAL = 0.5

####################################################################################################################
# IMAGE / ARCHIVE HELPERS
####################################################################################################################

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def is_image_name(filename: str) -> bool:
    _, ext = os.path.splitext(filename or "")
    return ext.lower() in IMAGE_EXTS


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


def find_first_image_file(folder: str) -> str | None:
    """Return the filename of the first image in a folder (numeric-prefix logic)."""
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
        images = sorted(f for f in os.listdir(folder) if is_image_name(f))
        return images[0] if images else None
    numeric_files.sort()
    for num, fn in numeric_files:
        if num == 1:
            return fn
    return numeric_files[0][1]


def find_first_image_in_archive(zip_path: str) -> str | None:
    """Return the archive-internal name of the first image (numeric-prefix logic)."""
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


def find_latest_gallery_entry(creator_folder: str) -> tuple[int | None, str | None, bool]:
    """Return (gallery_id, entry_name, is_dir) for the most recent gallery in a creator folder."""
    if not os.path.isdir(creator_folder):
        return None, None, False

    entries = []
    for name in os.listdir(creator_folder):
        if not name.startswith("("):
            continue
        full_path = os.path.join(creator_folder, name)
        is_dir = os.path.isdir(full_path)
        is_archive = (name.endswith(".cbz") or name.endswith(".zip")) and os.path.isfile(full_path)
        if not (is_dir or is_archive):
            continue
        entry_id = parse_gallery_id_from_title(name)
        if entry_id is None:
            continue
        if is_archive:
            entries.append((entry_id, os.path.splitext(name)[0], False))
        else:
            entries.append((entry_id, name, True))

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
    return max(cover_ids) if cover_ids else None


####################################################################################################################
# COVER HELPERS
####################################################################################################################

def _safe_symlink_or_copy(src: str, dest: str) -> bool:
    """Try to create a symlink; fall back to copying."""
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


def create_cover_link_or_copy(cover_source: str, cover_link: str, creator_name: str) -> bool:
    """Create a symlink to the cover; fall back to copying."""
    try:
        os.symlink(cover_source, cover_link)
        logger.debug(f"[COVERS] Updated cover symlink for {creator_name}: {cover_link} -> {cover_source}")
        return True
    except Exception as symlink_error:
        try:
            shutil.copy2(cover_source, cover_link)
            logger.debug(
                f"[COVERS] Symlink unavailable for {creator_name}; copied cover instead: "
                f"{cover_source} -> {cover_link} ({symlink_error})"
            )
            return True
        except Exception as copy_error:
            logger.debug(
                f"[COVERS] Failed to create cover link/copy for {creator_name}: "
                f"symlink={symlink_error}; copy={copy_error}"
            )
            return False


def link_creator_cover(creator_folder: str, cover_source: str) -> str | None:
    """Update the creator-level cover symlink/copy to point to cover_source."""
    try:
        _, ext = os.path.splitext(cover_source)
        for f in os.listdir(creator_folder):
            if f.startswith("cover") and f not in ("covers", ".covers"):
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
                chosen = next((fn for num, fn in numeric_files if num == 1), numeric_files[0][1])
                page1_file = os.path.join(gallery_path, chosen)
                _, ext = os.path.splitext(page1_file)
                cover_in_subfolder = os.path.join(covers_folder, f"{entry_name}{ext}")
                if not os.path.exists(cover_in_subfolder):
                    logger.debug(f"Copying cover into .covers: {cover_in_subfolder}")
                    shutil.copy2(page1_file, cover_in_subfolder)
                return link_creator_cover(creator_folder, cover_in_subfolder)

    return None


def ensure_creator_cover(creator_folder: str):
    """Restore a missing cover for a creator folder, using local sources."""
    try:
        if not os.path.isdir(creator_folder):
            return
        latest_id, entry_name, is_dir = find_latest_gallery_entry(creator_folder)
        if not entry_name or latest_id is None:
            return
        logger.debug(f"Cover missing for {creator_folder}; checking local sources for gallery {latest_id} (is_dir={is_dir}).")
        find_local_cover_and_link(creator_folder, entry_name, is_dir)
    except Exception as e:
        logger.debug(f"Failed to restore cover file in {creator_folder}: {e}")


def repair_creator_cover(creator_folder: str):
    """Repair a missing cover for a creator folder, downloading from API if needed."""
    try:
        logger.debug(f"[Cover Repair] Checking folder: {creator_folder}")
        if not os.path.isdir(creator_folder):
            return
        if any(
            f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
            for f in os.listdir(creator_folder)
        ):
            logger.debug(f"[Cover Repair] Cover file already exists in: {creator_folder}")
            return

        latest_id, entry_name, is_dir = find_latest_gallery_entry(creator_folder)
        logger.debug(f"[Cover Repair] Latest gallery entry: id={latest_id}, name={entry_name}, is_dir={is_dir}")
        if not entry_name or latest_id is None:
            return

        if find_local_cover_and_link(creator_folder, entry_name, is_dir):
            logger.debug(f"[Cover Repair] Cover restored from local sources for: {creator_folder}")
            return

        covers_folder = os.path.join(creator_folder, ".covers")
        os.makedirs(covers_folder, exist_ok=True)

        logger.debug(f"[Cover Repair] Cover not found locally; attempting download for Gallery {latest_id}")
        try:
            meta = scraperapi.Fetch.gallery_metadata(latest_id)
            if not meta:
                logger.warning(f"[Cover Repair] No metadata found for Gallery {latest_id}")
                return
            urls = scraperapi.Fetch.image_urls(meta, 1)
            if not urls:
                logger.warning(f"[Cover Repair] No image URLs found for Gallery {latest_id}")
                return
            url = urls[0]
            ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
            target = os.path.join(covers_folder, f"{entry_name}{ext}")
            session = scraperapi.Get.session(referrer="Cover Repair", status="return")
            resp = session.get(url, timeout=(60, 60))
            resp.raise_for_status()
            with open(target, "wb") as f:
                f.write(resp.content)
            logger.info(f"[Cover Repair] Cover downloaded for Gallery {latest_id}: {target}")
            link_creator_cover(creator_folder, target)
        except Exception as e:
            logger.warning(f"[Cover Repair] Failed to download missing cover for Gallery {latest_id}: {e}")
    except Exception as e:
        logger.debug(f"[Cover Repair] Failed to restore cover file in {creator_folder}: {e}")


def repair_covers_hook(download_path: str, referrer: str = "Extension Manager"):
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


def maintain_gallery_covers():
    """
    Maintain cover images for all galleries under orchestrator.download_path.
    Structure: CreatorFolder/cover.ext → .covers/(GalleryID) GalleryTitle.ext
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

            covers_folder = os.path.join(creator_path, ".covers")
            os.makedirs(covers_folder, exist_ok=True)

            latest_gallery_id = None
            latest_gallery_name = None

            for entry_name in os.listdir(creator_path):
                if entry_name.startswith("."):
                    continue
                entry_path = os.path.join(creator_path, entry_name)
                match = re.search(r"\((\d+)\)", entry_name)
                if not match:
                    continue
                try:
                    gallery_id = int(match.group(1))
                except ValueError:
                    continue

                if latest_gallery_id is None or gallery_id > latest_gallery_id:
                    latest_gallery_id = gallery_id
                    latest_gallery_name = entry_name

                if os.path.isdir(entry_path):
                    gallery_base = entry_name
                    pages = sorted(
                        f for f in os.listdir(entry_path)
                        if os.path.splitext(f)[1].lower() in IMAGE_EXTS
                    )
                    if pages:
                        first_page = os.path.join(entry_path, pages[0])
                        _, ext = os.path.splitext(first_page)
                        cover_dest = os.path.join(covers_folder, f"{gallery_base}{ext}")
                        if not os.path.exists(cover_dest):
                            try:
                                shutil.copy2(first_page, cover_dest)
                                logger.debug(f"[COVERS] Created cover for {creator_name}/{gallery_base}")
                            except Exception as e:
                                logger.debug(f"[COVERS] Failed to copy cover for {gallery_base}: {e}")

                elif entry_path.endswith((".cbz", ".zip")) and os.path.isfile(entry_path):
                    gallery_base = os.path.splitext(entry_name)[0]
                    try:
                        with zipfile.ZipFile(entry_path, "r") as zf:
                            pages = sorted(
                                f for f in zf.namelist()
                                if not f.endswith("/") and os.path.splitext(f)[1].lower() in IMAGE_EXTS
                            )
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

            if latest_gallery_name:
                gallery_base = (
                    os.path.splitext(latest_gallery_name)[0]
                    if latest_gallery_name.endswith((".cbz", ".zip"))
                    else latest_gallery_name
                )
                for ext in ("jpg", "jpeg", "png", "gif", "webp"):
                    cover_source = os.path.join(covers_folder, f"{gallery_base}.{ext}")
                    if os.path.exists(cover_source):
                        for old_ext in ("jpg", "jpeg", "png", "gif", "webp"):
                            old_cover = os.path.join(creator_path, f"cover.{old_ext}")
                            if os.path.islink(old_cover) or os.path.isfile(old_cover):
                                try:
                                    os.unlink(old_cover)
                                except Exception:
                                    pass
                        cover_link = os.path.join(creator_path, f"cover.{ext}")
                        create_cover_link_or_copy(cover_source, cover_link, creator_name)
                        break

    except Exception as e:
        logger.debug(f"[COVERS] Error in maintain_gallery_covers: {e}")


####################################################################################################################
# CLEANUP HELPERS
####################################################################################################################

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

    for dirpath, dirnames, filenames in os.walk(download_path, topdown=False):
        if dirpath == download_path:
            continue

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

        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            if os.path.islink(full_path) and not os.path.exists(os.readlink(full_path)):
                try:
                    os.unlink(full_path)
                    logger.info(f"Removed broken symlink: {full_path}")
                    broken_symlinks_removed += 1
                except Exception as e:
                    logger.warning(f"Failed to remove broken symlink {full_path}: {e}")

        if os.path.dirname(dirpath) == download_path:
            ensure_creator_cover(dirpath)

    if log_scan_summary:
        logger.info("Removed empty directories.")
        log_clarification()

    if broken_symlinks_removed > 0:
        logger.info(f"Fixed {broken_symlinks_removed} broken symlink(s).")

    if log_scan_summary:
        logger.info("Scan complete.")


def cleanup_hook():
    """Run all housekeeping tasks: covers, cleanup."""
    maintain_gallery_covers()
    repair_covers_hook(orchestrator.download_path, referrer=MODULE_REFERRER)
    cleanup_download_tree(orchestrator.download_path, remove_empty_artist_folder=True, log_scan_summary=True)


####################################################################################################################
# CORE HOOKS
####################################################################################################################

def pre_run_hook():
    """Called before the download run starts."""
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
    Download a single gallery page from one of the provided URLs.
    Tries mirrors in order with retries. Falls back to a fresh Tor session if configured.
    """
    orchestrator.refresh_globals()

    try:
        from mangascraper.core.downloader.download_manager import _shutdown_event
    except ImportError:
        _shutdown_event = None
    if _shutdown_event and _shutdown_event.is_set():
        logger.warning("Shutdown event detected, aborting image download.")
        return False

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

    success = try_download(downloader_session, urls, orchestrator.max_retries)

    if not success and orchestrator.use_tor:
        logger.warning(
            f"Gallery {gallery}: Page {page}: All retries failed, rotating Tor node and retrying once more..."
        )
        downloader_session = scraperapi.Get.session(referrer=f"{MODULE_REFERRER}", status="rebuild")
        success = try_download(downloader_session, urls, 1, tor_rotate=True)

    if success and page_update_hook:
        try:
            page_update_hook()
        except Exception as e:
            logger.warning(f"page_update_hook failed: {e}")

    if not success:
        log_clarification()
        logger.error(f"Gallery {gallery}: Page {page}: All mirrors failed after Tor rotate too: {urls}")
        if pbar and creator:
            pbar.set_postfix_str(f"Failed Creator: {creator}")

    return success


def pre_batch_hook(gallery_list):
    """Called before each batch starts."""
    orchestrator.refresh_globals()

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: Pre-batch Hook Inactive.")
        return

    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Pre-batch Hook Called.", "debug")
    return gallery_list


def pre_gallery_download_hook(gallery_id):
    """Called before each gallery download."""
    orchestrator.refresh_globals()

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: Pre-download Hook Inactive.")

    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Pre-download Hook Called: Gallery: {gallery_id}", "debug")


def during_gallery_download_hook(gallery_id):
    """Called during each gallery download (after metadata fetch)."""
    orchestrator.refresh_globals()

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: During-download Hook Inactive.")
        return

    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] During-download Hook Called: Gallery: {gallery_id}", "debug")


def post_batch_hook(current_batch_number: int, total_batch_numbers: int):
    """Called after each batch completes."""
    orchestrator.refresh_globals()

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {MODULE_REFERRER}: Post-batch Hook Inactive.")
        return

    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Post-batch Hook Called.", "debug")

    def _should_run_post_batch():
        if total_batch_numbers > MAX_X_BATCHES:
            return False
        interval = max(1, round(RUNS_PER_X_BATCHES * total_batch_numbers / EVERY_X_BATCHES))
        is_last_batch = current_batch_number == total_batch_numbers
        return (
            not orchestrator.skip_post_batch
            and not orchestrator.archiving
            and not is_last_batch
            and (current_batch_number % interval == 0)
        )

    if _should_run_post_batch():
        cleanup_hook()


def post_run_hook():
    """Called after the full download run completes."""
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
        cleanup_hook()


def test_hook():
    """Hook for testing functionality."""
    orchestrator.refresh_globals()
    log_clarification("debug")
    log(f"[{MODULE_REFERRER}] Test Hook Called.", "debug")