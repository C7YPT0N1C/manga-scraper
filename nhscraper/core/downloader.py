#!/usr/bin/env python3
# nhscraper/core/downloader.py
import os, sys, time, random, argparse, re, subprocess, urllib.parse # 'Default' imports

import threading, asyncio # Module-specific imports

from tqdm.asyncio import tqdm_asyncio

# When referencing globals from orchestrator
# explicitly reference them (e.g. orchestrator.VARIABLE_NAME)
from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *
from nhscraper.core import database as db
from nhscraper.core.api import (
    get_session, fetch_gallery_metadata,
    fetch_image_urls, get_meta_tags, make_filesystem_safe, clean_title
)
from nhscraper.extensions.extension_manager import get_selected_extension

"""
Core download logic for galleries and images.
Fetches metadata, downloads content, saves files,
and handles error recovery with retry strategies.
"""

####################################################################################################
# Global Variables
####################################################################################################

_module_referrer=f"Downloader" # Used in async_runner.* / cross-module calls

active_extension = "skeleton"
download_location = ""

skipped_galleries = []

####################################################################################################
# Select extension (skeleton fallback)
####################################################################################################
async def load_extension(suppess_pre_run_hook: bool = False):
    global active_extension, download_location
    
    fetch_env_vars() # Refresh env vars in case config changed.

    ext_name = orchestrator.extension_name
    # use async_runner.invoke() for cross-module call
    active_extension = await async_runner.invoke(get_selected_extension, ext_name, suppess_pre_run_hook=suppess_pre_run_hook)
    
    # Prefer extension-specific download path, fallback to config/global default
    download_location = getattr(active_extension, "DEDICATED_DOWNLOAD_PATH", None) or orchestrator.download_path
    
    log(f"Downloader: Using extension: {getattr(active_extension, '__name__', 'skeleton')} ({active_extension})", "debug")
    if not suppess_pre_run_hook or orchestrator.dry_run:
        log(f"Downloading Galleries To: {download_location}", "info")
    else:
        log(f"[DRY RUN] Would Download Galleries To: {download_location}", "info")

####################################################################################################
# UTILITIES
####################################################################################################

def time_estimate(context: str, id_list: list):
    current_run_num_of_galleries = len(id_list)
    
    # Best Case
    best_time_secs = (
        ((current_run_num_of_galleries / orchestrator.threads_galleries) * orchestrator.min_sleep ) +
        ((current_run_num_of_galleries / BATCH_SIZE) * orchestrator.batch_sleep_time)
    )
    
    best_time_mins = best_time_secs / 60
    best_time_days = best_time_secs / 60 / 60
    best_time_hours = best_time_secs / 60 / 60 / 24
    
    # Worst Case
    worst_time_secs = (
        ((current_run_num_of_galleries / orchestrator.threads_galleries) * orchestrator.max_sleep ) +
        ((current_run_num_of_galleries / BATCH_SIZE) * orchestrator.batch_sleep_time)
    )
    
    worst_time_mins = worst_time_secs / 60
    worst_time_days = worst_time_secs / 60 / 60
    worst_time_hours = worst_time_secs / 60 / 60 / 24
    
    log_clarification()
    log(
        f"{context} ({current_run_num_of_galleries} Galleries):"
        f"\nBest Case Time Estimate: {best_time_hours:.2f} Days / {best_time_days:.2f} Hours / {best_time_mins:.2f} Minutes"
        f"\nWorst Case Time Estimate: {worst_time_hours:.2f} Days / {worst_time_days:.2f} Hours / {worst_time_mins:.2f} Minutes",
        "warn"
    )

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

