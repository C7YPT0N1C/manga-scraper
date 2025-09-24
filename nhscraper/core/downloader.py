#!/usr/bin/env python3
# nhscraper/downloader.py

import os, time, random, concurrent.futures
from tqdm.contrib.concurrent import thread_map

from nhscraper.core import configurator
from nhscraper.core.configurator import *
from nhscraper.core import database as db
from nhscraper.core.api import (
    get_session, dynamic_sleep, fetch_gallery_metadata,
    fetch_image_urls, get_meta_tags, make_filesystem_safe, clean_title
)
from nhscraper.extensions.extension_loader import get_selected_extension  # Import active extension

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

    ext_name = configurator.extension
    active_extension = get_selected_extension(ext_name, suppess_pre_run_hook=suppess_pre_run_hook)
    
    # Prefer extension-specific download path, fallback to config/global default
    download_location = getattr(active_extension, "DEDICATED_DOWNLOAD_PATH", None) or download_path
    
    if suppess_pre_run_hook==False:
        logger.debug(f"Downloader: Using extension: {getattr(active_extension, '__name__', 'skeleton')} ({active_extension})")
        logger.info(f"Downloading Gallery To: {download_location}")

    if not dry_run:
        os.makedirs(download_location, exist_ok=True)
    else:
        if suppess_pre_run_hook==False:
            logger.info(f"[DRY RUN] Downloader: Skipping creation of: {download_location}")

####################################################################################################
# UTILITIES
####################################################################################################

def worst_case_time_estimate(context: str, id_list: list):
    current_run_num_of_galleries = len(id_list)
    current_batch_sleep_time = BATCH_SIZE * BATCH_SIZE_SLEEP_MULTIPLIER
    
    worst_time_secs = (
        ((current_run_num_of_galleries / threads_galleries) * configurator.max_sleep ) +
        ((current_run_num_of_galleries / BATCH_SIZE) * current_batch_sleep_time)
    )
    
    worst_time_mins = worst_time_secs / 60 # Convert To Minutes
    worst_time_days = worst_time_secs / 60 / 60 # Convert To Hours
    worst_time_hours = worst_time_secs / 60 / 60 / 24 # Convert To Days
    
    #log_clarification()
    #logger.info(f"Number of Galleries Processed: {len(id_list)}") # DEBUGGING
    #logger.info(f"Number of Threads: Gallery: {threads_galleries}, Image: {threads_images}") # DEBUGGING
    #logger.info(f"Batch Sleep Time: {current_batch_sleep_time:.2f}s per {BATCH_SIZE} galleries") # DEBUGGING
    #logger.info(f"Max Sleep Time: {configurator.max_sleep}") # DEBUGGING
    log_clarification("debug")
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
        path_parts.append(make_filesystem_safe(value))

    return os.path.join(*path_parts)

