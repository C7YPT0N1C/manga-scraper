#!/usr/bin/env python3
# nhscraper/nhscraper_api.py

import os, json, time, random, requests, cloudscraper, concurrent.futures, re
from flask import Flask, jsonify, request
from datetime import datetime
from urllib.parse import urljoin
from tqdm import tqdm
from threading import Thread, Lock

from nhscraper.config import (
    logger,
    config,
    NHENTAI_DIR,
    SUWAYOMI_DIR,
    GRAPHQL_URL,
    NHENTAI_API_BASE,
    MIRRORS
)
import nhscraper.graphql_api as gql

app = Flask(__name__)

# ===============================
# KEY
# ===============================
# [*] = Process / In Progress (Prefer logger.info)
# [+] = Success / Confirmation (Prefer logger.info)
# [!] = Warning/Error (Prefer logger.warning on soft errors, logger.error on critical errors)
# (Use logger.debug for debugging)

##################################################################################################################################
# GLOBAL STATE
##################################################################################################################################
last_gallery_id = None
running_galleries = []
gallery_metadata = {}  # global state for /status/galleries, key=gallery_id, value={'meta': {...}, 'status': ..., 'last_checked': ...}
state_lock = Lock()

##################################################################################################################################
# LOGGING
##################################################################################################################################
def log_clarification(): # Print new line for readability.
    print()
    logger.debug("")

##################################################################################################################################
# HTTP session
##################################################################################################################################
def build_session(): # Tor Lives Here.
    s = cloudscraper.create_scraper()
    s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*"})
    if config.get("use_tor", False):
        proxy = "socks5h://127.0.0.1:9050"
        s.proxies.update({"http": proxy, "https": proxy})
        logger.info(f"[+] Using Tor proxy: {proxy}")
    else:
        logger.warning("[+] Not using Tor proxy")
    return s

session = build_session()

def set_local_directory(): # Call GraphQL to update Suwayomi's local directory to match SUWAYOMI_DIR.
    gql.set_local_directory()

def update_library(): # Call GraphQL to update the library after galleries are downloaded.
    gql.update_library()

