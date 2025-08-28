#!/usr/bin/env python3
# nhscraper/api.py
import os
import sys
import time
import json
from datetime import datetime
from flask import Flask, jsonify, request
from core.logger import logger
from core.config import config
from extensions.extension_loader import INSTALLED_EXTENSIONS

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
                logger.info(f"[*] Pre-download hook executed for {getattr(ext, '__name__', 'unknown')}")
            except Exception as e:
                logger.error(f"[!] Pre-download hook failed in {getattr(ext, '__name__', 'unknown')}: {e}")
    return gallery_list

def run_post_download_hooks(completed_galleries):
    for ext in INSTALLED_EXTENSIONS:
        if hasattr(ext, "post_download_hook"):
            try:
                ext.post_download_hook(config, completed_galleries)
                logger.info(f"[*] Post-download hook executed for {getattr(ext, '__name__', 'unknown')}")
            except Exception as e:
                logger.error(f"[!] Post-download hook failed in {getattr(ext, '__name__', 'unknown')}: {e}")

# ===============================
# GraphQL / Suwayomi
# ===============================
import requests

def graphql_query(query, variables=None):
    url = config.get("graphql_url", "http://127.0.0.1:4567/api/graphql")
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            logger.warning(f"[!] GraphQL query returned errors: {data['errors']}")
        return data.get("data")
    except Exception as e:
        logger.error(f"[!] GraphQL request failed: {e}")
        return None

def create_suwayomi_category(name):
    query = """
    mutation($name: String!) {
        createCategory(input: { name: $name }) {
            id
            name
        }
    }
    """
    return graphql_query(query, {"name": name})

def assign_gallery_to_category(gallery_id, category_id):
    query = """
    mutation($galleryId: Int!, $categoryId: Int!) {
        assignGalleryToCategory(galleryId: $galleryId, categoryId: $categoryId)
    }
    """
    return graphql_query(query, {"galleryId": gallery_id, "categoryId": category_id})

# ===============================
# Flask Endpoints
# ===============================
@app.route("/scraper_status", methods=["GET"])
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
        logger.error(f"[!] Failed to trigger download: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ===============================
# Entrypoint
# ===============================
def main():
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()