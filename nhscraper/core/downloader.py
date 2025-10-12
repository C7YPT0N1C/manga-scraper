#!/usr/bin/env python3
# nhscraper/core/downloader.py

import os, time, random, concurrent.futures, math

from tqdm.contrib.concurrent import thread_map

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *
from nhscraper.core import database as db
from nhscraper.core.api import (
    get_session, dynamic_sleep, fetch_gallery_metadata,
    fetch_image_urls, get_meta_tags, make_filesystem_safe, clean_title
)
from nhscraper.extensions.extension_manager import get_selected_extension  # Import active extension

####################################################################################################
# Global Variables
####################################################################################################

active_extension = "skeleton"
download_location = ""

skipped_galleries = []

####################################################################################################
# Select extension (skeleton fallback)
####################################################################################################
def load_extension(suppess_pre_run_hook: bool = False):
    global active_extension, download_location
    
    fetch_env_vars() # Refresh env vars in case config changed.

    ext_name = orchestrator.extension
    active_extension = get_selected_extension(ext_name, suppess_pre_run_hook=suppess_pre_run_hook)
    
    # Prefer extension-specific download path, fallback to config/global default
    download_location = getattr(active_extension, "DEDICATED_DOWNLOAD_PATH", None) or download_path
    
    if suppess_pre_run_hook==False:
        logger.debug(f"Downloader: Using extension: {getattr(active_extension, '__name__', 'skeleton')} ({active_extension})")
        logger.info(f"Downloading Galleries To: {download_location}")

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
    median_case = compute_case(
        (orchestrator.min_api_sleep + orchestrator.max_api_sleep) / 2,
        (orchestrator.min_retry_sleep + orchestrator.max_retry_sleep) / 2
    )
    worst_case = compute_case(orchestrator.max_api_sleep, orchestrator.max_retry_sleep)

    # --- Output ---
    log_clarification("warning")
    log(f"Estimated Total API Hits: {total_api_hits}", "debug")
    log(f"Starting {context} with {num_galleries} Galleries{f' (Total {total_pages} Pages)' if context ==  "Run" else ''}):")
    log(f"Best Time Estimate:    {fmt_time(best_case)}")
    log(f"Average Time Estimate: {fmt_time(median_case)}")
    log(f"Worst Time Estimate:   {fmt_time(worst_case)}", "info")

def build_gallery_path(meta, iteration: dict = None):
    """
    Build the folder path for a gallery based on SUBFOLDER_STRUCTURE.
    """
    
    gallery_metas = active_extension.return_gallery_metas(meta)

    if iteration:
        for k, v in iteration.items():
            gallery_metas[k] = v

    template = getattr(active_extension, "SUBFOLDER_STRUCTURE", ["creator", "title"])
    path_parts = [download_location]

    for key in template:
        value = gallery_metas.get(key, "Unknown")
        if isinstance(value, list):
            value = value[0] if value else "Unknown"
        if not isinstance(value, str):
            value = str(value)
        path_parts.append(make_filesystem_safe(value))

    return os.path.join(*path_parts)

def update_skipped_galleries(ReturnReport: bool, meta=None, Reason: str = "No Reason Given."):
    global skipped_galleries
    
    fetch_env_vars() # Refresh env vars in case config changed.

    if ReturnReport:
        log_clarification()
        skipped_report = "\n".join(skipped_galleries)
        log(f"All Skipped Galleries:\n{skipped_report}")
    else:
        if not meta:
            logger.warning("Downloader: update_skipped_galleries called without meta while ReturnReport is False.")
            return

        gallery_id = meta.get("id", "Unknown")
        gallery_title = clean_title(meta)
        log_clarification("debug")
        skipped_galleries.append(f"Gallery: {gallery_id}: {Reason}")
        log(f"Downloader: Updated Skipped Galleries List: Gallery {gallery_id} ({gallery_title}): {Reason}", "debug")

