#!/usr/bin/env python3
# nhscraper/downloader.py

import os, time, random, concurrent.futures
from tqdm import tqdm
from functools import partial

from nhscraper.core.config import logger, config, log_clarification
from nhscraper.core import db
from nhscraper.core.fetchers import session, fetch_gallery_metadata, fetch_image_url, get_meta_tags, safe_name, clean_title
from nhscraper.extensions.extension_loader import * # Import active extension

####################################################################################################
# Select extension (skeleton fallback)
####################################################################################################
active_extension = get_selected_extension()
log_clarification()
logger.debug(f"Using extension: {getattr(active_extension, '__name__', 'skeleton')}")
download_location = getattr(active_extension, "EXTENSION_DOWNLOAD_PATH", "/opt/nhentai-scraper/downloads")
if not config.get("DRY_RUN", False):
    os.makedirs(download_location, exist_ok=True) # Ensure the folder exists

####################################################################################################
# UTILITIES
####################################################################################################
gallery_threads = 2
image_threads = 10

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

def dynamic_sleep(stage):
    if stage=="gallery":
        num_galleries = max(1, len(config.get("GALLERIES", [])))
        total_load = config.get("THREADS_GALLERIES", 2) * config.get("THREADS_IMAGES", 10)
        base_min, base_max = (0.3, 0.5) if stage == "metadata" else (0.5, 1)
        scale = min(max(1, total_load * min(num_galleries, 1000)/1000), 5)
        sleep_time = random.uniform(base_min*scale, base_max*scale)
        log_clarification()
        logger.debug(f"{stage.capitalize()} sleep: {sleep_time:.2f}s (scale {scale:.1f})")
        time.sleep(sleep_time)

def should_download_gallery(meta, num_pages):
    """
    Decide whether to download a gallery based on:
      - language requirements (must include requested language or "translated")
      - excluded tags (any tag in EXCLUDED_TAGS skips gallery)
      - existing files (skip if all pages exist)
    """
    if not meta:
        return False

    dry_run = config.get("DRY_RUN", False)
    gallery_id = meta.get("id")
    doujin_folder = build_gallery_path(meta)

    # 0 pages, skip
    if num_pages == 0:
        logger.warning(f"Gallery: {gallery_id}: No pages, skipping")
        return False

    # Skip if already fully downloaded
    if not dry_run and os.path.exists(doujin_folder):
        all_exist = all(
            any(os.path.exists(os.path.join(doujin_folder, f"{i+1}.{ext}"))
                for ext in ("jpg", "png", "gif", "webp"))
            for i in range(num_pages)
        )
        if all_exist:
            logger.info(f"Skipping {gallery_id} ({doujin_folder}), already complete.")
            return False

    # Check excluded tags
    excluded_tags = config.get("EXCLUDED_TAGS", [])
    if excluded_tags:
        gallery_tags = [t.lower() for t in get_meta_tags(meta, "tag")]
        if any(tag.lower() in gallery_tags for tag in excluded_tags):
            logger.info(f"Skipping Gallery: {gallery_id}: Filtered tags ({gallery_tags})")
            return False
    
    # Check language requirement
    allowed_langs = config.get("LANGUAGE", [])
    if allowed_langs:
        gallery_langs = get_meta_tags(meta, "language")
        gallery_langs_lower = [l.lower() for l in gallery_langs]
        allowed_lower = [l.lower() for l in allowed_langs]
        # Include 'translated' as acceptable if any requested language is present
        if not any(lang in gallery_langs_lower or "translated" in gallery_langs_lower for lang in allowed_lower):
            logger.info(f"Skipping Gallery: {gallery_id}: Filtered language ({gallery_langs})")
            return False

    return True


def process_galleries(gallery_ids):
    for gallery_id in gallery_ids:
        extension_name = getattr(active_extension, "__name__", "skeleton")
        db.mark_gallery_started(gallery_id, download_location, extension_name)

        gallery_attempts = 0
        max_gallery_attempts = config.get("MAX_RETRIES", 3)

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
                if not should_download_gallery(meta, num_pages):
                    logger.info("TEST: RETURNED FALSE?")
                    db.mark_gallery_completed(gallery_id)
                    active_extension.after_gallery_download_hook(meta)
                    break

                gallery_failed = False
                active_extension.during_gallery_download_hook(config, gallery_id, meta)

                artists = get_meta_tags(meta, "artist") or ["Unknown Artist"]
                gallery_title = clean_title(meta)

                grouped_tasks = []
                for artist in artists:
                    safe_artist = safe_name(artist)
                    doujin_folder = os.path.join(download_location, safe_artist, gallery_title)
                    if not config.get("DRY_RUN", False):
                        os.makedirs(doujin_folder, exist_ok=True)

                    artist_tasks = []
                    for i in range(num_pages):
                        page = i + 1
                        img_url = fetch_image_url(meta, page)
                        if not img_url:
                            logger.warning(f"Skipping Page {page} for artist {artist}: Failed to get URL")
                            gallery_failed = True
                            continue

                        img_filename = f"{page}.{img_url.split('.')[-1]}"
                        img_path = os.path.join(doujin_folder, img_filename)
                        artist_tasks.append((page, img_url, img_path, safe_artist))

                    if artist_tasks:
                        grouped_tasks.append((safe_artist, artist_tasks))

                total_images = sum(len(t[1]) for t in grouped_tasks)
                with concurrent.futures.ThreadPoolExecutor(max_workers=config["THREADS_IMAGES"]) as executor:
                    if config.get("DRY_RUN", False):
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
                                logger.info(f"Would download {img_url} -> {img_path}")
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
                                logger.info(f"Downloaded {img_url} -> {img_path}")
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

    gallery_ids = config.get("GALLERIES", [])
    active_extension.pre_run_hook(config, gallery_ids)

    if not gallery_ids:
        logger.error("No galleries specified. Use --galleries or --range.")
        return
    
    log_clarification()
    logger.info(f"Galleries to process: {gallery_ids[0]} -> {gallery_ids[-1]}" 
                if len(gallery_ids) > 1 else f"Galleries to process: {gallery_ids[0]}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.get("threads_galleries", 4)) as executor:
        #futures = [executor.submit(process_gallery, gid) for gid in gallery_ids]
        futures = [executor.submit(process_galleries, gallery_ids)]
        concurrent.futures.wait(futures)

    log_clarification()
    logger.info("All galleries processed")
    
    active_extension.after_all_galleries_download_hook(gallery_ids)
    active_extension.post_run_hook(config, gallery_ids)