# core/db.py
import os
import json
from nhscraper.core.logger import logger

CACHE_DIR = "./cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def get_cached_meta(gallery_id: int):
    cache_file = os.path.join(CACHE_DIR, f"{gallery_id}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                logger.info(f"Loaded cached metadata for gallery {gallery_id}")
                return json.load(f)
        except Exception as e:
            logger.warning(f"\nFailed to read cache for {gallery_id}: {e}")
    return None

def cache_gallery_meta(meta: dict):
    cache_file = os.path.join(CACHE_DIR, f"{meta['id']}.json")
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        logger.info(f"Metadata cached for gallery {meta['id']}")
    except Exception as e:
        logger.error(f"\nFailed to cache metadata for {meta['id']}: {e}")