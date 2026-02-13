#!/usr/bin/env python3
# mangascraper/extensions/extension_loader.py

import os, json, importlib, shutil, subprocess, sys

from urllib.request import urlopen

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.extensions import * # Ensure extensions package is recognised

# ------------------------------------------------------------
# Constants / Paths
# ------------------------------------------------------------
EXTENSIONS_DIR = os.path.dirname(__file__)
LOCAL_MANIFEST_PATH = os.path.join(EXTENSIONS_DIR, "local_manifest.json")

# Primary + backup repo / manifest locations
PRIMARY_BASE_REPO_URL = "https://github.com/C7YPT0N1C/manga-scraper-extensions/"
PRIMARY_REMOTE_MANIFEST_URL = (
    "https://github.com/C7YPT0N1C/manga-scraper-extensions/"
    "raw/main/master_manifest.json"
)

BACKUP_BASE_REPO_URL = "https://git.anthrosys.online/C7YPT0N1C/manga-scraper-extensions/"
BACKUP_REMOTE_MANIFEST_URL = (
    "https://git.anthrosys.online/C7YPT0N1C/"
    "manga-scraper-extensions/raw/branch/main/master_manifest.json"
)

INSTALLED_EXTENSIONS = []

#######################################################################
# Helpers
#######################################################################
def load_local_manifest():
    """
    Load the local manifest, create it from remote if it doesn't exist.
    """
    
    if not os.path.exists(LOCAL_MANIFEST_PATH):
        logger.warning("Local manifest not found. Creating from remote...")
        update_local_manifest_from_remote()
    with open(LOCAL_MANIFEST_PATH, "r", encoding="utf-8") as f:
        json_load = json.load(f)
        #log("Local Manifest: {json_load}", "debug")
        return json_load

def save_local_manifest(manifest: dict):
    """
    Save the local manifest to disk.
    """
    
    with open(LOCAL_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

def fetch_remote_manifest():
    """
    Fetch remote manifest.json with backup fallback.
    """
    
    try:
        with urlopen(PRIMARY_REMOTE_MANIFEST_URL) as response:
            return json.load(response)
    except Exception as e:
        log_clarification()
        logger.warning(f"Failed to fetch primary remote manifest: {e}")
        try:
            with urlopen(BACKUP_REMOTE_MANIFEST_URL) as response:
                logger.warning("Using backup remote manifest URL")
                return json.load(response)
        except Exception as e2:
            log_clarification()
            logger.error(f"Failed to fetch backup manifest: {e2}")
            return {"extensions": []}

def update_local_manifest_from_remote():
    """
    Merge remote manifest into local manifest, keeping installed flags intact.
    """
    
    remote_manifest = fetch_remote_manifest()
    local_manifest = {"extensions": []}
    if os.path.exists(LOCAL_MANIFEST_PATH):
        with open(LOCAL_MANIFEST_PATH, "r", encoding="utf-8") as f:
            local_manifest = json.load(f)

    local_by_name = {
        ext.get("name"): ext for ext in local_manifest.get("extensions", [])
        if ext.get("name")
    }
    merged_extensions = []

    for remote_ext in remote_manifest.get("extensions", []):
        remote_name = remote_ext.get("name")
        if not remote_name:
            continue

        local_ext = local_by_name.get(remote_name)
        if local_ext:
            installed = local_ext.get("installed", False)
            merged = {**local_ext, **remote_ext}
            merged["installed"] = installed
            if installed:
                merged["version"] = local_ext.get("version")
        else:
            merged = dict(remote_ext)
            merged["installed"] = False
            log_clarification("debug")
            log(f"Added new extension to local manifest: {remote_name}", "debug")

        merged_extensions.append(merged)

    # Preserve local-only entries that are not in remote manifest
    remote_names = {ext.get("name") for ext in merged_extensions}
    for local_ext in local_manifest.get("extensions", []):
        local_name = local_ext.get("name")
        if local_name and local_name not in remote_names:
            merged_extensions.append(local_ext)

    local_manifest["extensions"] = merged_extensions
    save_local_manifest(local_manifest)
    return local_manifest

# ------------------------------------------------------------
# Refresh manifest and installed extensions
# ------------------------------------------------------------
def _reload_extensions():
    """
    Update manifest, reinstall missing extensions, and reload INSTALLED_EXTENSIONS.
    """
    
    update_local_manifest_from_remote()
    load_installed_extensions()
    return load_local_manifest()

# ------------------------------------------------------------
# Sparse clone repo
# ------------------------------------------------------------
def sparse_clone(extension_name: str, url: str):
    ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)

    # Initialise empty repo
    subprocess.run(["git", "init", ext_folder], check=True)
    subprocess.run(["git", "-C", ext_folder, "remote", "add", "origin", url], check=True)
    subprocess.run(["git", "-C", ext_folder, "config", "core.sparseCheckout", "true"], check=True)

    # Configure sparse-checkout to fetch the top-level folder
    sparse_file = os.path.join(ext_folder, ".git", "info", "sparse-checkout")
    with open(sparse_file, "w", encoding="utf-8") as f:
        f.write(f"{extension_name}/*\n")  # Fetch everything inside the repo folder

    # Pull the branch (assumes 'main')
    subprocess.run(["git", "-C", ext_folder, "pull", "origin", "main"], check=True)

    # Flatten nested folder if exists
    repo_folder = os.path.join(ext_folder, extension_name)
    if os.path.exists(repo_folder) and os.path.isdir(repo_folder):
        for item in os.listdir(repo_folder):
            shutil.move(os.path.join(repo_folder, item), ext_folder)
        shutil.rmtree(repo_folder)  # Remove the now-empty nested folder

    log(f"Clone complete: {extension_name} -> {ext_folder}", "debug")

