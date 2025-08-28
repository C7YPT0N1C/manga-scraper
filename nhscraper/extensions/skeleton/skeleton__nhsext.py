#!/usr/bin/env python3
# extensions/skeleton/skeleton__nhsext.py

import os, subprocess, json
from core.logger import logger
from core.config import update_env

# Global variable for download path, leave empty initially
extension_download_path = ""

# Hook for pre-download functionality. Set download path to extension's desired download path.
def pre_download_hook(config, gallery_list):
    global extension_download_path
    extension_download_path = "./downloads_skeleton"  # example path
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)
    logger.info("[*] Skeleton extension: pre-download hook called")
    return gallery_list

# Hook for functionality during download
def during_download_hook(config, gallery_id, gallery_metadata):
    logger.info(f"[*] Skeleton extension: during-download hook for {gallery_id}")

# Hook for functionality after each gallery download
def after_gallery_download(meta: dict):
    logger.info(f"[*] Skeleton extension: gallery {meta['id']} downloaded")

# Hook for functionality after all downloads are complete
def after_all_downloads(all_meta: list):
    logger.info(f"[*] Skeleton extension: batch of {len(all_meta)} galleries downloaded")

# Hook for post-download functionality. Reset download path.
def post_download_hook(config, completed_galleries):
    global extension_download_path
    extension_download_path = ""  # Reset after downloads
    update_env("EXTENSION_DOWNLOAD_PATH", "")
    logger.info("[*] Skeleton extension: post-download hook called")

# ------------------------------
# Install / Uninstall
# ------------------------------
def install_extension():
    global extension_download_path
    extension_download_path = "./downloads_skeleton"
    os.makedirs(extension_download_path, exist_ok=True)
    update_env("EXTENSION_DOWNLOAD_PATH", extension_download_path)
    logger.info(f"[+] Skeleton extension installed at {extension_download_path}")

def uninstall_extension():
    global extension_download_path
    try:
        if os.path.exists(extension_download_path):
            os.rmdir(extension_download_path)
        extension_download_path = ""
        update_env("EXTENSION_DOWNLOAD_PATH", "")
        logger.info("[+] Skeleton extension uninstalled")
    except Exception as e:
        logger.error(f"[!] Failed to uninstall skeleton extension: {e}")