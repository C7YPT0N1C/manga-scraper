#!/usr/bin/env python3
# mangascraper/core/downloader/download_manager.py

import os, sys, time, random, math, zipfile, shutil, signal, tempfile, threading
from typing import Callable
from tqdm import tqdm
from tqdm.contrib.concurrent import thread_map

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.core.api import api as scraperapi
from mangascraper.core.api.api import *
from mangascraper.core.downloader import _downloader_ext
from dovetail import Dovetail

####################################################################################################
# Global Variables
####################################################################################################

download_location = ""
ARCHIVE_TEMP_ROOT = os.path.join(orchestrator.TEMP_DIR, "archive_temp")

skipped_galleries = {}
skipped_galleries_lock = threading.Lock()
failed_galleries = {}
failed_galleries_lock = threading.Lock()


def _ensure_managed_download_root(suppess_pre_run_hook: bool = False):
    """Ensure marker file and DownloadLocations entry for a managed download root."""
    
    global download_location
    
    orchestrator.refresh_globals()
    
    safe_root = str(orchestrator.download_path or "").strip()
    if not safe_root:
        return

    marker_path = os.path.join(safe_root, scraperapi.DOWNLOAD_ROOT_MARKER_FILE)
    marker_text = scraperapi.DOWNLOAD_ROOT_MARKER_WARNING

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would ensure managed download marker at: {marker_path}")
        return

    os.makedirs(safe_root, exist_ok=True)
    try:
        write_marker = True
        if os.path.isfile(marker_path):
            try:
                with open(marker_path, "r", encoding="utf-8", errors="replace") as f:
                    existing = f.read()
                write_marker = (existing != marker_text)
            except Exception:
                write_marker = True

        if write_marker:
            with open(marker_path, "w", encoding="utf-8") as f:
                f.write(marker_text)
    except Exception as e:
        logger.warning(f"[Downloader] Could not write marker file at '{marker_path}': {e}")

    try:
        scraperapi.DB.upsert_download_location(safe_root)
    except Exception as e:
        logger.warning(f"[Downloader] Could not upsert download location '{safe_root}': {e}")
    
    download_location = safe_root
    
    if suppess_pre_run_hook==False:
        log(f"Downloading Galleries To: {download_location}")

    if not orchestrator.dry_run:
        os.makedirs(download_location, exist_ok=True)
    else:
        if suppess_pre_run_hook==False:
            logger.info(f"[DRY RUN] Would Download Galleries To: {download_location}")

####################################################################################################
# UTILITIES
####################################################################################################

def time_estimate(context: str, id_list: list, average_gallery_download_time: int = 5):
    """
    Estimate total runtime (best, median, worst) for current gallery set.
    Takes into account number of pages (images) per gallery.
    """
    num_galleries = len(id_list)
    if num_galleries == 0:
        return

    # --- Use global page count if available ---
    total_pages = orchestrator.total_gallery_images or (num_galleries * 20)  # fallback estimate

    # --- API hits (pages + galleries) ---
    #                          FETCHING IDS          GET METADATA    IMAGE DOWNLOADING
    total_api_hits = math.ceil(num_galleries / 25) + num_galleries + total_pages
    
    # Average parallel work happening per gallery thread
    effective_parallelism = max(1, orchestrator.threads_images / orchestrator.threads_galleries)

    # --- Batch timing ---
    full_batches = num_galleries // BATCH_SIZE
    remaining_batches = num_galleries % BATCH_SIZE
    total_batch_sleep_time = (full_batches + 1 if remaining_batches else full_batches) * batch_sleep_time

    # --- Galleries ---
    downloads_per_batch = math.ceil(BATCH_SIZE / orchestrator.threads_galleries)
    remaining_downloads_per_batch = math.ceil(remaining_batches / orchestrator.threads_galleries) if remaining_batches else 0
    total_gallery_download_time = (downloads_per_batch + remaining_downloads_per_batch) * average_gallery_download_time

    # --- Helper for formatting ---
    def fmt_time(seconds: float) -> str:
        seconds = int(seconds)
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)

        parts = []
        if days > 0:
            parts.append(f"{days} Day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} Hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} Min{'s' if minutes != 1 else ''}")
        if seconds > 0 or not parts:  # always show seconds
            parts.append(f"{seconds} Sec{'s' if seconds != 1 else ''}")

        return ", ".join(parts)

    # --- Time computation ---
    def compute_case(api_sleep, retry_sleep):
        return (
            total_batch_sleep_time +
            (total_api_hits * api_sleep) +
            (total_gallery_download_time * retry_sleep)
        ) / effective_parallelism
    
    best_case = compute_case(orchestrator.min_api_sleep, orchestrator.min_retry_sleep)
    worst_case = compute_case(orchestrator.max_api_sleep, orchestrator.max_retry_sleep)
    total_pages_suffix = f" (Total {total_pages} Pages)" if context == "Run" else ""

    # --- Output ---
    log_clarification("warning")
    log(f"Starting {context} with {num_galleries} Galleries{total_pages_suffix}:")
    log(f"Estimated Time: {fmt_time(best_case)} - {fmt_time(worst_case)}\n")
    log(f"[API] Estimated Total API Hits: {total_api_hits}\n", "debug")
    
# Space monitoring for progress display
space_monitor = {
    "total_estimated_bytes": 0,
    "total_actual_bytes": 0,
    "galleries_processed": 0,
}

# Dovetail worker-pool management for graceful shutdown
_active_dovetails = []
_shutdown_event = None

def _register_dovetail(dovetail: Dovetail):
    """Register a Dovetail instance for graceful shutdown."""
    _active_dovetails.append(dovetail)

def _shutdown_all_executors(wait=True):
    """Shutdown all registered Dovetail pools gracefully."""
    for dovetail in _active_dovetails:
        try:
            dovetail.shutdown(wait=wait)
        except Exception as e:
            logger.warning(f"Error shutting down executor: {e}")
    _active_dovetails.clear()


def _is_db_locked_error(error: Exception) -> bool:
    text = str(error or "").strip().lower()
    return "database is locked" in text or "database table is locked" in text


