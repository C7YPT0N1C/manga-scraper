#!/usr/bin/env python3
# core/downloader.py

import os, json, time, random, threading, requests
from tqdm import tqdm
from requests.exceptions import HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed

from nhscraper.core.logger import *
from nhscraper.core.config import config, get_download_path
from nhscraper.core.fetchers import max_retries, fetch_gallery_metadata, fetch_image_url, session
from nhscraper.extensions.extension_loader import INSTALLED_EXTENSIONS

# ------------------------------
# Helper Functions
# ------------------------------
def sanitize_filename(name: str) -> str:
    """Sanitize folder/file names to remove invalid filesystem characters."""
    return "".join(c for c in name if c.isalnum() or c in " ._-").strip()

def resolve_gallery_folder(meta: dict) -> str:
    """
    Resolve the final download folder path for a gallery.
    Priority:
      1. Extension-specific path from config (EXTENSION_DOWNLOAD_PATH)
      2. Default download path (DOWNLOAD_PATH)
    """
    base_path = config.get("EXTENSION_DOWNLOAD_PATH") or get_download_path()
    if config.get("title_type") == "pretty" and config.get("title_sanitise", True):
        folder_name = sanitize_filename(meta["title_pretty"])
    elif config.get("title_type") == "english":
        folder_name = meta.get("title_english") or f"gallery_{meta['id']}"
    else:
        folder_name = meta.get("title_japanese") or f"gallery_{meta['id']}"
    
    artist_part = meta["artists"][0] if meta.get("artists") else "Unknown"
    gallery_folder = os.path.join(base_path, artist_part, folder_name)

    log_clarification("debug")
    logger.debug(f"Resolved gallery folder for {meta['id']}: {gallery_folder}")
    return gallery_folder

def should_download_gallery(meta: dict) -> bool:
    """Determine whether the gallery passes the language/tags filters."""
    # Language filter
    if config.get("language") and meta.get("language") not in config["language"]:
        log_clarification("info")
        logger.info(f"Skipping gallery {meta['id']} due to language filter")
        return False
    # Excluded tags
    if config.get("excluded_tags"):
        if any(tag in config["excluded_tags"] for tag in meta.get("tags", [])):
            log_clarification("info")
            logger.info(f"Skipping gallery {meta['id']} due to excluded tags")
            return False
    log_clarification("debug")
    logger.debug(f"Gallery {meta['id']} passed filters")
    return True

# ------------------------------
# Core Download Functions
# ------------------------------
class tqdm_logger_suppress:
    def __enter__(self):
        self._level = logger.level
        logger.setLevel(100)  # suppress logs temporarily
    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.setLevel(self._level)

def process_gallery(gallery_id: int):
    """Download a single gallery and trigger extension hooks."""
    try:
        meta = fetch_gallery_metadata(gallery_id)
        if not meta or not should_download_gallery(meta):
            return None

        gallery_folder = resolve_gallery_folder(meta)
        if not config.get("dry_run"):
            os.makedirs(gallery_folder, exist_ok=True)

        images = meta.get("images", [])
        if not images:
            logger.warning(f"No images found for gallery {gallery_id}")
            return None

        # Image progress bar inside gallery
        with tqdm(total=len(images),
                  desc=f"Gallery {gallery_id}",
                  unit="img",
                  position=1,
                  leave=False,
                  dynamic_ncols=True,
                  colour="cyan") as pbar:

            def download_worker(img_url):
                filename = os.path.basename(img_url)
                target_path = os.path.join(gallery_folder, filename)
                retries = 0
                max_retries = 3

                while retries < max_retries:
                    try:
                        if config.get("dry_run"):
                            logger.info(f"Dry-run: Would download {img_url} -> {target_path}")
                        else:
                            fetch_image_url(img_url, target_path)
                        break
                    except requests.HTTPError as e:
                        if e.response.status_code == 429:
                            wait = (2 ** retries) + random.uniform(0, 1)
                            logger.warning(f"429 for image {filename}, retrying in {wait:.1f}s (attempt {retries+1})")
                            time.sleep(wait)
                            retries += 1
                        else:
                            logger.error(f"HTTPError downloading {img_url}: {e}")
                            break
                    except Exception as e:
                        logger.error(f"Failed to download {img_url}: {e}")
                        break
                else:
                    logger.error(f"Max retries reached for {img_url}, skipping.")
                
                pbar.update(1)

            with ThreadPoolExecutor(max_workers=config.get("threads_images", 4)) as executor:
                for img_url in images:
                    executor.submit(download_worker, img_url)

        return meta

    except Exception as e:
        logger.error(f"Error processing Gallery {gallery_id}: {e}")
        return None


def download_galleries(gallery_list: list):
    """Download multiple galleries with a clean nested progress bar."""
    all_meta = []
    with tqdm(total=len(gallery_list),
              desc="All galleries",
              unit="gallery",
              position=0,
              leave=True,
              dynamic_ncols=True,
              colour="green") as overall_pbar:

        with ThreadPoolExecutor(max_workers=config.get("threads_galleries", 1)) as executor:
            futures = {executor.submit(process_gallery, gid): gid for gid in gallery_list}
            for future in as_completed(futures):
                gid = futures[future]
                try:
                    result = future.result()
                    if result:
                        all_meta.append(result)
                except Exception as e:
                    logger.error(f"Exception in gallery {gid}: {e}")
                finally:
                    overall_pbar.update(1)

    return all_meta