def update_skipped_galleries(ReturnReport: bool, meta=None, Reason: str = "No Reason Given."):
    global skipped_galleries
    
    fetch_env_vars() # Refresh env vars in case config changed.

    if ReturnReport:
        log_clarification("debug")
        skipped_report = "\n".join(skipped_galleries)
        log(f"All Skipped Galleries:\n{skipped_report}")
    else:
        if not meta:
            logger.warning("Downloader: update_skipped_galleries called without meta while ReturnReport is False.")
            return

        gallery_id = meta.get("id", "Unknown")
        gallery_title = clean_title(meta)
        log_clarification("debug")
        skipped_galleries.append(f"Gallery {gallery_id}: {Reason}")
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
    if not dry_run and os.path.exists(doujin_folder):
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
                f"Folder: {doujin_folder}"
            )
            log_clarification()
            update_skipped_galleries(False, meta, "Already downloaded.")
            return False

    excluded_gallery_tags = [t.lower() for t in excluded_tags]
    gallery_tags = [t.lower() for t in get_meta_tags("Downloader: Should_Download_Gallery", meta, "tag")]
    blocked_tags = []

    allowed_gallery_language = [l.lower() for l in configurator.language]
    gallery_langs = [l.lower() for l in get_meta_tags("Downloader: Should_Download_Gallery", meta, "language")]
    blocked_langs = []

    for tag in gallery_tags:
        if tag in excluded_gallery_tags:
            blocked_tags.append(tag)

    if allowed_gallery_language:
        has_allowed = any(lang in allowed_gallery_language for lang in gallery_langs)
        has_translated = "translated" in gallery_langs and has_allowed
        if not (has_allowed or has_translated):
            blocked_langs = gallery_langs[:]

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
    for gallery_id in batch_ids:
        extension_name = getattr(active_extension, "__name__", "skeleton")
        if not dry_run:
            db.mark_gallery_started(gallery_id, download_location, extension_name)
        else:
            log_clarification()
            logger.info(f"[DRY RUN] Downloader: Would mark gallery {gallery_id} as started.")

        gallery_attempts = 0

        while gallery_attempts < configurator.max_retries:
            gallery_attempts += 1
            try:
                active_extension.pre_gallery_download_hook(gallery_id)
                log_clarification("debug")
                logger.debug("######################## GALLERY START ########################")
                log_clarification("debug")
                logger.debug(f"Downloader: Starting Gallery: {gallery_id} (Attempt {gallery_attempts}/{configurator.max_retries})")

                meta = fetch_gallery_metadata(gallery_id)
                if not meta or not isinstance(meta, dict):
                    logger.warning(f"Downloader: Failed to fetch metadata for Gallery: {gallery_id}")
                    if not dry_run and gallery_attempts >= configurator.max_retries:
                        db.mark_gallery_failed(gallery_id)
                    continue

                num_pages = len(meta.get("images", {}).get("pages", []))
                active_extension.during_gallery_download_hook(gallery_id)
                gallery_metas = active_extension.return_gallery_metas(meta)
                creators = gallery_metas["creator"]
                gallery_title = gallery_metas["title"]
                
                time.sleep(dynamic_sleep("gallery", batch_ids, gallery_attempts)) # Sleep before starting gallery.

                # --- Decide if gallery should be skipped ---
                skip_gallery = False
                for creator in creators:
                    iteration = {"creator": [creator]}
                    if not should_download_gallery(meta, gallery_title, num_pages, iteration):
                        skip_gallery = True
                        break

                if skip_gallery:
                    if not dry_run:
                        db.mark_gallery_skipped(gallery_id)
                    else:
                        log_clarification()
                        logger.info(f"[DRY RUN] Downloader: Would mark gallery {gallery_id} as skipped.")
                    break  # exit retry loop, skip gallery

                # --- Prepare primary folder (first creator only) ---
                primary_creator = make_filesystem_safe(creators[0]) if creators else "Unknown"
                log(f"Downloader: Primary Creator for Gallery {gallery_id}: {primary_creator}", "debug")
                primary_folder = build_gallery_path(meta, {"creator": [creators[0]]})

                if dry_run:
                    log(f"[DRY RUN] Downloader: Would create primary folder for {creators[0]}: {primary_folder}", "debug")
                else:
                    os.makedirs(primary_folder, exist_ok=True)

                # --- Symlink all additional creators to the primary folder ---
                for extra_creator in creators[1:]:
                    extra_creator_safe = make_filesystem_safe(extra_creator)
                    extra_folder = build_gallery_path(meta, {"creator": [extra_creator_safe]})
                    parent_dir = os.path.dirname(extra_folder)
                    os.makedirs(parent_dir, exist_ok=True)  # ensure parent exists

                    if dry_run:
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
                    with concurrent.futures.ThreadPoolExecutor(max_workers=threads_images) as executor:
                        if not dry_run:
                            local_session = get_session(referrer="Downloader", status="return")
                            submit_creator_tasks(executor, tasks, gallery_id, local_session, primary_creator)
                        else:
                            for _ in tasks:
                                time.sleep(0.1)  # fake delay

                if not dry_run:
                    active_extension.after_completed_gallery_download_hook(meta, gallery_id)
                    db.mark_gallery_completed(gallery_id)

                log_clarification()
                logger.info(f"Downloader: Completed Gallery: {gallery_id}")
                break  # exit retry loop on success

            except Exception as e:
                logger.error(f"Downloader: Error processing Gallery {gallery_id}: {e}")
                if not dry_run and gallery_attempts >= configurator.max_retries:
                    db.mark_gallery_failed(gallery_id)

####################################################################################################
# MAIN
####################################################################################################

def start_batch(batch_list=None):
    # Load extension. active_extension.pre_run_hook() is called by extension_loader when extension is loaded.
    load_extension(suppess_pre_run_hook=True) # Load extension without calling pre_run_hook again.
    
    active_extension.pre_batch_hook(batch_list)

    log_clarification()
    logger.info(f"Galleries to process: {batch_list[0]} -> {batch_list[-1]} ({len(batch_list)})"
                if len(batch_list) > 1 else f"Galleries to process: {batch_list[0]} ({len(batch_list)})")
    log_clarification()

    thread_map(
        lambda gid: process_galleries([gid]),
        batch_list,
        max_workers=threads_galleries,
        desc="Processing galleries",
        unit="gallery"
    )

    active_extension.post_batch_hook()

def start_downloader(gallery_list=None):
    """
    This is one this module's entrypoints.
    """
    
    log_clarification("debug")
    logger.debug("Downloader: Ready.")
    log("Downloader: Debugging Started.", "debug")
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    start_time = time.perf_counter()  # Start timer
    
    worst_case_time_estimate(f"Run", gallery_list)
    
    load_extension(suppess_pre_run_hook=False) # Load extension and call pre_run_hook.
    
    BATCH_SLEEP_TIME = (BATCH_SIZE * BATCH_SIZE_SLEEP_MULTIPLIER) # Seconds to sleep between batches.
    for batch_num in range(0, len(gallery_list), BATCH_SIZE):
        batch_list = gallery_list[batch_num:batch_num + BATCH_SIZE]
        
        worst_case_time_estimate(f"Batch {batch_num}", batch_list)
        
        log_clarification()
        logger.info(f"Downloading Batch {batch_num//BATCH_SIZE + 1} with {len(batch_list)} Galleries...")
    
        start_batch(batch_list) # Start batch.
        
        if batch_num + BATCH_SIZE < len(gallery_list): # Not last batch
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
    log_clarification("debug")
    log(f"All ({len(gallery_list)}) Galleries Processed In {human_runtime}.")