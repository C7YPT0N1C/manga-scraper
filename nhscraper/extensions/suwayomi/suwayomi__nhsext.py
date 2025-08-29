#!/usr/bin/env python3
# extensions/suwayomi/suwayomi__nhsext.py

import os, subprocess, json

from nhscraper.core.logger import *
from nhscraper.core.config import update_env, config

# Global variable for download path, leave empty initially
extension_download_path = "/opt/suwayomi/downloads"

"""
Suwayomi metadata (details.json)format:

{
  "title": "AUTHOR_NAME",
  "author": "AUTHOR_NAME",
  "artist": "AUTHOR_NAME",
  "description": "An archive of AUTHOR_NAME's works.",
  "genre": ["tags_here"],
  "status": "1",
  "_status values": ["0=Unknown","1=Ongoing","2=Completed","3=Licensed"]
}
"""

# Hook for pre-download functionality. Set download path to extension's desired download path.
def pre_download_hook(config_dict, gallery_list):
    global extension_download_path
    extension_download_path = "/opt/suwayomi/local"
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)
    
    logger.debug(f"Suwayomi extension: Pre-download hook called")
    return gallery_list

# Hook for functionality during download
def during_download_hook(config_dict, gallery_id, gallery_metadata):
    logger.debug(f"Suwayomi extension: During-download hook for gallery {gallery_id}")

# Hook for functionality after each gallery download
def after_gallery_download(meta: dict):
    global extension_download_path
    artist = meta["artists"][0] if meta.get("artists") else "Unknown"
    details = {
        "title": artist,
        "author": artist,
        "artist": artist,
        "description": f"An archive of {artist}'s works.",
        "genre": meta.get("tags", []),
        "status": "1",
    }

    # Create folders
    gallery_folder = os.path.join(extension_download_path, artist, f"{meta['id']}")
    os.makedirs(gallery_folder, exist_ok=True)

    # Save details.json
    details_file = os.path.join(gallery_folder, "details.json")
    if config["DRY_RUN"]:
        logger.debug(f"Dry-run: Would save details.json to {details_file}")
    else:
        with open(details_file, "w", encoding="utf-8") as f:
            json.dump(details, f, ensure_ascii=False, indent=2)
        logger.debug(f"Suwayomi metadata saved for gallery {meta['id']}")

# Hook for functionality after all downloads are complete
def after_all_downloads(all_meta: list):
    logger.debug(f"Suwayomi extension: batch of {len(all_meta)} galleries downloaded")

# Hook for post-download functionality. Reset download path.
def post_download_hook(config_dict, completed_galleries):
    global extension_download_path
    extension_download_path = ""  # Reset after downloads
    update_env("EXTENSION_DOWNLOAD_PATH", "")
    logger.debug(f"Suwayomi extension: Post-download hook called")

# ------------------------------
# Install / Uninstall
# ------------------------------
def install_extension():
    global extension_download_path
    SUWAYOMI_DIR = "/opt/suwayomi/local"
    extension_download_path = SUWAYOMI_DIR
    os.makedirs(extension_download_path, exist_ok=True)
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)
    logger.info(f"Suwayomi extension installed at {extension_download_path}")

    # Systemd service
    service_file = "/etc/systemd/system/suwayomi-server.service"
    service_content = f"""[Unit]
Description=Suwayomi Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={SUWAYOMI_DIR}
ExecStart={SUWAYOMI_DIR}/suwayomi-server
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    with open(service_file, "w") as f:
        f.write(service_content)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "suwayomi-server"], check=True)
    logger.info("Suwayomi systemd service created and started")
    
    print("")
    print("Suwayomi Web: http://$IP:4567/")
    print("Suwayomi GraphQL: http://$IP:4567/api/graphql")

def uninstall_extension():
    global extension_download_path
    SUWAYOMI_DIR = "/opt/suwayomi/local"
    try:
        extension_download_path = ""
        update_env("EXTENSION_DOWNLOAD_PATH", "")
        service_file = "/etc/systemd/system/suwayomi-server.service"
        if os.path.exists(service_file):
            os.remove(service_file)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            log_clarification("info")
            logger.info("Suwayomi systemd service removed")
        log_clarification("info")
        logger.info("Suwayomi extension uninstalled")
    except Exception as e:
        log_clarification("error")
        logger.error(f"Failed to uninstall Suwayomi extension: {e}")

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
            log_clarification("warning")
            logger.warning(f"GraphQL query returned errors: {data['errors']}")
        return data.get("data")
    except Exception as e:
        log_clarification("error")
        logger.error(f"GraphQL request failed: {e}")
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