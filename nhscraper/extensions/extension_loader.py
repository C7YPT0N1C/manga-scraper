#!/usr/bin/env python3
# nhscraper/extensions/extension_loader.py
import os, sys, time, random, argparse, re, subprocess, urllib.parse # 'Default' imports

import threading, asyncio, aiohttp, aiohttp_socks, json, importlib, shutil # Module-specific imports

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *
from nhscraper.extensions import *  # Ensure extensions package is recognised

"""
Dynamic extension system for the downloader.
Loads, validates, and integrates external extensions
that add or override functionality.
"""

_module_referrer=f"Extension Loader" # Used in executor.* calls

# ------------------------------------------------------------
# Constants / Paths
# ------------------------------------------------------------
EXTENSIONS_DIR = os.path.dirname(__file__)
LOCAL_MANIFEST_PATH = os.path.join(EXTENSIONS_DIR, "local_manifest.json")

# Primary + backup repo / manifest URLs
PRIMARY_URL_BASE_REPO = "https://github.com/C7YPT0N1C/nhentai-scraper-extensions/"
PRIMARY_URL_REMOTE_MANIFEST = (
    "https://raw.githubusercontent.com/C7YPT0N1C/nhentai-scraper-extensions/"
    "main/master_manifest.json"
)

BACKUP_URL_BASE_REPO = "https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper-extensions/"
BACKUP_URL_REMOTE_MANIFEST = (
    "https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper-extensions/"
    "raw/branch/dev/master_manifest.json"
)

INSTALLED_EXTENSIONS = []
installed_extensions_lock = threading.Lock()

