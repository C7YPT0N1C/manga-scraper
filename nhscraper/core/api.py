#!/usr/bin/env python3
# nhscraper/api.py
import os
import sys
import time
import json
from datetime import datetime
from flask import Flask, jsonify, request
from nhscraper.core.logger import *
from nhscraper.core.config import config
from nhscraper.extensions.extension_loader import INSTALLED_EXTENSIONS

# ===============================
# Runtime log setup
# ===============================
LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)
runtime_log_file = os.path.join(
    LOG_DIR, f"runtime-{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)

# Flask app
app = Flask(__name__)

# ===============================
# Extension Pre/Post Hooks
# ===============================
def run_pre_download_hooks(gallery_list):
    for ext in INSTALLED_EXTENSIONS:
        if hasattr(ext, "pre_download_hook"):
            try:
                gallery_list = ext.pre_download_hook(config, gallery_list)
                log_clarification()
                logger.info(f"Pre-download hook executed for {getattr(ext, '__name__', 'unknown')}")
            except Exception as e:
                log_clarification()
                logger.error(f"Pre-download hook failed in {getattr(ext, '__name__', 'unknown')}: {e}")
    return gallery_list

def run_post_download_hooks(completed_galleries):
    for ext in INSTALLED_EXTENSIONS:
        if hasattr(ext, "post_download_hook"):
            try:
                ext.post_download_hook(config, completed_galleries)
                log_clarification()
                logger.info(f"Post-download hook executed for {getattr(ext, '__name__', 'unknown')}")
            except Exception as e:
                log_clarification()
                logger.error(f"Post-download hook failed in {getattr(ext, '__name__', 'unknown')}: {e}")

# ===============================
# Flask Endpoints
# ===============================
@app.route("/status", methods=["GET"])
def scraper_status():
    runtime_log = {
        "last_run": datetime.now().isoformat(),
        "success": True,
        "downloaded": 0,
        "skipped": 0,
        "error": None
    }
    return jsonify(runtime_log)

@app.route("/trigger_download", methods=["POST"])
def trigger_download():
    try:
        payload = request.json
        gallery_list = payload.get("gallery_list", [])
        gallery_list = run_pre_download_hooks(gallery_list)
        # Downloader should be invoked externally; we just pass the list
        run_post_download_hooks(gallery_list)
        return jsonify({"status": "success", "galleries": len(gallery_list)})
    except Exception as e:
        log_clarification()
        logger.error(f"Failed to trigger download: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ===============================
# Entrypoint
# ===============================
def main():
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()