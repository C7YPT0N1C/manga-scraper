#!/usr/bin/env python3
# nhscraper/downloader.py

import os, time, random, concurrent.futures
from tqdm.contrib.concurrent import thread_map

from nhscraper.core.config import *
from nhscraper.core import database as db
from nhscraper.core.api import (
    build_session, session, dynamic_sleep, fetch_gallery_metadata,
    fetch_image_urls, get_meta_tags, safe_name, clean_title
)
from nhscraper.extensions.extension_loader import get_selected_extension  # Import active extension

####################################################################################################
# Global Variables
####################################################################################################
active_extension = "skeleton"
download_location = ""

gallery_threads = config.get("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES)
image_threads = config.get("THREADS_IMAGES", DEFAULT_THREADS_IMAGES)

downloader_dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)

skipped_galleries = []

####################################################################################################
# Select extension (skeleton fallback)
####################################################################################################
def load_extension():
    global active_extension, download_location

    ext_name = config.get("EXTENSION", "skeleton")
    active_extension = get_selected_extension(ext_name)
    logger.info(f"Downloader: Using extension: {getattr(active_extension, '__name__', 'skeleton')}")

    # Prefer extension-specific download path, fallback to config/global default
    download_location = getattr(active_extension, "DEDICATED_DOWNLOAD_PATH", None) \
                        or config.get("DOWNLOAD_PATH", DEFAULT_DOWNLOAD_PATH)

    if not downloader_dry_run:
        os.makedirs(download_location, exist_ok=True)
    else:
        logger.info(f"[DRY RUN] Downloader: Skipping creation of: {download_location}")

    logger.info(f"Downloader: Using download path: {download_location}")

####################################################################################################
# UTILITIES
####################################################################################################

def worst_case_time_estimate(context: str, id_list: list):
    current_run_num_of_galleries = len(id_list)
    current_run_gallery_threads = gallery_threads
    current_run_image_threads = image_threads
    current_run_gallery_sleep_max = config.get("MAX_SLEEP", DEFAULT_MAX_SLEEP)
    current_batch_sleep_time = BATCH_SIZE * BATCH_SIZE_SLEEP_MULTIPLIER
    
    worst_time_secs = (
        ((current_run_num_of_galleries / current_run_gallery_threads) * current_run_gallery_sleep_max ) +
        ((current_run_num_of_galleries / BATCH_SIZE) * current_batch_sleep_time)
    )
    
    worst_time_mins = worst_time_secs / 60 # Convert To Minutes
    worst_time_days = worst_time_secs / 60 / 60 # Convert To Hours
    worst_time_hours = worst_time_secs / 60 / 60 / 24 # Convert To Days
    
    log_clarification()
    #logger.info(f"Number of Galleries Processed: {len(id_list)}") # DEBUGGING
    #logger.info(f"Number of Threads: Gallery: {current_run_gallery_threads}, Image: {current_run_image_threads}") # DEBUGGING
    #logger.info(f"Batch Sleep Time: {current_batch_sleep_time:.2f}s per {BATCH_SIZE} galleries") # DEBUGGING
    #logger.info(f"Max Sleep Time: {current_run_gallery_sleep_max}") # DEBUGGING
    log(f"{context} Worst Case Time Estimate = {worst_time_mins:.2f} Minutes / {worst_time_days:.2f} Hours / {worst_time_hours:.2f} Days")

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
        path_parts.append(safe_name(value))

    return os.path.join(*path_parts)

def update_skipped_galleries(ReturnReport: bool, meta=None, Reason: str = "No Reason Given."):
    global skipped_galleries

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
        log_clarification()
        skipped_galleries.append(f"Gallery {gallery_id}: {Reason}")
        log(f"Downloader: Updated Skipped Galleries List: Gallery {gallery_id} ({gallery_title}): {Reason}", "debug")