def _wait_for_db_unlock(context: str = "database") -> None:
    orchestrator.refresh_globals()
    wait_seconds = max(1.0, float(getattr(orchestrator, "min_retry_sleep", 1) or 1))
    logger.warning(f"[Downloader] {context} is locked. Waiting {wait_seconds:.1f}s before retrying...")
    time.sleep(wait_seconds)


def _call_db_with_lock_wait(func, *args, context: str = "database", **kwargs):
    while True:
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if _is_db_locked_error(exc):
                _wait_for_db_unlock(context=context)
                continue
            raise


def _safe_symlink_or_copy(src: str, dest: str) -> bool:
    """Prefer symlink; fall back to copying (file/dir) when symlink is unavailable."""
    try:
        os.symlink(src, dest)
        return True
    except Exception as symlink_error:
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            logger.debug(f"[Downloader] Symlink unavailable; copied instead: {src} -> {dest} ({symlink_error})")
            return True
        except Exception as copy_error:
            logger.warning(
                f"[Downloader] Failed to create link/copy for '{dest}': symlink={symlink_error}; copy={copy_error}"
            )
            return False


def run_gallery_batch(
    gallery_ids: list[int],
    process_gallery_sync: Callable[[int], None],
) -> list[Exception]:
    """Run sync gallery processors with async orchestration via Dovetail."""

    orchestrator.refresh_globals()
    gallery_workers = max(1, int(orchestrator.threads_galleries or 1))

    dvt = None
    results: list = []
    def download_gallery_task(gallery_id: int):
        return process_gallery_sync(int(gallery_id))

    try:
        with Dovetail(
            max_workers=gallery_workers,
            trace=bool(orchestrator.debug),
            trace_logger=logger,
            trace_prefix="DVT-GalleryPool",
        ) as dvt:
            log("[DOVETAIL] Gallery worker pool initialised.", "debug")
            _register_dovetail(dvt)
            outcomes = dvt.task.map_blocking(
                download_gallery_task,
                gallery_ids,
                max_concurrency=gallery_workers,
                return_exceptions=True,
            )
            results = [out for out in outcomes if isinstance(out, Exception)]
    finally:
        if dvt is not None:
            try:
                _active_dovetails.remove(dvt)
            except ValueError:
                pass
    return results

def _signal_handler(signum, frame):
    """Handle Ctrl+C (SIGINT) and SIGTERM for graceful shutdown."""
    logger.warning(f"\nReceived signal {signum}, shutting down gracefully...")
    _shutdown_all_executors(wait=True)
    raise KeyboardInterrupt("Graceful shutdown initiated")

