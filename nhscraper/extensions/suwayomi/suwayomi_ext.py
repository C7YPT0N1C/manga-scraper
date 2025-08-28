#!/usr/bin/env python3
# extensions/suwayomi/suwayomi__nhsext.py

import os, subprocess, json
from core.logger import logger
from core.config import update_env, config

# Global variable for download path, leave empty initially
extension_download_path = ""

# Hook for pre-download functionality. Set download path to extension's desired download path.
def pre_download_hook(config_dict, gallery_list):
    global extension_download_path
    extension_download_path = "/opt/suwayomi/local"
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)
    logger.info(f"[*] Suwayomi extension: Pre-download hook called")
    return gallery_list

# Hook for functionality during download
def during_download_hook(config_dict, gallery_id, gallery_metadata):
    logger.info(f"[*] Suwayomi extension: During-download hook for gallery {gallery_id}")

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
        logger.info(f"[+] Dry-run: Would save details.json to {details_file}")
    else:
        with open(details_file, "w", encoding="utf-8") as f:
            json.dump(details, f, ensure_ascii=False, indent=2)
        logger.info(f"[+] Suwayomi metadata saved for gallery {meta['id']}")

# Hook for functionality after all downloads are complete
def after_all_downloads(all_meta: list):
    logger.info(f"[*] Suwayomi extension: batch of {len(all_meta)} galleries downloaded")

# Hook for post-download functionality. Reset download path.
def post_download_hook(config_dict, completed_galleries):
    global extension_download_path
    extension_download_path = ""  # Reset after downloads
    update_env("EXTENSION_DOWNLOAD_PATH", "")
    logger.info(f"[*] Suwayomi extension: Post-download hook called")

# ------------------------------
# Install / Uninstall
# ------------------------------
def install_extension():
    global extension_download_path
    SUWAYOMI_DIR = "/opt/suwayomi/local"
    extension_download_path = SUWAYOMI_DIR
    os.makedirs(extension_download_path, exist_ok=True)
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)
    logger.info(f"[+] Suwayomi extension installed at {extension_download_path}")

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
    logger.info("[+] Suwayomi systemd service created and started")

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
            logger.info("[+] Suwayomi systemd service removed")
        logger.info("[+] Suwayomi extension uninstalled")
    except Exception as e:
        logger.error(f"[!] Failed to uninstall Suwayomi extension: {e}")