#######################################################################

# ------------------------------------------------------------
# Extension Loader
# ------------------------------------------------------------
def load_installed_extensions(suppess_pre_run_hook: bool = False):
    """
    This is one this module's entrypoints.
    
    Load installed extensions dynamically; reinstall if missing.
    """
    
    orchestrator.refresh_globals()
    
    INSTALLED_EXTENSIONS.clear()  # Ensure no duplicates if called multiple times
    manifest = load_local_manifest()

    remote_manifest = fetch_remote_manifest()
    remote_by_name = {
        ext.get("name"): ext for ext in remote_manifest.get("extensions", [])
        if ext.get("name")
    }
    updates_requested = []
    for ext in manifest.get("extensions", []):
        if not ext.get("installed", False):
            continue

        remote_entry = remote_by_name.get(ext.get("name"))
        if not remote_entry:
            continue

        local_version = ext.get("version")
        remote_version = remote_entry.get("version")
        if remote_version and is_remote_version_newer(local_version, remote_version):
            if _prompt_extension_update(ext.get("name"), local_version, remote_version):
                updates_requested.append(ext.get("name"))

    for extension_name in updates_requested:
        install_selected_extension(extension_name, reinstall=True, prompt_for_update=False)

    if updates_requested:
        manifest = load_local_manifest()
    
    for ext in manifest.get("extensions", []):
        ext_folder = os.path.join(EXTENSIONS_DIR, ext["name"])
        entry_point = os.path.join(ext_folder, ext["entry_point"])

        # If marked installed but entry point missing, reinstall
        if ext.get("installed", False) and not os.path.exists(entry_point):
            logger.warning(f"Extension '{ext['name']}' marked as installed but missing files. Reinstalling...")
            install_selected_extension(ext["name"], reinstall=True)
            entry_point = os.path.join(ext_folder, ext["entry_point"])  # refresh path after install

        if os.path.exists(entry_point):
            module_name = f"mangascraper.extensions.{ext['name']}.{ext['entry_point'].replace('.py', '')}"    
            try:
                module = importlib.import_module(module_name)
                INSTALLED_EXTENSIONS.append(module)
                if suppess_pre_run_hook == False: # Call the extension's pre run hook if not skipped
                    log(f"Extension: {ext['name']}: Loaded.", "debug")
            
            except Exception as e:
                logger.warning(f"Extension: {ext['name']}: Failed to load: {e}. Is an external program managing it?")
        else:
            logger.warning(f"Extension: {ext['name']}: Entry point not found.")

# ------------------------------------------------------------
# Install / Uninstall Extension
# ------------------------------------------------------------
def is_remote_version_newer(local_version: str, remote_version: str) -> bool:
    """
    Compares semantic version strings (e.g., "1.2.3").
    Returns True if remote_version > local_version.
    """
    
    def parse(v):
        return [int(x) for x in v.split(".") if x.isdigit()]
    
    lv = parse(local_version or "0.0.0")
    rv = parse(remote_version or "0.0.0")
    # Pad shorter versions with zeros
    length = max(len(lv), len(rv))
    lv += [0] * (length - len(lv))
    rv += [0] * (length - len(rv))
    return rv > lv

