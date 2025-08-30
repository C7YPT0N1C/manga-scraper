#!/usr/bin/env python3
# extensions/suwayomi/suwayomi__nhsext.py

import os, json, subprocess, requests

from nhscraper.core.logger import *
from nhscraper.core.config import *

# ===============================
# GLOBALS
# ===============================
SUWAYOMI_DIR = "/opt/suwayomi/local"
extension_download_path = SUWAYOMI_DIR
GRAPHQL_URL = config.get("GRAPHQL_URL", "http://127.0.0.1:4567/api/graphql")
STUB_ONLY = False  # If True, no real GraphQL calls are made, only stubs

# ===============================
# GRAPHQL REQUEST
# ===============================
def graphql_request(query, variables=None, USE_STUB=False):
    if STUB_ONLY or USE_STUB:
        log_clarification()
        logger.info(f"[STUB] Skipping GraphQL call. Query: {query}, Variables: {variables}")
        return {"data": "stub"}

    if config.get("dry_run", False):
        log_clarification()
        logger.info("[DRY-RUN] Skipping GraphQL request")
        return None

    try:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = requests.post(GRAPHQL_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data and data["errors"]:
            log_clarification()
            logger.warning(f"GraphQL request returned errors: {data['errors']}")
        return data.get("data")
    except Exception as e:
        log_clarification()
        logger.error(f"GraphQL request failed: {e}")
        return None

# ===============================
# SUWAYOMI INSTALL/UNINSTALL
# ===============================
def install_extension():
    global extension_download_path
    extension_download_path = SUWAYOMI_DIR
    os.makedirs(extension_download_path, exist_ok=True)
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)
    logger.info(f"Suwayomi extension installed at {extension_download_path}")

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
    print("\nSuwayomi Web: http://$IP:4567/")
    print("Suwayomi GraphQL: http://$IP:4567/api/graphql")

def uninstall_extension():
    global extension_download_path
    extension_download_path = ""
    update_env("EXTENSION_DOWNLOAD_PATH", "")
    service_file = "/etc/systemd/system/suwayomi-server.service"
    if os.path.exists(service_file):
        os.remove(service_file)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        logger.info("Suwayomi systemd service removed")
    logger.info("Suwayomi extension uninstalled")

# ===============================
# DOWNLOAD HOOKS
# ===============================
def pre_download_hook(config_dict, gallery_list):
    global extension_download_path
    extension_download_path = SUWAYOMI_DIR
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)
    logger.debug("Suwayomi extension: Pre-download hook called")
    return gallery_list

def during_download_hook(config_dict, gallery_id, gallery_metadata):
    logger.debug(f"Suwayomi extension: During-download hook for gallery {gallery_id}")

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

    gallery_folder = os.path.join(extension_download_path, artist, f"{meta['id']}")
    os.makedirs(gallery_folder, exist_ok=True)
    details_file = os.path.join(gallery_folder, "details.json")
    if config.get("DRY_RUN", False):
        logger.debug(f"Dry-run: Would save details.json to {details_file}")
    else:
        with open(details_file, "w", encoding="utf-8") as f:
            json.dump(details, f, ensure_ascii=False, indent=2)
        logger.debug(f"Suwayomi metadata saved for gallery {meta['id']}")

def after_all_downloads(all_meta: list):
    logger.debug(f"Suwayomi extension: batch of {len(all_meta)} galleries downloaded")

def post_download_hook(config_dict, completed_galleries):
    global extension_download_path
    extension_download_path = ""
    update_env("EXTENSION_DOWNLOAD_PATH", "")
    logger.debug("Suwayomi extension: Post-download hook called")

# ===============================
# SUWAYOMI GRAPHQL HELPERS
# ===============================
def create_suwayomi_category(name):
    query = """
    mutation($name: String!) {
        createCategory(input: { name: $name }) { id name }
    }
    """
    return graphql_request(query, {"name": name})

def assign_gallery_to_category(gallery_id, category_id):
    query = """
    mutation($galleryId: Int!, $categoryId: Int!) {
        assignGalleryToCategory(galleryId: $galleryId, categoryId: $categoryId)
    }
    """
    return graphql_request(query, {"galleryId": gallery_id, "categoryId": category_id})