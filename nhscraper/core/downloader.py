#!/usr/bin/env python3
# nhscraper/downloader.py

import os, time, random, concurrent.futures
from tqdm import tqdm

from nhscraper.core.logger import *
from nhscraper.core.config import config
from nhscraper.core import db
from nhscraper.core.fetchers import session, fetch_gallery_metadata, fetch_image_url

# Import active extension
from nhscraper.extensions import active_extension

####################################################################################################
# LOGGING
####################################################################################################
def log_clarification():  
    print()
    logger.debug("")

log_clarification()
logger.info("[+] Downloader ready.")
logger.debug("[*] Downloader Debugging started.")

####################################################################################################
# UTILITIES
####################################################################################################
def safe_name(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def clean_title(meta):
    title_obj = meta.get("title", {}) or {}
    title_type = config.get("title_type", "pretty").lower()
    title = title_obj.get(title_type) or title_obj.get("english") or title_obj.get("japanese") or title_obj.get("pretty") or f"Gallery_{meta.get('id')}"
    if "|" in title: title = title.split("|")[-1].strip()
    import re
    title = re.sub(r'(\s*\[.*?\]\s*)+$', '', title.strip())
    return safe_name(title)

def dynamic_sleep(stage="gallery"):
    num_galleries = max(1, len(config.get("galleries", [])))
    total_load = config.get("threads_galleries", 1) * config.get("threads_images", 4)
    base_min, base_max = (0.3, 0.5) if stage == "metadata" else (0.5, 1)
    scale = min(max(1, total_load * min(num_galleries, 1000)/1000), 5)
    sleep_time = random.uniform(base_min*scale, base_max*scale)
    log_clarification()
    logger.debug(f"[!] {stage.capitalize()} sleep: {sleep_time:.2f}s (scale {scale:.1f})")
    time.sleep(sleep_time)

####################################################################################################
# IMAGE DOWNLOAD
####################################################################################################
def download_image(url, path, session, retries=None):
    if retries is None:
        retries = config.get("MAX_RETRIES", 3)

    if os.path.exists(path):
        logger.debug(f"[!] Already exists, skipping: {path}")
        return True

    if config.get("dry_run", False):
        logger.info(f"[+] [DRY-RUN] Would download {url} -> {path}")
        return True

    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=30, stream=True)
            if r.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"[!] 429 rate limit hit for {url}, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            logger.debug(f"[+] Downloaded: {path}")
            return True
        except Exception as e:
            wait = 2 ** attempt
            logger.warning(f"[!] Attempt {attempt} failed for {url}: {e}, retrying in {wait}s")
            time.sleep(wait)
    logger.error(f"[!] Failed to download after {retries} attempts: {url}")
    return False

def should_download_gallery(meta):
    if not meta:
        return False
    for artist in meta.get("artists", ["Unknown Artist"]):
        artist_folder = os.path.join(SUWAYOMI_DIR, safe_name(artist))
        doujin_folder = os.path.join(artist_folder, clean_title(meta))
        if os.path.exists(doujin_folder):
            num_pages = meta.get("num_pages", 0)
            all_exist = all(
                any(os.path.exists(os.path.join(doujin_folder, f"{i+1}.{ext}"))
                    for ext in ("jpg","png","gif","webp"))
                for i in range(num_pages)
            )
            if all_exist:
                logger.info(f"[!] Skipping {meta['id']} ({doujin_folder}), already complete.")
                return False
    return True

####################################################################################################
# GALLERY PROCESSING
####################################################################################################
def process_gallery(gallery_id):
    download_location = None
    extension_name = getattr(active_extension, "__name__", "skeleton")
    db.mark_gallery_started(gallery_id, download_location, extension_name)

    gallery_attempts = 0
    max_gallery_attempts = config.get("MAX_RETRIES", 3)

    while gallery_attempts < max_gallery_attempts:
        gallery_attempts += 1
        try:
            dynamic_sleep("gallery")
            logger.info(f"[*] Starting Gallery {gallery_id} (Attempt {gallery_attempts}/{max_gallery_attempts})")

            meta = fetch_gallery_metadata(gallery_id)
            if not meta:
                logger.warning(f"[!] Failed to fetch metadata for Gallery {gallery_id}")
                if gallery_attempts >= max_gallery_attempts:
                    db.mark_gallery_failed(gallery_id)
                continue

            if not should_download_gallery(meta):
                logger.info(f"[!] Skipping Gallery {gallery_id}, already downloaded")
                db.mark_gallery_completed(gallery_id)
                active_extension.after_gallery_download(meta)
                return

            gallery_failed = False

            # Call pre-download hook once per gallery batch
            active_extension.during_download_hook(config, gallery_id, meta)

            for artist in meta.get("artists", ["Unknown Artist"]):
                artist_folder = os.path.join(SUWAYOMI_DIR, safe_name(artist))
                doujin_folder = os.path.join(artist_folder, clean_title(meta))
                os.makedirs(doujin_folder, exist_ok=True)

                num_pages = meta.get("num_pages", 0)
                if num_pages == 0:
                    logger.warning(f"[!] Gallery {gallery_id} has no pages, skipping")
                    gallery_failed = True
                    break

                # Threaded downloads with progress bar
                futures = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=config["threads_images"]) as executor:
                    for i in range(num_pages):
                        page = i + 1
                        img_url = fetch_image_url(meta, page)
                        if not img_url:
                            logger.warning(f"[!] Skipping Page {page}, failed to get URL")
                            gallery_failed = True
                            continue
                        img_path = os.path.join(doujin_folder, f"{page}.{img_url.split('.')[-1]}")
                        futures.append(executor.submit(download_image, img_url, img_path, session))

                    for _ in tqdm(concurrent.futures.as_completed(futures),
                                  total=len(futures),
                                  desc=f"Gallery {gallery_id} ({safe_name(artist)})",
                                  unit="img", leave=True):
                        pass

            if gallery_failed:
                logger.warning(f"[!] Gallery {gallery_id} encountered download issues, retrying...")
                continue

            # Call after-gallery hook
            active_extension.after_gallery_download(meta)
            db.mark_gallery_completed(gallery_id)
            logger.info(f"[+] Completed gallery {gallery_id}")
            break

        except Exception as e:
            log_clarification()
            logger.error(f"[!] Error processing gallery {gallery_id}: {e}")
            if gallery_attempts >= max_gallery_attempts:
                db.mark_gallery_failed(gallery_id)

####################################################################################################
# MAIN
####################################################################################################
def main():
    gallery_ids = config.get("galleries", [])
    active_extension.pre_download_hook(config, gallery_ids)

    if not gallery_ids:
        logger.error("[!] No galleries specified. Use --galleries or --range.")
        return
    
    log_clarification()
    logger.info(f"[*] Galleries to process: {gallery_ids[0]} -> {gallery_ids[-1]}" 
                if len(gallery_ids) > 1 else f"[*] Gallery to process: {gallery_ids[0]}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.get("threads_galleries", 4)) as executor:
        futures = [executor.submit(process_gallery, gid) for gid in gallery_ids]
        concurrent.futures.wait(futures)

    log_clarification()
    logger.info("[*] All galleries processed")
    
    active_extension.after_all_downloads(gallery_ids)
    active_extension.post_download_hook(config, gallery_ids)

if __name__ == "__main__":
    main()