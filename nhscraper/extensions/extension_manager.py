#!/usr/bin/env python3
# nhscraper/extensions/extension_loader.py
import os, sys, time, random, argparse, re, subprocess, urllib.parse # 'Default' imports

import inspect, threading, asyncio, aiohttp, aiohttp_socks, json, importlib, shutil # Module-specific imports

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *
from nhscraper.extensions import *  # Ensure extensions package is recognised

"""
Dynamic extension system for the downloader.
Loads, validates, and integrates external extensions
that add or override functionality.
"""

_module_referrer=f"Extension Loader" # Used in executor.* / cross-module calls

# ------------------------------------------------------------
# Constants / Paths
# ------------------------------------------------------------
EXTENSIONS_DIR = os.path.dirname(__file__)
LOCAL_MANIFEST_PATH = os.path.join(EXTENSIONS_DIR, "local_manifest.json")

# Primary + backup repo / manifest URLs
PRIMARY_URL_BASE_REPO = "https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper-extensions/"
PRIMARY_URL_REMOTE_MANIFEST = (
    "https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper-extensions/"
    "raw/branch/main/master_manifest.json"
)

BACKUP_URL_BASE_REPO = "https://github.com/C7YPT0N1C/nhentai-scraper-extensions/"
BACKUP_URL_REMOTE_MANIFEST = (
    "https://raw.githubusercontent.com/C7YPT0N1C/nhentai-scraper-extensions/"
    "main/master_manifest.json"
)

INSTALLED_EXTENSIONS = []
current_manifest = None
extension_lock = threading.Lock()

#######################################################################
# Helpers
#######################################################################