def should_download_gallery(meta, gallery_title, num_pages, iteration: dict = None):
    """
    Decide whether to download a gallery or skip it.
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if not meta:
        update_skipped_galleries(False, meta, "Not Meta.")
        return False

    gallery_id = meta.get("id")
    doujin_folder = build_gallery_path(meta, iteration)

    if num_pages == 0:
        logger.warning(
            f"Downloader: Skipping Gallery: {gallery_id}\n"
            "Reason: No Pages.\n"
            f"Title: {gallery_title}\n"
        )
        log_clarification()
        update_skipped_galleries(False, meta, "No Pages.")
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
                f"Downloader: Skipping Gallery: {gallery_id}\n"
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
    gallery_tags = [t.lower() for t in get_meta_tags("Downloader: Should_Download_Gallery", meta, "tag")]
    blocked_tags = []
    
    for tag in gallery_tags:
        if tag in excluded_gallery_tags:
            blocked_tags.append(tag)

    # --- Allowed Languages ---
    allowed_gallery_language = [lang.lower() for lang in orchestrator.language]
    gallery_langs = [l.lower() for l in get_meta_tags("Downloader: Should_Download_Gallery", meta, "language")]
    blocked_langs = []

    if allowed_gallery_language:
        has_allowed = any(lang in allowed_gallery_language for lang in gallery_langs)
        has_translated = "translated" in gallery_langs and has_allowed
        if not (has_allowed or has_translated):
            blocked_langs = gallery_langs[:]

    #log_clarification("debug") # NOTE: DEBUGGING
    #log(f"Excluded Genres: {excluded_gallery_tags}", "debug")
    #log(f"Allowed Languages: {allowed_gallery_language}", "debug")
    
    if blocked_tags or blocked_langs:
        logger.info(
            f"Downloader: Skipping Gallery: {gallery_id}\n"
            "Reason: Blocked Tags In Metadata.\n"
            f"Title: {gallery_title}\n"
            f"Filtered tags: {blocked_tags}\n"
            f"Filtered languages: {blocked_langs}"
        )
        log_clarification()
        update_skipped_galleries(False, meta, f"Filtered tags: {blocked_tags}, languages: {blocked_langs}")
        return False

    return True

def submit_creator_tasks(executor, creator_tasks, gallery_id, local_session, safe_creator_name):
    """
    Submit download tasks for a single creator's pages.
    """
    
    futures = [
        executor.submit(
            active_extension.download_images_hook,
            gallery_id, page, urls, path, local_session, None, safe_creator_name
        )
        for page, urls, path, _ in creator_tasks
    ]
    # Just wait for completion
    for _ in concurrent.futures.as_completed(futures):
        pass

####################################################################################################
# CORE
####################################################################################################
def process_galleries(batch_ids):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    for gallery_id in batch_ids:
        extension_name = getattr(active_extension, "__name__", "skeleton")
        if not orchestrator.dry_run:
            db.mark_gallery_started(gallery_id, download_location, extension_name)
        else:
            log_clarification()
            logger.info(f"[DRY RUN] Downloader: Would mark Gallery {gallery_id} as started.")

        gallery_attempts = 0

        while gallery_attempts < orchestrator.max_retries:
            gallery_attempts += 1
            try:
                active_extension.pre_gallery_download_hook(gallery_id)
                log_clarification("debug")
                logger.debug("######################## GALLERY START ########################")
                log_clarification("debug")
                logger.debug(f"Downloader: Starting Gallery: {gallery_id} (Attempt {gallery_attempts}/{orchestrator.max_retries})")

                meta = fetch_gallery_metadata(gallery_id)
                if not meta or not isinstance(meta, dict):
                    logger.warning(f"Downloader: Failed to fetch metadata for Gallery: {gallery_id}")
                    if not orchestrator.dry_run and gallery_attempts >= orchestrator.max_retries:
                        db.mark_gallery_failed(gallery_id)
                    continue

                num_pages = len(meta.get("images", {}).get("pages", []))
                active_extension.during_gallery_download_hook(gallery_id)
                gallery_metas = active_extension.return_gallery_metas(meta)
                creators = gallery_metas["creator"]
                gallery_title = gallery_metas["title"]
                
                time.sleep(dynamic_sleep("gallery", attempt=gallery_attempts)) # Sleep before starting gallery.

                # --- Decide if gallery should be skipped ---
                skip_gallery = False
                for creator in creators:
                    iteration = {"creator": [creator]}
                    if not should_download_gallery(meta, gallery_title, num_pages, iteration):
                        skip_gallery = True
                        break

                if skip_gallery:
                    if not orchestrator.dry_run:
                        db.mark_gallery_skipped(gallery_id)
                    else:
                        log_clarification()
                        logger.info(f"[DRY RUN] Downloader: Would mark Gallery {gallery_id} as skipped.")
                    break  # exit retry loop, skip gallery

                # --- Prepare primary folder (first creator only) ---
                primary_creator = make_filesystem_safe(creators[0]) if creators else "Unknown"
                log(f"Downloader: Primary Creator for Gallery: {gallery_id}: {primary_creator}", "debug")
                primary_folder = build_gallery_path(meta, {"creator": [creators[0]]})

                if orchestrator.dry_run:
                    log(f"[DRY RUN] Downloader: Would create primary folder for {creators[0]}: {primary_folder}", "debug")
                else:
                    os.makedirs(primary_folder, exist_ok=True)

                # --- Symlink all additional creators to the primary folder ---
                for extra_creator in creators[1:]:
                    extra_creator_safe = make_filesystem_safe(extra_creator)
                    extra_folder = build_gallery_path(meta, {"creator": [extra_creator_safe]})
                    parent_dir = os.path.dirname(extra_folder)
                    os.makedirs(parent_dir, exist_ok=True)  # ensure parent exists

                    if orchestrator.dry_run:
                        log(f"[DRY RUN] Downloader: Would symlink {extra_folder} -> {primary_folder}", "debug")
                    else:
                        if os.path.islink(extra_folder):
                            os.unlink(extra_folder)  # remove old symlink only
                        elif os.path.exists(extra_folder):
                            logger.warning(f"Downloader: Extra folder already exists and is not a symlink: {extra_folder}")
                            continue  # skip creating symlink if real folder exists
                        os.symlink(primary_folder, extra_folder)
                        logger.info(f"Downloader: Symlinked {primary_creator} -> {extra_creator_safe}")

                # --- Prepare download tasks (only once, for primary creator) ---
                tasks = []
                for i in range(num_pages):
                    page = i + 1
                    img_urls = fetch_image_urls(meta, page)
                    if not img_urls:
                        logger.warning(f"Downloader: Skipping Page {page} for {primary_creator}: Failed to get URLs")
                        update_skipped_galleries(False, meta, "Failed to get URLs.")
                        continue

                    img_filename = f"{page}.{img_urls[0].split('.')[-1]}"
                    img_path = os.path.join(primary_folder, img_filename)
                    tasks.append((page, img_urls, img_path, primary_creator))

                # --- Download images (once, in primary creator's folder) ---
                if tasks:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=threads_images) as executor:
                        if not orchestrator.dry_run:
                            local_session = get_session(referrer="Downloader", status="return")
                            submit_creator_tasks(executor, tasks, gallery_id, local_session, primary_creator)
                        else:
                            for _ in tasks:
                                time.sleep(0.1)  # fake delay

                if not orchestrator.dry_run:
                    active_extension.after_completed_gallery_download_hook(meta, gallery_id)
                    db.mark_gallery_completed(gallery_id)

                log_clarification()
                logger.info(f"Downloader: Completed Gallery: {gallery_id}")
                break  # exit retry loop on success

            except Exception as e:
                logger.error(f"Downloader: Error processing Gallery: {gallery_id}: {e}")
                if not orchestrator.dry_run and gallery_attempts >= orchestrator.max_retries:
                    db.mark_gallery_failed(gallery_id)

####################################################################################################
# MAIN
####################################################################################################

def start_batch(current_batch_number: int = 1, total_batch_numbers: int = 1, batch_list=None):
    # Load extension. active_extension.pre_run_hook() is called by extension_loader when extension is loaded.
    load_extension(suppess_pre_run_hook=True) # Load extension without calling pre_run_hook again.
    
    active_extension.pre_batch_hook(batch_list)

    log_clarification()
    logger.info(
        f"Galleries to process: {batch_list[0]} -> {batch_list[-1]} ({len(batch_list)})"
        if len(batch_list) > 1 else f"Galleries to process: {batch_list[0]} ({len(batch_list)})"
    )
    log_clarification()

    # Each gallery is processed in parallel with its own thread
    thread_map(
        lambda gid: process_galleries([gid]),
        batch_list,
        max_workers=orchestrator.threads_galleries,
        desc=f"Batch {current_batch_number}/{total_batch_numbers}",
        unit="gallery"
    )

    active_extension.post_batch_hook(current_batch_number, total_batch_numbers)

def start_downloader(gallery_list=None):
    """
    This is one this module's entrypoints.
    """
    
    global galleries
    
    log_clarification("debug")
    logger.debug("Downloader: Ready.")
    log("Downloader: Debugging Started.", "debug")
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    orchestrator.galleries = gallery_list
    
    start_time = time.perf_counter()  # Start timer
    
    time_estimate(f"Run", gallery_list)
    
    load_extension(suppess_pre_run_hook=False) # Load extension and call pre_run_hook.
    
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
    
        start_batch(current_batch_number, total_batch_numbers, batch_list) # Start batch.
        
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
    
    #update_skipped_galleries(True) # Report all skipped galleries at end
    log_clarification()
    log(f"All ({len(gallery_list)}) Galleries Processed In {human_runtime}.")

    active_extension.post_run_hook()