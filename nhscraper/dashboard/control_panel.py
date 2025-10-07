import os, time, random, cloudscraper, requests, re, json, threading, socket, urllib.parse

from flask import Flask, jsonify, request

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *

################################################################################################################
# GLOBAL VARIABLES
################################################################################################################

app = Flask(__name__)
last_gallery_id = None
running_galleries = []
gallery_metadata = {}  # global state for /status/galleries, key=gallery_id, value={'meta': {...}, 'status': ..., 'last_checked': ...}
state_lock = threading.Lock()

##################################################################################################################################
# API STATE HELPERS
##################################################################################################################################

def get_tor_ip():
    """
    Fetch current IP, through Tor if enabled.
    """
    try:
        if use_tor:
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
                if entry["download_attempts"] >= orchestrator.max_retries:
                    logger.error(f"Gallery {gallery_id}: Download failed after {orchestrator.max_retries} attempts")

        entry["last_checked"] = datetime.now().isoformat()
        logger.info(f"Gallery {gallery_id}: {stage} stage updated: {'success' if success else 'failure'}")

##################################################################################################################################
# FLASK ENDPOINTS
##################################################################################################################################

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
    """
    Return live metadata and status for all galleries.
    """
    
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
    """
    This is one this module's entrypoints.
    """
    
    app.run(
    host="0.0.0.0",
    port=5000,
    debug=debug
)