def _format_bytes(bytes_val: int) -> str:
    """Format bytes to human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.2f} PB"

def get_available_disk_space(path: str) -> int:
    """Get available disk space in bytes at the given path."""
    try:
        # Cross-platform disk usage API (works on Windows and POSIX)
        usage = shutil.disk_usage(path)
        return int(usage.free)
    except Exception as e:
        logger.warning(f"Failed to check disk space: {e}")
        return -1  # Return -1 if we can't check

def _is_network_share(path: str) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        mount_path = os.path.realpath(path)
        best_match = ("", "")
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_point = parts[1]
                fs_type = parts[2]
                if mount_path.startswith(mount_point.rstrip("/") + "/") or mount_path == mount_point:
                    if len(mount_point) > len(best_match[0]):
                        best_match = (mount_point, fs_type)
        network_fs_types = {
            "nfs",
            "nfs4",
            "cifs",
            "smbfs",
            "sshfs",
            "fuse.sshfs",
            "davfs",
            "fuse.glusterfs",
            "fuse.ceph",
        }
        is_network = best_match[1] in network_fs_types
        if is_network:
            logger.debug(f"Download directory '{path}' is on a network share (filesystem: {best_match[1]} at mount point: {best_match[0]}). Optimising download.")
        else:
            logger.debug(f"Download directory '{path}' is on a local filesystem (filesystem: {best_match[1]} at mount point: {best_match[0]}).")
        return is_network
    except Exception as e:
        logger.warning(f"Failed to determine if '{path}' is a network share: {e}")
        return False

def pre_download_checks(gallery_ids: list) -> tuple:
    """
    Triggers metadata fetching for all galleries.
    Estimates total download size for all galleries.
    Returns (total_estimated_bytes, galleries_list_to_download).
    
    If insufficient space, prompts user to download as many as fit.
    """
    
    log(f"Fetching metadata and estimating download size for {len(gallery_ids)} galleries.")
    log("This may take a while, patience...")
    
    download_estimated = 0
    gallery_sizes = []  # List of (gallery_id, estimated_bytes)
    
    # Estimate size for each gallery
    for gallery_id in gallery_ids:
        try:
            meta = scraperapi.Fetch.gallery_metadata(gallery_id)
            if meta and isinstance(meta, dict):
                estimated_size, _, _ = scraperapi.Build.gallery_size_estimate(meta)
                gallery_sizes.append((gallery_id, estimated_size))
                download_estimated += estimated_size
        except Exception as e:
            logger.debug(f"Failed to estimate size for Gallery {gallery_id}: {e}")
            # Use a default estimate if we can't fetch metadata
            default_size = 1024 * 1024 * 16  # ~16 MB default
            gallery_sizes.append((gallery_id, default_size))
            download_estimated += default_size
    
    download_estimated = download_estimated * 2 # idk just keep this here lmfao

    # Always check available space on the actual download location (network share or not)
    available_on_target = get_available_disk_space(download_location)
    available_on_staging = None
    use_staging = False
    if orchestrator.gallery_format != "directory" and _is_network_share(download_location):
        os.makedirs(ARCHIVE_TEMP_ROOT, exist_ok=True)
        available_on_staging = get_available_disk_space(ARCHIVE_TEMP_ROOT)
        use_staging = True
    else:
        available_on_staging = available_on_target  # fallback, not used

    available = available_on_target

    # Add buffer for parallel downloads and temporary overhead
    avg_gallery_size = download_estimated / max(1, len(gallery_ids))
    parallel_galleries = min(len(gallery_ids), max(1, orchestrator.threads_galleries))
    parallel_buffer = avg_gallery_size * parallel_galleries
    safety_buffer = download_estimated * 0.2
    required_with_buffer = download_estimated + parallel_buffer + safety_buffer
    
    total_estimated = download_estimated + required_with_buffer
    
    log(
        f"Space Usage Estimate ({len(gallery_ids)} Galleries):\n"
        f"-     Total download size: {_format_bytes(total_estimated)}\n"
        f"-     Available disk space (target): {_format_bytes(available_on_target)}\n"
        + (f"-     Available disk space (staging): {_format_bytes(available_on_staging)}\n" if use_staging else "")
        + f"-     Estimated download size: {_format_bytes(download_estimated)}\n"
        + f"-     Required with buffer: {_format_bytes(required_with_buffer)}\n"
    )
    
    # If sufficient space on the target, return all galleries
    if available_on_target < 0 or available_on_target >= required_with_buffer:
        log("Sufficient space available. Proceeding with download.\n")
        return download_estimated, gallery_ids
    
    # Insufficient space - ask user if they want to download as many as fit
    logger.warning(
        f"Insufficient space for all galleries!\n"
        f"  Total estimated download size: {_format_bytes(total_estimated)}\n"
        f"  Available (target): {_format_bytes(available_on_target)}\n"
        + (f"  Available (staging): {_format_bytes(available_on_staging)}\n" if use_staging else "")
    )

    # Calculate how many galleries can fit on the target
    running_total = 0
    available_for_galleries = max(0, available_on_target - parallel_buffer - safety_buffer)
    galleries_that_fit = []
    for gallery_id, size in gallery_sizes:
        if running_total + size <= available_for_galleries:
            galleries_that_fit.append(gallery_id)
            running_total += size
        else:
            break

    # If using a staging folder, warn if it may not have enough space for the largest gallery
    if use_staging and galleries_that_fit:
        largest_gallery = max([size for _, size in gallery_sizes], default=0)
        if available_on_staging is not None and available_on_staging < largest_gallery:
            logger.warning(
                f"Staging folder ({ARCHIVE_TEMP_ROOT}) may not have enough space for the largest gallery (needs {_format_bytes(largest_gallery)}, has {_format_bytes(available_on_staging)}). "
                "Will fall back to downloading and zipping directly on the network share for those galleries."
            )
    
    log_clarification()
    logger.info(
        f"You can download {len(galleries_that_fit)} out of {len(gallery_ids)} galleries "
        f"({_format_bytes(running_total)} total, buffered)"
    )
    
    # Prompt user
    user_input = input(f"\nDownload {len(galleries_that_fit)} galleries that fit? (y/n): ").strip().lower()
    
    if user_input == 'y':
        logger.info(f"Proceeding with {len(galleries_that_fit)} galleries.")
        return running_total, galleries_that_fit
    else:
        logger.info("Download cancelled by user.")
        return 0, []

def build_gallery_path(meta, iteration: dict = None, base_path: str | None = None):
    """
    Build the folder path for a gallery based on SUBFOLDER_STRUCTURE.
    """
    
    gallery_metas = scraperapi.Helpers.summary(
        meta,
        _downloader_ext.MODULE_REFERRER,
    )

    if iteration:
        for k, v in iteration.items():
            gallery_metas[k] = v

    path_parts = [base_path or download_location]
    raw_creators = scraperapi.Helpers.creator_candidates(meta)
    primary_raw_creator = raw_creators[0] if raw_creators else "Unknown Creator"
    primary_display_creator = scraperapi.Helpers.sanitise(primary_raw_creator)

    for key in _downloader_ext.SUBFOLDER_STRUCTURE:
        value = gallery_metas.get(key, "Unknown")
        if key == "creator":
            current_base = os.path.join(*path_parts)
            creator_folder = scraperapi.Helpers.choose_creator_folder_name(
                raw_name=primary_raw_creator,
                base_path=current_base,
                fallback_name=primary_display_creator,
            )
            path_parts.append(creator_folder)
            continue

        if isinstance(value, list):
            value = value[0] if value else "Unknown"
        if not isinstance(value, str):
            value = str(value)
        path_parts.append(scraperapi.Helpers.sanitise(value))

    return os.path.join(*path_parts)

def update_skipped_galleries(ReturnReport: bool, meta=None, Reason: str = "No Reason Given.", reportable: bool = True):
    global skipped_galleries

    orchestrator.refresh_globals()

    if ReturnReport:
        with skipped_galleries_lock:
            reportable_entries = {gid: e for gid, e in skipped_galleries.items() if e.get("reportable")}
        if not reportable_entries:
            return

        lines = []
        for gid in sorted(reportable_entries.keys()):
            entry = reportable_entries[gid]
            creators = entry.get("creators") or ["Unknown Creator"]
            lines.append(
                f"Gallery: {gid} | Title: {entry.get('title', 'Unknown Title')} | "
                f"Creators: {', '.join(creators)} | Pages: {entry.get('pages', 'Unknown')} | "
                f"Reason: {entry.get('reason', 'No Reason Given.')}"
            )
        log_clarification()
        log(f"Skipped Galleries (matched filter):\n" + "\n".join(lines), "warning")
        return

    if not meta:
        return

    gid = scraperapi.Helpers.normalise_integer(meta.get("id"))
    if gid is None:
        return

    gallery_title = scraperapi.Helpers.sanitise(meta)
    creators = scraperapi.Get.artists(meta) or scraperapi.Get.groups(meta) or ["Unknown Creator"]
    pages = len(meta.get("images", {}).get("pages", [])) if isinstance(meta.get("images"), dict) else "Unknown"

    entry = {
        "title": gallery_title,
        "creators": [scraperapi.Helpers.safe_text(c).strip() for c in creators if scraperapi.Helpers.safe_text(c).strip()] or ["Unknown Creator"],
        "pages": pages,
        "reason": scraperapi.Helpers.safe_text(Reason, "No Reason Given."),
        "reportable": reportable,
    }

    with skipped_galleries_lock:
        # Don't overwrite a reportable entry with a non-reportable one
        existing = skipped_galleries.get(gid)
        if existing is None or (reportable and not existing.get("reportable")):
            skipped_galleries[gid] = entry

    log_clarification("debug")
    log(f"[Downloader] Skipped Gallery {gid} ({gallery_title}): {Reason}", "debug")

def update_failed_galleries(ReturnReport: bool, gallery_id=None, meta=None, Reason: str = "No Reason Given."):
    global failed_galleries

    orchestrator.refresh_globals()

    if ReturnReport:
        if not failed_galleries:
            return

        lines = []
        with failed_galleries_lock:
            for gid in sorted(failed_galleries.keys()):
                entry = failed_galleries[gid]
                creators = entry.get("creators") or ["Unknown Creator"]
                creator_text = ", ".join(creators)
                lines.append(
                    f"Gallery: {gid} | Title: {entry.get('title', 'Unknown Title')} | "
                    f"Creators: {creator_text} | Pages: {entry.get('pages', 'Unknown')} | "
                    f"Reason: {entry.get('reason', 'No Reason Given.')}"
                )

        log_clarification()
        log(f"Failed Galleries:\n" + "\n".join(lines), "warning")
        return

    gid = scraperapi.Helpers.normalise_integer(gallery_id)
    if gid is None:
        logger.warning("[Downloader] update_failed_galleries called without a valid gallery_id.")
        return

    cached_meta = scraperapi.Cache.Load.cache(gallery_id=gid) or {}
    title = "Unknown Title"
    creators = ["Unknown Creator"]
    pages = "Unknown"

    if isinstance(meta, dict) and meta:
        title = scraperapi.Helpers.sanitise(meta)
        creators = scraperapi.Get.artists(meta) or scraperapi.Get.groups(meta) or ["Unknown Creator"]
        pages = len(meta.get("images", {}).get("pages", [])) if isinstance(meta.get("images"), dict) else "Unknown"
    elif isinstance(cached_meta, dict) and cached_meta:
        title = scraperapi.Helpers.safe_text(cached_meta.get("title") or cached_meta.get("clean_title") or f"Gallery {gid}")
        creators = cached_meta.get("artists") or cached_meta.get("groups") or ["Unknown Creator"]
        pages = cached_meta.get("pages", "Unknown")

    entry = {
        "title": title,
        "creators": [scraperapi.Helpers.safe_text(c).strip() for c in creators if scraperapi.Helpers.safe_text(c).strip()] or ["Unknown Creator"],
        "pages": pages,
        "reason": scraperapi.Helpers.safe_text(Reason, "No Reason Given."),
    }

    with failed_galleries_lock:
        failed_galleries[gid] = entry

    log_clarification("debug")
    log(
        f"[Downloader] Recorded failed gallery: {gid} ({entry['title']}) | Creators: {', '.join(entry['creators'])} | Reason: {entry['reason']}",
        "debug",
    )

def should_download_gallery(meta, gallery_title, num_pages, iteration: dict = None):
    """
    Decide whether to download a gallery or skip it.
    """
    
    orchestrator.refresh_globals()
    
    if not meta:
        update_skipped_galleries(False, meta, "Not Meta.", reportable=False)
        return False

    gallery_id = meta.get("id")
    doujin_folder = build_gallery_path(meta, iteration)

    if num_pages == 0:
        logger.warning(
            f"[Downloader] Skipping Gallery: {gallery_id}\n"
            "Reason: No Pages.\n"
            f"Title: {gallery_title}\n"
        )
        log_clarification()
        update_skipped_galleries(False, meta, "No Pages.", reportable=False)
        return False

    # Skip only if NOT in dry-run
    if not orchestrator.dry_run and os.path.exists(doujin_folder):
        all_exist = all(
            any(os.path.exists(os.path.join(doujin_folder, f"{i+1}.{ext}"))
                for ext in ("jpg", "png", "gif", "webp"))
            for i in range(num_pages)
        )
        if all_exist:
            logger.info(
                f"[Downloader] Skipping Gallery: {gallery_id}\n"
                "Reason: Already Downloaded.\n"
                f"Title: {gallery_title}\n"
                f"In Folder: {doujin_folder.removesuffix(gallery_title)}"
            )
            log_clarification()
            update_skipped_galleries(False, meta, "Already downloaded.")
            return False
    
    # ------------------------------------------------------------------------
    # Filtering (FALLBACK, FILTERING NOW OCCURS DURING GALLERY ID FETCH)
    # ------------------------------------------------------------------------

    # --- Excluded Tags ---
    excluded_gallery_tags = [tag.lower() for tag in excluded_tags]
    gallery_tags = [t.lower() for t in scraperapi.Get.meta_tags("[Downloader] Should_Download_Gallery", meta, "tag")]
    blocked_tags = []
    
    for tag in gallery_tags:
        if tag in excluded_gallery_tags:
            blocked_tags.append(tag)

    # --- Allowed Languages ---
    allowed_gallery_language = [lang.lower() for lang in orchestrator.language]
    gallery_langs = [l.lower() for l in scraperapi.Get.meta_tags("[Downloader] Should_Download_Gallery", meta, "language")]
    blocked_langs = []

    if allowed_gallery_language:
        has_allowed = any(lang in allowed_gallery_language for lang in gallery_langs)
        has_translated = "translated" in gallery_langs
        allow_translated = "translated" in allowed_gallery_language
        if not (has_allowed or (has_translated and allow_translated)):
            blocked_langs = gallery_langs[:]

    #log_clarification("debug") # NOTE: DEBUGGING
    #log(f"Excluded Genres: {excluded_gallery_tags}", "debug")
    #log(f"Allowed Languages: {allowed_gallery_language}", "debug")
    
    if blocked_tags or blocked_langs:
        logger.info(
            f"[Downloader] Skipping Gallery: {gallery_id}\n"
            "Reason: Blocked Tags In Metadata.\n"
            f"Title: {gallery_title}\n"
            f"Filtered tags: {blocked_tags}\n"
            f"Filtered languages: {blocked_langs}"
        )
        log_clarification()
        update_skipped_galleries(False, meta, f"Filtered tags: {blocked_tags}, languages: {blocked_langs}", reportable=False)
        return False

    return True

def submit_creator_tasks(creator_tasks, gallery_id, local_session, safe_creator_name):
    """
    Submit download tasks for a single creator's pages.
    """

    orchestrator.refresh_globals()
    image_workers = max(1, int(orchestrator.threads_images or 1))

    dvt = None
    def download_image_task(task_tuple):
        page, urls, path, _ = task_tuple
        return bool(
            _downloader_ext.download_images_hook(
                gallery_id,
                page,
                urls,
                path,
                local_session,
                None,
                safe_creator_name,
            )
        )

    try:
        with Dovetail(
            max_workers=image_workers,
            trace=bool(orchestrator.debug),
            trace_logger=logger,
            trace_prefix="DVT-ImagePool",
        ) as dvt:
            log("[DOVETAIL] Image worker pool initialised.", "debug")
            _register_dovetail(dvt)

            results = dvt.task.map_blocking(
                download_image_task,
                creator_tasks,
                max_concurrency=image_workers,
                return_exceptions=True,
            )
            all_succeeded = True
            for result in results:
                if isinstance(result, Exception):
                    all_succeeded = False
                    continue
                if not result:
                    all_succeeded = False
            return all_succeeded
    finally:
        if dvt is not None:
            try:
                _active_dovetails.remove(dvt)
            except ValueError:
                pass

#----------------------
# ARCHIVE CONVERSION
#----------------------
def finalise_gallery_format(
    gallery_id: int,
    gallery_folder: str,
    format_type: str,
    final_parent_dir: str | None = None,
):
    """
    Convert downloaded gallery folder to specified format (zip or cbz).
    Cover extraction is handled by extension hooks.
    
    Args:
        gallery_id: ID of the gallery (for logging)
        gallery_folder: Path to the folder containing downloaded images
        format_type: One of "directory" (no-op), "zip", or "cbz"
    
    Returns:
        str: Path to the final archive or folder
    """
    
    if format_type == "directory":
        return gallery_folder
    
    if not os.path.exists(gallery_folder):
        logger.warning(f"[Downloader] Gallery folder not found: {gallery_folder}")
        return gallery_folder
    
    # Get base path and create archive path
    parent_dir = os.path.dirname(gallery_folder)
    folder_name = os.path.basename(gallery_folder)
    archive_ext = ".cbz" if format_type == "cbz" else ".zip"
    archive_filename = scraperapi.Helpers.archive_filename(folder_name, archive_ext)
    archive_path = os.path.join(final_parent_dir or parent_dir, archive_filename)
    temp_archive_path = None
    
    try:
        # Get list of image files sorted for proper reading order
        image_files = sorted([
            f for f in os.listdir(gallery_folder)
            if os.path.isfile(os.path.join(gallery_folder, f))
        ])
        
        if not image_files:
            logger.warning(f"[Downloader] No images found in {gallery_folder}")
            return gallery_folder
        
        use_temp_archive = _is_network_share(final_parent_dir or parent_dir)
        if use_temp_archive:
            os.makedirs(ARCHIVE_TEMP_ROOT, exist_ok=True)
            fd, temp_archive_path = tempfile.mkstemp(
                prefix=f"{folder_name}-",
                suffix=archive_ext,
                dir=ARCHIVE_TEMP_ROOT,
            )
            os.close(fd)
            archive_target = temp_archive_path
        else:
            archive_target = archive_path

        logger.debug(f"[Downloader] Creating {format_type} archive for Gallery {gallery_id}: {archive_target}")
        
        # Create zip/cbz archive with images in sorted order
        with zipfile.ZipFile(archive_target, 'w', zipfile.ZIP_DEFLATED) as zf:
            for img_file in image_files:
                img_path = os.path.join(gallery_folder, img_file)
                arcname = os.path.join(folder_name, img_file)  # Keep folder structure in archive
                zf.write(img_path, arcname=arcname)

        if use_temp_archive:
            os.makedirs(os.path.dirname(archive_path), exist_ok=True)
            try:
                os.replace(archive_target, archive_path)
                logger.debug(f"[Downloader] Moved archive for Gallery {gallery_id} from temp folder to download folder: {archive_target} -> {archive_path}")
            except OSError:
                shutil.move(archive_target, archive_path)
                logger.debug(f"[Downloader] Moved archive for Gallery {gallery_id} from temp folder to download folder (shutil.move fallback): {archive_target} -> {archive_path}")
        
        logger.debug(f"[Downloader] Created {format_type} archive for Gallery {gallery_id}")
        
        return archive_path
        
    except Exception as e:
        logger.error(f"[Downloader] Failed to create {format_type} archive for Gallery {gallery_id}: {e}")
        if temp_archive_path and os.path.exists(temp_archive_path):
            try:
                os.unlink(temp_archive_path)
            except Exception:
                pass
        return gallery_folder

####################################################################################################
# MAIN
####################################################################################################

def process_galleries(batch_ids, on_gallery_status: Callable[[str], None] | None = None):
    orchestrator.refresh_globals()
    
    for gallery_id in batch_ids:
        if not orchestrator.dry_run:
            _call_db_with_lock_wait(
                scraperapi.DB.Gallery.start,
                gallery_id,
                download_location,
                context=f"Gallery {gallery_id} start",
            )
        else:
            log_clarification()
            logger.info(f"[DRY RUN] [Downloader] Would mark Gallery {gallery_id} as started.")

        gallery_attempts = 0

        while gallery_attempts < orchestrator.max_retries:
            meta = None
            gallery_attempts += 1
            try:
                _downloader_ext.pre_gallery_download_hook(gallery_id)
                log_clarification("debug")
                logger.debug("######################## GALLERY START ########################")
                log_clarification("debug")
                logger.debug(f"[Downloader] Starting Gallery: {gallery_id} (Attempt {gallery_attempts}/{orchestrator.max_retries})")

                meta = scraperapi.Fetch.gallery_metadata(gallery_id)
                if not meta or not isinstance(meta, dict):
                    logger.warning(f"[Downloader] Failed to fetch metadata for Gallery: {gallery_id}")
                    if not orchestrator.dry_run and gallery_attempts >= orchestrator.max_retries:
                        update_failed_galleries(False, gallery_id=gallery_id, Reason="Failed to fetch metadata.")
                        scraperapi.DB.Gallery.fail(gallery_id)
                        if on_gallery_status:
                            on_gallery_status("failed")
                    continue

                num_pages = len(meta.get("images", {}).get("pages", []))
                _downloader_ext.during_gallery_download_hook(gallery_id)
                gallery_metas = scraperapi.Helpers.summary(
                    meta,
                    _downloader_ext.MODULE_REFERRER,
                )

                creator_entries = scraperapi.Helpers.resolve_creator_entries(meta, download_location)
                creators = [entry["folder_name"] for entry in creator_entries] or ["Unknown Creator"]
                gallery_title = gallery_metas["title"]
                
                # Estimate size for progress tracking
                estimated_size, _, img_count = scraperapi.Build.gallery_size_estimate(meta)
                space_monitor["total_estimated_bytes"] += estimated_size * 2 # keep this here i think
                
                time.sleep(scraperapi.Sleep.dynamic("gallery", attempt=gallery_attempts)) # Sleep before starting gallery.

                # --- Decide if gallery should be skipped ---
                skip_gallery = False
                for creator in creators:
                    iteration = {"creator": [creator]}
                    if not should_download_gallery(meta, gallery_title, num_pages, iteration):
                        skip_gallery = True
                        break

                if skip_gallery:
                    if not orchestrator.dry_run:
                        _call_db_with_lock_wait(
                            scraperapi.DB.Gallery.skip,
                            gallery_id,
                            context=f"Gallery {gallery_id} skip",
                        )
                    else:
                        log_clarification()
                        logger.info(f"[DRY RUN] [Downloader] Would mark Gallery {gallery_id} as skipped.")
                    if on_gallery_status:
                        on_gallery_status("skipped")
                    break  # exit retry loop, skip gallery

                use_local_archive = (
                    orchestrator.gallery_format != "directory"
                    and _is_network_share(download_location)
                )
                archive_parent_dir = None
                if use_local_archive:
                    os.makedirs(ARCHIVE_TEMP_ROOT, exist_ok=True)
                    archive_parent_dir = os.path.dirname(
                        build_gallery_path(meta, {"creator": [creators[0]]})
                    )

                # --- Prepare primary folder (first creator only) ---
                primary_creator = scraperapi.Helpers.sanitise(creators[0]) if creators else "Unknown"
                log(f"[Downloader] Primary Creator for Gallery: {gallery_id}: {primary_creator}", "debug")
                primary_folder = build_gallery_path(
                    meta,
                    {"creator": [creators[0]]},
                    base_path=ARCHIVE_TEMP_ROOT if use_local_archive else None,
                )

                if orchestrator.dry_run:
                    log(f"[DRY RUN] [Downloader] Would create primary folder for {creators[0]}: {primary_folder}", "debug")
                else:
                    os.makedirs(primary_folder, exist_ok=True)

                # --- Prepare download tasks (only once, for primary creator) ---
                # Use zero-padded filenames so lexicographic sorting preserves numeric page order.
                tasks = []
                pad_width = max(1, len(str(num_pages)))
                for i in range(num_pages):
                    page = i + 1
                    img_urls = scraperapi.Fetch.image_urls(meta, page)
                    if not img_urls:
                        logger.warning(f"[Downloader] Skipping Page {page} for {primary_creator}: Failed to get URLs")
                        update_skipped_galleries(False, meta, "Failed to get URLs.", reportable=False)
                        continue

                    ext = img_urls[0].split('.')[-1]
                    img_filename = f"{str(page).zfill(pad_width)}.{ext}"
                    img_path = os.path.join(primary_folder, img_filename)
                    tasks.append((page, img_urls, img_path, primary_creator))

                # --- Download images (once, in primary creator's folder) ---
                if tasks:
                    page_downloads_succeeded = True
                    if not orchestrator.dry_run:
                        local_session = scraperapi.Get.session(referrer="Downloader", status="return")
                        page_downloads_succeeded = submit_creator_tasks(tasks, gallery_id, local_session, primary_creator)
                    else:
                        for _ in tasks:
                            time.sleep(0.1)  # fake delay

                    if not page_downloads_succeeded:
                        raise RuntimeError(f"One or more pages failed for Gallery {gallery_id}")

                # --- Finalise gallery format (archive) BEFORE creating symlinks ---
                finalised_path = primary_folder
                if not orchestrator.dry_run:
                    if orchestrator.gallery_format != "directory":
                        finalised_path = finalise_gallery_format(
                            gallery_id,
                            primary_folder,
                            orchestrator.gallery_format,
                            final_parent_dir=archive_parent_dir,
                        )

                # --- Symlink all additional creators to the finalised path (archive or folder) ---
                for extra_creator in creators[1:]:
                    extra_creator_safe = scraperapi.Helpers.sanitise(extra_creator)
                    extra_folder = build_gallery_path(meta, {"creator": [extra_creator_safe]})
                    
                    # If archiving, append the extension for the symlink target
                    if orchestrator.gallery_format != "directory":
                        archive_ext = ".cbz" if orchestrator.gallery_format == "cbz" else ".zip"
                        extra_parent = os.path.dirname(extra_folder)
                        extra_name = os.path.basename(extra_folder)
                        extra_folder = os.path.join(
                            extra_parent,
                            scraperapi.Helpers.archive_filename(extra_name, archive_ext),
                        )
                    parent_dir = os.path.dirname(extra_folder)
                    os.makedirs(parent_dir, exist_ok=True)  # ensure parent exists

                    if orchestrator.dry_run:
                        target_name = "archive" if orchestrator.gallery_format != "directory" else "primary folder"
                        log(f"[DRY RUN] [Downloader] Would symlink {extra_folder} -> {target_name}", "debug")
                    else:
                        if os.path.normcase(os.path.normpath(extra_folder)) == os.path.normcase(os.path.normpath(finalised_path)):
                            logger.debug(f"[Downloader] Skipping self-symlink for Gallery {gallery_id}: {extra_folder}")
                            continue
                        if os.path.islink(extra_folder):
                            os.unlink(extra_folder)  # remove old symlink only
                        elif os.path.exists(extra_folder):
                            logger.warning(f"[Downloader] Extra path already exists and is not a symlink/copy target: {extra_folder}")
                            continue  # skip replacing real existing content
                        if _safe_symlink_or_copy(finalised_path, extra_folder):
                            logger.debug(f"[Downloader] Linked {primary_creator} -> {extra_creator_safe} (target: {os.path.basename(finalised_path)})")

                if not orchestrator.dry_run:
                    _call_db_with_lock_wait(
                        scraperapi.DB.Gallery.complete,
                        gallery_id,
                        context=f"Gallery {gallery_id} complete",
                    )
                    _downloader_ext.after_completed_gallery_download_hook(meta, gallery_id)
                    if use_local_archive and os.path.isdir(primary_folder):
                        shutil.rmtree(primary_folder, ignore_errors=True)
                    
                    # Track actual size downloaded
                    actual_bytes = 0
                    try:
                        if orchestrator.gallery_format != "directory":
                            # Count archive size
                            actual_bytes = os.path.getsize(finalised_path)
                        else:
                            # Sum all downloaded files
                            for root, dirs, files in os.walk(primary_folder):
                                for f in files:
                                    actual_bytes += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        actual_bytes = estimated_size  # Use estimate if we can't measure
                    
                    space_monitor["total_actual_bytes"] += actual_bytes
                    space_monitor["galleries_processed"] += 1

                logger.debug(f"[Downloader] Completed Gallery: {gallery_id}")
                log_clarification()
                if on_gallery_status:
                    on_gallery_status("completed")
                break  # exit retry loop on success

            except Exception as e:
                if _is_db_locked_error(e):
                    _wait_for_db_unlock(context=f"Gallery {gallery_id} database")
                    gallery_attempts = max(gallery_attempts - 1, 0)
                    continue
                logger.error(f"[Downloader] Error processing Gallery: {gallery_id}: {e}")
                if not orchestrator.dry_run and gallery_attempts >= orchestrator.max_retries:
                    update_failed_galleries(False, gallery_id=gallery_id, meta=meta, Reason=str(e))
                    _call_db_with_lock_wait(
                        scraperapi.DB.Gallery.fail,
                        gallery_id,
                        context=f"Gallery {gallery_id} fail",
                    )
                    if on_gallery_status:
                        on_gallery_status("failed")

def start_batch(current_batch_number: int = 1, total_batch_numbers: int = 1, batch_list=None, overall_start_index: int = 0, overall_total_galleries: int | None = None):
    _downloader_ext.pre_batch_hook(batch_list)

    log_clarification()
    logger.info(
        f"Galleries to process: {batch_list[0]} -> {batch_list[-1]} ({len(batch_list)})"
        if len(batch_list) > 1 else f"Galleries to process: {batch_list[0]} ({len(batch_list)})"
    )
    log_clarification()

    # --- Calculate total pages and gallery page ranges ---
    gallery_page_counts = []
    total_pages = 0
    for gid in batch_list:
        try:
            meta = scraperapi.Fetch.gallery_metadata(gid)
            num_pages = len(meta.get("images", {}).get("pages", [])) if meta and isinstance(meta, dict) else 0
        except Exception:
            num_pages = 0
        gallery_page_counts.append(num_pages)
        total_pages += num_pages

    bar_format = (
        "{desc:<} {percentage:3.0f}%|{bar}| [{n_fmt}/{total_fmt} Pages, {rate_fmt}{postfix}, {elapsed}<{remaining}]"
    )
    page_progress = tqdm(
        total=total_pages,
        desc=f"Gallery 1 / {len(batch_list)}",
        unit="page",
        bar_format=bar_format,
        position=0,
        dynamic_ncols=True
    )

    # Precompute gallery page milestones
    gallery_milestones = []
    running = 0
    for count in gallery_page_counts:
        running += count
        gallery_milestones.append(running)

    # Shared state for progress
    page_lock = threading.Lock()
    progress_state = {"pages": 0, "gallery": 1}
    gallery_state = {"completed": 0, "failed": 0, "skipped": 0}
    batch_started_at = time.perf_counter()
    total_gallery_count = overall_total_galleries or len(batch_list)

    def _set_progress_postfix() -> None:
        page_progress.set_postfix_str(
            f"Completed: {gallery_state['completed']} | Failed: {gallery_state['failed']} | Skipped: {gallery_state['skipped']}",
            refresh=False,
        )

    _set_progress_postfix()
    scraperapi.RuntimeProgress.update(
        current_gallery_number=min(overall_start_index + 1, total_gallery_count) if batch_list else 0,
        current_gallery_id=batch_list[0] if batch_list else None,
        total_galleries=total_gallery_count,
        pages_processed=0,
        total_pages=total_pages,
        pages_per_second=0,
        eta_seconds=0,
        download_speed_bytes=0,
    )

    def page_update_hook():
        with page_lock:
            progress_state["pages"] += 1
            # Check if we crossed a gallery milestone
            while (progress_state["gallery"] <= len(gallery_milestones) and
                   progress_state["pages"] > gallery_milestones[progress_state["gallery"] - 1]):
                progress_state["gallery"] += 1
            page_progress.set_description(f"Gallery {min(progress_state['gallery'], len(batch_list))} / {len(batch_list)}")
            _set_progress_postfix()
            page_progress.update(1)

            elapsed = max(time.perf_counter() - batch_started_at, 0.001)
            pages_processed = progress_state["pages"]
            pages_per_second = pages_processed / elapsed if pages_processed > 0 else 0
            remaining_pages = max(total_pages - pages_processed, 0)
            eta_seconds = math.ceil(remaining_pages / pages_per_second) if pages_per_second > 0 and remaining_pages > 0 else 0
            local_gallery_index = min(max(progress_state["gallery"] - 1, 0), max(len(batch_list) - 1, 0))
            scraperapi.RuntimeProgress.update(
                current_gallery_number=min(overall_start_index + progress_state["gallery"], total_gallery_count),
                current_gallery_id=batch_list[local_gallery_index] if batch_list else None,
                total_galleries=total_gallery_count,
                pages_processed=pages_processed,
                total_pages=total_pages,
                pages_per_second=round(pages_per_second, 2),
                eta_seconds=eta_seconds,
                download_speed_bytes=round(space_monitor["total_actual_bytes"] / elapsed, 2) if space_monitor["total_actual_bytes"] > 0 else 0,
            )

    def gallery_status_hook(status: str):
        with page_lock:
            if status in gallery_state:
                gallery_state[status] += 1
            _set_progress_postfix()
            page_progress.refresh()

    # Patch the download_images_hook to call our page_update_hook after each page
    orig_download_images_hook = _downloader_ext.download_images_hook
    def wrapped_download_images_hook(*args, **kwargs):
        if _shutdown_event and _shutdown_event.is_set():
            logger.warning("Shutdown event detected in download_images_hook, aborting page download.")
            return None
        result = orig_download_images_hook(*args, **kwargs)
        page_update_hook()
        return result
    if orig_download_images_hook:
        _downloader_ext.download_images_hook = wrapped_download_images_hook

    # Each gallery is processed in parallel via the in-module Dovetail coordinator.
    errors = run_gallery_batch(
        gallery_ids=[int(gid) for gid in batch_list],
        process_gallery_sync=lambda gid: process_galleries([int(gid)], on_gallery_status=gallery_status_hook),
    )
    for exc in errors:
        logger.error(f"[Downloader] Gallery task failed: {exc}")

    # Restore original hook
    if orig_download_images_hook:
        _downloader_ext.download_images_hook = orig_download_images_hook
    page_progress.close()
    _downloader_ext.post_batch_hook(current_batch_number, total_batch_numbers)

def start_downloader(gallery_list=None):
    """
    This is one this module's entrypoints.
    """
    
    global galleries, failed_galleries, skipped_galleries
    
    log_clarification("debug")
    logger.debug("[Downloader] Ready.")
    log("[Downloader] Debugging Started.", "debug")

    with failed_galleries_lock:
        failed_galleries = {}
    with skipped_galleries_lock:
        skipped_galleries = {}
    space_monitor["total_estimated_bytes"] = 0
    space_monitor["total_actual_bytes"] = 0
    space_monitor["galleries_processed"] = 0
    
    # Setup signal handlers for graceful shutdown (Ctrl+C, SIGTERM)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    scraperapi.RuntimeProgress.start_server()
    
    orchestrator.refresh_globals()
    
    if gallery_list is None:
        gallery_list = scraperapi.Cache.Load.queued_galleries()
        if not gallery_list:
            logger.warning("No galleries queued in database; no galleries to download.")

    # Ensure all gallery IDs are integers
    gallery_list = [int(gid) for gid in gallery_list]
    orchestrator.galleries = gallery_list
    scraperapi.RuntimeProgress.update(
        current_gallery_number=0,
        current_gallery_id=None,
        total_galleries=len(gallery_list),
        pages_processed=0,
        total_pages=0,
        pages_per_second=0,
        eta_seconds=0,
        download_speed_bytes=0,
    )
    
    start_time = time.perf_counter()  # Start timer
    
    time_estimate(f"Run", gallery_list)
    
    _ensure_managed_download_root(suppess_pre_run_hook=False) # Call pre_run_hook.
    
    # Estimate total download size and prompt if space insufficient
    if not orchestrator.dry_run:
        _, gallery_list = pre_download_checks(gallery_list)
        if not gallery_list:
            logger.warning("No galleries to download. Exiting.")
            scraperapi.Cache.Save.queued_galleries([]) # Clear gallery queue
            scraperapi.RuntimeProgress.update(current_gallery_number=0, current_gallery_id=None, total_galleries=0)
            return
    
    for batch_num in range(0, len(gallery_list), BATCH_SIZE):
        batch_list = gallery_list[batch_num:batch_num + BATCH_SIZE]
        
        # batch_num is the start index (0, BATCH_SIZE, 2*BATCH_SIZE, ...)
        current_batch_number = (batch_num // BATCH_SIZE) + 1
        
        # ceil division to compute total batches correctly
        total_batch_numbers = (len(gallery_list) + BATCH_SIZE - 1) // BATCH_SIZE
        
        current_out_of_total_batch_number = f"{current_batch_number} / {total_batch_numbers}"
        
        time_estimate(f"Batch {current_out_of_total_batch_number}", batch_list)
        
        log_clarification()
        logger.info(f"Downloading Batch {current_out_of_total_batch_number} with {len(batch_list)} Galleries...")
    
        start_batch(
            current_batch_number,
            total_batch_numbers,
            batch_list,
            overall_start_index=batch_num,
            overall_total_galleries=len(gallery_list),
        )  # Start batch.
        
        if batch_num + BATCH_SIZE < len(gallery_list): # Not last batch
            log_clarification()
            logger.info(f"Batch {current_out_of_total_batch_number} complete. Sleeping {orchestrator.batch_sleep_time}s before next batch...")
            time.sleep(orchestrator.batch_sleep_time) # Pause between batches
    
        else: # Last batch
            log_clarification()
            logger.info(f"All batches complete.")
            
    end_time = time.perf_counter()  # End timer
    runtime = end_time - start_time

    # Convert seconds to h:m:s
    hours, rem = divmod(runtime, 3600)
    minutes, seconds = divmod(rem, 60)
    human_runtime = f"{int(hours)}h {int(minutes)}m {seconds:.2f}s" if hours else f"{int(minutes)}m {seconds:.2f}s" if minutes else f"{seconds:.2f}s"
    
    # Report space usage statistics
    log_clarification()
    if space_monitor["galleries_processed"] > 0:
        log(
            f"Space Usage Summary ({space_monitor['galleries_processed']} Galleries):\n"
            f"-     Total download size: {_format_bytes(space_monitor['total_actual_bytes'])}\n"
            f"-     Estimated download size: {_format_bytes(space_monitor['total_estimated_bytes'])}\n"
            f"-     Average per gallery: {_format_bytes(space_monitor['total_actual_bytes'] // space_monitor['galleries_processed'])}\n"
        )
    
    update_failed_galleries(True)
    update_skipped_galleries(True)
    log_clarification()
    log(f"All ({len(gallery_list)}) Galleries Processed In {human_runtime}.\n")

    _downloader_ext.post_run_hook()
    
    # Clean up temp archive folder if used
    if orchestrator.gallery_format != "directory" and _is_network_share(download_location):
        try:
            if os.path.exists(ARCHIVE_TEMP_ROOT):
                shutil.rmtree(ARCHIVE_TEMP_ROOT)
                logger.info(f"Cleaned up temp archive folder: {ARCHIVE_TEMP_ROOT}")
        except Exception as e:
            logger.warning(f"Failed to clean up temp archive folder: {e}")

    scraperapi.Cache.Save.queued_galleries([]) # Clear gallery queue
    scraperapi.RuntimeProgress.update(
        current_gallery_number=len(gallery_list),
        current_gallery_id=None,
        total_galleries=len(gallery_list),
        eta_seconds=0,
    )
    scraperapi.RuntimeProgress.stop_server()