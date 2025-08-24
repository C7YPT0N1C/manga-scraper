#!/usr/bin/env python3
import os
import sys
import json
import time
import argparse
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import requests

# =======================
# DEFAULT CONFIG
# =======================
DEFAULT_ROOT = "/opt/suwayomi/local/"
PROGRESS_FILE = "progress.json"
SKIPPED_LOG = "skipped.log"
VERBOSE = True

DEFAULT_EXCLUDE_TAGS = ["snuff","guro","cuntboy","cuntbusting","ai generated"]
DEFAULT_INCLUDE_TAGS = []
DEFAULT_LANGUAGE = "english"

RICTERZ_CMD = "nhentai"  # RicterZ/nhentai CLI must be installed and in PATH
TOR_PROXY = "socks5h://127.0.0.1:9050"

scraper_status = {
    "running": False,
    "last_checked": None,
    "last_gallery": None,
    "errors": [],
    "active_galleries": [],
    "using_tor": False,
}

# =======================
# UTILITY FUNCTIONS
# =======================
def log(msg):
    if VERBOSE:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {"last_id": 0}

def save_progress(last_id):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"last_id": last_id}, f)

def sanitize(name: str):
    return "".join(c if c.isalnum() or c in " ._-" else "_" for c in name)

def log_skipped(gallery_id, reason, root):
    with open(os.path.join(root, SKIPPED_LOG), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} | {gallery_id} | {reason}\n")
    log(f"[-] Skipped {gallery_id}: {reason}")

# =======================
# METADATA GENERATION
# =======================
def generate_suwayomi_metadata(gallery):
    metadata = {
        "title": gallery["title"],
        "author": gallery["artists"][0] if gallery["artists"] else "Unknown Artist",
        "artist": gallery["artists"][0] if gallery["artists"] else "Unknown Artist",
        "description": f"An archive of {gallery['artists'][0]}'s works." if gallery["artists"] else "",
        "genre": gallery["tags"],
        "status": "1",
        "_status values": ["0=Unknown","1=Ongoing","2=Completed","3=Licensed"]
    }
    return metadata

# =======================
# FETCH & PARSE
# =======================
def fetch_gallery_html(gallery_id, session, use_tor=False):
    url = f"https://nhentai.net/g/{gallery_id}/"
    headers = {"User-Agent": args.user_agent}
    cookies = {"session": args.cookie}

    proxies = {"http": TOR_PROXY, "https": TOR_PROXY} if use_tor else None

    for _ in range(3):
        try:
            r = session.get(url, headers=headers, cookies=cookies, proxies=proxies, timeout=10)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r.text
        except Exception as e:
            time.sleep(1)
    return None

def parse_gallery(gallery_id, session, root):
    html = fetch_gallery_html(gallery_id, session, args.use_tor)
    if not html:
        log_skipped(gallery_id, "404/fetch failed", root)
        return None

    soup = BeautifulSoup(html, "html.parser")

    lang_tag = soup.find("span", class_="language")
    if lang_tag and args.language.lower() not in lang_tag.text.lower():
        log_skipped(gallery_id, f"Non-{args.language}", root)
        return None

    tags = [t.text.lower() for t in soup.select(".tags a.tag")]
    if any(tag in args.exclude_tags for tag in tags):
        log_skipped(gallery_id, "Excluded tags", root)
        return None
    if args.include_tags and not any(tag in args.include_tags for tag in tags):
        log_skipped(gallery_id, "Missing required tags", root)
        return None

    artist_tags = soup.select(".artist a") + soup.select(".group a")
    artists = [sanitize(t.text.strip()) for t in artist_tags] or ["Unknown Artist"]

    title_tag = soup.select_one(".title h1,.title h2")
    title = sanitize(title_tag.text.strip()) if title_tag else f"Gallery_{gallery_id}"

    return {
        "id": gallery_id,
        "artists": artists,
        "title": title,
        "tags": tags
    }

# =======================
# DOWNLOAD
# =======================
def download_gallery(gallery, root):
    try:
        scraper_status["active_galleries"].append(gallery["id"])
        for artist in gallery["artists"]:
            artist_dir = Path(root) / artist
            artist_dir.mkdir(parents=True, exist_ok=True)
            doujin_dir = artist_dir / gallery["title"]
            doujin_dir.mkdir(exist_ok=True)

            # Build RicterZ command
            cmd = [
                RICTERZ_CMD,
                "--id", str(gallery["id"]),
                "--useragent", args.user_agent,
                "--cookie", args.cookie,
                "--output", str(doujin_dir)
            ]
            if args.use_tor:
                cmd = ["torsocks"] + cmd

            subprocess.run(cmd, check=True)

            # Generate details.json
            details_path = doujin_dir / "details.json"
            with open(details_path, "w", encoding="utf-8") as f:
                json.dump(generate_suwayomi_metadata(gallery), f, indent=2)
        log(f"[+] Downloaded gallery {gallery['id']}")
    except Exception as e:
        log_skipped(gallery["id"], f"Failed: {e}", root)
        scraper_status["errors"].append(str(e))
    finally:
        scraper_status["active_galleries"].remove(gallery["id"])
        scraper_status["last_gallery"] = gallery["id"]
        save_progress(gallery["id"])

# =======================
# MAIN
# =======================
def main():
    global args, VERBOSE

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=999999)
    parser.add_argument("--threads-galleries", type=int, default=3)
    parser.add_argument("--threads-images", type=int, default=5)
    parser.add_argument("--exclude-tags", type=str, default=",".join(DEFAULT_EXCLUDE_TAGS))
    parser.add_argument("--include-tags", type=str, default=",".join(DEFAULT_INCLUDE_TAGS))
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--use-tor", action="store_true")
    parser.add_argument("--use-vpn", action="store_true")
    parser.add_argument("--user-agent", required=True)
    parser.add_argument("--cookie", required=True)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    VERBOSE = args.verbose
    args.exclude_tags = [t.strip().lower() for t in args.exclude_tags.split(",") if t.strip()]
    args.include_tags = [t.strip().lower() for t in args.include_tags.split(",") if t.strip()]

    root = args.root
    Path(root).mkdir(parents=True, exist_ok=True)

    progress = load_progress()
    start_id = args.start if args.start else progress.get("last_id", 0) + 1

    session = requests.Session()

    log("[*] Starting scraper...")
    scraper_status["running"] = True
    scraper_status["using_tor"] = args.use_tor

    with ThreadPoolExecutor(max_workers=args.threads_galleries) as executor:
        futures = {}
        for gallery_id in range(start_id, args.end + 1):
            gallery = parse_gallery(gallery_id, session, root)
            if gallery:
                futures[executor.submit(download_gallery, gallery, root)] = gallery_id
            time.sleep(0.2)

        for fut in as_completed(futures):
            pass

    scraper_status["running"] = False
    log("[*] All done.")

if __name__ == "__main__":
    main()