async def load_local_manifest():
    """
    Load the local manifest, create it from remote if it doesn't exist.
    """
    
    if not os.path.exists(LOCAL_MANIFEST_PATH):
        log("Local manifest not found. Creating from remote...", "warning")
        await update_local_manifest_from_remote(force=True)
    
    with open(LOCAL_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

async def save_local_manifest(manifest: dict):
    with open(LOCAL_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

async def fetch_remote_manifest(force: bool = False):
    """
    Fetch remote manifest.json with backup fallback, using global cache.
    Set force=True to ignore cache and re-fetch from remote.
    """
    
    global current_manifest

    # If already cached, return it immediately
    with extension_lock:
        if current_manifest is not None and not force:
            return current_manifest

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
        log("Fetching remote manifest from Primary Server...", "info")
        data = await _fetch(PRIMARY_URL_REMOTE_MANIFEST)
        log(f"Sucessfully fetched remote manifest from Primary Server", "info")
        #log(f"Sucessfully fetched remote manifest from Primary Server: {data}", "info") # NOTE: DEBUGGING
    except Exception as e:
        log(f"Failed to fetch remote manifest from Primary Server: {e}", "warning")
        try:
            log("Attempting to fetch remote manifest from Backup Server...", "info")
            data = await _fetch(BACKUP_URL_REMOTE_MANIFEST)
            log(f"Sucessfully fetched remote manifest from Backup Server", "info")
            #log(f"Sucessfully fetched remote manifest from Backup Server: {data}", "info") # NOTE: DEBUGGING
        except Exception as e2:
            log(f"Failed to fetch remote manifest from Backup Server: {e2}", "warning")
            data = {"extensions": []}

    # Save result into cache
    with extension_lock:
        current_manifest = data

    return data

async def update_local_manifest_from_remote(force: bool = False):
    """
    Merge remote manifest into local manifest, keeping installed flags intact.
    """
    remote_manifest = await fetch_remote_manifest(force=force) # now cached
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

async def _reload_extensions():
    """Refresh manifest and installed extensions"""
    
    await update_local_manifest_from_remote(force=False)
    await load_installed_extensions()
    return await load_local_manifest()

async def sparse_clone(extension_name: str, url: str):
    """Sparse clone repo (blocking, offloaded)"""
    
    def _clone():
        ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)
        
        # Safety: if ext_folder already has a .git, wipe it first
        git_dir = os.path.join(ext_folder, ".git")
        if os.path.exists(git_dir):
            shutil.rmtree(ext_folder)
            os.makedirs(ext_folder, exist_ok=True)
        
        try:
            subprocess.run(["git", "init", ext_folder], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", ext_folder, "remote", "add", "origin", url], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", ext_folder, "config", "core.sparseCheckout", "true"], check=True, capture_output=True, text=True)
            sparse_file = os.path.join(ext_folder, ".git", "info", "sparse-checkout")
            with open(sparse_file, "w", encoding="utf-8") as f:
                f.write(f"{extension_name}/*\n")
            subprocess.run(["git", "-C", ext_folder, "pull", "origin", "main"], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            log(f"sparse_clone git command failed: cmd={e.cmd} rc={e.returncode} stdout={e.stdout} stderr={e.stderr}", "error")
            raise

        # move content if pull created a subfolder
        repo_folder = os.path.join(ext_folder, extension_name)
        if os.path.exists(repo_folder) and os.path.isdir(repo_folder):
            for item in os.listdir(repo_folder):
                shutil.move(os.path.join(repo_folder, item), ext_folder)
            shutil.rmtree(repo_folder)

        log(f"Clone complete: {extension_name} -> {ext_folder}", "debug")

    return await executor.io_to_thread(_clone)

#######################################################################
# Extension Loader
#######################################################################

async def load_installed_extensions(suppess_pre_run_hook: bool = False):
    fetch_env_vars() # Refresh env vars in case config changed.

    with extension_lock:
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
    manifest = await update_local_manifest_from_remote(force=False) # Always use cache
    
    ext_entry = next((ext for ext in manifest.get("extensions", []) if ext["name"] == extension_name), None)
    if not ext_entry:
        log(f"Extension '{extension_name}' not found in remote manifest", "error")
        return
    else:
        log(f"Extension '{extension_name}' found in remote manifest", "info")

    ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)
    
    # Safety: if ext_folder already has a .git, wipe it first
    git_dir = os.path.join(ext_folder, ".git")
    if os.path.exists(git_dir):
        shutil.rmtree(ext_folder)
        os.makedirs(ext_folder, exist_ok=True)
    
    if reinstall and os.path.exists(ext_folder):
        shutil.rmtree(ext_folder)

    update_needed = False
    local_version = ext_entry.get("version")
    
    # Use cached manifest instead of refetching
    remote_manifest = await fetch_remote_manifest(force=False)
    remote_entry = next((e for e in remote_manifest.get("extensions", []) if e["name"] == extension_name), {})
    remote_version = remote_entry.get("version")

    if ext_entry.get("installed", False):
        if remote_version and is_remote_version_newer(local_version, remote_version):
            log(f"Updating extension '{extension_name}' to {remote_version} from {local_version}", "warning")
            update_needed = True
        elif reinstall:
            update_needed = True
    else:
        update_needed = True

    if not update_needed:
        log(f"Extension '{extension_name}' already installed and up-to-date", "warning")
        return

    repo_url = ext_entry.get("repo_url", "")
    if not repo_url:
        log(f"Extension '{extension_name}' repo_url missing in manifest entry: {ext_entry!r}", "error")
        return

    # Ensure ext folder exists before cloning
    if not os.path.exists(ext_folder):
        os.makedirs(ext_folder, exist_ok=True)

    # Try primary clone, then backup replacement if needed
    cloned = False
    try:
        log(f"Sparse cloning {extension_name} from {repo_url}...", "debug")
        await sparse_clone(extension_name, repo_url)
        cloned = True
    except Exception as e:
        log(f"Failed to sparse-clone from primary repo: {e}", "warning")
        # Try to form a backup URL if possible
        try:
            if PRIMARY_URL_BASE_REPO and BACKUP_URL_BASE_REPO and PRIMARY_URL_BASE_REPO in repo_url:
                backup_url = repo_url.replace(PRIMARY_URL_BASE_REPO, BACKUP_URL_BASE_REPO)
            else:
                # If repo_url already points to primary raw, try swapping known bases
                backup_url = repo_url
            log(f"Retrying sparse-clone with backup repo: {backup_url}", "debug")
            # remove any partial content and retry
            shutil.rmtree(ext_folder, ignore_errors=True)
            os.makedirs(ext_folder, exist_ok=True)
            await sparse_clone(extension_name, backup_url)
            cloned = True
        except Exception as e2:
            log(f"Failed to sparse-clone from backup repo: {e2}", "error")
            cloned = False

    if not cloned:
        log(f"Cloning extension '{extension_name}' failed, aborting installation.", "error")
        return

    # Import module
    module_name = f"nhscraper.extensions.{extension_name}.{ext_entry['entry_point'].replace('.py', '')}"
    try:
        module = importlib.import_module(module_name)
    except Exception as e:
        log(f"Importing extension '{extension_name}' failed: {e}", "error")
        return

    # Call install_extension if present
    if hasattr(module, "install_extension"):
        try:
            install_obj = module.install_extension
            if inspect.iscoroutinefunction(install_obj):
                # async install
                log(f"Running install for extension '{extension_name}'", "debug")
                await install_obj()
            else:
                # sync install: run in thread (use call_appropriately from executor)
                log(f"Running install for extension '{extension_name}'", "debug")
                await executor.call_appropriately(install_obj)
            log(f"Installed extension '{extension_name}' successfully.", "info")
        except Exception as e:
            log(f"Installing extension '{extension_name}' failed: {e}", "error")
            return

    # Mark installed and persist manifest
    try:
        ext_entry["installed"] = True
        await save_local_manifest(manifest)
        log(f"Installed extension '{extension_name}' and updated manifest.", "info")
    except Exception as e:
        log(f"Failed to save local manifest after extension '{extension_name}' install: {e}", "error")

async def uninstall_selected_extension(extension_name: str):
    manifest = await load_local_manifest()
    ext_entry = next((ext for ext in manifest["extensions"] if ext["name"] == extension_name), None)
    if not ext_entry or not ext_entry.get("installed", False):
        log(f"Extension '{extension_name}' not installed", "warning")
        return

    module_name = f"nhscraper.extensions.{extension_name}.{ext_entry['entry_point'].replace('.py', '')}"
    module = importlib.import_module(module_name)
    if hasattr(module, "uninstall_extension"):
        if inspect.iscoroutinefunction(module.uninstall_extension):
            await module.uninstall_extension()
        else:
            await executor.run_blocking(module.uninstall_extension)
        
        log(f"Uninstalled extension '{extension_name}' successfully.", "warning")

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

    await update_local_manifest_from_remote(force=False)
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
                if inspect.iscoroutinefunction(ext.pre_run_hook):
                    await ext.pre_run_hook()
                else:
                    await executor.run_blocking(ext.pre_run_hook)
                
                log(f"Selected extension: {final_name}", "info")
            return ext

    log("Failed to load the requested extension or skeleton!", "error")
    return None