##################################################################################################################################
# UTILITIES
##################################################################################################################################
def safe_name(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def clean_title(meta):
    title_obj = meta.get("title", {}) or {}
    title_type = config.get("title_type", "pretty").lower()
    title = title_obj.get(title_type) or title_obj.get("english") or title_obj.get("japanese") or title_obj.get("pretty") or f"Gallery_{meta.get('id')}"
    if "|" in title: title = title.split("|")[-1].strip()
    title = re.sub(r'(\s*\[.*?\]\s*)+$', '', title.strip())
    return safe_name(title)

##################################################################################################################################
# GALLERY / IMAGE HANDLERS
##################################################################################################################################
def get_gallery_metadata(gallery_id: int, retries=3, delay=2): # Fetch gallery metadata and update global state safely.
    url = urljoin(NHENTAI_API_BASE, f"gallery/{gallery_id}")

    for attempt in range(1, retries + 1):
        try:
            logger.debug(f"[+] Gallery {gallery_id}: Fetching metadata, attempt {attempt}")

            r = session.get(url, timeout=30)
            if r.status_code == 429:
                wait = random.uniform(1, 3)
                logger.warning(f"[!] HTTP 429 for Gallery {gallery_id}, sleeping {wait:.1f}s")
                time.sleep(wait)
                continue

            r.raise_for_status()
            data = r.json()

            tags = data.get("tags", [])
            artists = [t.get("name") for t in tags if t.get("type") == "artist"] or ["Unknown Artist"]

            meta = {
                "id": data.get("id"),
                "media_id": data.get("media_id"),
                "title": data.get("title", {}),
                "tags": [t.get("name") for t in tags if "name" in t],
                "artists": artists,
                "num_pages": data.get("num_pages"),
                "images": data.get("images", {}),
                "url": f"https://nhentai.net/g/{gallery_id}/",
            }

            logger.debug(f"[+] Gallery {gallery_id}: Fetched metadata.")
            log_clarification()

            # Update shared state atomically
            with state_lock:
                global last_gallery_id
                last_gallery_id = gallery_id

                if gallery_id not in running_galleries:
                    running_galleries.append(gallery_id)

                # Preserve status if set; refresh meta + last_checked every time
                prev = gallery_metadata.get(gallery_id, {})
                gallery_metadata[gallery_id] = {
                    "meta": meta,
                    "status": prev.get("status", "incomplete"),
                    "last_checked": datetime.now().isoformat(),
                }

            return meta

        except Exception as e:
            logger.warning(f"[!] Attempt {attempt} failed for {gallery_id}: {e}")
            time.sleep(delay)

    # All retries exhausted: mark failed, log clearly
    with state_lock:
        gallery_metadata[gallery_id] = {
            "meta": None,
            "status": "failed",
            "last_checked": datetime.now().isoformat(),
        }
    logger.error(f"[!] Failed to fetch metadata for {gallery_id} after {retries} attempts")
    return None

def get_image_url(meta, page, mirror_index=0): # Generate the full image URL for a gallery page with robust checks.
    try:
        gallery_id = meta.get("id")
        images = meta.get("images") or {}
        pages = images.get("pages")

        if not pages or not isinstance(pages, list):
            logger.error(f"[!] Gallery {gallery_id}: No page list in metadata")
            return None

        if page < 1 or page > len(pages):
            logger.error(f"[!] Gallery {gallery_id}: Page {page} out of range (1..{len(pages)})")
            return None

        file_info = pages[page - 1]
        ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
        ext = ext_map.get(file_info.get("t"), "jpg")

        media_id = meta.get("media_id")
        if not media_id:
            logger.error(f"[!] Gallery {gallery_id}: Missing media_id in metadata")
            return None

        base = MIRRORS[mirror_index % len(MIRRORS)]
        url = f"{base}/galleries/{media_id}/{page}.{ext}"

        logger.debug(f"[+] Page {page} of Gallery {gallery_id} - GET URL: Using mirror {base.split('/')[2]} -> {url}")
        log_clarification()

        return url

    except Exception as e:
        logger.error(f"[!] Failed to get image URL (Gallery {meta.get('id')}, page {page}): {e}")

##################################################################################################################################
# STATE HELPERS
##################################################################################################################################
def get_last_gallery_id():
    with state_lock:
        return last_gallery_id

def get_running_galleries():
    with state_lock:
        return list(running_galleries)
        
def update_gallery_state(gallery_id: int, stage="download", success=True):
    # Unified function to update gallery state per stage.
    # stage: 'download' or 'graphql'
    # success: True if stage completed, False if failed
    max_attempts = config.get("max_attempts", 3)
    
    with state_lock:
        entry = gallery_metadata.setdefault(gallery_id, {"meta": None})
        entry.setdefault("download_attempts", 0)
        entry.setdefault("graphql_attempts", 0)

        if stage == "download":
            if success:
                entry["download_status"] = "completed"
            else:
                entry["download_attempts"] += 1
                entry["download_status"] = "failed"
                if entry["download_attempts"] >= max_attempts:
                    logger.error(f"[!] Gallery {gallery_id} download failed after {max_attempts} attempts")
        elif stage == "graphql":
            if success:
                entry["graphql_status"] = "completed"
            else:
                entry["graphql_attempts"] += 1
                entry["graphql_status"] = "failed"
                if entry["graphql_attempts"] >= max_attempts:
                    logger.error(f"[!] Gallery {gallery_id} GraphQL update failed after {max_attempts} attempts")

        entry["last_checked"] = datetime.now().isoformat()
        logger.info(f"[+] Gallery {gallery_id} {stage} stage updated: {'success' if success else 'failure'}")

def get_tor_ip():
    """Fetch current IP, through Tor if enabled."""
    try:
        if config.get("use_tor", False):
            r = requests.get("https://httpbin.org/ip",
                             proxies={
                                 "http": "socks5h://127.0.0.1:9050",
                                 "https": "socks5h://127.0.0.1:9050"
                             },
                             timeout=10)
        else:
            r = requests.get("https://httpbin.org/ip", timeout=10)
        r.raise_for_status()
        return r.json().get("origin")
    except Exception as e:
        return f"Error: {e}"

##################################################################################################################################
# FLASK ENDPOINTS
##################################################################################################################################
@app.route("/skeleton", methods=["GET", "POST"])
def skeleton_endpoint():
    if request.method == "GET":
        return jsonify({
            "timestamp": datetime.now().isoformat(),
            "message": "[+] Skeleton GET live response"
        })
    elif request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        return jsonify({
            "timestamp": datetime.now().isoformat(),
            "message": "[+] Skeleton POST live response",
            "received_data": data
        })

@app.route("/status", methods=["GET"])
def status_endpoint():
    return jsonify({
        "errors": [],
        "last_checked": datetime.now().isoformat(),
        "last_gallery": get_last_gallery_id(),
        "running_galleries": get_running_galleries(),
        "tor_ip": get_tor_ip()
    })

@app.route("/status/galleries", methods=["GET"])
def all_galleries_status():
    """Return live metadata and status for all galleries."""
    with state_lock:
        for gid in running_galleries:
            if gid in gallery_metadata:
                gallery_metadata[gid]["status"] = "running"
                gallery_metadata[gid]["last_checked"] = datetime.now().isoformat()

        result = {
            gid: {
                "gallery_id": gid,
                "status": info.get("status"),
                "last_checked": info.get("last_checked"),
                "meta": info.get("meta")
            }
            for gid, info in gallery_metadata.items()
        }

    return jsonify({
        "last_checked": datetime.now().isoformat(),
        "galleries": result
    })

##################################################################################################################################
# MAIN ENTRYPOINT
##################################################################################################################################
if __name__ == "__main__":
    log_clarification()
    logger.info("[+] API Server initialised.")
    logger.debug("[*] API Debugging started.")
    
    app.run(
    host="0.0.0.0",
    port=5000,
    debug=config.get("VERBOSE", False)
)