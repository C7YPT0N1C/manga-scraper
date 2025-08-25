#!/usr/bin/env python3
import os
import sys
import json
import logging
import argparse
from datetime import datetime
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor

import cloudscraper
import requests
from dotenv import load_dotenv, set_key

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

parser.add_argument("--start", type=int, help="Starting gallery ID (Default: 500000)")
parser.add_argument("--end", type=int, help="Ending gallery ID (Default: 600000)")
parser.add_argument("--excluded-tags", type=str, help="Comma-separated list of tags to exclude galleries (e.g: video game, yaoi, cosplay) (Default: none)")
parser.add_argument("--language", type=str, help="Comma-separated list of languages to include (e.g: english, japanese) (Default: english)")
parser.add_argument("--cookie", type=str, help="nhentai cookie string (REQUIRED AS A FLAG OR IN ENVIRONMENT FILE: (/opt/nhentai-scraper/config.env) )")
parser.add_argument("--user-agent", type=str, help="Browser User-Agent")
parser.add_argument("--threads-galleries", type=int, help="Number of concurrent galleries to be downloaded (Default: 3)")
parser.add_argument("--threads-images", type=int, help="Threads per gallery (Default: 5)")
parser.add_argument("--dry-run", action="store_true", help="Simulate downloads and GraphQL without downloading anything.")
parser.add_argument("--use-tor", action="store_true", help="Route requests via Tor. Requires Tor to be running on localhost:9050")
parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")

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
    "start": get_config("NHENTAI_START_ID", 0),
    "end": get_config("NHENTAI_END_ID", 0),
    "threads_galleries": get_config("THREADS_GALLERIES", 3),
    "threads_images": get_config("THREADS_IMAGES", 5),
    "cookie": get_config("NHENTAI_COOKIE", ""),
    "user_agent": get_config("NHENTAI_USER_AGENT", ""),
    "use_tor": get_config("USE_TOR", False, True),
    "verbose": get_config("NHENTAI_VERBOSE", False, True),
    "dry_run": get_config("NHENTAI_DRY_RUN", False, True),
}

if config["verbose"]:
    logger.setLevel(logging.DEBUG)

# ===============================
# HTTP SESSION
# ===============================
def build_session():
    s = cloudscraper.create_scraper()
    s.headers.update({
        "User-Agent": config["user_agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://nhentai.net/",
        "Cookie": config["cookie"],
    })
    if config["use_tor"]:
        proxy = "socks5h://127.0.0.1:9050"
        s.proxies.update({"http": proxy, "https": proxy})
        logger.info(f"[*] Using Tor proxy: {proxy}")
    else:
        logger.info("[*] Not using Tor proxy")
    return s

session = build_session()

# ===============================
# UTILITIES
# ===============================
def safe_name(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def suwayomi_folder_name(meta, artist):
    title = meta.get("title", f"Gallery_{meta.get('id', '000000')}")
    return os.path.join(SUWAYOMI_DIR, safe_name(artist), safe_name(title))

def write_details_json(folder, meta):
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "details.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        logger.debug(f"[+] Wrote {path}")
    except Exception as e:
        logger.error(f"[!] Failed to write details.json in {folder}: {e}")

# ===============================
# NHENTAI API
# ===============================
def get_gallery_metadata(gallery_id: int):
    url = urljoin(NHENTAI_API_BASE, f"gallery/{gallery_id}")
    try:
        r = session.get(url, timeout=30)
        if r.status_code != 200:
            logger.error(f"[!] API {gallery_id} HTTP {r.status_code}")
            return None
        data = r.json()
        title_obj = data.get("title", {}) or {}
        title = title_obj.get("english") or title_obj.get("japanese") or title_obj.get("pretty") or f"Gallery_{gallery_id}"
        tags = data.get("tags", [])
        artists = [t["name"] for t in tags if t.get("type") == "artist"] or ["Unknown"]
        meta = {
            "id": data.get("id", gallery_id),
            "title": title,
            "tags": [t.get("name") for t in tags if "name" in t],
            "artists": artists,
            "num_pages": data.get("num_pages"),
            "images": data.get("images", {}),
            "url": f"https://nhentai.net/g/{gallery_id}/",
        }
        return meta
    except Exception as e:
        logger.error(f"[!] Failed to fetch metadata for {gallery_id}: {e}")
        return None

# ===============================
# IMAGE DOWNLOAD
# ===============================
def download_image(url, path):
    if config["dry_run"]:
        logger.info(f"[DRY-RUN] Would download: {url} -> {path}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        resp = session.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        with open(path, "wb") as f:
            for chunk in resp.iter_content(1024*8):
                f.write(chunk)
        logger.info(f"[+] Downloaded: {path}")
    except Exception as e:
        logger.error(f"[!] Failed to download {url}: {e}")

def download_gallery_images(meta, folder):
    # Construct image URLs
    base = "https://i.nhentai.net/galleries/"
    media_id = str(meta.get("media_id", meta.get("id")))
    images_info = meta.get("images", {})
    if not images_info:
        logger.warning(f"[!] No image info for gallery {meta.get('id')}")
        return

    ext_map = {"j": "jpg", "p": "png", "g": "gif"}
    pages = images_info.get("pages") or []
    if not pages:
        pages = [images_info.get("cover")]  # fallback

    def download_page(i, p):
        ext_type = p.get("t")
        ext = ext_map.get(ext_type, "jpg")
        url = f"{base}{media_id}/{i+1}.{ext}"
        path = os.path.join(folder, f"{i+1}.{ext}")
        download_image(url, path)

    with ThreadPoolExecutor(max_workers=config["threads_images"]) as exe:
        for i, page in enumerate(pages):
            exe.submit(download_page, i, page)

# ===============================
# GRAPHQL
# ===============================
def graphql_request(query, variables):
    if config["dry_run"]:
        logger.info("[DRY-RUN] Skipping GraphQL request")
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
# MAIN LOOP
# ===============================
def main():
    logger.info(f"Starting galleries {config['start']} -> {config['end']}")
    for gallery_id in range(config['start'], config['end']+1):
        logger.info(f"[*] Starting gallery {gallery_id}")
        meta = get_gallery_metadata(gallery_id)
        if not meta:
            logger.error(f"[!] Failed to fetch metadata for {gallery_id}")
            continue

        for artist in meta.get("artists", ["Unknown"]):
            folder = suwayomi_folder_name(meta, artist)
            write_details_json(folder, meta)
            create_or_update_gallery(meta, folder)
            download_gallery_images(meta, folder)

    logger.info("[*] All galleries processed.")

if __name__ == "__main__":
    main()