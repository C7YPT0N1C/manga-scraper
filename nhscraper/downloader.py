#!/usr/bin/env python3
# nhscraper/downloader.py

import os
import logging
import time
import random
import concurrent.futures
import json
from datetime import datetime
from tqdm import tqdm
from nhscraper.config import logger, config, SUWAYOMI_DIR
import nhscraper.nhscraper_api as api
import nhscraper.graphql_api as gql

##################################################################################################################################
# LOGGING
##################################################################################################################################
def log_clarification():  # Print new line for readability.
    print()
    logger.debug("")

log_clarification()
logger.info("[+] Downloader ready.")
logger.debug("[*] Downloader Debugging started.")

##################################################################################################################################
# UTILITIES
##################################################################################################################################
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

##################################################################################################################################
# DETAILS.JSON HANDLER
##################################################################################################################################
def update_details_json(artist, tags):
    artist_folder = os.path.join(SUWAYOMI_DIR, safe_name(artist))
    os.makedirs(artist_folder, exist_ok=True)
    details_path = os.path.join(artist_folder, "details.json")

    if os.path.exists(details_path):
        with open(details_path, "r", encoding="utf-8") as f:
            details = json.load(f)
    else:
        details = {
            "title": f"{artist}'s Works",
            "author": artist,
            "artist": artist,
            "description": f"An archive of {artist}'s Works.",
            "genre": [],
            "status": "1",
            "_status values": ["0 = Unknown", "1 = Ongoing", "2 = Completed", "3 = Licensed"]
        }

    current_tags = set(details.get("genre", []))
    new_tags = set(tags)
    updated_tags = current_tags.union(new_tags)

    if updated_tags == current_tags:
        logger.debug(f"[*] No new tags for {artist}, skipping details.json write")
        return

    details["genre"] = sorted(updated_tags)

    with open(details_path, "w", encoding="utf-8") as f:
        json.dump(details, f, indent=4, ensure_ascii=False)
    
    log_clarification()
    logger.info(f"[*] Processing new tags for {artist}: {sorted(new_tags - current_tags)}")

##################################################################################################################################
# IMAGE DOWNLOAD
##################################################################################################################################
def download_image(url, path, session, retries=3, delay=2):
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
                time.sleep(random.uniform(1,3))
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
            logger.warning(f"[!] Attempt {attempt} failed for {url}: {e}")
            time.sleep(delay)
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

##################################################################################################################################
# GALLERY PROCESSING
##################################################################################################################################
def process_gallery(gallery_id):
    with api.state_lock:
        entry = api.gallery_metadata.setdefault(gallery_id, {"meta": None})
        entry.setdefault("download_attempts", 0)
        entry.setdefault("graphql_attempts", 0)

    api.running_galleries.append(gallery_id)
    gallery_attempts = 0
    max_gallery_attempts = 3  # Retry full gallery download up to 3 times

    while gallery_attempts < max_gallery_attempts:
        gallery_attempts += 1
        try:
            dynamic_sleep("gallery")
            logger.info(f"[*] Starting Gallery {gallery_id} (Attempt {gallery_attempts}/{max_gallery_attempts})")

            meta = api.get_gallery_metadata(gallery_id)
            if not meta:
                logger.warning(f"[!] Failed to fetch metadata for Gallery {gallery_id}")
                if gallery_attempts >= max_gallery_attempts:
                    api.update_gallery_state(gallery_id, stage="download", status="failed")
                continue

            if not should_download_gallery(meta):
                logger.info(f"[!] Skipping Gallery {gallery_id}, already downloaded")
                api.update_gallery_state(gallery_id, stage="download", status="completed")
                return

            gallery_failed = False
            for artist in meta.get("artists", ["Unknown Artist"]):
                artist_folder = os.path.join(SUWAYOMI_DIR, safe_name(artist))
                doujin_folder = os.path.join(artist_folder, clean_title(meta))
                os.makedirs(doujin_folder, exist_ok=True)

                num_pages = meta.get("num_pages", 0)
                if num_pages == 0:
                    logger.warning(f"[!] Gallery {gallery_id} has no pages, skipping")
                    gallery_failed = True
                    break

                # Queue image downloads
                with concurrent.futures.ThreadPoolExecutor(max_workers=config["threads_images"]) as executor:
                    futures = []
                    for i in range(num_pages):
                        page = i + 1
                        img_url = api.get_image_url(meta, page)
                        if not img_url:
                            logger.warning(f"[!] Skipping Page {page}, failed to get URL")
                            gallery_failed = True
                            continue
                        img_path = os.path.join(doujin_folder, f"{page}.{img_url.split('.')[-1]}")
                        futures.append(executor.submit(download_image, img_url, img_path, api.session))

                    # Wait for downloads
                    for _ in tqdm(concurrent.futures.as_completed(futures), total=len(futures),
                                  desc=f"Gallery {gallery_id} ({safe_name(artist)})", unit="img"):
                        pass

            if gallery_failed:
                logger.warning(f"[!] Gallery {gallery_id} encountered download issues, retrying...")
                continue

            # Update details.json for each artist
            for artist in meta.get("artists", ["Unknown Artist"]):
                update_details_json(artist, meta.get("tags", []))

            # Retry GraphQL calls up to 3 times
            gql_attempts = 0
            max_gql_attempts = 3
            gql_success = False
            while gql_attempts < max_gql_attempts and not gql_success:
                gql_attempts += 1
                try:
                    gql.set_local_directory()
                    gql.update_library()
                    gql_success = True
                    api.update_gallery_state(gallery_id, stage="graphql", status="completed")
                except Exception as e:
                    log_clarification()
                    logger.warning(f"[!] GraphQL attempt {gql_attempts} failed for Gallery {gallery_id}: {e}")
                    time.sleep(2)

            if not gql_success:
                api.update_gallery_state(gallery_id, stage="graphql", status="failed")
                logger.error(f"[!] GraphQL failed for Gallery {gallery_id} after {max_gql_attempts} attempts")

            api.update_gallery_state(gallery_id, stage="download", status="completed")
            logger.info(f"[+] Completed gallery {gallery_id}")
            break  # Break the gallery retry loop after successful processing

        except Exception as e:
            log_clarification()
            logger.error(f"[!] Error processing gallery {gallery_id}: {e}")
            if gallery_attempts >= max_gallery_attempts:
                api.update_gallery_state(gallery_id, stage="download", status="failed")

        finally:
            if gallery_id in api.running_galleries:
                api.running_galleries.remove(gallery_id)

##################################################################################################################################
# MAIN
##################################################################################################################################
def main():
    gallery_ids = config["galleries"]
    gql.set_local_directory()

    if not gallery_ids:
        logger.error("[!] No galleries specified. Use --galleries or --range.")
        return
    
    log_clarification()
    logger.info(f"[*] Galleries to process: {gallery_ids[0]} -> {gallery_ids[-1]}" 
                if len(gallery_ids) > 1 else f"[*] Gallery to process: {gallery_ids[0]}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=config["threads_galleries"]) as executor:
        futures = [executor.submit(process_gallery, gid) for gid in gallery_ids]
        concurrent.futures.wait(futures)

    log_clarification()
    logger.info("[*] All galleries processed")
    
    gql.update_library()

if __name__ == "__main__":
    main()