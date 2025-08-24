#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import logging
import argparse
from datetime import datetime
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===============================
# CONFIGURATION
# ===============================
NHENTAI_DIR = "/opt/nhentai-scraper"
RICTERZ_DIR = "/opt/ricterz_nhentai"
RICTERZ_BIN = os.path.join(RICTERZ_DIR, "nhentai", "cmdline.py")
SUWAYOMI_DIR = "/opt/suwayomi"
LOGS_DIR = os.path.join(NHENTAI_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

GRAPHQL_URL = "http://127.0.0.1:4567/api/graphql"

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
parser.add_argument("--threads-galleries", type=int, default=3)
parser.add_argument("--threads-images", type=int, default=5)
parser.add_argument("--cookie", type=str, required=True)
parser.add_argument("--user-agent", type=str, required=True)
parser.add_argument("--use-tor", action="store_true")
parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
parser.add_argument("--dry-run", action="store_true", help="Simulate downloads")
args = parser.parse_args()

if args.verbose:
    logger.setLevel(logging.DEBUG)

PYTHON_BIN = os.path.join(NHENTAI_DIR, "venv", "bin", "python3")

# ===============================
# UTILITIES
# ===============================
def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def run_ricterz(gallery_id, output_dir):
    """Run RicterZ nhentai cmdline.py for a given gallery ID with per-image progress"""
    cmd = [
        PYTHON_BIN,
        RICTERZ_BIN,
        "--id", str(gallery_id),
        "--download",
        "-t", str(args.threads_images),
        "-o", output_dir,
        "--cookie", args.cookie,
        "--user-agent", args.user_agent
    ]
    if args.use_tor:
        cmd += ["--proxy", "socks5h://127.0.0.1:9050"]

    if args.dry_run:
        logger.info(f"[DRY-RUN] Would run: {' '.join(cmd)}")
        return True

    logger.debug(f"Running RicterZ command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            if "Downloading image" in line:
                logger.info(f"[{gallery_id}] {line.strip()}")
        logger.info(f"[+] Successfully processed gallery {gallery_id}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"[!] Failed to process gallery {gallery_id}: {e.stderr.strip()}")
        return False

def get_artists(meta):
    """Return list of artists, handle missing or multiple artists"""
    artist_field = meta.get("artist") or meta.get("group") or "Unknown"
    artists = [a.strip() for a in artist_field.replace("|", ",").split(",") if a.strip()]
    if not artists:
        artists = ["Unknown"]
    return artists

def suwayomi_folder_name(meta, artist):
    """Generate Suwayomi-compatible folder name"""
    title = meta.get("title", f"Gallery_{meta.get('id', '000000')}")
    safe_artist = artist.replace("/", "-").replace("\\", "-")
    return os.path.join(SUWAYOMI_DIR, safe_artist, title)

# ===============================
# GRAPHQL FUNCTIONS
# ===============================
def graphql_request(query, variables):
    """Send a GraphQL request to Suwayomi server"""
    if args.dry_run:
        logger.info("[DRY-RUN] Skipping GraphQL request")
        return None
    payload = {"query": query, "variables": variables}
    try:
        resp = requests.post(GRAPHQL_URL, json=payload)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[!] GraphQL request failed: {e}")
        return None

def create_or_update_gallery(meta, folder):
    """Add or update gallery in Suwayomi via GraphQL"""
    query = """
    mutation addGallery($input: GalleryInput!) {
        addGallery(input: $input) {
            id
            title
        }
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
# MAIN LOOP
# ===============================
def main():
    logger.info(f"Starting galleries {args.start} -> {args.end}")
    if args.dry_run:
        logger.info("[*] Dry-run mode is active; no files will be downloaded.\n")

    for gallery_id in range(args.start, args.end + 1):
        logger.info(f"[*] Starting gallery {gallery_id}")

        if args.dry_run:
            logger.info(f"[DRY-RUN] Would fetch metadata and download gallery {gallery_id}")
            continue

        meta_cmd = [
            PYTHON_BIN,
            RICTERZ_BIN,
            "--id", str(gallery_id),
            "--cookie", args.cookie,
            "--user-agent", args.user_agent,
            "--show"
        ]
        if args.use_tor:
            meta_cmd += ["--proxy", "socks5h://127.0.0.1:9050"]

        try:
            meta_output = subprocess.check_output(meta_cmd, text=True)
            meta = json.loads(meta_output)
        except Exception as e:
            logger.error(f"[!] Failed to fetch metadata for {gallery_id}: {e}")
            continue

        artists = get_artists(meta)
        for artist in artists:
            folder = suwayomi_folder_name(meta, artist)
            os.makedirs(folder, exist_ok=True)

            # Always send to GraphQL
            create_or_update_gallery(meta, folder)

            success = run_ricterz(gallery_id, folder)
            if success:
                logger.info(f"[+] Gallery {gallery_id} processed for artist '{artist}'")
            else:
                logger.error(f"[!] Gallery {gallery_id} failed for artist '{artist}'")

    logger.info("[*] All galleries processed.")

if __name__ == "__main__":
    main()