#!/usr/bin/env python3
# nhscraper/downloader.py

import os, time, random, concurrent.futures
from tqdm import tqdm

from nhscraper.core.config import logger, config, log_clarification
from nhscraper.core import db
from nhscraper.core.fetchers import build_session, session, fetch_gallery_metadata, fetch_image_url
from nhscraper.extensions.extension_loader import * # Import active extension

# ------------------------------
# Select extension (skeleton fallback)
# ------------------------------
active_extension = get_selected_extension()
log_clarification()
logger.debug(f"Using extension: {getattr(active_extension, '__name__', 'skeleton')}")
download_location = getattr(active_extension, "EXTENSION_DOWNLOAD_PATH", "/opt/nhentai-scraper/downloads")
if not config.get("DRY_RUN", False):
    os.makedirs(download_location, exist_ok=True) # Ensure the folder exists

####################################################################################################
# UTILITIES
####################################################################################################
def get_tag_names(meta, tag_type):
    """
    Extracts all tag names of a given type (artist, group, tag, parody, etc.) from meta['tags'].
    Returns ['Unknown'] if none found.
    """
    if not meta or "tags" not in meta:
        return ["Unknown"]
    names = [t["name"] for t in meta["tags"] if t.get("type") == tag_type and t.get("name")]
    return names or ["Unknown"]