def _prompt_extension_update(extension_name: str, local_version: str, remote_version: str) -> bool:
    if not sys.stdin.isatty():
        logger.warning(
            "Non-interactive session: skipping update prompt for "
            f"'{extension_name}' ({local_version} -> {remote_version})."
        )
        return False

    prompt = (
        f"Update extension '{extension_name}' from {local_version} to {remote_version}? "
        "[y/N]: "
    )
    response = input(prompt).strip().lower()
    return response in ("y", "yes")

def install_selected_extension(extension_name: str, reinstall: bool = False, prompt_for_update: bool = True):
    """
    Installs an extension. If reinstall is True, forces reinstallation. Runs install hook if available.
    """
    local_manifest = load_local_manifest()
    local_entry = next((ext for ext in local_manifest.get("extensions", []) if ext.get("name") == extension_name), None)
    local_version = local_entry.get("version") if local_entry else None
    locally_installed = local_entry.get("installed", False) if local_entry else False

    remote_manifest = fetch_remote_manifest()
    remote_entry = next((ext for ext in remote_manifest.get("extensions", []) if ext.get("name") == extension_name), None)
    remote_version = remote_entry.get("version") if remote_entry else None

    if local_entry is None:
        update_local_manifest_from_remote()
        local_manifest = load_local_manifest()
        local_entry = next((ext for ext in local_manifest.get("extensions", []) if ext.get("name") == extension_name), None)
        if local_entry is None:
            logger.error(f"Extension '{extension_name}': Not found in remote manifest")
            return

    update_needed = False
    if locally_installed:
        if remote_version and is_remote_version_newer(local_version, remote_version):
            if prompt_for_update:
                if _prompt_extension_update(extension_name, local_version, remote_version):
                    update_needed = True
                else:
                    logger.warning(
                        f"Extension '{extension_name}': Update skipped (local {local_version}, remote {remote_version})."
                    )
                    return
            else:
                update_needed = True
        elif reinstall:
            update_needed = True
    else:
        update_needed = True

    if not update_needed:
        logger.warning(f"Extension '{extension_name}': Already installed and up-to-date (version {local_version})")
        return

    manifest = update_local_manifest_from_remote()
    ext_entry = next((ext for ext in manifest["extensions"] if ext.get("name") == extension_name), None)
    if not ext_entry:
        logger.error(f"Extension '{extension_name}': Not found in remote manifest")
        return

    ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)

    # Remove old folder if reinstalling
    if reinstall and os.path.exists(ext_folder):
        shutil.rmtree(ext_folder)

    repo_url = ext_entry.get("repo_url", "")
    if not os.path.exists(ext_folder):
        os.makedirs(ext_folder, exist_ok=True)

    def sparse_clone(extension_name: str, url: str):
        ext_folder = os.path.join(EXTENSIONS_DIR, extension_name)

        # Initialise empty repo
        subprocess.run(["git", "init", ext_folder], check=True)
        subprocess.run(["git", "-C", ext_folder, "remote", "add", "origin", url], check=True)
        subprocess.run(["git", "-C", ext_folder, "config", "core.sparseCheckout", "true"], check=True)

        # Configure sparse-checkout to fetch the top-level folder
        sparse_file = os.path.join(ext_folder, ".git", "info", "sparse-checkout")
        with open(sparse_file, "w", encoding="utf-8") as f:
            f.write(f"{extension_name}/*\n")  # Fetch everything inside the repo folder

        # Pull the branch (assumes 'main')
        subprocess.run(["git", "-C", ext_folder, "pull", "origin", "main"], check=True)

        # Check if repo folder exists inside ext_folder (double nesting)
        repo_folder = os.path.join(ext_folder, extension_name)
        if os.path.exists(repo_folder) and os.path.isdir(repo_folder):
            for item in os.listdir(repo_folder):
                shutil.move(os.path.join(repo_folder, item), ext_folder)
            shutil.rmtree(repo_folder)  # Remove the now-empty nested folder

        print(f"Clone complete: {extension_name} -> {ext_folder}")

    try:
        log(f"Sparse cloning {extension_name} from {repo_url}...", "debug")
        sparse_clone(extension_name, repo_url)
    except Exception as e:
        logger.warning(f"Failed to sparse-clone from primary repo: {e}")
        if BACKUP_BASE_REPO_URL:
            backup_url = repo_url.replace(PRIMARY_BASE_REPO_URL, BACKUP_BASE_REPO_URL)
            try:
                log(f"Retrying sparse-clone with backup repo: {backup_url}", "debug")
                # clean up half-baked folder before retry
                shutil.rmtree(ext_folder, ignore_errors=True)
                os.makedirs(ext_folder, exist_ok=True)
                sparse_clone(extension_name, backup_url)
            except Exception as e2:
                logger.error(f"Failed to sparse-clone from backup repo: {e2}")
                return
        else:
            return

    # Import and run install hook
    entry_point = ext_entry["entry_point"]
    module_name = f"mangascraper.extensions.{extension_name}.{entry_point.replace('.py', '')}"
    module = importlib.import_module(module_name)
    if hasattr(module, "install_extension"):
        module.install_extension()
        logger.warning(f"Extension '{extension_name}': Installed successfully.")

    # Update manifest
    ext_entry["installed"] = True
    if remote_version:
        ext_entry["version"] = remote_version
    save_local_manifest(manifest)