async def symlink_extra_creators(primary_folder: str, creators: list[str], meta: dict):
    """
    Create symlinks for extra creators pointing to the primary folder.

    Args:
        primary_folder (str): Path to the main gallery folder.
        creators (list[str]): All creators (first is primary, rest symlinked).
        meta (dict): Metadata for building gallery paths.
    """
    if len(creators) <= 1:
        return  # nothing to symlink

    primary_creator = make_filesystem_safe(creators[0])

    for extra_creator in creators[1:]:
        extra_creator_safe = make_filesystem_safe(extra_creator)

        # Build path for extra creator
        extra_folder = build_gallery_path(meta, {"creator": [extra_creator_safe]})
        parent_dir = os.path.dirname(extra_folder)

        def _make_symlink_sync():
            # synchronous function to be run in a thread
            os.makedirs(parent_dir, exist_ok=True)

            if orchestrator.dry_run:
                log(f"[DRY RUN] Downloader: Would symlink {extra_folder} -> {primary_folder}", "debug")
                return

            if os.path.islink(extra_folder):
                os.unlink(extra_folder)  # remove old symlink
            elif os.path.exists(extra_folder):
                log(f"Downloader: Extra folder already exists and is not a symlink: {extra_folder}", "warning")
                return  # skip if folder already exists

            os.symlink(primary_folder, extra_folder)
            log(f"Downloader: Symlinked {extra_creator_safe} -> {primary_creator}", "info")

        # schedule as background thread-safe work (non-blocking to caller)
        # `asyncio.to_thread` returns an awaitable
        await async_runner.io_to_thread(_make_symlink_sync)

async def update_skipped_galleries(ReturnReport: bool, meta=None, Reason: str = "No Reason Given."):
    """
    Updates skipped galleries list (async).
    """
    global skipped_galleries
    fetch_env_vars() # Refresh env vars in case config changed.

    if ReturnReport:
        log_clarification()
        skipped_report = "\n".join(skipped_galleries)
        log(f"All Skipped Galleries:\n{skipped_report}")
    else:
        if not meta:
            log("Downloader: update_skipped_galleries called without meta while ReturnReport is False.", "warn")
            return

        gallery_id = meta.get("id", "Unknown")
        # async_runner.invoke() used because clean_title may be sync or async
        gallery_title = await async_runner.invoke(clean_title, meta)
        log_clarification("debug")
        skipped_galleries.append(f"Gallery: {gallery_id}: {Reason}")
        log(f"Downloader: Updated Skipped Galleries List: Gallery {gallery_id} ({gallery_title}): {Reason}", "debug")

def should_download_gallery(meta, gallery_title, num_pages, iteration: dict = None):
    """
    Decide whether to download a gallery or skip it.
    """
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if not meta:
        # schedule the skip update in background
        async_runner.spawn_task(update_skipped_galleries(False, meta, "Not Meta."))
        return False

    gallery_id = meta.get("id")
    doujin_folder = build_gallery_path(meta, iteration)

    if num_pages == 0:
        log(f"Downloader: Skipping Gallery: {gallery_id}\nReason: No Pages.\nTitle: {gallery_title}", "warn")
        log_clarification()
        async_runner.spawn_task(update_skipped_galleries(False, meta, "No Pages."))
        return False

    if not orchestrator.dry_run and os.path.exists(doujin_folder):
        all_exist = all(
            any(os.path.exists(os.path.join(doujin_folder, f"{i+1}.{ext}"))
                for ext in ("jpg", "png", "gif", "webp"))
            for i in range(num_pages)
        )
        if all_exist:
            log(f"Downloader: Skipping Gallery: {gallery_id}\nReason: Already Downloaded.\nTitle: {gallery_title}", "info")
            log_clarification()
            async_runner.spawn_task(update_skipped_galleries(False, meta, "Already downloaded."))
            return False

    excluded_gallery_tags = [tag.lower() for tag in orchestrator.excluded_tags_list]
    gallery_tags = [t.lower() for t in get_meta_tags(meta, "tag")]
    blocked_tags = []

    allowed_gallery_language = [lang.lower() for lang in orchestrator.language_list]
    gallery_langs = [l.lower() for l in get_meta_tags(meta, "language")]
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
        log(f"Downloader: Skipping Gallery: {gallery_id}\nReason: Blocked Tags.\nTitle: {gallery_title}", "info")
        log_clarification()
        async_runner.spawn_task(update_skipped_galleries(False, meta, f"Filtered tags: {blocked_tags}, languages: {blocked_langs}"))
        return False

    return True