#######################################################################
# Helpers
#######################################################################
async def load_local_manifest():
    """
    Load the local manifest, create it from remote if it doesn't exist.
    """
    
    if not os.path.exists(LOCAL_MANIFEST_PATH):
        log("Local manifest not found. Creating from remote...", "warning")
        await update_local_manifest_from_remote()
    
    with open(LOCAL_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

async def save_local_manifest(manifest: dict):
    """Save the local manifest to disk."""
    
    executor.spawn_task(
        lambda: json.dump(manifest, open(LOCAL_MANIFEST_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    )

async def fetch_remote_manifest():
    """
    Fetch remote manifest.json with backup fallback.
    """
    
    async def _fetch(url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError as e:
                        raise Exception(f"Invalid JSON at {url}: {e}")
                raise Exception(f"HTTP {resp.status} {url}")

    try:
        log_clarification()
        log(f"Fetching remote manifest from Primary Server...", "info")
        data = await _fetch(PRIMARY_URL_REMOTE_MANIFEST)
        log(f"Sucessfully fetched remote manifest from Primary Server: {data}", "info")
        return data
    
    except Exception as e:
        log(f"Failed to fetch remote manifest from Primary Server: {e}", "warning")
        try:
            log_clarification()
            log(f"Attempting to fetch remote manifest from Backup Server...", "info")
            data = await _fetch(BACKUP_URL_REMOTE_MANIFEST)
            log(f"Sucessfully fetched remote manifest from Backup Server: {data}", "info")
            return data
        except Exception as e2:
            log(f"Failed to fetch remote manifest from Backup Server: {e2}", "warning")
            return {"extensions": []}

async def update_local_manifest_from_remote():
    """
    Merge remote manifest into local manifest, keeping installed flags intact.
    """
    
    remote_manifest = await fetch_remote_manifest()
    local_manifest = {"extensions": []}
    if os.path.exists(LOCAL_MANIFEST_PATH):
        local_manifest = await load_local_manifest()

    local_names = [ext["name"] for ext in local_manifest.get("extensions", [])]

    for remote_ext in remote_manifest.get("extensions", []):
        if remote_ext["name"] not in local_names:
            remote_ext["installed"] = False
            local_manifest["extensions"].append(remote_ext)
            log(f"Added new extension to local manifest: {remote_ext['name']}", "debug")

    await save_local_manifest(local_manifest)
    return local_manifest

# ------------------------------------------------------------
# Refresh manifest and installed extensions
# ------------------------------------------------------------
async def _reload_extensions():
    await update_local_manifest_from_remote()
    await load_installed_extensions()
    return await load_local_manifest()

# ------------------------------------------------------------
# Sparse clone repo (blocking, offloaded)
# ------------------------------------------------------------
async def sparse_clone(extension_name: str, url: str):
    async def _clone():
        ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)

        subprocess.run(["git", "init", ext_folder], check=True)
        subprocess.run(["git", "-C", ext_folder, "remote", "add", "origin", url], check=True)
        subprocess.run(["git", "-C", ext_folder, "config", "core.sparseCheckout", "true"], check=True)

        sparse_file = os.path.join(ext_folder, ".git", "info", "sparse-checkout")
        with open(sparse_file, "w", encoding="utf-8") as f:
            f.write(f"{extension_name}/*\n")

        subprocess.run(["git", "-C", ext_folder, "pull", "origin", "main"], check=True)

        repo_folder = os.path.join(ext_folder, extension_name)
        if os.path.exists(repo_folder) and os.path.isdir(repo_folder):
            for item in os.listdir(repo_folder):
                shutil.move(os.path.join(repo_folder, item), ext_folder)
            shutil.rmtree(repo_folder)

        log(f"Clone complete: {extension_name} -> {ext_folder}", "debug")

    return executor.run_blocking(_clone)

#######################################################################
# Extension Loader
#######################################################################
async def load_installed_extensions(suppess_pre_run_hook: bool = False):
    fetch_env_vars() # Refresh env vars in case config changed.

    with installed_extensions_lock:
        INSTALLED_EXTENSIONS.clear()
        manifest = await load_local_manifest()

        for ext in manifest.get("extensions", []):
            ext_folder = os.path.join(EXTENSIONS_DIR, ext["name"])
            entry_point = os.path.join(ext_folder, ext["entry_point"])

            if ext.get("installed", False) and not os.path.exists(entry_point):
                log(f"Extension '{ext['name']}' marked as installed but missing files. Reinstalling...", "warning")
                await install_selected_extension(ext["name"], reinstall=True)
                entry_point = os.path.join(ext_folder, ext["entry_point"])

            if os.path.exists(entry_point):
                module_name = f"nhscraper.extensions.{ext['name']}.{ext['entry_point'].replace('.py', '')}"
                try:
                    module = importlib.import_module(module_name)
                    INSTALLED_EXTENSIONS.append(module)
                    if not suppess_pre_run_hook:
                        log(f"Extension: {ext['name']}: Loaded.", "debug")
                except Exception as e:
                    log(f"Extension: {ext['name']}: Failed to load: {e}", "warning")
            else:
                log(f"Extension: {ext['name']}: Entry point not found.", "warning")

#######################################################################
# Install / Uninstall Extension
#######################################################################
def is_remote_version_newer(local_version: str, remote_version: str) -> bool:
    def parse(v):
        return [int(x) for x in v.split(".") if x.isdigit()]
    lv, rv = parse(local_version or "0.0.0"), parse(remote_version or "0.0.0")
    length = max(len(lv), len(rv))
    lv += [0] * (length - len(lv))
    rv += [0] * (length - len(rv))
    return rv > lv

async def install_selected_extension(extension_name: str, reinstall: bool = False):
    manifest = await update_local_manifest_from_remote()
    ext_entry = next((ext for ext in manifest["extensions"] if ext["name"] == extension_name), None)
    if not ext_entry:
        log(f"Extension '{extension_name}': Not found in remote manifest", "error")
        return
    else:
        log(f"Extension '{extension_name}': Found in remote manifest", "info")

    ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)
    if reinstall and os.path.exists(ext_folder):
        shutil.rmtree(ext_folder)

    update_needed = False
    local_version = ext_entry.get("version")
    remote_manifest = await fetch_remote_manifest()
    remote_entry = next((e for e in remote_manifest.get("extensions", []) if e["name"] == extension_name), {})
    remote_version = remote_entry.get("version")

    if ext_entry.get("installed", False):
        if remote_version and is_remote_version_newer(local_version, remote_version):
            log(f"Extension '{extension_name}': Updating to {remote_version} from {local_version}", "warning")
            update_needed = True
        elif reinstall:
            update_needed = True
    else:
        update_needed = True

    if not update_needed:
        log(f"Extension '{extension_name}': Already installed and up-to-date", "warning")
        return

    repo_url = ext_entry.get("repo_url", "")
    if not os.path.exists(ext_folder):
        os.makedirs(ext_folder, exist_ok=True)

    try:
        log(f"Sparse cloning {extension_name} from {repo_url}...", "debug")
        await sparse_clone(extension_name, repo_url)
    except Exception as e:
        log(f"Failed to sparse-clone from primary repo: {e}", "warning")
        if BASE_REPO_BACKUP_URL:
            backup_url = repo_url.replace(PRIMARY_URL_BASE_REPO, BACKUP_URL_BASE_REPO)
            try:
                log(f"Retrying sparse-clone with backup repo: {backup_url}", "debug")
                shutil.rmtree(ext_folder, ignore_errors=True)
                os.makedirs(ext_folder, exist_ok=True)
                await sparse_clone(extension_name, backup_url)
            except Exception as e2:
                log(f"Failed to sparse-clone from backup repo: {e2}", "error")
                return
        else:
            return

    module_name = f"nhscraper.extensions.{extension_name}.{ext_entry['entry_point'].replace('.py', '')}"
    module = importlib.import_module(module_name)
    if hasattr(module, "install_extension"):
        executor.run_blocking(module.install_extension)
        log(f"Extension '{extension_name}': Installed successfully.", "warning")

    ext_entry["installed"] = True
    await save_local_manifest(manifest)

async def uninstall_selected_extension(extension_name: str):
    manifest = await load_local_manifest()
    ext_entry = next((ext for ext in manifest["extensions"] if ext["name"] == extension_name), None)
    if not ext_entry or not ext_entry.get("installed", False):
        log(f"Extension '{extension_name}': Not installed", "warning")
        return

    module_name = f"extensions.{extension_name}.{ext_entry['entry_point'].replace('.py', '')}"
    module = importlib.import_module(module_name)
    if hasattr(module, "uninstall_extension"):
        executor.run_blocking(module.uninstall_extension)
        log(f"Extension '{extension_name}': Uninstalled successfully.", "warning")

    ext_entry["installed"] = False
    await save_local_manifest(manifest)

#######################################################################
# Get selected extension
#######################################################################
async def get_selected_extension(name: str = "skeleton", suppess_pre_run_hook: bool = False):
    fetch_env_vars() # Refresh env vars in case config changed.
    original_name = name

    if not suppess_pre_run_hook:
        log("Extension Loader: Ready.", "debug")

    await update_local_manifest_from_remote()
    await load_installed_extensions()
    manifest = await load_local_manifest()

    skeleton_entry = next((e for e in manifest.get("extensions", []) if e["name"].lower() == "skeleton"), None)
    if skeleton_entry is None or not skeleton_entry.get("installed", False):
        log("Skeleton extension not installed, installing now...", "warning")
        await install_selected_extension("skeleton", reinstall=True)
        manifest = await _reload_extensions()

    ext_entry = next((e for e in manifest.get("extensions", []) if e["name"].lower() == original_name.lower()), None)
    if ext_entry is None:
        log(f"Extension '{original_name}' not found in manifest, falling back to skeleton", "warning")
        name = "skeleton"
    elif not ext_entry.get("installed", False):
        log(f"Extension '{original_name}' not installed, installing now...", "warning")
        await install_selected_extension(original_name, reinstall=True)
        manifest = await _reload_extensions()

    final_name = original_name if ext_entry else "skeleton"

    for ext in INSTALLED_EXTENSIONS:
        if getattr(ext, "__name__", "").lower().endswith(f"{final_name.lower()}__nhsext"):
            if not suppess_pre_run_hook and hasattr(ext, "pre_run_hook"):
                executor.run_blocking(ext.pre_run_hook)
                log(f"Selected extension: {final_name}", "info")
            return ext

    log("Failed to load the requested extension or skeleton!", "error")
    return None