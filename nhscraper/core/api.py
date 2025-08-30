#!/usr/bin/env python3
# nhscraper/nhscraper_api.py

import os, json, time, random, re, requests
from flask import Flask, jsonify, request
from datetime import datetime
from urllib.parse import urljoin
from tqdm import tqdm
from threading import Thread, Lock

from nhscraper.core.logger import *
from nhscraper.core.config import *

app = Flask(__name__)

# ------------------------------
# LOG CLARIFICATION
# Prints Blank Line To Make Logs Look Cleaner)
# ------------------------------
def log_clarification():  
    print()
    logger.debug("")

log_clarification()
logger.info("API: Ready.")
logger.debug("API: Debugging Started.")

##################################################################################################################################
# GLOBAL STATE
##################################################################################################################################
last_gallery_id = None
running_galleries = []
gallery_metadata = {}  # global state for /status/galleries, key=gallery_id, value={'meta': {...}, 'status': ..., 'last_checked': ...}
state_lock = Lock()

##################################################################################################################################
# UTILITIES
##################################################################################################################################
def safe_name(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").strip()

def clean_title(meta):
    title_obj = meta.get("title", {}) or {}
    title_type = config.get("TITLE_TYPE", "pretty").lower()
    title = title_obj.get(title_type) or title_obj.get("english") or title_obj.get("japanese") or title_obj.get("pretty") or f"Gallery_{meta.get('id')}"
    if "|" in title: title = title.split("|")[-1].strip()
    title = re.sub(r'(\s*\[.*?\]\s*)+$', '', title.strip())
    return safe_name(title)

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
    max_attempts = config.get("MAX_ATTEMPTS", 3)
    
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
                    logger.error(f"Gallery {gallery_id} download failed after {max_attempts} attempts")
        elif stage == "graphql":
            if success:
                entry["graphql_status"] = "completed"
            else:
                entry["graphql_attempts"] += 1
                entry["graphql_status"] = "failed"
                if entry["graphql_attempts"] >= max_attempts:
                    logger.error(f"Gallery {gallery_id} GraphQL update failed after {max_attempts} attempts")

        entry["last_checked"] = datetime.now().isoformat()
        logger.info(f"Gallery {gallery_id} {stage} stage updated: {'success' if success else 'failure'}")

def get_tor_ip():
    """Fetch current IP, through Tor if enabled."""
    try:
        if config.get("USE_TOR", True):
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
            "message": "Skeleton GET live response"
        })
    elif request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        return jsonify({
            "timestamp": datetime.now().isoformat(),
            "message": "Skeleton POST live response",
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
    app.run(
    host="0.0.0.0",
    port=5000,
    debug=config.get("VERBOSE", False)
)