####################################################################################################
# CORE
####################################################################################################
async def process_galleries(batch_ids, current_batch_number: int = 1, total_batch_numbers: int = 1):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    for gallery_id in batch_ids:
        extension_name = getattr(active_extension, "__name__", "skeleton")
        if not orchestrator.dry_run:
            await db.mark_gallery_started(gallery_id, download_location, extension_name)
        else:
            log(f"[DRY RUN] Would mark gallery {gallery_id} as started.", "info")

        gallery_attempts = 0

        while gallery_attempts < orchestrator.max_retries:
            gallery_attempts += 1
            try:
                # pre_gallery_download_hook is sync; async_runner.invoke() used
                await async_runner.invoke(active_extension.pre_gallery_download_hook, gallery_id)

                meta = await fetch_gallery_metadata(gallery_id)
                if not meta or not isinstance(meta, dict):
                    log(f"Downloader: Failed to fetch metadata for Gallery: {gallery_id}", "warn")
                    if not orchestrator.dry_run and gallery_attempts >= orchestrator.max_retries:
                        await db.mark_gallery_failed(gallery_id)
                    continue

                num_pages = len(meta.get("images", {}).get("pages", []))
                
                # during_gallery_download_hook is sync; async_runner.invoke() used
                await async_runner.invoke(active_extension.during_gallery_download_hook, gallery_id)
                
                # return_gallery_metas is sync; async_runner.invoke() used
                gallery_metas = await async_runner.invoke(active_extension.return_gallery_metas, meta)
                
                creators = gallery_metas["creator"]
                gallery_title = gallery_metas["title"]
                
                await dynamic_sleep(stage="gallery", batch_ids=batch_ids, attempt=gallery_attempts) # Sleep before starting gallery

                # --- Decide if gallery should be skipped ---
                skip_gallery = False
                for creator in creators:
                    iteration = {"creator": [creator]}
                    if not should_download_gallery(meta, gallery_title, num_pages, iteration):
                        skip_gallery = True
                        break

                if skip_gallery:
                    if not orchestrator.dry_run:
                        await db.mark_gallery_skipped(gallery_id)
                    else:
                        log(f"[DRY RUN] Would mark gallery {gallery_id} as skipped.", "info")
                    break
                
                # --- Prepare primary folder (first creator only) ---
                primary_creator = make_filesystem_safe(creators[0]) if creators else "Unknown"
                primary_folder = build_gallery_path(meta, {"creator": [creators[0]]})

                if not orchestrator.dry_run:
                    # create folder in thread, use async_runner.invoke() just in case
                    async_runner.invoke(os.makedirs, primary_folder, exist_ok=True)
                
                # --- Symlink all additional creators to the primary folder ---
                # Schedule symlinks in the background (non-blocking)
                async_runner.spawn_task(symlink_extra_creators(primary_folder, creators, meta))

                # --- Schedule download tasks (only once, for primary creator) ---
                tasks = []
                for i in range(num_pages):
                    page = i + 1
                    # fetch_image_urls is synchronous in API module -> use async_runner.invoke()
                    img_urls = async_runner.invoke(fetch_image_urls, meta, page)
                    
                    if not img_urls:
                        # schedule update_skipped_galleries in background
                        
                        async_runner.spawn_task(
                            update_skipped_galleries(False, meta, "Failed to get URLs."),
                            type="image"
                        )
                        continue

                    img_filename = f"{page}.{img_urls[0].split('.')[-1]}"
                    img_path = os.path.join(primary_folder, img_filename)
                    
                    # Use async_runner.invoke()
                    downloader_session = async_runner.invoke(get_session, status="rebuild") # Use fresh session

                    # Build coroutine wrapper that will call extension hook via async_runner.invoke()
                    async def _image_task(gid=gallery_id, pg=page, urls=img_urls, path=img_path, creator=primary_creator): 
                        # download_images_hook is sync; async_runner.invoke() used
                        return async_runner.invoke(active_extension.download_images_hook, gid, pg, urls, path, downloader_session, None, creator)

                    tasks.append(_image_task())

                # --- Download images (once, in primary creator's folder) ---
                if tasks:
                    # spawn each image task under the image semaphore
                    gather_tasks = [
                        async_runner.spawn_task(
                            t,
                            type="image"
                        )
                        for i, t in enumerate(tasks, start=1)
                    ]
                    await asyncio.gather(*gather_tasks)

                if not orchestrator.dry_run:
                    # after_completed_gallery_download_hook is sync; async_runner.invoke() used
                    await async_runner.invoke(active_extension.after_completed_gallery_download_hook, meta, gallery_id)
                    await db.mark_gallery_completed(gallery_id)

                log(f"Downloader: Completed Gallery: {gallery_id}", "info")
                break

            except Exception as e:
                log(f"Downloader: Error processing Gallery: {gallery_id}: {e}", "error")
                if not orchestrator.dry_run and gallery_attempts >= orchestrator.max_retries:
                    await db.mark_gallery_failed(gallery_id)

