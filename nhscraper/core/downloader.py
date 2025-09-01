#!/usr/bin/env python3
# nhscraper/downloader.py

import os, time, random, concurrent.futures
from tqdm import tqdm
from functools import partial

from nhscraper.core.config import *
from nhscraper.core import db
from nhscraper.core.fetchers import session, fetch_gallery_metadata, fetch_image_url, get_meta_tags, safe_name, clean_title
from nhscraper.extensions.extension_loader import get_selected_extension # Import active extension

####################################################################################################
# Global Variables
####################################################################################################
active_extension = "skeleton"
download_location = ""
gallery_threads = 2
image_threads = 10
meta = None
skipped_galleries = []

####################################################################################################
# Select extension (skeleton fallback)
####################################################################################################
def load_extension():
    global active_extension
    global download_location

    active_extension = get_selected_extension()
    log_clarification()
    logger.info(f"Using extension: {getattr(active_extension, '__name__', 'skeleton')}")

    download_location = config.get("DOWNLOAD_PATH", DEFAULT_DOWNLOAD_PATH) # Updated by loaded extension via extension_loader
    if not config.get("DRY_RUN", DEFAULT_DRY_RUN):
        os.makedirs(download_location, exist_ok=True) # Ensure the folder exists
    log_clarification()
    logger.info(f"Using download path: {download_location}")

####################################################################################################
# UTILITIES
####################################################################################################
def build_gallery_path(meta):
    # Ask extension for variables
    subs = active_extension.build_gallery_subfolders(meta)

    # Load template from extension (SUB1/SUB2/etc.)
    template = getattr(active_extension, "SUBFOLDER_STRUCTURE", ["artist", "title"])

    # Start with download base
    path_parts = [download_location]

    # Append resolved variables
    for key in template:
        value = subs.get(key, "Unknown")
        path_parts.append(safe_name(value))

    return os.path.join(*path_parts)

def dynamic_sleep(stage): # TEST
    if stage=="gallery":
        num_galleries = max(1, len(config.get("GALLERIES", DEFAULT_GALLERIES)))
        total_load = config.get("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES) * config.get("THREADS_IMAGES", DEFAULT_THREADS_IMAGES)
        base_min, base_max = (0.3, 0.5) if stage == "metadata" else (0.5, 1)
        scale = min(max(1, total_load * min(num_galleries, 1000)/1000), 5)
        sleep_time = random.uniform(base_min*scale, base_max*scale)
        log_clarification()
        logger.debug(f"{stage.capitalize()} sleep: {sleep_time:.2f}s (scale {scale:.1f})")
        time.sleep(sleep_time)
        
def update_skipped_galleries(Reason: str = "No Reason Given.", ReturnReport: bool = False):
    global skipped_galleries
    
    gallery_id = meta.get("id")

    if ReturnReport == False:
        log_clarification()
        skipped_galleries.append(f"{gallery_id}: {Reason}")
        logger.debug(f"Updated Skipped Galleries List with '{gallery_id}: {Reason}'")
    else:
        log_clarification()
        logger.debug(f"All Skipped Galleries: {skipped_galleries}")

def should_download_gallery(meta, gallery_title, num_pages):
    """
    Decide whether to download a gallery based on:
      - language requirements (must include requested language or "translated")
      - excluded tags (any tag in EXCLUDED_TAGS skips gallery)
      - existing files (skip if all pages exist)
    """
    if not meta:
        update_skipped_galleries("Not Meta.", False)
        return False

    dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)
    gallery_id = meta.get("id")
    doujin_folder = build_gallery_path(meta)

    # Skip if gallery has 0 pages
    if num_pages == 0:
        log_clarification()
        logger.warning(
            f"Skipping Gallery: {gallery_id}:\n"
            f"Title: {gallery_title}\n"
            "Reason: No pages."
        )
        update_skipped_galleries("No Pages.", False)
        return False

    # Skip if gallery already fully downloaded
    if not dry_run and os.path.exists(doujin_folder):
        all_exist = all(
            any(os.path.exists(os.path.join(doujin_folder, f"{i+1}.{ext}"))
                for ext in ("jpg", "png", "gif", "webp"))
            for i in range(num_pages)
        )
        if all_exist:
            log_clarification()
            logger.info(
                f"Skipping Gallery: {gallery_id}:\n"
                f"Title: {gallery_title}\n"
                f"Folder: {doujin_folder}\n"
                "Reason: Already complete."
            )
            update_skipped_galleries("Already complete.", False)
            return False

    # Skip if gallery has excluded tags or doesn't meet language requirements
    excluded_tags = [t.lower() for t in config.get("EXCLUDED_TAGS", DEFAULT_EXCLUDED_TAGS)]
    gallery_tags = [t.lower() for t in get_meta_tags(meta, "tag")]
    blocked_tags = []
    
    allowed_langs = [l.lower() for l in config.get("LANGUAGE", DEFAULT_LANGUAGE)]
    gallery_langs = [l.lower() for l in get_meta_tags(meta, "language")]
    blocked_langs = []

    # Check tags
    for tag in gallery_tags:
        if tag in excluded_tags:
            blocked_tags.append(tag)

    # Check languages
    if allowed_langs:
        has_allowed = any(lang in allowed_langs for lang in gallery_langs)
        has_translated = "translated" in gallery_langs and has_allowed

        if not (has_allowed or has_translated):
            blocked_langs = gallery_langs[:]  # keep full list for logging

    # Final decision
    if blocked_tags or blocked_langs:
        log_clarification()
        logger.info(
            f"Skipping Gallery: {gallery_id}:\n"
            f"Title: {gallery_title}\n"
            f"Filtered tags: {blocked_tags}\n"
            f"Filtered languages: {blocked_langs}"
        )
        update_skipped_galleries("Contains either filtered tags or filtered languages (see logs).", False)
        return False

    return True

