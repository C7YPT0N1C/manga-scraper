#!/usr/bin/env python3
import os
import sys
import json
import logging
import argparse
import time
from datetime import datetime
import random
from urllib.parse import urljoin
import concurrent.futures
from tqdm import tqdm

import cloudscraper
import requests
from dotenv import load_dotenv, set_key

# ===============================
# KEY
# ===============================
# [*] = Process / In Progress
# [+] = Success / Confirmation
# [!] = Warning/Error

# ===============================
# CONFIGURATION
# ===============================
NHENTAI_DIR = "/opt/nhentai-scraper"
SUWAYOMI_DIR = "/opt/suwayomi/local"
ENV_FILE = os.path.join(NHENTAI_DIR, "nhentai-scraper.env")
LOGS_DIR = os.path.join(NHENTAI_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

GRAPHQL_URL = "http://127.0.0.1:4567/api/graphql"
NHENTAI_API_BASE = "https://nhentai.net/api/"


# ===============================
# LOGGING
# ===============================
logger = logging.getLogger("nhentai-scraper")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
fh = logging.FileHandler(os.path.join(LOGS_DIR, "nhentai-scraper.log"))
fh.setFormatter(formatter)
logger.addHandler(fh)
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(formatter)
logger.addHandler(ch)

# ===============================
# LOAD ENV
# ===============================
load_dotenv(dotenv_path=ENV_FILE)

def update_env(key, value):
    set_key(ENV_FILE, key, str(value))

# ===============================
# ARGUMENTS
# ===============================
parser = argparse.ArgumentParser(description="NHentai scraper with Suwayomi integration")

parser.add_argument("--start", type=int, help="Starting gallery ID (Default: 592000)")
parser.add_argument("--end", type=int, help="Ending gallery ID (Default: 600000")
parser.add_argument("--excluded-tags", type=str, help="Comma-separated list of tags to exclude galleries (Default: empty)")
parser.add_argument("--language", type=str, help="Comma-separated list of languages to include (Default: english)")
parser.add_argument("--title-type", type=str, choices=["english", "japanese", "pretty"], help="Gallery title type for folder names (Default: pretty)")
parser.add_argument("--threads-galleries", type=int, help="Number of concurrent galleries (Default: 1)")
parser.add_argument("--threads-images", type=int, help="Threads per gallery (Default: 4)")
parser.add_argument("--use-tor", action="store_true", help="Route requests via Tor (Default: false)")
parser.add_argument("--dry-run", action="store_true", help="Simulate downloads and GraphQL without saving (Default: false)")
parser.add_argument("--verbose", action="store_true", help="Enable debug logging (Default: false)")

args = parser.parse_args()

# ===============================
# CONFIG MERGE
# ===============================
def get_config(name, default=None, is_bool=False):
    env_val = os.getenv(name)
    arg_val = getattr(args, name.lower(), None)
    if arg_val is not None:
        update_env(name, arg_val if not is_bool else str(arg_val))
        return arg_val
    if env_val is not None:
        if is_bool:
            return str(env_val).lower() in ("1", "true", "yes")
        if isinstance(default, int):
            try:
                return int(env_val)
            except ValueError:
                return default
        return str(env_val).strip('"').strip("'")
    return default

config = {
    "start": get_config("NHENTAI_START_ID", 500000),
    "end": get_config("NHENTAI_END_ID", 600000),
    "excluded_tags": get_config("EXCLUDE_TAGS", ""),
    "language": get_config("LANGUAGE", "english"),
    "title_type": get_config("TITLE_TYPE", "pretty"),
    "threads_galleries": get_config("THREADS_GALLERIES", 1),
    "threads_images": get_config("THREADS_IMAGES", 4),
    "use_tor": get_config("USE_TOR", False, True),
    "dry_run": get_config("NHENTAI_DRY_RUN", False, True),
    "verbose": get_config("NHENTAI_VERBOSE", False, True),
}

# ===============================
# CLI OVERRIDES
# ===============================
if args.start is not None:
    config["start"] = args.start

if args.end is not None:
    config["end"] = args.end
    
if args.excluded_tags:
    config["excluded_tags"] = args.excluded_tags

if args.language:
    config["language"] = args.language

if args.title_type:
    config["title_type"] = args.title_type

if args.threads_galleries is not None:
    config["threads_galleries"] = args.threads_galleries

if args.threads_images is not None:
    config["threads_images"] = args.threads_images

if args.use_tor:
    config["use_tor"] = True

if args.dry_run:
    config["dry_run"] = True

if args.verbose:
    config["verbose"] = True
    logger.setLevel(logging.DEBUG)

# ===============================
# MIRRORS
# ===============================
def get_mirrors():
    env_mirrors = os.getenv("NHENTAI_MIRRORS") # Check env
    mirrors = []

    if env_mirrors:
        mirrors = [m.strip() for m in env_mirrors.split(",") if m.strip()]

    # Always start with i.nhentai.net
    mirrors = ["https://i.nhentai.net"] + [m for m in mirrors if m != "https://i.nhentai.net"]

    return mirrors

MIRRORS = get_mirrors()
logger.debug(f"[+] Mirrors set: {MIRRORS}")

# ===============================
# HTTP SESSION
# ===============================
def build_session():
    s = cloudscraper.create_scraper()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://nhentai.net/",
    })
    if config["use_tor"]:
        proxy = "socks5h://127.0.0.1:9050"
        s.proxies.update({"http": proxy, "https": proxy})
        logger.info(f"[+] Using Tor proxy: {proxy}")
    else:
        logger.info("[+] Not using Tor proxy")
    return s