####################################################################################################
# MAIN
####################################################################################################
async def start_batch(batch_list=None, current_batch_number: int = 1, total_batch_numbers: int = 1):
    # active_extension.pre_run_hook() is called by extension_loader when extension is loaded.
    await load_extension(suppess_pre_run_hook=True)
    
    # pre_batch_hook is sync; async_runner.invoke() used
    await async_runner.invoke(active_extension.pre_batch_hook, batch_list)

    # Spawn Gallery Threads (Max of threads_galleries) (Use async_runner instead of direct gather)
    # Build tasks list and then use tqdm_asyncio.gather to monitor them
    
    # NOTE: YOU MUST USE async_runner.spawn_task(_async_func(*args), type="TYPE")
    # NOTE: type= 'gallery', 'image', or 'default' (generic task, if default, you don't need to specify type)
    gallery_tasks = [
        async_runner.spawn_task(
            process_galleries([gid], current_batch_number, total_batch_numbers),
            type="gallery"
        )
        for gid in batch_list
    ]
    await tqdm_asyncio.gather(*gallery_tasks, desc="Processing galleries")
    
    # post_batch_hook is sync; async_runner.invoke() used
    await async_runner.invoke(active_extension.post_batch_hook)

async def start_downloader(gallery_list=None):
    """
    This is one this module's entrypoints.
    """
    
    log_clarification("debug")
    log("Downloader: Ready.", "debug")
    log("Downloader: Debugging Started.", "debug")

    fetch_env_vars() # Refresh env vars in case config changed.
    start_time = asyncio.get_event_loop().time()
    time_estimate(f"Run", gallery_list)
    
    # active_extension.pre_run_hook() is called by extension_loader when extension is loaded.
    await load_extension(suppess_pre_run_hook=False)

    for batch_num in range(0, len(gallery_list), BATCH_SIZE):
        batch_list = gallery_list[batch_num:batch_num + BATCH_SIZE]
        
        # batch_num is the start index (0, BATCH_SIZE, 2*BATCH_SIZE, ...)
        current_batch_number = (batch_num // BATCH_SIZE) + 1
        
        # ceil division to compute total batches correctly
        total_batch_numbers = (len(gallery_list) + BATCH_SIZE - 1) // BATCH_SIZE
        
        current_out_of_total_batch_number = f"{current_batch_number} / {total_batch_numbers}"
        
        time_estimate(f"Batch {current_out_of_total_batch_number}", batch_list)
        
        log_clarification()
        log(f"Downloading Batch {current_out_of_total_batch_number} with {len(batch_list)} Galleries...", "info")
        
        await start_batch(batch_list, current_batch_number, total_batch_numbers)

        if batch_num + BATCH_SIZE < len(gallery_list):
            await dynamic_sleep(wait=orchestrator.batch_sleep_time, dynamic=False)

    # post_run_hook is sync; async_runner.invoke() used
    await async_runner.invoke(active_extension.post_run_hook)
    
    end_time = asyncio.get_event_loop().time()
    runtime = end_time - start_time
    
    # Convert seconds to h:m:s
    hours, rem = divmod(runtime, 3600)
    minutes, seconds = divmod(rem, 60)
    human_runtime = f"{int(hours)}h {int(minutes)}m {seconds:.2f}s" if hours else f"{int(minutes)}m {seconds:.2f}s" if minutes else f"{seconds:.2f}s"

    log(f"All ({len(gallery_list)}) Galleries Processed In {human_runtime}.")