def should_download_gallery(meta, gallery_title, num_pages, iteration: dict = None):
    """
    Decide whether to download a gallery or skip it.
    """
    
    if not meta:
        update_skipped_galleries(False, meta, "Not Meta.")
        return False

    gallery_id = meta.get("id")
    doujin_folder = build_gallery_path(meta, iteration)

    if num_pages == 0:
        log_clarification()
        logger.warning(
            f"Downloader: Skipping Gallery: {gallery_id}\n"
            "Reason: No Pages.\n"
            f"Title: {gallery_title}\n"
        )
        update_skipped_galleries(False, meta, "No Pages.")
        return False

    # Skip only if NOT in dry-run
    if not downloader_dry_run and os.path.exists(doujin_folder):
        all_exist = all(
            any(os.path.exists(os.path.join(doujin_folder, f"{i+1}.{ext}"))
                for ext in ("jpg", "png", "gif", "webp"))
            for i in range(num_pages)
        )
        if all_exist:
            log_clarification()
            logger.info(
                f"Downloader: Skipping Gallery: {gallery_id}\n"
                "Reason: Already Downloaded.\n"
                f"Title: {gallery_title}\n"
                f"Folder: {doujin_folder}"
            )
            update_skipped_galleries(False, meta, "Already downloaded.")
            return False

    excluded_tags = [t.lower() for t in config.get("EXCLUDED_TAGS", DEFAULT_EXCLUDED_TAGS)]
    gallery_tags = [t.lower() for t in get_meta_tags("Downloader: Should_Download_Gallery", meta, "tag")]
    blocked_tags = []

    allowed_langs = [l.lower() for l in config.get("LANGUAGE", DEFAULT_LANGUAGE)]
    gallery_langs = [l.lower() for l in get_meta_tags("Downloader: Should_Download_Gallery", meta, "language")]
    blocked_langs = []

    for tag in gallery_tags:
        if tag in excluded_tags:
            blocked_tags.append(tag)

    if allowed_langs:
        has_allowed = any(lang in allowed_langs for lang in gallery_langs)
        has_translated = "translated" in gallery_langs and has_allowed
        if not (has_allowed or has_translated):
            blocked_langs = gallery_langs[:]

    if blocked_tags or blocked_langs:
        log_clarification()
        logger.info(
            f"Downloader: Skipping Gallery: {gallery_id}\n"
            "Reason: Blocked Tags In Metadata.\n"
            f"Title: {gallery_title}\n"
            f"Filtered tags: {blocked_tags}\n"
            f"Filtered languages: {blocked_langs}"
        )
        update_skipped_galleries(False, meta, f"Filtered tags: {blocked_tags}, languages: {blocked_langs}")
        return False

    return True

def submit_creator_tasks(executor, creator_tasks, gallery_id, session, safe_creator_name):
    """
    Submit download tasks for a single creator's pages.
    """
    
    futures = [
        executor.submit(
            active_extension.download_images_hook,
            gallery_id, page, urls, path, session, None, safe_creator_name
        )
        for page, urls, path, _ in creator_tasks
    ]
    # Just wait for completion
    for _ in concurrent.futures.as_completed(futures):
        pass

####################################################################################################
# CORE
####################################################################################################
def process_galleries(gallery_ids):
    for gallery_id in gallery_ids:
        extension_name = getattr(active_extension, "__name__", "skeleton")
        if not downloader_dry_run:
            db.mark_gallery_started(gallery_id, download_location, extension_name)
        else:
            log_clarification()
            logger.info(f"[DRY RUN] Downloader: Would mark gallery {gallery_id} as started.")

        gallery_attempts = 0
        max_gallery_attempts = config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES)

        while gallery_attempts < max_gallery_attempts:
            gallery_attempts += 1
            try:
                active_extension.pre_gallery_download_hook(gallery_id)
                log_clarification()
                logger.info("######################## GALLERY START ########################")
                log_clarification()
                logger.info(f"Downloader: Starting Gallery: {gallery_id} (Attempt {gallery_attempts}/{max_gallery_attempts})")

                meta = fetch_gallery_metadata(gallery_id)
                if not meta or not isinstance(meta, dict):
                    logger.warning(f"Downloader: Failed to fetch metadata for Gallery: {gallery_id}")
                    if not downloader_dry_run and gallery_attempts >= max_gallery_attempts:
                        db.mark_gallery_failed(gallery_id)
                    continue

                num_pages = len(meta.get("images", {}).get("pages", []))
                active_extension.during_gallery_download_hook(gallery_id)
                gallery_metas = active_extension.return_gallery_metas(meta)
                creators = gallery_metas["creator"]
                gallery_title = gallery_metas["title"]
                
                time.sleep(dynamic_sleep("gallery", gallery_attempts)) # Sleep before starting gallery.

                # --- Decide if gallery should be skipped ---
                skip_gallery = False
                for creator in creators:
                    iteration = {"creator": [creator]}
                    if not should_download_gallery(meta, gallery_title, num_pages, iteration):
                        skip_gallery = True
                        break

                if skip_gallery:
                    if not downloader_dry_run:
                        db.mark_gallery_skipped(gallery_id)
                    else:
                        log_clarification()
                        logger.info(f"[DRY RUN] Downloader: Would mark gallery {gallery_id} as skipped.")
                    break  # exit retry loop, skip gallery

                # --- Prepare primary folder (first creator only) ---
                primary_creator = safe_name(creators[0]) if creators else "Unknown"
                log(f"Downloader: Primary Creator for Gallery {gallery_id}: {primary_creator}", "debug")
                primary_folder = build_gallery_path(meta, {"creator": [creators[0]]})

                if downloader_dry_run:
                    log(f"[DRY RUN] Downloader: Would create primary folder for {creators[0]}: {primary_folder}", "debug")
                else:
                    os.makedirs(primary_folder, exist_ok=True)

                # --- Symlink all additional creators to the primary folder ---
                for extra_creator in creators[1:]:
                    extra_creator_safe = safe_name(extra_creator)
                    extra_folder = build_gallery_path(meta, {"creator": [extra_creator_safe]})
                    parent_dir = os.path.dirname(extra_folder)
                    os.makedirs(parent_dir, exist_ok=True)  # ensure parent exists

                    if downloader_dry_run:
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

                # --- Download images (once, in primary creator’s folder) ---
                if tasks:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=image_threads) as executor:
                        if not downloader_dry_run:
                            submit_creator_tasks(executor, tasks, gallery_id, session, primary_creator)
                        else:
                            for _ in tasks:
                                time.sleep(0.1)  # fake delay

                if not downloader_dry_run:
                    active_extension.after_completed_gallery_download_hook(meta, gallery_id)
                    db.mark_gallery_completed(gallery_id)

                log_clarification()
                logger.info(f"Downloader: Completed Gallery: {gallery_id}")
                break  # exit retry loop on success

            except Exception as e:
                logger.error(f"Downloader: Error processing Gallery {gallery_id}: {e}")
                if not downloader_dry_run and gallery_attempts >= max_gallery_attempts:
                    db.mark_gallery_failed(gallery_id)

