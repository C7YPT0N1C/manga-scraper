#!/usr/bin/env python3
# extensions/extension_loader.py

import os, json, importlib, subprocess
from urllib.request import urlopen

from nhscraper.core.config import *
from nhscraper.extensions import * # Ensure extensions package is recognised

# ------------------------------
# Constants / Paths
# ------------------------------
EXTENSIONS_DIR = os.path.dirname(__file__)
LOCAL_MANIFEST_PATH = os.path.join(EXTENSIONS_DIR, "local_manifest.json")

# Primary + backup manifest locations
REMOTE_MANIFEST_URL = (
    "https://code.zenithnetwork.online/C7YPT0N1C/"
    "nhentai-scraper-extensions/raw/branch/main/master_manifest.json"
)
REMOTE_MANIFEST_BACKUP_URL = (
    "https://github.com/C7YPT0N1C/nhentai-scraper-extensions/"
    "raw/main/master_manifest.json"
)

# If the base repo URLs can also fail
BASE_REPO_URL = "https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper-extensions/"
BASE_REPO_BACKUP_URL = "https://github.com/C7YPT0N1C/nhentai-scraper-extensions/"

INSTALLED_EXTENSIONS = []

# ------------------------------
# Helper functions
# ------------------------------
def load_local_manifest():
    """Load the local manifest, create it from remote if it doesn't exist."""
    if not os.path.exists(LOCAL_MANIFEST_PATH):
        logger.info("Local manifest not found. Creating from remote...")
        update_local_manifest_from_remote()
    with open(LOCAL_MANIFEST_PATH, "r", encoding="utf-8") as f:
        json_load = json.load(f)
        #logger.debug("Local Manifest: {json_load}")
        return json_load

def save_local_manifest(manifest: dict):
    """Save the local manifest to disk."""
    with open(LOCAL_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

def fetch_remote_manifest():
    """Fetch remote manifest.json with backup fallback."""
    try:
        with urlopen(REMOTE_MANIFEST_URL) as response:
            return json.load(response)
    except Exception as e:
        log_clarification()
        logger.warning(f"Failed to fetch primary remote manifest: {e}")
        try:
            with urlopen(REMOTE_MANIFEST_BACKUP_URL) as response:
                logger.info("Using backup remote manifest URL")
                return json.load(response)
        except Exception as e2:
            log_clarification()
            logger.error(f"Failed to fetch backup manifest: {e2}")
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
            log_clarification()
            logger.debug(f"Added new extension to local manifest: {remote_ext['name']}")

    save_local_manifest(local_manifest)
    return local_manifest

# ------------------------------
# Extension Loader
# ------------------------------
def load_installed_extensions():
    """Load installed extensions dynamically."""
    INSTALLED_EXTENSIONS.clear()  # Ensure no duplicates if called multiple times
    manifest = load_local_manifest()
    
    for ext in manifest.get("extensions", []):
        if ext.get("installed", False):
            ext_folder = os.path.join(EXTENSIONS_DIR, ext["name"])
            entry_point = os.path.join(ext_folder, ext["entry_point"])
            if os.path.exists(entry_point):
                module_name = f"nhscraper.extensions.{ext['name']}.{ext['entry_point'].replace('.py', '')}"    

                try:
                    module = importlib.import_module(module_name)
                    INSTALLED_EXTENSIONS.append(module)
                    logger.debug(f"Extension: {ext['name']}: Loaded.")
                except Exception as e:
                    logger.warning(f"Extension: {ext['name']}: Failed to load: {e}")
            else:
                logger.warning(f"Extension: {ext['name']}: Entry point not found.")

# ------------------------------
# Install / Uninstall Extension
# ------------------------------
def install_selected_extension(extension_name: str):
    manifest = update_local_manifest_from_remote()
    ext_entry = next((ext for ext in manifest["extensions"] if ext["name"] == extension_name), None)
    if not ext_entry:
        logger.error(f"Extension '{extension_name}': Not found in remote manifest")
        return

    if ext_entry.get("installed", False):
        logger.info(f"Extension '{extension_name}': Already installed")
        return

    repo_url = ext_entry.get("repo_url", "")
    ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)

    if not os.path.exists(ext_folder):
        try:
            logger.debug(f"Sparse cloning {extension_name} from {repo_url}...")

            os.makedirs(ext_folder, exist_ok=True)
            subprocess.run(["git", "init", ext_folder], check=True)
            subprocess.run(["git", "-C", ext_folder, "remote", "add", "origin", repo_url], check=True)
            subprocess.run(["git", "-C", ext_folder, "config", "core.sparseCheckout", "true"], check=True)

            # Enable sparse-checkout of only the needed extension folder
            sparse_file = os.path.join(ext_folder, ".git", "info", "sparse-checkout")
            with open(sparse_file, "w") as f:
                f.write(f"{extension_name}/\n")

            subprocess.run(["git", "-C", ext_folder, "pull", "origin", "main"], check=True)

        except Exception as e:
            logger.error(f"Failed to sparse-clone {extension_name}: {e}")
            return

    # Import and run install hook
    entry_point = ext_entry["entry_point"]
    module_name = f"nhscraper.extensions.{extension_name}.{entry_point.replace('.py', '')}"
    module = importlib.import_module(module_name)
    if hasattr(module, "install_extension"):
        module.install_extension()
        log_clarification()
        logger.info(f"Extension '{extension_name}': Installed successfully.")

    # Update manifest
    ext_entry["installed"] = True
    save_local_manifest(manifest)

