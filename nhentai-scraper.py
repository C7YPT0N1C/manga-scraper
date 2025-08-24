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
from dotenv import load_dotenv, set_key

# ===============================
# CONFIGURATION
# ===============================
NHENTAI_DIR = "/opt/nhentai-scraper"
RICTERZ_DIR = "/opt/ricterz_nhentai"
RICTERZ_BIN = os.path.normpath(os.path.join(RICTERZ_DIR, "nhentai", "cmdline.py"))
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
    """Update .env file with new value."""
    set_key(ENV_FILE, key, str(value))

# ===============================
# ARGUMENTS
# ===============================
parser = argparse.ArgumentParser(description="NHentai Scraper Wrapper")

parser.add_argument("--start", type=int, help="Start gallery ID")
parser.add_argument("--end", type=int, help="End gallery ID")
parser.add_argument("--threads-galleries", type=int, help="Concurrent galleries")
parser.add_argument("--threads-images", type=int, help="Threads for RicterZ downloader")
parser.add_argument("--cookie", type=str, help="nhentai cookie string")
parser.add_argument("--user-agent", type=str, help="browser User-Agent")
parser.add_argument("--use-tor", action="store_true", help="route requests via Tor (socks5h://127.0.0.1:9050)")
parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
parser.add_argument("--dry-run", action="store_true", help="Simulate downloads and GraphQL")

args = parser.parse_args()

# ===============================
# MERGE ENV + ARGS (Args override env)
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
            return int(env_val)
        return env_val
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

PYTHON_BIN = os.path.join(NHENTAI_DIR, "venv", "bin", "python3")

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
        logger.debug("[*] Using Tor proxy for metadata/API calls")
    return s

session = build_session()

# ===============================
# UTILITIES
# ===============================
def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_gallery_metadata_api(gallery_id: int):
    url = urljoin(NHENTAI_API_BASE, f"gallery/{gallery_id}")
    try:
        logger.debug(f"[API] GET {url}")
        r = session.get(url, timeout=30)
        if r.status_code != 200:
            logger.error(f"[!] API {gallery_id} HTTP {r.status_code}")
            return None
        data = r.json()
        title_obj = data.get("title", {}) or {}
        title = title_obj.get("english") or title_obj.get("japanese") or title_obj.get("pretty") or f"Gallery_{gallery_id}"
        tags = data.get("tags", []) or []
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
    except Exception as e:
        logger.error(f"[!] Failed to fetch metadata for {gallery_id}: {e}")
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
# GRAPHQL
# ===============================
def graphql_request(query, variables):
    if config["dry_run"]:
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
# RICTERZ DOWNLOADER
# ===============================
def run_ricterz_download(gallery_id, output_dir):
    if os.path.exists(os.path.join(output_dir, "details.json")):
        logger.info(f"[SKIP] {output_dir} already exists")
        return True
    cmd = [
        PYTHON_BIN,
        RICTERZ_BIN,
        "--id", str(gallery_id),
        "--download",
        "-t", str(config["threads_images"]),
        "-o", output_dir,
        "--cookie", config["cookie"],
        "--user-agent", config["user_agent"],
    ]
    if config["use_tor"]:
        cmd += ["--proxy", "socks5h://127.0.0.1:9050"]

    if config["dry_run"]:
        logger.info(f"[DRY-RUN] Would run: {' '.join(cmd)}")
        return True

    logger.debug(f"Running RicterZ command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
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
    logger.info(f"Starting galleries {config['start']} -> {config['end']}")
    if config["dry_run"]:
        logger.info("[*] Dry-run mode is active; no files will be downloaded.\n")

    for gallery_id in range(config["start"], config["end"] + 1):
        logger.info(f"[*] Starting gallery {gallery_id}")

        meta = get_gallery_metadata_api(gallery_id)
        if not meta:
            logger.error(f"[!] Failed to fetch metadata for {gallery_id}")
            continue

        artists = get_artists(meta)
        for artist in artists:
            folder = suwayomi_folder_name(meta, artist)
            os.makedirs(folder, exist_ok=True)
            write_details_json(folder, meta)
            create_or_update_gallery(meta, folder)
            run_ricterz_download(gallery_id, folder)

    logger.info("[*] All galleries processed.")

if __name__ == "__main__":
    main()