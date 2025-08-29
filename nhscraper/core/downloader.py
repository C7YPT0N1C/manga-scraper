#!/usr/bin/env python3
# core/downloader.py

import os
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from nhscraper.logger import logger
from nhscraper.core.config import config, get_download_path
from nhscraper.core.fetchers import fetch_gallery_metadata, fetch_image_url
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
    return gallery_folder

def should_download_gallery(meta: dict) -> bool:
    """Determine whether the gallery passes the language/tags filters."""
    # Language filter
    if config.get("language") and meta.get("language") not in config["language"]:
        logger.info(f"Skipping gallery {meta['id']} due to language filter")
        return False
    # Excluded tags
    if config.get("excluded_tags"):
        if any(tag in config["excluded_tags"] for tag in meta.get("tags", [])):
            logger.info(f"Skipping gallery {meta['id']} due to excluded tags")
            return False
    return True

# ------------------------------
# Core Download Functions
# ------------------------------

def process_gallery(gallery_id: int):
    """Download a single gallery and trigger extension hooks."""
    try:
        meta = fetch_gallery_metadata(gallery_id, use_tor=config.get("use_tor"))
        if not should_download_gallery(meta):
            return None

        # Pre-download extension hooks
        for ext in INSTALLED_EXTENSIONS:
            if hasattr(ext, "during_download_hook"):
                ext.during_download_hook(config, gallery_id, meta)

        gallery_folder = resolve_gallery_folder(meta)
        if not config.get("dry_run"):
            os.makedirs(gallery_folder, exist_ok=True)
        else:
            logger.info(f"Dry-run: Would create folder {gallery_folder}")

        # Download images
        def download_worker(img_url):
            filename = os.path.basename(img_url)
            target_path = os.path.join(gallery_folder, filename)
            if config.get("dry_run"):
                logger.info(f"Dry-run: Would download {img_url} -> {target_path}")
            else:
                download_image(img_url, target_path, use_tor=config.get("use_tor"))

        with ThreadPoolExecutor(max_workers=config.get("threads_images", 4)) as executor:
            for img_url in meta.get("images", []):
                executor.submit(download_worker, img_url)

        # Post-download hooks per gallery
        for ext in INSTALLED_EXTENSIONS:
            if hasattr(ext, "after_gallery_download"):
                ext.after_gallery_download(meta)

        return meta
    except Exception as e:
        logger.error(f"\nError processing gallery {gallery_id}: {e}")
        return None

def download_galleries(gallery_list: list):
    """Process a list of gallery IDs concurrently using threads."""
    logger.info(f"Starting download of {len(gallery_list)} galleries")
    # Pre-download extension hooks
    for ext in INSTALLED_EXTENSIONS:
        if hasattr(ext, "pre_download_hook"):
            gallery_list = ext.pre_download_hook(config, gallery_list)

    all_meta = []
    with ThreadPoolExecutor(max_workers=config.get("threads_galleries", 1)) as executor:
        futures = {executor.submit(process_gallery, gid): gid for gid in gallery_list}
        for future in futures:
            result = future.result()
            if result:
                all_meta.append(result)

    # After all downloads
    for ext in INSTALLED_EXTENSIONS:
        if hasattr(ext, "after_all_downloads"):
            ext.after_all_downloads(all_meta)

    # Reset extension download paths
    for ext in INSTALLED_EXTENSIONS:
        if hasattr(ext, "post_download_hook"):
            ext.post_download_hook(config, all_meta)

    logger.info(f"Completed download of {len(all_meta)} galleries")
    return all_meta