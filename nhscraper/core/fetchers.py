# core/fetchers.py
import requests
from nhscraper.core.logger import *

session = requests.Session()

def fetch_gallery_metadata(gallery_id: int):
    url = f"https://nhentai.net/api/gallery/{gallery_id}"
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data
    except Exception as e:
        log_clarification("warning")
        logger.warning(f"Failed to fetch metadata for gallery {gallery_id}: {e}")
        return None

def fetch_image_url(meta: dict, page: int):
    try:
        ext = meta["images"]["pages"][page-1]["t"]
        filename = f"{page}.{ext}"
        url = f"https://i.nhentai.net/galleries/{meta['media_id']}/{filename}"
        return url
    except Exception as e:
        log_clarification("warning")
        logger.warning(f"Failed to build image URL for gallery {meta['id']} page {page}: {e}")
        return None