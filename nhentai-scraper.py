#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import logging
import argparse
from datetime import datetime
from urllib.parse import urljoin

import requests
import cloudscraper

# ===============================
# CONFIGURATION
# ===============================
NHENTAI_DIR = "/opt/nhentai-scraper"
RICTERZ_DIR = "/opt/ricterz_nhentai"
RICTERZ_BIN = os.path.normpath(os.path.join(RICTERZ_DIR, "nhentai", "cmdline.py"))
SUWAYOMI_DIR = "/opt/suwayomi/local"
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
# ARGUMENTS
# ===============================
parser = argparse.ArgumentParser(description="NHentai Scraper Wrapper")
parser.add_argument("--start", type=int, required=True, help="Start gallery ID")
parser.add_argument("--end", type=int, required=True, help="End gallery ID")
parser.add_argument("--threads-galleries", type=int, default=3, help="(reserved) concurrent galleries")
parser.add_argument("--threads-images", type=int, default=5, help="threads for RicterZ downloader")
parser.add_argument("--cookie", type=str, required=True, help="nhentai cookie string")
parser.add_argument("--user-agent", type=str, required=True, help="browser User-Agent")
parser.add_argument("--use-tor", action="store_true", help="route requests via Tor (socks5h://127.0.0.1:9050)")
parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
parser.add_argument("--dry-run", action="store_true", help="Simulate downloads and GraphQL")
args = parser.parse_args()

if args.verbose:
    logger.setLevel(logging.DEBUG)

PYTHON_BIN = os.path.join(NHENTAI_DIR, "venv", "bin", "python3")

# ===============================
# HTTP SESSION (Cloudflare-aware)
# ===============================
def build_session():
    s = cloudscraper.create_scraper()
    s.headers.update({
        "User-Agent": args.user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://nhentai.net/",
        "Cookie": args.cookie,
    })
    if args.use_tor:
        proxy = "socks5h://127.0.0.1:9050"
        s.proxies.update({"http": proxy, "https": proxy})
        logger.debug("[*] Using Tor proxy for metadata/API calls")
    return s

session = build_session()

# ===============================
# UTILITIES
# ===============================
def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_gallery_metadata_api(gallery_id: int):
    """
    Fetch metadata from nhentai public API: /api/gallery/{id}
    Returns a dict or None.
    """
    url = urljoin(NHENTAI_API_BASE, f"gallery/{gallery_id}")
    try:
        logger.debug(f"[API] GET {url}")
        r = session.get(url, timeout=30)
        if r.status_code != 200:
            logger.error(f"[!] API {gallery_id} HTTP {r.status_code}")
            return None
        data = r.json()
        # Shape metadata
        title_obj = data.get("title", {}) or {}
        title = title_obj.get("english") or title_obj.get("japanese") or title_obj.get("pretty") or f"Gallery_{gallery_id}"
        tags = data.get("tags", []) or []

        # Extract artists from tags
        artists = [t["name"] for t in tags if t.get("type") == "artist"]
        if not artists:
            artists = ["Unknown"]

        meta = {
            "id": data.get("id", gallery_id),
            "title": title,
            "tags": [t.get("name") for t in tags if "name" in t],
            "artists": artists,
            "num_pages": data.get("num_pages"),
            "url": f"https://nhentai.net/g/{gallery_id}/",
        }
        logger.debug(f"[API] Parsed metadata for {gallery_id}: {json.dumps(meta)[:300]}")
        return meta
    except requests.RequestException as e:
        logger.error(f"[!] API request failed for {gallery_id}: {e}")
        return None
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"[!] API JSON decode failed for {gallery_id}: {e}")
        return None

def get_artists(meta):
    artists = meta.get("artists") or ["Unknown"]
    cleaned = [a.strip() for a in artists if a and a.strip()]
    return cleaned or ["Unknown"]

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
# GRAPHQL FUNCTIONS
# ===============================
def graphql_request(query, variables):
    if args.dry_run:
        logger.info("[DRY-RUN] Skipping GraphQL request")
        return None
    payload = {"query": query, "variables": variables}
    try:
        resp = requests.post(GRAPHQL_URL, json=payload, timeout=30)
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
            "artists": get_artists(meta),
            "tags": meta.get("tags", []),
            "url": meta.get("url"),
            "local_path": folder
        }
    }
    result = graphql_request(query, variables)
    if result:
        logger.info(f"[+] Gallery {meta.get('id')} sent to Suwayomi GraphQL")

# ===============================
# DOWNLOADER (RicterZ cmdline.py)
# ===============================
def run_ricterz_download(gallery_id, output_dir):
    """
    Use RicterZ/nhentai/cmdline.py to download images to output_dir.
    """
    cmd = [
        PYTHON_BIN,
        RICTERZ_BIN,
        "--id", str(gallery_id),
        "--download",
        "-t", str(args.threads_images),
        "-o", output_dir,
        "--cookie", args.cookie,
        "--user-agent", args.user_agent,
    ]
    if args.use_tor:
        cmd += ["--proxy", "socks5h://127.0.0.1:9050"]

    if args.dry_run:
        logger.info(f"[DRY-RUN] Would run: {' '.join(cmd)}")
        return True

    logger.debug(f"Running RicterZ command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        # Surface some progress lines if present
        for line in result.stdout.splitlines():
            if "Downloading" in line or "image" in line.lower():
                logger.info(f"[{gallery_id}] {line.strip()}")
        logger.info(f"[+] Successfully downloaded gallery {gallery_id}")
        return True
    except subprocess.CalledProcessError as e:
        combined = (e.stdout or "") + "\n" + (e.stderr or "")
        logger.error(f"[!] Download failed for {gallery_id}: {combined.strip()}")
        return False

# ===============================
# MAIN LOOP
# ===============================
def main():
    logger.info(f"Starting galleries {args.start} -> {args.end}")
    if args.dry_run:
        logger.info("[*] Dry-run mode is active; no files will be downloaded.\n")

    for gallery_id in range(args.start, args.end + 1):
        logger.info(f"[*] Starting gallery {gallery_id}")

        # 1) Fetch metadata from API
        meta = get_gallery_metadata_api(gallery_id)
        if not meta:
            logger.error(f"[!] Failed to fetch metadata for {gallery_id}")
            continue

        # 2) For each artist, prepare folder (artist/title), write metadata, send GraphQL
        artists = get_artists(meta)
        for artist in artists:
            folder = suwayomi_folder_name(meta, artist)
            os.makedirs(folder, exist_ok=True)
            write_details_json(folder, meta)

            # GraphQL (active unless dry-run)
            create_or_update_gallery(meta, folder)

            # 3) Download images via RicterZ tool
            success = run_ricterz_download(gallery_id, folder)
            if success:
                logger.info(f"[+] Gallery {gallery_id} processed for artist '{artist}'")
            else:
                logger.error(f"[!] Gallery {gallery_id} failed for artist '{artist}'")

    logger.info("[*] All galleries processed.")

if __name__ == "__main__":
    main()