####################################################################################################
# MAIN
####################################################################################################

def start_batch(batch_list=None):
    batch_ids = batch_list
    
    active_extension.pre_batch_hook(batch_ids)
    
    # Rebuild session.
    #build_session(referrer="Downloader", rebuild=True)

    log_clarification()
    logger.info(f"Downloader: Galleries to process: {batch_ids[0]} -> {batch_ids[-1]} ({len(batch_ids)})"
                if len(batch_ids) > 1 else f"Downloader: Galleries to process: {batch_ids[0]} ({len(batch_ids)})")

    thread_map(
        lambda gid: process_galleries([gid]),
        batch_ids,
        max_workers=gallery_threads,
        desc="Processing galleries",
        unit="gallery"
    )

    load_extension()
    active_extension.post_batch_hook()

def start_downloader(gallery_list=None):
    log_clarification()
    logger.info("Downloader: Ready.")
    log("Downloader: Debugging Started.", "debug")
    
    start_time = time.perf_counter()  # Start timer
    
    # Load extension. active_extension.pre_run_hook() is called by extension_loader when extension is loaded.
    load_extension()
    
    gallery_ids = gallery_list
    
    log_clarification()
    if not gallery_ids:
        logger.error("No galleries specified. Use --galleries or --range.")
        return
    
    worst_case_time_estimate(f"Run", gallery_ids)
    
    BATCH_SLEEP_TIME = (BATCH_SIZE * BATCH_SIZE_SLEEP_MULTIPLIER) # Seconds to sleep between batches.
    for batch_num in range(0, len(gallery_ids), BATCH_SIZE):
        batch_ids = gallery_ids[batch_num:batch_num + BATCH_SIZE]
        
        worst_case_time_estimate(f"Batch {batch_num}", batch_ids)
        
        log_clarification()
        logger.info(f"Downloading Batch {batch_num//BATCH_SIZE + 1} with {len(batch_ids)} Galleries...")
    
        start_batch(batch_ids) # Start batch.
        
        if batch_num + BATCH_SIZE < len(gallery_ids): # Not last batch
            log_clarification()
            logger.info(f"Batch {batch_num//BATCH_SIZE + 1} complete. Sleeping {BATCH_SLEEP_TIME}s before next batch...")
            time.sleep(BATCH_SLEEP_TIME) # Pause between batches
        else: # Last batch
            log_clarification()
            logger.info(f"All batches complete.")
    
    active_extension.post_run_hook()
    
    end_time = time.perf_counter()  # End timer
    runtime = end_time - start_time

    # Convert seconds to h:m:s
    hours, rem = divmod(runtime, 3600)
    minutes, seconds = divmod(rem, 60)
    human_runtime = f"{int(hours)}h {int(minutes)}m {seconds:.2f}s" if hours else f"{int(minutes)}m {seconds:.2f}s" if minutes else f"{seconds:.2f}s"

    #update_skipped_galleries(True) # Report all skipped galleries at end
    log_clarification()
    logger.info(f"All ({len(gallery_ids)}) Galleries Processed In {human_runtime}.")