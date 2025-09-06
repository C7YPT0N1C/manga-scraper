#!/usr/bin/env python3
# nhscraper/downloader.py

import os, time, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from tqdm.contrib.concurrent import thread_map

from nhscraper.core.config import *
from nhscraper.core import database as db
from nhscraper.core.api import (
    session, dynamic_sleep, fetch_gallery_metadata,
    fetch_image_urls, get_meta_tags, safe_name, clean_title
)
from nhscraper.extensions.extension_loader import get_selected_extension  # Import active extension

####################################################################################################
# Global Variables
####################################################################################################
active_extension = "skeleton"
download_location = ""
gallery_threads = 2
image_threads = 10
skipped_galleries = []

####################################################################################################
# Select extension (skeleton fallback)
####################################################################################################
def load_extension():
    global active_extension
    global download_location

    ext_name = config.get("EXTENSION", "skeleton")
    active_extension = get_selected_extension(ext_name)
    logger.info(f"Using extension: {getattr(active_extension, '__name__', 'skeleton')}")

    # Prefer extension-specific download path, fallback to config/global default
    download_location = getattr(active_extension, "DEDICATED_DOWNLOAD_PATH", None) \
                        or config.get("DOWNLOAD_PATH", DEFAULT_DOWNLOAD_PATH)

    if not config.get("DRY_RUN", DEFAULT_DRY_RUN):
        os.makedirs(download_location, exist_ok=True)
    else:
        logger.info(f"[DRY-RUN] Skipping creation of: {download_location}")

    logger.info(f"Using download path: {download_location}")

####################################################################################################
# UTILITIES
####################################################################################################
def build_gallery_path(meta, iteration: dict = None):
    """Build the folder path for a gallery based on SUBFOLDER_STRUCTURE."""
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
            logger.warning("update_skipped_galleries called without meta while ReturnReport is False.")
            return

        gallery_id = meta.get("id", "Unknown")
        gallery_title = clean_title(meta)
        log_clarification()
        skipped_galleries.append(f"Gallery {gallery_id}: {Reason}")
        log(f"Updated Skipped Galleries List: Gallery {gallery_id} ({gallery_title}): {Reason}'")

def should_download_gallery(meta, gallery_title, num_pages, iteration: dict = None):
    """Decide whether to download a gallery or skip it."""
    if not meta:
        update_skipped_galleries(False, meta, "Not Meta.")
        return False

    dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)
    gallery_id = meta.get("id")
    doujin_folder = build_gallery_path(meta, iteration)

    if num_pages == 0:
        log_clarification()
        logger.warning(f"Skipping Gallery: {gallery_id} (Reason: No pages)")
        update_skipped_galleries(False, meta, "No Pages.")
        return False

    if not dry_run and os.path.exists(doujin_folder):
        all_exist = all(
            any(os.path.exists(os.path.join(doujin_folder, f"{i+1}.{ext}"))
                for ext in ("jpg", "png", "gif", "webp"))
            for i in range(num_pages)
        )
        if all_exist:
            log_clarification()
            logger.info(
                f"Skipping Gallery: {gallery_id} (Reason: Already complete)\n"
                f"Title: {gallery_title}\nFolder: {doujin_folder}"
            )
            update_skipped_galleries(False, meta, "Already downloaded.")
            return False

    excluded_tags = [t.lower() for t in config.get("EXCLUDED_TAGS", DEFAULT_EXCLUDED_TAGS)]
    gallery_tags = [t.lower() for t in get_meta_tags("Should_Download_Gallery", meta, "tag")]
    blocked_tags = []

    allowed_langs = [l.lower() for l in config.get("LANGUAGE", DEFAULT_LANGUAGE)]
    gallery_langs = [l.lower() for l in get_meta_tags("Should_Download_Gallery", meta, "language")]
    blocked_langs = []

    log_clarification()
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
            f"Skipping Gallery: {gallery_id}\n"
            f"Title: {gallery_title}\n"
            f"Filtered tags: {blocked_tags}\n"
            f"Filtered languages: {blocked_langs}"
        )
        update_skipped_galleries(False, meta, f"Filtered tags: {blocked_tags}, languages: {blocked_langs}")
        return False

    return True

def submit_creator_tasks(executor, creator_tasks, gallery_id, session, pbar, safe_creator_name):
    futures = [
        executor.submit(
            active_extension.download_images_hook,
            gallery_id, page, urls, path, session, pbar, safe_creator_name
        )
        for page, urls, path, _ in creator_tasks
    ]
    for _ in as_completed(futures):
        pbar.update(1)