def uninstall_selected_extension(extension_name: str):
    """Uninstall an extension."""
    manifest = load_local_manifest()
    ext_entry = next((ext for ext in manifest["extensions"] if ext["name"] == extension_name), None)
    if not ext_entry or not ext_entry.get("installed", False):
        log_clarification()
        logger.warning(f"Extension '{extension_name}': Not installed")
        return

    # Import and run uninstall hook
    ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)
    entry_point = ext_entry["entry_point"]
    module_name = f"extensions.{extension_name}.{entry_point.replace('.py', '')}"
    module = importlib.import_module(module_name)
    if hasattr(module, "uninstall_extension"):
        module.uninstall_extension()
        log_clarification()
        logger.info(f"Extension '{extension_name}': Uninstalled successfully.")

    # Update manifest
    ext_entry["installed"] = False
    save_local_manifest(manifest)

# ------------------------------
# Get selected extension (with skeleton fallback)
# ------------------------------
def get_selected_extension(name: str = "skeleton"):
    """
    Returns the selected extension module.
    If the extension is not installed, installs it first.
    Defaults to 'skeleton' if none specified or if requested extension not found.
    """
    log_clarification()
    logger.info("Extension Loader: Ready.")
    logger.debug("Extension Loader: Debugging Started.")

    # Ensure local manifest is up-to-date
    update_local_manifest_from_remote()

    # Load installed extensions
    load_installed_extensions()

    # Try to find the requested extension in the manifest
    manifest = load_local_manifest()
    ext_entry = next((e for e in manifest.get("extensions", []) if e["name"].lower() == name.lower()), None)

    if ext_entry is None:
        logger.warning(f"Extension '{name}' not found in manifest, falling back to skeleton")
        name = "skeleton"
        ext_entry = next((e for e in manifest.get("extensions", []) if e["name"].lower() == "skeleton"), None)

    # Install if not installed
    if not ext_entry.get("installed", False):
        logger.info(f"Extension '{name}' not installed, installing now...")
        install_selected_extension(name)
        # Reload installed extensions after installation
        load_installed_extensions()

    # Find and return the module
    for ext in INSTALLED_EXTENSIONS:
        if getattr(ext, "__name__", "").lower().endswith(f"{name.lower()}__nhsext"):
            # Always call the extension's install_extension hook if it exists
            if hasattr(ext, "install_extension"):
                ext.install_extension()
            # Optional: update download path
            if hasattr(ext, "update_extension_download_path"):
                ext.update_extension_download_path()
            log_clarification()
            logger.info(f"Selected extension: {name}")
            return ext

    # Fallback to skeleton if still not found
    logger.warning(f"Extension '{name}' not loaded properly, falling back to skeleton")
    for ext in INSTALLED_EXTENSIONS:
        if getattr(ext, "__name__", "").lower().endswith("skeleton__nhsext"):
            if hasattr(ext, "install_extension"):
                ext.install_extension()
            if hasattr(ext, "update_extension_download_path"):
                ext.update_extension_download_path()
            return ext

    logger.error("Skeleton extension not found! This should never happen.")
    return None

# ------------------------------
# Run on import
# ------------------------------
load_installed_extensions()