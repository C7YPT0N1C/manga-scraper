#!/usr/bin/env python3
# nhscraper/downloader.py

import os, time, random, concurrent.futures
from tqdm import tqdm
from functools import partial

from nhscraper.core.config import logger, config, log_clarification
from nhscraper.core import db
from nhscraper.core.fetchers import session, fetch_gallery_metadata, fetch_image_url, get_meta_tag_names, safe_name, clean_title
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

def dynamic_sleep(stage="gallery"):
    num_galleries = max(1, len(config.get("GALLERIES", [])))
    total_load = config.get("THREADS_GALLERIES", 4) * config.get("THREADS_IMAGES", 4)
    base_min, base_max = (0.3, 0.5) if stage == "metadata" else (0.5, 1)
    scale = min(max(1, total_load * min(num_galleries, 1000)/1000), 5)
    sleep_time = random.uniform(base_min*scale, base_max*scale)
    log_clarification()
    logger.debug(f"{stage.capitalize()} sleep: {sleep_time:.2f}s (scale {scale:.1f})")
    time.sleep(sleep_time)

def should_download_gallery(meta, num_pages):
    if not meta:
        return False

    dry_run = config.get("DRY_RUN", False)

    if num_pages == 0:
        logger.warning(f"Gallery {meta.get('id')} has no pages, skipping")
        return False

    doujin_folder = build_gallery_path(meta)

    # Skip existence check if dry-run
    if not dry_run and os.path.exists(doujin_folder):
        all_exist = all(
            any(os.path.exists(os.path.join(doujin_folder, f"{i+1}.{ext}"))
                for ext in ("jpg", "png", "gif", "webp"))
            for i in range(num_pages)
        )
        if all_exist:
            logger.info(f"Skipping {meta['id']} ({doujin_folder}), already complete.")
            return False

    return True

def process_galleries(gallery_ids):
    """
    Process multiple galleries with retries, database logging, and a unified progress bar.
    Downloads images into [DOWNLOAD_LOCATION]/[ARTIST]/[GALLERY_TITLE]/ folders.
    """
    extension_name = getattr(active_extension, "__name__", "skeleton")
    
    log_clarification()
    logger.info(f"Active Download Location: {download_location}")

    gallery_attempts_dict = {gid: 0 for gid in gallery_ids}
    max_gallery_attempts = config.get("MAX_RETRIES", 3)

    # First pass: fetch metadata and compute total images
    galleries_meta = {}
    total_images = 0
    for gallery_id in gallery_ids:
        gallery_attempts_dict[gallery_id] += 1
        try:
            meta = fetch_gallery_metadata(gallery_id)
            if not meta or not isinstance(meta, dict):
                logger.warning(f"Failed to fetch metadata for Gallery {gallery_id}")
                continue

            num_pages = len(meta.get("images", {}).get("pages", []))
            if num_pages == 0:
                logger.warning(f"Gallery {gallery_id} has no pages, skipping")
                db.mark_gallery_failed(gallery_id)
                continue

            artists = get_meta_tag_names(meta, "artist") or ["Unknown Artist"]
            total_images += num_pages * len(artists)
            galleries_meta[gallery_id] = meta

        except Exception as e:
            logger.error(f"Error fetching metadata for Gallery {gallery_id}: {e}")
            continue

    if not galleries_meta:
        logger.warning("No galleries to process.")
        return

    logger.info(f"Total images to download across all galleries: {total_images}")

    # Unified progress bar
    with tqdm(total=total_images, desc="All Galleries", unit="img", leave=True) as pbar:
        with concurrent.futures.ThreadPoolExecutor(max_workers=config["THREADS_IMAGES"]) as executor:
            futures = []

            for gallery_id, meta in galleries_meta.items():
                db.mark_gallery_started(gallery_id, download_location, extension_name)
                active_extension.during_gallery_download_hook(config, gallery_id, meta)

                gallery_failed = False
                gallery_title = clean_title(meta)
                artists = get_meta_tag_names(meta, "artist") or ["Unknown Artist"]
                num_pages = len(meta.get("images", {}).get("pages", []))

                for artist in artists:
                    safe_artist = safe_name(artist)
                    doujin_folder = os.path.join(download_location, safe_artist, gallery_title)
                    if not config.get("DRY_RUN", False):
                        os.makedirs(doujin_folder, exist_ok=True)

                    for i in range(num_pages):
                        page = i + 1
                        img_url = fetch_image_url(meta, page)
                        if not img_url:
                            logger.warning(f"Skipping Page {page} for artist {artist}: Failed to get URL")
                            gallery_failed = True
                            continue

                        img_filename = f"{page}.{img_url.split('.')[-1]}"
                        img_path = os.path.join(doujin_folder, img_filename)

                        futures.append(
                            executor.submit(
                                active_extension.download_images_hook,
                                gallery_id, page, img_url, img_path, session, pbar, safe_artist
                            )
                        )

                # Wait for images of this gallery to complete
                for _ in concurrent.futures.as_completed(futures):
                    pbar.update(1)

                if gallery_failed:
                    logger.warning(f"Gallery {gallery_id} encountered download issues.")
                    continue

                active_extension.after_gallery_download_hook(meta)
                db.mark_gallery_completed(gallery_id)
                logger.info(f"Completed Gallery {gallery_id}")

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