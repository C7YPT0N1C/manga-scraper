#!/usr/bin/env python3
# extensions/extension_loader.py

import os
import json
import importlib
import subprocess
from urllib.request import urlopen
from core.logger import logger

# ------------------------------
# Constants / Paths
# ------------------------------
EXTENSIONS_DIR = os.path.dirname(__file__)
LOCAL_MANIFEST_PATH = os.path.join(EXTENSIONS_DIR, "local_manifest.json")
REMOTE_MANIFEST_URL = "https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper-extensions/raw/branch/main/master_manifest.json"

INSTALLED_EXTENSIONS = []

# ------------------------------
# Helper functions
# ------------------------------
def load_local_manifest():
    """Load the local manifest, create it from remote if it doesn't exist."""
    if not os.path.exists(LOCAL_MANIFEST_PATH):
        logger.info("[*] Local manifest not found. Creating from remote...")
        update_local_manifest_from_remote()
    with open(LOCAL_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_local_manifest(manifest: dict):
    """Save the local manifest to disk."""
    with open(LOCAL_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

def fetch_remote_manifest():
    """Fetch remote manifest.json."""
    try:
        with urlopen(REMOTE_MANIFEST_URL) as response:
            return json.load(response)
    except Exception as e:
        logger.error(f"[!] Failed to fetch remote manifest: {e}")
        return {"extensions": []}

def update_local_manifest_from_remote():
    """Merge remote manifest into local manifest, keeping installed flags intact."""
    remote_manifest = fetch_remote_manifest()
    local_manifest = {"extensions": []}
    if os.path.exists(LOCAL_MANIFEST_PATH):
        with open(LOCAL_MANIFEST_PATH, "r", encoding="utf-8") as f:
            local_manifest = json.load(f)

    local_names = [ext["name"] for ext in local_manifest.get("extensions", [])]

    for remote_ext in remote_manifest.get("extensions", []):
        if remote_ext["name"] not in local_names:
            remote_ext["installed"] = False  # new extension default
            local_manifest["extensions"].append(remote_ext)
            logger.info(f"[+] Added new extension to local manifest: {remote_ext['name']}")

    save_local_manifest(local_manifest)
    return local_manifest

# ------------------------------
# Extension Loader
# ------------------------------
def load_installed_extensions():
    """Load installed extensions dynamically."""
    manifest = load_local_manifest()
    for ext in manifest.get("extensions", []):
        if ext.get("installed", False):
            ext_folder = os.path.join(EXTENSIONS_DIR, ext["name"])
            entry_point = os.path.join(ext_folder, ext["entry_point"])
            if os.path.exists(entry_point):
                module_name = f"extensions.{ext['name']}.{ext['entry_point'].replace('.py', '')}"
                try:
                    module = importlib.import_module(module_name)
                    INSTALLED_EXTENSIONS.append(module)
                    logger.info(f"[+] Loaded installed extension: {ext['name']}")
                except Exception as e:
                    logger.warning(f"[!] Failed to load extension {ext['name']}: {e}")
            else:
                logger.warning(f"[!] Entry point not found for extension {ext['name']}")

# ------------------------------
# Install / Uninstall Extension
# ------------------------------
def install_extension(extension_name: str):
    """Install an extension if not already installed."""
    manifest = update_local_manifest_from_remote()
    ext_entry = next((ext for ext in manifest["extensions"] if ext["name"] == extension_name), None)
    if not ext_entry:
        logger.error(f"[!] Extension '{extension_name}' not found in remote manifest")
        return

    if ext_entry.get("installed", False):
        logger.info(f"[*] Extension '{extension_name}' is already installed")
        return

    # Clone/download extension if needed
    ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)
    if not os.path.exists(ext_folder):
        repo_url = ext_entry["repo_url"]
        logger.info(f"[*] Cloning {extension_name} from {repo_url}...")
        subprocess.run(["git", "clone", repo_url, ext_folder], check=True)

    # Import and run install hook
    entry_point = ext_entry["entry_point"]
    module_name = f"extensions.{extension_name}.{entry_point.replace('.py', '')}"
    module = importlib.import_module(module_name)
    if hasattr(module, "install_extension"):
        module.install_extension()
        logger.info(f"[+] Extension '{extension_name}' installed successfully")

    # Update manifest
    ext_entry["installed"] = True
    save_local_manifest(manifest)

def uninstall_extension(extension_name: str):
    """Uninstall an extension."""
    manifest = load_local_manifest()
    ext_entry = next((ext for ext in manifest["extensions"] if ext["name"] == extension_name), None)
    if not ext_entry or not ext_entry.get("installed", False):
        logger.warning(f"[*] Extension '{extension_name}' is not installed")
        return

    # Import and run uninstall hook
    ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)
    entry_point = ext_entry["entry_point"]
    module_name = f"extensions.{extension_name}.{entry_point.replace('.py', '')}"
    module = importlib.import_module(module_name)
    if hasattr(module, "uninstall_extension"):
        module.uninstall_extension()
        logger.info(f"[+] Extension '{extension_name}' uninstalled successfully")

    # Update manifest
    ext_entry["installed"] = False
    save_local_manifest(manifest)

# ------------------------------
# Get selected extension (with skeleton fallback)
# ------------------------------
def get_selected_extension(name: str = "skeleton"):
    """
    Returns the selected extension module.
    Defaults to 'skeleton' if none specified or if requested extension not installed.
    """
    update_local_manifest_from_remote()
    load_installed_extensions()

    # Try requested extension first
    for ext in INSTALLED_EXTENSIONS:
        if getattr(ext, "__name__", "").lower().endswith(f"{name.lower()}__nhsext"):
            return ext

    # Fallback to skeleton
    for ext in INSTALLED_EXTENSIONS:
        if getattr(ext, "__name__", "").lower().endswith("skeleton__nhsext"):
            return ext

    logger.error("[!] Skeleton extension not found! This should never happen.")
    return None

# ------------------------------
# Run on import
# ------------------------------
load_installed_extensions()