def uninstall_selected_extension(extension_name: str):
    """
    Uninstalls an extension. Runs uninstall hook if available.
    """
    
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
        logger.warning(f"Extension '{extension_name}': Uninstalled successfully.")

    # Update manifest
    ext_entry["installed"] = False
    save_local_manifest(manifest)

# ------------------------------------------------------------
# Get selected extension (with skeleton fallback)
def get_selected_extension(name: str = "skeleton", suppess_pre_run_hook: bool = False):
    """
    This is one this module's entrypoints.
    
    Returns the selected extension module.
    If the extension is not installed, installs it first.
    Ensures 'skeleton' is always installed to provide a valid download path.
    """
    
    orchestrator.refresh_globals()
    
    original_name = name  # Save the originally requested extension

    if suppess_pre_run_hook == False: # Call the extension's pre run hook if not skipped
        log_clarification("debug")
        logger.debug("Extension Loader: Ready.")
        log("Extension Loader: Debugging Started.", "debug")

    # Ensure local manifest is up-to-date
    update_local_manifest_from_remote()

    # Load installed extensions
    load_installed_extensions()

    # Load manifest
    manifest = load_local_manifest()

    # Ensure skeleton is installed first
    skeleton_entry = next((e for e in manifest.get("extensions", []) if e["name"].lower() == "skeleton"), None)
    if skeleton_entry is None or not skeleton_entry.get("installed", False):
        logger.warning("Skeleton extension not installed, installing now...")
        install_selected_extension("skeleton", reinstall=True)
        manifest = _reload_extensions()

    # Ensure the requested extension is installed
    ext_entry = next((e for e in manifest.get("extensions", []) if e["name"].lower() == original_name.lower()), None)
    if ext_entry is None:
        update_local_manifest_from_remote()
        manifest = load_local_manifest()
        ext_entry = next((e for e in manifest.get("extensions", []) if e["name"].lower() == original_name.lower()), None)

    if ext_entry is None:
        logger.warning(f"Extension '{original_name}' not found in manifest, falling back to skeleton")
        name = "skeleton"
    elif not ext_entry.get("installed", False):
        logger.warning(f"Extension '{original_name}' not installed, installing now...")
        install_selected_extension(original_name, reinstall=True)
        manifest = _reload_extensions()

    # Final name to load (fall back to skeleton if necessary)
    final_name = original_name if ext_entry else "skeleton"

    # Find and return the module
    for ext in INSTALLED_EXTENSIONS:
        if getattr(ext, "__name__", "").lower().endswith(f"{final_name.lower()}__msext"):
            #if hasattr(ext, "install_extension"): # This runs the installer again, not necessary
            #    ext.install_extension()
            if suppess_pre_run_hook == False: # Call the extension's pre run hook if not skipped
                if hasattr(ext, "pre_run_hook"):
                    ext.pre_run_hook()
                
                log_clarification()
                logger.info(f"Selected extension: {final_name}")
            
            return ext

    # If we reach here, something went really wrong
    logger.error("Failed to load the requested extension or skeleton! This should never happen, so something went really wrong.")
    return None