def safe_name(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def clean_title(meta):
    title_obj = meta.get("title", {}) or {}
    title_type = config.get("TITLE_TYPE", "pretty").lower()
    title = title_obj.get(title_type) or title_obj.get("english") or title_obj.get("japanese") or title_obj.get("pretty") or f"Gallery_{meta.get('id')}"
    if "|" in title: title = title.split("|")[-1].strip()
    import re
    title = re.sub(r'(\s*\[.*?\]\s*)+$', '', title.strip())
    return safe_name(title)

def dynamic_sleep(stage="gallery"):
    num_galleries = max(1, len(config.get("GALLERIES", [])))
    total_load = config.get("THREADS_GALLERIES", 1) * config.get("THREADS_IMAGES", 4)
    base_min, base_max = (0.3, 0.5) if stage == "metadata" else (0.5, 1)
    scale = min(max(1, total_load * min(num_galleries, 1000)/1000), 5)
    sleep_time = random.uniform(base_min*scale, base_max*scale)
    log_clarification()
    logger.debug(f"{stage.capitalize()} sleep: {sleep_time:.2f}s (scale {scale:.1f})")
    time.sleep(sleep_time)

def should_download_gallery(meta):
    if not meta:
        return False

    dry_run = config.get("DRY_RUN", False)
    gallery_title = clean_title(meta)
    num_pages = len(meta.get("images", {}).get("pages", []))

    if num_pages == 0:
        logger.warning(f"Gallery {meta.get('id')} has no pages, skipping")
        return False

    for artist in get_tag_names(meta, "artist") or ["Unknown Artist"]:
        artist_folder = os.path.join(download_location, safe_name(artist))
        doujin_folder = os.path.join(artist_folder, gallery_title)

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

def download_image(gallery, page, url, path, session, retries=None):
    """
    Downloads an image from URL to the given path.
    Respects DRY_RUN and retries up to config['MAX_RETRIES'].
    """
    import requests

    if not url:
        logger.warning(f"Gallery {gallery}: Page {page}: No URL, skipping")
        return False

    if retries is None:
        retries = config.get("MAX_RETRIES", 3)

    if os.path.exists(path):
        logger.debug(f"Already exists, skipping: {path}")
        return True

    if config.get("DRY_RUN", False):
        log_clarification()
        logger.info(f"[DRY-RUN] Would download {url} -> {path}")
        return True

    # Ensure session is a requests.Session
    if not isinstance(session, requests.Session):
        session = requests.Session()

    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=30, stream=True)
            if r.status_code == 429:
                wait = 2 ** attempt
                log_clarification()
                logger.warning(f"429 rate limit hit for {url}, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()

            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            log_clarification()
            logger.debug(f"Downloaded Gallery {gallery}: Page {page} -> {path}")
            return True

        except Exception as e:
            wait = 2 ** attempt
            log_clarification()
            logger.warning(f"Attempt {attempt} failed for {url}: {e}, retrying in {wait}s")
            time.sleep(wait)

    log_clarification()
    logger.error(f"Gallery {gallery}: Page {page}: Failed to download after {retries} attempts: {url}")
    return False

####################################################################################################
# GALLERY PROCESSING
####################################################################################################
def process_gallery(gallery_id):
    extension_name = getattr(active_extension, "__name__", "skeleton")
    
    log_clarification()
    logger.info(f"Active Download Location: {download_location}")
    
    db.mark_gallery_started(gallery_id, download_location, extension_name)

    gallery_attempts = 0
    max_gallery_attempts = config.get("MAX_RETRIES", 3)

    while gallery_attempts < max_gallery_attempts:
        gallery_attempts += 1
        try:
            log_clarification()
            logger.info(f"Starting Gallery {gallery_id} (Attempt {gallery_attempts}/{max_gallery_attempts})")
            dynamic_sleep("gallery")

            meta = fetch_gallery_metadata(gallery_id)
            if not meta:
                logger.warning(f"Failed to fetch metadata for Gallery {gallery_id}")
                if gallery_attempts >= max_gallery_attempts:
                    db.mark_gallery_failed(gallery_id)
                continue

            if not should_download_gallery(meta):
                logger.info(f"Skipping Gallery {gallery_id}, already downloaded")
                db.mark_gallery_completed(gallery_id)
                active_extension.after_gallery_download(meta)
                return

            gallery_failed = False

            active_extension.during_download_hook(config, gallery_id, meta)

            artists = get_tag_names(meta, "artist") or ["Unknown Artist"]
            gallery_title = clean_title(meta)

            for artist in artists:
                artist_folder = os.path.join(download_location, safe_name(artist))
                doujin_folder = os.path.join(artist_folder, gallery_title)
                if not config.get("DRY_RUN", False):
                    os.makedirs(doujin_folder, exist_ok=True)

                num_pages = len(meta.get("images", {}).get("pages", []))
                if num_pages == 0:
                    log_clarification()
                    logger.warning(f"Gallery {gallery_id} has no pages, skipping")
                    gallery_failed = True
                    break

                # Threaded downloads
                futures = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=config["THREADS_IMAGES"]) as executor:
                    log_clarification()
                    for i in range(num_pages):
                        page = i + 1

                        img_url = fetch_image_url(meta, page)
                        if not img_url:
                            logger.warning(f"Skipping Page {page}: Failed to get URL")
                            gallery_failed = True
                            continue
                        
                        # Correct path: always [ARTIST]/[TITLE]/image
                        img_filename = f"{page}.{img_url.split('.')[-1]}"
                        img_path = os.path.join(doujin_folder, img_filename)

                        futures.append(executor.submit(download_image, gallery_id, page, img_url, img_path, session))

                    for _ in tqdm(concurrent.futures.as_completed(futures),
                                  total=len(futures),
                                  desc=f"Gallery {gallery_id} ({safe_name(artist)})",
                                  unit="img", leave=True):
                        pass

            if gallery_failed:
                log_clarification()
                logger.warning(f"Gallery {gallery_id} encountered download issues, retrying...")
                continue

            active_extension.after_gallery_download(meta)
            db.mark_gallery_completed(gallery_id)
            log_clarification()
            logger.info(f"Completed Gallery {gallery_id}")
            break

        except Exception as e:
            log_clarification()
            logger.error(f"Error processing Gallery {gallery_id}: {e}")
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
    active_extension.pre_download_hook(config, gallery_ids)

    if not gallery_ids:
        logger.error("No galleries specified. Use --galleries or --range.")
        return
    
    log_clarification()
    logger.info(f"Galleries to process: {gallery_ids[0]} -> {gallery_ids[-1]}" 
                if len(gallery_ids) > 1 else f"Galleries to process: {gallery_ids[0]}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.get("threads_galleries", 4)) as executor:
        futures = [executor.submit(process_gallery, gid) for gid in gallery_ids]
        concurrent.futures.wait(futures)

    log_clarification()
    logger.info("All galleries processed")
    
    active_extension.after_all_downloads(gallery_ids)
    active_extension.post_download_hook(config, gallery_ids)