####################################################################################################
# CORE
####################################################################################################
def process_galleries(gallery_ids):
    """Process one or more galleries (usually just one)."""
    dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)

    for gallery_id in gallery_ids:
        extension_name = getattr(active_extension, "__name__", "skeleton")
        prefix = f"[Gallery {gallery_id}] "  # prefix for logs

        if not dry_run:
            db.mark_gallery_started(gallery_id, download_location, extension_name)
        else:
            logger.info(f"{prefix}[DRY-RUN] Would mark gallery as started.")

        gallery_attempts = 0
        max_gallery_attempts = config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES)

        while gallery_attempts < max_gallery_attempts:
            gallery_attempts += 1
            try:
                log_clarification()
                active_extension.pre_gallery_download_hook(gallery_id)
                logger.info(f"{prefix}Starting (Attempt {gallery_attempts}/{max_gallery_attempts})")
                time.sleep(dynamic_sleep("gallery"))

                meta = fetch_gallery_metadata(gallery_id)
                if not meta or not isinstance(meta, dict):
                    logger.warning(f"{prefix}Failed to fetch metadata.")
                    if not dry_run and gallery_attempts >= max_gallery_attempts:
                        db.mark_gallery_failed(gallery_id)
                    continue

                num_pages = len(meta.get("images", {}).get("pages", []))
                gallery_failed = False
                active_extension.during_gallery_download_hook(gallery_id)

                gallery_metas = active_extension.return_gallery_metas(meta)
                creators = gallery_metas["creator"]
                gallery_title = gallery_metas["title"]

                grouped_tasks = []
                for creator in creators:
                    iteration = {"creator": [creator]}
                    safe_creator_name = safe_name(creator)

                    doujin_folder = build_gallery_path(meta, iteration)
                    if dry_run:
                        log(f"{prefix}[DRY-RUN] Would create folder for {creator}: {doujin_folder}")
                    else:
                        os.makedirs(doujin_folder, exist_ok=True)

                    creator_tasks = []
                    for i in range(num_pages):
                        page = i + 1
                        img_urls = fetch_image_urls(meta, page)
                        if not img_urls:
                            logger.warning(f"{prefix}Skipping Page {page} for {creator}: Failed to get URLs")
                            update_skipped_galleries(False, meta, "Failed to get URLs.")
                            gallery_failed = True
                            continue

                        img_filename = f"{page}.{img_urls[0].split('.')[-1]}"
                        img_path = os.path.join(doujin_folder, img_filename)
                        creator_tasks.append((page, img_urls, img_path, safe_creator_name))

                    if creator_tasks:
                        grouped_tasks.append((safe_creator_name, creator_tasks))

                    log_clarification()

                if not should_download_gallery(meta, gallery_title, num_pages, iteration):
                    if not dry_run:
                        db.mark_gallery_skipped(gallery_id)
                    else:
                        logger.info(f"{prefix}[DRY-RUN] Would mark as skipped.")
                    break
                else:
                    total_images = sum(len(t[1]) for t in grouped_tasks)
                    with ThreadPoolExecutor(max_workers=config["THREADS_IMAGES"]) as executor:
                        desc = f"{'[DRY-RUN] ' if dry_run else ''}Gallery: {gallery_id}"
                        with tqdm(total=total_images, desc=desc, unit="img", leave=True) as pbar:
                            for safe_creator_name, creator_tasks in grouped_tasks:
                                pbar.set_postfix_str(f"Creator: {safe_creator_name}")
                                if not dry_run:
                                    submit_creator_tasks(executor, creator_tasks, gallery_id, session, pbar, safe_creator_name)
                                else:
                                    for _ in creator_tasks:
                                        time.sleep(0.01)  # fake progress
                                        pbar.update(1)

                    if gallery_failed:
                        logger.warning(f"{prefix}Encountered issues, retrying...")
                        continue

                    if not dry_run:
                        active_extension.after_completed_gallery_download_hook(meta, gallery_id)
                        db.mark_gallery_completed(gallery_id)
                    logger.info(f"{prefix}Completed.")
                    log_clarification()
                    break

            except Exception as e:
                logger.error(f"{prefix}Error: {e}")
                if not dry_run and gallery_attempts >= max_gallery_attempts:
                    db.mark_gallery_failed(gallery_id)

####################################################################################################
# MAIN
####################################################################################################
def start_downloader(gallery_list=None):
    dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)

    log_clarification()
    logger.info("Downloader: Ready.")
    log("Downloader: Debugging Started.")
    load_extension()

    gallery_ids = gallery_list or config.get("GALLERIES", DEFAULT_GALLERIES)
    active_extension.pre_run_hook(gallery_ids)

    if not gallery_ids:
        logger.error("No galleries specified. Use --galleries or --range.")
        return

    log_clarification()
    logger.info(f"Galleries to process: {gallery_ids[0]} -> {gallery_ids[-1]}" 
                if len(gallery_ids) > 1 else f"Galleries to process: {gallery_ids[0]}")

    # ✅ Use thread_map for global gallery bar
    thread_map(
        lambda gid: process_galleries([gid]),
        gallery_ids,
        max_workers=config.get("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES),
        desc="Processing galleries",
        unit="gallery"
    )

    log_clarification()
    logger.info("All galleries processed.")
    update_skipped_galleries(True)

    if not dry_run:
        active_extension.post_run_hook()
    else:
        logger.info("[DRY-RUN] Would call post_run_hook()")