def process_galleries(gallery_ids):
    global meta
    
    for gallery_id in gallery_ids:
        extension_name = getattr(active_extension, "__name__", "skeleton")
        db.mark_gallery_started(gallery_id, download_location, extension_name)

        gallery_attempts = 0
        max_gallery_attempts = config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES)

        while gallery_attempts < max_gallery_attempts:
            gallery_attempts += 1
            try:
                log_clarification()
                logger.info(f"Starting Gallery: {gallery_id}: (Attempt {gallery_attempts}/{max_gallery_attempts})")
                dynamic_sleep("gallery")

                meta = fetch_gallery_metadata(gallery_id)
                if not meta or not isinstance(meta, dict):
                    logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id}")
                    if gallery_attempts >= max_gallery_attempts:
                        db.mark_gallery_failed(gallery_id)
                    continue

                num_pages = len(meta.get("images", {}).get("pages", []))
                gallery_failed = False
                active_extension.during_gallery_download_hook(config, gallery_id, meta)

                artists = get_meta_tags(meta, "artist") or ["Unknown Artist"]
                gallery_title = clean_title(meta)

                grouped_tasks = []
                for artist in artists:
                    safe_artist = safe_name(artist)
                    doujin_folder = os.path.join(download_location, safe_artist, gallery_title)
                    if not config.get("DRY_RUN", DEFAULT_DRY_RUN):
                        os.makedirs(doujin_folder, exist_ok=True)

                    artist_tasks = []
                    for i in range(num_pages):
                        page = i + 1
                        img_url = fetch_image_url(meta, page)
                        if not img_url:
                            logger.warning(f"Skipping Page {page} for artist {artist}: Failed to get URL")
                            update_skipped_galleries("Failed to get URL.", False)
                            gallery_failed = True
                            continue

                        img_filename = f"{page}.{img_url.split('.')[-1]}"
                        img_path = os.path.join(doujin_folder, img_filename)
                        artist_tasks.append((page, img_url, img_path, safe_artist))

                    if artist_tasks:
                        grouped_tasks.append((safe_artist, artist_tasks))
                
                if not should_download_gallery(meta, gallery_title, num_pages):
                    db.mark_gallery_completed(gallery_id)
                    active_extension.after_gallery_download_hook(meta)
                    break

                total_images = sum(len(t[1]) for t in grouped_tasks)
                with concurrent.futures.ThreadPoolExecutor(max_workers=config["THREADS_IMAGES"]) as executor:
                    if config.get("DRY_RUN", DEFAULT_DRY_RUN):
                        with tqdm(total=total_images, desc=f"[DRY-RUN] Gallery: {gallery_id}", unit="img", position=0, leave=True) as pbar:
                            for safe_artist, artist_tasks in grouped_tasks:
                                pbar.set_postfix_str(f"Artist: {safe_artist}")
                                futures = [
                                    executor.submit(
                                        active_extension.download_images_hook,
                                        gallery_id, page, url, path, session, pbar, safe_artist
                                    )
                                    for page, url, path, _ in artist_tasks
                                ]
                                for _ in concurrent.futures.as_completed(futures):
                                    pbar.update(1)
                    else:
                        with tqdm(total=total_images, desc=f"Gallery: {gallery_id}", unit="img", position=0, leave=True) as pbar:
                            for safe_artist, artist_tasks in grouped_tasks:
                                pbar.set_postfix_str(f"Artist: {safe_artist}")
                                futures = [
                                    executor.submit(
                                        active_extension.download_images_hook,
                                        gallery_id, page, url, path, session, pbar, safe_artist
                                    )
                                    for page, url, path, _ in artist_tasks
                                ]
                                for _ in concurrent.futures.as_completed(futures):
                                    pbar.update(1)

                if gallery_failed:
                    logger.warning(f"Gallery: {gallery_id}: Encountered download issues, retrying...")
                    continue

                active_extension.after_gallery_download_hook(meta)
                db.mark_gallery_completed(gallery_id)
                logger.info(f"Completed Gallery: {gallery_id}")
                log_clarification()
                break

            except Exception as e:
                logger.error(f"Error processing Gallery: {gallery_id}: {e}")
                if gallery_attempts >= max_gallery_attempts:
                    db.mark_gallery_failed(gallery_id)

####################################################################################################
# MAIN
####################################################################################################
def start_downloader():
    log_clarification()
    logger.info("Downloader: Ready.")
    logger.debug("Downloader: Debugging Started.")
    
    log_clarification()
    load_extension() # Load extension variables, etc

    gallery_ids = config.get("GALLERIES", DEFAULT_GALLERIES)
    active_extension.pre_run_hook(config, gallery_ids)

    if not gallery_ids:
        logger.error("No galleries specified. Use --galleries or --range.")
        return
    
    log_clarification()
    logger.info(f"Galleries to process: {gallery_ids[0]} -> {gallery_ids[-1]}" 
                if len(gallery_ids) > 1 else f"Galleries to process: {gallery_ids[0]}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.get("THREADS_GALLERIES", DEFAULT_THREADS_GALLERIES)) as executor:
        #futures = [executor.submit(process_gallery, gid) for gid in gallery_ids]
        futures = [executor.submit(process_galleries, gallery_ids)]
        concurrent.futures.wait(futures)

    log_clarification()
    logger.info("All galleries processed")
    update_skipped_galleries("", True)
    
    active_extension.post_run_hook(config, gallery_ids)