session = build_session()

# ===============================
# UTILITIES
# ===============================
def clean_gallery_title(meta):
    # Extract a clean, readable title from NHentai metadata.
    
    title_obj = meta.get("title", {}) or {}
    title_type = config.get("title_type", "pretty").lower()
    
    # Prefer the requested title type
    if title_type == "english":
        raw_title = title_obj.get("english") or title_obj.get("pretty") or f"Gallery_{meta.get('id')}"
    elif title_type == "japanese":
        raw_title = title_obj.get("japanese") or title_obj.get("pretty") or f"Gallery_{meta.get('id')}"
    else:
        raw_title = title_obj.get("pretty") or f"Gallery_{meta.get('id')}"

    # Split on | and take the last part (main work title)
    if "|" in raw_title:
        raw_title = raw_title.split("|")[-1].strip()
    
    return safe_name(raw_title)
    
def safe_name(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def suwayomi_folder_name(meta, artist):
    # Returns the folder path for Suwayomi galleries.
    
    title = get_gallery_title(meta)
    folder_name = f"{safe_name(artist)} - {title}"
    return os.path.join(SUWAYOMI_DIR, folder_name)

def write_details_json(folder, meta):
    # Writes a Suwayomi-compatible details.json file instead of raw NHentai metadata.
    
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "details.json")
        
        # Pick the first artist as main author/artist
        main_artist = meta.get("artists", ["Unknown Artist"])[0]
        # Use pretty title or fallback
        title = meta.get("title", {}).get("pretty") or f"Gallery_{meta.get('id')}"
        # NHentai tags go into genre
        tags = meta.get("tags", [])

        suwayomi_meta = {
            "title": title,
            "author": main_artist,
            "artist": main_artist,
            "description": f"An archive of {title}.",
            "genre": tags,
            "status": "1",  # default ongoing
            "_status values": ["0 = Unknown", "1 = Ongoing", "2 = Completed", "3 = Licensed"]
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(suwayomi_meta, f, ensure_ascii=False, indent=2)
        
        logger.debug(f"[+] Wrote {path}")
    except Exception as e:
        logger.error(f"[!] Failed to write details.json in {folder}: {e}")

# ===============================
# NHENTAI API
# ===============================
def dynamic_sleep(stage):
    # Sleeps for a random time based on scraper load.
    num_threads = config.get('threads_galleries', 1)
    num_galleries = max(1, config.get('end', 1) - config.get('start', 0) + 1)
    
    # Base ranges (seconds)
    if stage == "metadata":
        base_min, base_max = 0.3, 0.5  # Metadata fetches are lighter
    elif stage == "gallery":
        base_min, base_max = 0.5, 1 # Gallery fetches are heavier
    else:
        base_min, base_max = 0.5, 0.5 # Default to moderate wait

    # Calculate a scale factor to adjust sleep time based on load.
    # 1. num_threads * min(num_galleries, 1000) → approximate "workload" but cap galleries at 1000 for sanity.
    # 2. Divide by 10 → normalizes the workload so that sleep times aren’t too extreme.
    # 3. max(1, ...) → ensures the scale is never less than 1, so sleep doesn't shrink too much.
    # 4. min(..., 5) → caps the scale at 5 to prevent extremely long sleeps for huge workloads.
    scale = min(max(1, (num_threads * min(num_galleries, 1000)) / 10), 5)
    
    if stage == "metadata":
        logger.debug(f"[!] Metadata Gatherer: API rate limit hit, sleeping for {scale:.1f}s before retry")
    elif stage == "gallery":
        logger.debug(f"[!] Gallery Downloader: API rate limit hit, sleeping for {scale:.1f}s before retry")
    else:
        logger.debug(f"[!] API rate limit hit, sleeping for {scale:.1f}s before retry")
    
    sleep_time = random.uniform(base_min * scale, base_max * scale)
    time.sleep(sleep_time)
    
def get_gallery_metadata(gallery_id: int, retries=3, delay=2):
    url = urljoin(NHENTAI_API_BASE, f"gallery/{gallery_id}")
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 429:
                dynamic_sleep("metadata") # Wait as to not get rate limited.
                logger.warning(f"[!] API {gallery_id} HTTP 429: waiting {wait:.1f}s before retry")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                logger.error(f"[!] API {gallery_id} HTTP {r.status_code}")
                return None
            data = r.json()
            title_obj = data.get("title", {}) or {}
            tags = data.get("tags", [])
            artists = [t["name"] for t in tags if t.get("type") == "artist"] or ["Unknown Artist"]
            meta = {
                "id": data.get("id", gallery_id),
                "media_id": data.get("media_id"),
                "title": title_obj,
                "tags": [t.get("name") for t in tags if "name" in t],
                "artists": artists,
                "num_pages": data.get("num_pages"),
                "images": data.get("images", {}),
                "url": f"https://nhentai.net/g/{gallery_id}/",
            }
            return meta
        except Exception as e:
            logger.error(f"[!] Attempt {attempt} failed for {gallery_id}: {e}")
            time.sleep(delay)
    logger.error(f"[!] All retries failed for gallery {gallery_id}")
    return None

def get_gallery_title(meta):
    # Returns the gallery title based on --title-type flag,
    # falling back to 'pretty' if the requested type is unavailable.
    title_obj = meta.get("title", {}) or {}
    title_type = config.get("title_type", "pretty").lower()

    # Try requested type first
    title = title_obj.get(title_type)

    # Fallback chain
    if not title:
        if title_type != "english":
            title = title_obj.get("english")
        if not title and title_type != "japanese":
            title = title_obj.get("japanese")
        if not title:
            title = title_obj.get("pretty")

    return safe_name(title or f"Gallery_{meta.get('id')}")

# ===============================
# IMAGE DOWNLOAD
# ===============================
def get_image_url(meta: dict, page: int, mirror_index=0) -> str:
    # Build the URL for a specific image page using the mirror list.
    try:
        media_id = meta["media_id"]
        file_info = meta["images"]["pages"][page - 1]
        file_type = file_info["t"]
        ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
        ext = ext_map.get(file_type, "jpg")

        base_url = MIRRORS[mirror_index % len(MIRRORS)]
        return f"{base_url}/galleries/{media_id}/{page}.{ext}"
    except Exception as e:
        logger.error(f"[!] Failed to build image URL for page {page}: {e}")
        return None

def download_image(url, path, retries=3, delay=2):
    # Downloads an image from the URL, trying all mirrors in order.
    if config["dry_run"]:
        logger.info(f"[*] [DRY-RUN] Would download: {url} -> {path}")
        return

    os.makedirs(os.path.dirname(path), exist_ok=True)

    for attempt in range(1, retries + 1):
        for mirror_index, mirror in enumerate(MIRRORS):
            mirror_url = url.replace("https://i.nhentai.net", mirror)
            try:
                resp = session.get(mirror_url, stream=True, timeout=30)
                resp.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in resp.iter_content(1024*8):
                        f.write(chunk)
                logger.info(f"[+] Downloaded: {path}")
                time.sleep(0.5)
                return  # Success
            except Exception as e:
                logger.warning(f"[!] Attempt {attempt}, mirror {mirror} failed: {e}")
                time.sleep(delay)

    logger.error(f"[!] All retries failed for {url}")

# ===============================
# GRAPHQL
# ===============================
def graphql_request(query, variables):
    if config["dry_run"]:
        logger.info("[*] [DRY-RUN] Skipping GraphQL request")
        return None
    try:
        resp = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[!] GraphQL request failed: {e}")
        return None

def create_or_update_gallery(meta, folder):
    query = """
    mutation addGallery($input: GalleryInput!) {
      addGallery(input: $input) { id title }
    }
    """
    variables = {
        "input": {
            "id": meta.get("id"),
            "title": meta.get("title"),
            "artists": meta.get("artists"),
            "tags": meta.get("tags", []),
            "url": meta.get("url"),
            "local_path": folder
        }
    }
    graphql_request(query, variables)

# ===============================
# MAIN LOOP (multi-threaded)
# ===============================
def process_gallery(gallery_id):
    dynamic_sleep("gallery") # Wait as to not get rate limited.
    logger.info(f"[*] Starting gallery {gallery_id}")
    meta = get_gallery_metadata(gallery_id)
    if not meta:
        logger.error(f"[!] Failed to fetch metadata for {gallery_id}")
        return

    # --- Language filter ---
    gallery_tags = [t.lower() for t in meta.get("tags", [])]
    gallery_langs = set(["english", "japanese", "chinese"]) & set(gallery_tags)
    if not set(config["language"]) & gallery_langs:
        logger.info(f"[!] Skipping gallery {gallery_id}: language not in {config['language']}")
        return

    # --- Process per artist ---
    for artist in meta.get("artists", ["Unknown Artist"]):
        # Folder style: /artist - doujin/
        title = get_gallery_title(meta)
        folder = os.path.join(SUWAYOMI_DIR, f"{safe_name(artist)} - {title}")
        num_pages = meta.get("num_pages", 0)

        # Skip if all pages already exist
        all_exist = all(
            os.path.exists(os.path.join(folder, f"{i+1}.jpg")) or
            os.path.exists(os.path.join(folder, f"{i+1}.png")) or
            os.path.exists(os.path.join(folder, f"{i+1}.gif"))
            for i in range(num_pages)
        )
        if all_exist and num_pages > 0:
            logger.info(f"[+] Skipping {gallery_id} ({folder}), already complete.")
            continue

        # Write details.json
        write_details_json(folder, meta)
        create_or_update_gallery(meta, folder)

        # Download images
        if num_pages > 0:
            if config['dry_run']:
                for i in range(num_pages):
                    url = get_image_url(meta, i + 1)
                    path = os.path.join(folder, f"{i+1}.{url.split('.')[-1]}")
                    logger.info(f"[*] [DRY-RUN] Would download: {url} -> {path}")
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=config['threads_images']) as img_executor:
                    futures = []
                    for i in range(num_pages):
                        url = get_image_url(meta, i + 1)
                        path = os.path.join(folder, f"{i+1}.{url.split('.')[-1]}")
                        futures.append(img_executor.submit(download_image, url, path))

                    for _ in tqdm(concurrent.futures.as_completed(futures), total=num_pages,
                                  desc=f"Gallery {gallery_id} ({safe_name(artist)})", unit="img"):
                        pass

def main():
    try:
        start, end = int(config['start']), int(config['end'])
        gallery_ids = list(range(start, end + 1))
        logger.info(f"\n[*] Galleries to process: {start} -> {end}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=config['threads_galleries']) as gallery_executor:
            gallery_futures = [gallery_executor.submit(process_gallery, gid) for gid in gallery_ids]
            concurrent.futures.wait(gallery_futures)

        logger.info("\n[*] All galleries processed.")
        return 0
    except Exception as e:
        logger.exception(e)
        return 1

if __name__ == "__main__":
    sys.exit(main())