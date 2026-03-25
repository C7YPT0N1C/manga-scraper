#!/usr/bin/env python3
# mangascraper/extensions/extension_loader.py

import os, json, importlib, importlib.util, shutil, subprocess, sys, re

from urllib.request import urlopen

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.core.api import api as scraperapi
from mangascraper.extensions import * # Ensure extensions package is recognised

# ------------------------------------------------------------
# Constants / Paths
# ------------------------------------------------------------
EXTENSIONS_DIR = "/opt/manga-scraper/mangascraper/extensions"
os.makedirs(EXTENSIONS_DIR, exist_ok=True)
REMOTE_EXTENSIONS_TMP = f"{orchestrator.TEMP_DIR}/manga-scraper-extensions"
LOCAL_MANIFEST_PATH = os.path.join(EXTENSIONS_DIR, "local_manifest.json")

# Primary + backup repo / manifest locations
PRIMARY_BASE_REPO_URL = "https://github.com/C7YPT0N1C/manga-scraper-extensions/"
PRIMARY_REMOTE_MANIFEST_URL = (
    "https://github.com/C7YPT0N1C/manga-scraper-extensions/"
    "raw/main/master_manifest.json"
)

BACKUP_BASE_REPO_URL = "https://git.anthrosys.uk/C7YPT0N1C/manga-scraper-extensions/"
BACKUP_REMOTE_MANIFEST_URL = (
    "https://git.anthrosys.uk/C7YPT0N1C/"
    "manga-scraper-extensions/raw/branch/main/master_manifest.json"
)

INSTALLED_EXTENSIONS = []

#######################################################################
# Manifest / Metadata Helpers
#######################################################################
def load_local_manifest():
    """
    Load the local manifest, create it from remote if it doesn't exist.
    """
    if not os.path.exists(LOCAL_MANIFEST_PATH):
        logger.warning("Local manifest not found. Creating from remote...")
        ensure_local_manifest_exists()
        if not os.path.exists(LOCAL_MANIFEST_PATH):
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

def ensure_local_manifest_exists():
    if os.path.exists(LOCAL_MANIFEST_PATH):
        return
    repo_url = PRIMARY_BASE_REPO_URL
    backup_url = BACKUP_BASE_REPO_URL if BACKUP_BASE_REPO_URL else None
    for candidate in (repo_url, backup_url):
        if not candidate:
            continue
        try:
            _ensure_remote_repo_tmp(candidate)
            tmp_manifest_path = os.path.join(REMOTE_EXTENSIONS_TMP, "master_manifest.json")
            if os.path.exists(tmp_manifest_path):
                os.makedirs(EXTENSIONS_DIR, exist_ok=True)
                shutil.copy2(tmp_manifest_path, LOCAL_MANIFEST_PATH)
                log(f"Local manifest created from tmp repo: {LOCAL_MANIFEST_PATH}", "debug")
                return
        except Exception:
            continue
    try:
        remote_manifest = fetch_remote_manifest()
        os.makedirs(EXTENSIONS_DIR, exist_ok=True)
        with open(LOCAL_MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(remote_manifest, f, ensure_ascii=False, indent=2)
        log(f"Local manifest created from remote manifest: {LOCAL_MANIFEST_PATH}", "debug")
    except Exception:
        pass

# ------------------------------------------------------------
# Refresh Manifest Cache
# ------------------------------------------------------------
def _reload_extensions():
    """
    Update manifest, reinstall missing extensions, and reload INSTALLED_EXTENSIONS.
    """

    update_local_manifest_from_remote()
    load_installed_extensions()
    return load_local_manifest()

#######################################################################
# Remote Repo Sync (Full Clone)
#######################################################################
def _clear_directory(path: str):
    for entry in os.listdir(path):
        entry_path = os.path.join(path, entry)
        if os.path.isdir(entry_path) and not os.path.islink(entry_path):
            shutil.rmtree(entry_path)
        else:
            os.remove(entry_path)


def _read_manifest_versions(manifest_path: str) -> dict:
    if not os.path.exists(manifest_path):
        return {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return {
        ext.get("name"): ext.get("version")
        for ext in manifest.get("extensions", [])
        if ext.get("name")
    }


def _parse_semver_tuple(version_text: str) -> tuple[int, int, int]:
    text = str(version_text or "").strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return (-1, -1, -1)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _resolve_extension_version_dir(
    tmp_root: str,
    extension_branch: str,
    extension_name: str,
    preferred_version: str | None = None,
) -> str | None:
    """
    Resolve extension source directory using new layout:
    /<repo>/<branch>/<extension>/<version>
    """
    branch = str(extension_branch or "main").strip() or "main"
    extension_root = os.path.join(tmp_root, branch, extension_name)
    if not os.path.isdir(extension_root):
        return None

    preferred = str(preferred_version or "").strip()
    if preferred:
        preferred_path = os.path.join(extension_root, preferred)
        if os.path.isdir(preferred_path):
            return preferred_path

    versioned_candidates = []
    for entry in os.listdir(extension_root):
        version_dir = os.path.join(extension_root, entry)
        if not os.path.isdir(version_dir):
            continue
        semver = _parse_semver_tuple(entry)
        if semver == (-1, -1, -1):
            continue
        versioned_candidates.append((semver, version_dir))

    if not versioned_candidates:
        return None
    versioned_candidates.sort(key=lambda item: item[0], reverse=True)
    return versioned_candidates[0][1]


def _ensure_remote_repo_tmp(url: str):
    log(f"Syncing extensions repo: {url}", "debug")
    os.makedirs(REMOTE_EXTENSIONS_TMP, exist_ok=True)

    tmp_manifest_path = os.path.join(REMOTE_EXTENSIONS_TMP, "master_manifest.json")
    tmp_versions = _read_manifest_versions(tmp_manifest_path)
    remote_versions = {
        ext.get("name"): ext.get("version")
        for ext in fetch_remote_manifest().get("extensions", [])
        if ext.get("name")
    }

    needs_refresh = not tmp_versions or tmp_versions != remote_versions
    if needs_refresh:
        log("Remote manifest differs or missing; refreshing tmp repo...", "debug")
        _clear_directory(REMOTE_EXTENSIONS_TMP)
        subprocess.run(["git", "clone", "--depth", "1", url, REMOTE_EXTENSIONS_TMP], check=True)
        log(f"Clone complete: {REMOTE_EXTENSIONS_TMP}", "debug")
    else:
        log("Tmp repo is up to date; reusing existing clone.", "debug")


def sync_remote_extensions_repo(
    url: str,
    extension_name: str | None = None,
    extension_version: str | None = None,
    extension_branch: str = "main",
):
    _ensure_remote_repo_tmp(url)

    if extension_name:
        source_dir = _resolve_extension_version_dir(
            REMOTE_EXTENSIONS_TMP,
            extension_branch,
            extension_name,
            preferred_version=extension_version,
        )
        if not source_dir or not os.path.isdir(source_dir):
            raise FileNotFoundError(
                f"Remote extension folder missing for '{extension_name}' on branch '{extension_branch}'"
                f" (preferred version: {extension_version or 'latest'})."
            )
        target_dir = os.path.join(EXTENSIONS_DIR, extension_name)
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        os.makedirs(EXTENSIONS_DIR, exist_ok=True)
        shutil.move(source_dir, target_dir)
        log(f"Remote extension moved into: {target_dir}", "debug")
    else:
        if os.path.exists(EXTENSIONS_DIR):
            shutil.rmtree(EXTENSIONS_DIR)
        shutil.copytree(REMOTE_EXTENSIONS_TMP, EXTENSIONS_DIR)
        log(f"Remote extensions synced into: {EXTENSIONS_DIR}", "debug")

    # Quick sanity checks for entry points (debug only)
    try:
        manifest_path = os.path.join(EXTENSIONS_DIR, "master_manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                remote_manifest = json.load(f)
            for ext in remote_manifest.get("extensions", []):
                entry = ext.get("entry_point")
                name = ext.get("name")
                if not entry or not name:
                    continue
                entry_path = os.path.join(EXTENSIONS_DIR, name, entry)
                if os.path.exists(entry_path):
                    log(f"Entry point ok: {entry_path}", "debug")
                else:
                    log(f"Entry point missing: {entry_path}", "warning")
    except Exception as e:
        log(f"Failed entry point sanity check: {e}", "warning")

#######################################################################
# Extension Loader
#######################################################################
def _load_extension_module(module_name: str, entry_point: str):
    spec = importlib.util.spec_from_file_location(module_name, entry_point)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {entry_point}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

def _find_manifest_entry(manifest: dict, extension_name: str) -> dict | None:
    for ext in manifest.get("extensions", []):
        if ext.get("name", "").lower() == extension_name.lower():
            return ext
    return None

def load_single_extension(
    extension_name: str,
    install_if_missing: bool = False,
    prompt_for_update: bool = True,
    override_download_path: str | None = None,
):
    """Load a single extension by name, installing it if requested or missing files are detected."""
    previous_download_path = None
    if override_download_path is not None:
        previous_download_path = config.get("EXTENSION_DOWNLOAD_PATH")
        config["EXTENSION_DOWNLOAD_PATH"] = override_download_path
        orchestrator.refresh_globals()
    manifest = load_local_manifest()
    ext_entry = _find_manifest_entry(manifest, extension_name)
    if ext_entry is None:
        update_local_manifest_from_remote()
        manifest = load_local_manifest()
        ext_entry = _find_manifest_entry(manifest, extension_name)
    if ext_entry is None:
        logger.warning(f"Extension '{extension_name}' not found in manifest")
        if override_download_path is not None:
            config["EXTENSION_DOWNLOAD_PATH"] = previous_download_path
            orchestrator.refresh_globals()
        return None

    if install_if_missing and not ext_entry.get("installed", False):
        logger.warning(f"Extension '{ext_entry['name']}' not installed, installing now...")
        install_selected_extension(ext_entry["name"], reinstall=False, prompt_for_update=prompt_for_update)
        manifest = load_local_manifest()
        ext_entry = _find_manifest_entry(manifest, extension_name)
        if ext_entry is None:
            if override_download_path is not None:
                config["EXTENSION_DOWNLOAD_PATH"] = previous_download_path
                orchestrator.refresh_globals()
            return None

    ext_folder = os.path.join(EXTENSIONS_DIR, ext_entry["name"])
    entry_point = os.path.join(ext_folder, ext_entry["entry_point"])
    if ext_entry.get("installed", False) and not os.path.exists(entry_point):
        logger.warning(
            f"Extension '{ext_entry['name']}' marked as installed but missing files. Reinstalling..."
        )
        install_selected_extension(ext_entry["name"], reinstall=True, prompt_for_update=prompt_for_update)
        manifest = load_local_manifest()
        ext_entry = _find_manifest_entry(manifest, extension_name)
        if ext_entry is None:
            return None
        ext_folder = os.path.join(EXTENSIONS_DIR, ext_entry["name"])
        entry_point = os.path.join(ext_folder, ext_entry["entry_point"])

    if not os.path.exists(entry_point):
        logger.warning(f"Extension: {ext_entry['name']}: Entry point not found.")
        if override_download_path is not None:
            config["EXTENSION_DOWNLOAD_PATH"] = previous_download_path
            orchestrator.refresh_globals()
        return None

    module_name = f"mangascraper.extensions.{ext_entry['name']}.{ext_entry['entry_point'].replace('.py', '')}"
    try:
        return _load_extension_module(module_name, entry_point)
    except Exception as e:
        logger.warning(
            f"Extension: {ext_entry['name']}: Failed to load: {e}. Is an external program managing it?"
        )
        return None
    finally:
        if override_download_path is not None:
            config["EXTENSION_DOWNLOAD_PATH"] = previous_download_path
            orchestrator.refresh_globals()

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
                module = _load_extension_module(module_name, entry_point)
                INSTALLED_EXTENSIONS.append(module)
                if suppess_pre_run_hook == False: # Call the extension's pre run hook if not skipped
                    log(f"Extension: {ext['name']}: Loaded.", "debug")
            except Exception as e:
                logger.warning(f"Extension: {ext['name']}: Failed to load: {e}. Is an external program managing it?")
        else:
            logger.warning(f"Extension: {ext['name']}: Entry point not found.")

#######################################################################
# Install / Uninstall Extension
#######################################################################
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
    repo_url = ext_entry.get("repo_url", "")
    if not repo_url:
        logger.error(f"Extension '{extension_name}': Missing repo_url in manifest")
        return

    try:
        log(f"Syncing remote extensions from {repo_url}...", "debug")
        target_branch = str(ext_entry.get("branch") or "main").strip() or "main"
        sync_remote_extensions_repo(
            repo_url,
            extension_name=extension_name,
            extension_version=remote_version or ext_entry.get("version"),
            extension_branch=target_branch,
        )
    except Exception as e:
        logger.warning(f"Failed to sync from primary repo: {e}")
        if BACKUP_BASE_REPO_URL:
            backup_url = repo_url.replace(PRIMARY_BASE_REPO_URL, BACKUP_BASE_REPO_URL)
            try:
                log(f"Retrying sync with backup repo: {backup_url}", "debug")
                sync_remote_extensions_repo(
                    backup_url,
                    extension_name=extension_name,
                    extension_version=remote_version or ext_entry.get("version"),
                    extension_branch=target_branch,
                )
            except Exception as e2:
                logger.error(f"Failed to sync from backup repo: {e2}")
                return
        else:
            return

    # Import and run install hook
    entry_point = ext_entry["entry_point"]
    module_name = f"mangascraper.extensions.{extension_name}.{entry_point.replace('.py', '')}"
    entry_point_path = os.path.join(ext_folder, entry_point)
    try:
        module = _load_extension_module(module_name, entry_point_path)
    except Exception as e:
        logger.error(f"Extension '{extension_name}': Failed to load entry point after install: {e}")
        return
    if hasattr(module, "install_extension"):
        module.install_extension()
        logger.warning(f"Extension '{extension_name}': Installed successfully.")

    # Update manifest
    ext_entry["installed"] = True
    if remote_version:
        ext_entry["version"] = remote_version
    save_local_manifest(manifest)


def ensure_extension_cli(extension_name: str):
    """Ensure a single extension is installed via the --install-extension CLI flow."""
    update_local_manifest_from_remote()
    manifest = load_local_manifest()
    ext_entry = _find_manifest_entry(manifest, extension_name)
    if not ext_entry:
        logger.warning(f"Extension '{extension_name}' not found in manifest")
        return

    entry_point = ext_entry.get("entry_point")
    ext_folder = os.path.join(EXTENSIONS_DIR, ext_entry["name"])
    entry_path = os.path.join(ext_folder, entry_point) if entry_point else None

    if ext_entry.get("installed", False):
        if entry_path and not os.path.exists(entry_path):
            install_selected_extension(extension_name, reinstall=True, prompt_for_update=False)
        else:
            logger.warning(
                f"Extension '{extension_name}': Already installed and up-to-date."
            )
        return

    install_selected_extension(extension_name, reinstall=False, prompt_for_update=False)

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

#######################################################################
# Extension Selection
#######################################################################
def ensure_extension_runtime(name: str = "skeleton", suppess_pre_run_hook: bool = False):
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

    # Try to load requested extension only
    ext = load_single_extension(
        original_name,
        install_if_missing=True,
        prompt_for_update=False,
    )
    final_name = original_name
    if ext is None:
        logger.warning(f"Extension '{original_name}' not available, falling back to skeleton")
        ext = load_single_extension(
            "skeleton",
            install_if_missing=True,
            prompt_for_update=False,
            override_download_path=DEFAULT_EXTENSION_DOWNLOAD_PATH,
        )
        final_name = "skeleton"

    if ext is None:
        logger.error("Failed to load the requested extension or skeleton! This should never happen, so something went really wrong.")
        return None

    if suppess_pre_run_hook == False:
        if hasattr(ext, "pre_run_hook"):
            ext.pre_run_hook()
        log_clarification()
        logger.info(f"Selected extension: {final_name}")

    return ext

#######################################################################
# Backwards-Compatible Wrappers
#######################################################################
def install_extension_cli(extension_name: str):
    return ensure_extension_cli(extension_name)

def get_selected_extension(name: str = "skeleton", suppess_pre_run_hook: bool = False):
    return ensure_extension_runtime(name, suppess_pre_run_hook=suppess_pre_run_hook)

#######################################################################
# Extension Download Path Helpers
#######################################################################
def get_extension_download_path(extension_name: str) -> str:
    """
    Get the appropriate download path for an extension.

    Priority:
    1. If orchestrator has a custom extension_download_path (from CLI or config), use it
    2. Else, use the extension's manifest image_download_path
    3. Else, use DEFAULT_EXTENSION_DOWNLOAD_PATH

    Args:
        extension_name: Name of the extension (lowercase)

    Returns:
        str: The download path for the extension
    """
    orchestrator.refresh_globals()
    override_download_path = getattr(orchestrator, "extension_download_path", None)
    default_path = DEFAULT_EXTENSION_DOWNLOAD_PATH

    def _ensure_trailing_slash(path: str) -> str:
        if not path:
            return path
        return path if path.endswith("/") else f"{path}/"

    # If a custom path was set via CLI or config, use it
    if override_download_path:
        override_norm = os.path.normpath(override_download_path)
        default_norm = os.path.normpath(default_path)
        if override_norm != default_norm:
            resolved = _ensure_trailing_slash(override_download_path)
            logger.debug(
                f"Extension download path resolved: {resolved} (source=override)"
            )
            return resolved

    # Get the extension's default from manifest
    manifest = load_local_manifest()
    for ext in manifest.get("extensions", []):
        if ext.get("name") == extension_name.lower():
            manifest_path = ext.get("image_download_path")
            if manifest_path:
                resolved = _ensure_trailing_slash(manifest_path)
                logger.debug(
                    f"Extension download path resolved: {resolved} (source=manifest)"
                )
                return resolved

    # Fall back to default
    resolved = _ensure_trailing_slash(default_path)
    logger.debug(f"Extension download path resolved: {resolved} (source=default)")
    return resolved

def get_extension_manifest_info(extension_name: str) -> dict | None:
    """
    Get manifest entry for an extension.

    Args:
        extension_name: Name of the extension (lowercase)

    Returns:
        dict: The extension's manifest entry, or None if not found
    """
    extension_name = str(extension_name or "").lower()
    manifest = load_local_manifest()
    for ext in manifest.get("extensions", []):
        if ext.get("name") == extension_name:
            return ext
    return None

def calculate_extension_download_path(extension_name: str) -> str:
    """
    Calculate the DEDICATED_DOWNLOAD_PATH for an extension.
    This helper function removes code duplication from skeleton and suwayomi extensions.

    Priority:
    1. If orchestrator has a custom extension_download_path (not the default), use it
    2. Else, use the extension's manifest image_download_path
    3. Else, use DEFAULT_EXTENSION_DOWNLOAD_PATH

    Args:
        extension_name: Name of the extension (lowercase, e.g., "skeleton", "suwayomi")

    Returns:
        str: The DEDICATED_DOWNLOAD_PATH for the extension

    Usage in extensions:
        from mangascraper.extensions.extension_manager import calculate_extension_download_path
        DEDICATED_DOWNLOAD_PATH = calculate_extension_download_path("skeleton")
    """

    extension_name = str(extension_name or "").lower()
    orchestrator.refresh_globals()
    override_download_path = getattr(orchestrator, "extension_download_path", None)
    default_path = DEFAULT_EXTENSION_DOWNLOAD_PATH

    def _ensure_trailing_slash(path: str) -> str:
        if not path:
            return path
        return path if path.endswith("/") else f"{path}/"

    # If a custom path was set via CLI or config (and it's not the default), use it
    if override_download_path:
        override_norm = os.path.normpath(override_download_path)
        default_norm = os.path.normpath(default_path)
        if override_norm != default_norm:
            resolved = _ensure_trailing_slash(override_download_path)
            logger.debug(
                f"Extension download path resolved: {resolved} (source=override)"
            )
            return resolved

    # Get the extension's default from manifest
    ext_info = get_extension_manifest_info(extension_name)
    if ext_info:
        manifest_path = ext_info.get("image_download_path")
        if manifest_path:
            resolved = _ensure_trailing_slash(manifest_path)
            logger.debug(
                f"Extension download path resolved: {resolved} (source=manifest)"
            )
            return resolved

    # Fall back to default
    resolved = _ensure_trailing_slash(default_path)
    logger.debug(f"Extension download path resolved: {resolved} (source=default)")
    return resolved

#######################################################################
# Shared Extension Helpers (Non-Hook)
#######################################################################

def parse_gallery_id(text: str) -> int | None:
    if not text:
        return None
    match = re.search(r"\((\d+)\)", str(text))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None

def find_latest_gallery_entry(creator_folder: str) -> tuple[int | None, str | None, bool]:
    if not os.path.isdir(creator_folder):
        return None, None, False

    entries = []
    for name in os.listdir(creator_folder):
        if not name.startswith("("):
            continue
        full_path = os.path.join(creator_folder, name)
        is_dir = os.path.isdir(full_path)
        is_cbz = name.endswith(".cbz") and os.path.isfile(full_path)
        is_zip = name.endswith(".zip") and os.path.isfile(full_path)
        is_archive = is_cbz or is_zip
        if not (is_dir or is_archive):
            continue
        entry_id = parse_gallery_id(name)
        if entry_id is None:
            continue
        # For archives, strip extension for entry_name, and set is_dir False
        if is_archive:
            entry_name = os.path.splitext(name)[0]
            entries.append((entry_id, entry_name, False))
        else:
            entry_name = name
            entries.append((entry_id, entry_name, True))

    if not entries:
        return None, None, False

    entries.sort(key=lambda item: item[0], reverse=True)
    return entries[0]

def find_latest_cover_id(covers_folder: str) -> int | None:
    if not os.path.isdir(covers_folder):
        return None
    cover_ids = []
    for name in os.listdir(covers_folder):
        entry_id = parse_gallery_id(name)
        if entry_id is not None:
            cover_ids.append(entry_id)
    if not cover_ids:
        return None
    return max(cover_ids)

def link_creator_cover(creator_folder: str, cover_source: str) -> str | None:
    try:
        _, ext = os.path.splitext(cover_source)
        for f in os.listdir(creator_folder):
            if f.startswith("cover") and f != "covers" and f != ".covers":
                try:
                    os.unlink(os.path.join(creator_folder, f))
                except Exception:
                    pass
        cover_link = os.path.join(creator_folder, f"cover{ext}")
        os.symlink(cover_source, cover_link)
        logger.info(f"Cover updated for {creator_folder}: {cover_link} -> {cover_source}")
        return cover_link
    except Exception:
        return None

def find_local_cover_and_link(creator_folder: str, entry_name: str, is_dir: bool) -> str | None:
    covers_folder = os.path.join(creator_folder, ".covers")
    if not os.path.isdir(covers_folder):
        os.makedirs(covers_folder, exist_ok=True)

    candidates = [
        f for f in os.listdir(covers_folder)
        if os.path.splitext(f)[0] == entry_name
    ]
    if candidates:
        candidates.sort()
        cover_source = os.path.join(covers_folder, candidates[0])
        logger.debug(f"Cover found in .covers: {cover_source}")
        return link_creator_cover(creator_folder, cover_source)

    if is_dir:
        gallery_path = os.path.join(creator_folder, entry_name)
        if os.path.isdir(gallery_path):
            logger.debug(f"Latest gallery is a folder; checking page 1 in {gallery_path}")
            # Find files with a leading numeric page index (handles zero-padded names)
            numeric_files = []
            for fn in os.listdir(gallery_path):
                try:
                    m = re.match(r"^(\d+)\.", fn)
                    if m:
                        numeric_files.append((int(m.group(1)), fn))
                except Exception:
                    continue
            if numeric_files:
                numeric_files.sort()
                chosen = None
                for num, fn in numeric_files:
                    if num == 1:
                        chosen = fn
                        break
                if chosen is None:
                    chosen = numeric_files[0][1]
                page1_file = os.path.join(gallery_path, chosen)
                _, ext = os.path.splitext(page1_file)
                cover_in_subfolder = os.path.join(covers_folder, f"{entry_name}{ext}")
                if not os.path.exists(cover_in_subfolder):
                    logger.debug(f"Copying cover into .covers: {cover_in_subfolder}")
                    shutil.copy2(page1_file, cover_in_subfolder)
                return link_creator_cover(creator_folder, cover_in_subfolder)

    return None

def ensure_creator_cover(creator_folder: str):
    try:
        if not os.path.isdir(creator_folder):
            return
        # Use find_latest_gallery_entry, which considers both directories and .cbz/.zip archives
        latest_id, entry_name, is_dir = find_latest_gallery_entry(creator_folder)
        if not entry_name or latest_id is None:
            return

        logger.debug(f"Cover missing for {creator_folder}; checking local sources for gallery {latest_id} (is_dir={is_dir}).")
        find_local_cover_and_link(creator_folder, entry_name, is_dir)
    except Exception as e:
        logger.debug(f"Failed to restore cover file in {creator_folder}: {e}")

def repair_creator_cover(creator_folder: str):
    try:
        logger.debug(f"[Cover Repair] Checking folder: {creator_folder}")
        if not os.path.isdir(creator_folder):
            logger.debug(f"[Cover Repair] Folder does not exist: {creator_folder}")
            return
        if any(
            f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
            for f in os.listdir(creator_folder)
        ):
            logger.debug(f"[Cover Repair] Cover file already exists in: {creator_folder}")
            return

        # Use find_latest_gallery_entry, which considers both directories and .cbz/.zip archives
        latest_id, entry_name, is_dir = find_latest_gallery_entry(creator_folder)
        logger.debug(f"[Cover Repair] Latest gallery entry: id={latest_id}, name={entry_name}, is_dir={is_dir}")
        if not entry_name or latest_id is None:
            logger.debug(f"[Cover Repair] No valid gallery entry found in: {creator_folder}")
            return

        if find_local_cover_and_link(creator_folder, entry_name, is_dir):
            logger.debug(f"[Cover Repair] Cover restored from local sources for: {creator_folder}")
            return

        covers_folder = os.path.join(creator_folder, ".covers")
        if not os.path.isdir(covers_folder):
            logger.debug(f"[Cover Repair] Creating covers folder: {covers_folder}")
            os.makedirs(covers_folder, exist_ok=True)

        logger.debug(f"[Cover Repair] Cover not found locally; attempting download for Gallery {latest_id}")
        try:
            meta = scraperapi.Fetch.gallery_metadata(latest_id)
            logger.debug(f"[Cover Repair] Fetched metadata for Gallery {latest_id}: {meta is not None}")
            if not meta:
                logger.warning(f"[Cover Repair] No metadata found for Gallery {latest_id}")
                return
            urls = scraperapi.Fetch.image_urls(meta, 1)
            logger.debug(f"[Cover Repair] Fetched image URLs for Gallery {latest_id}: {urls}")
            if not urls:
                logger.warning(f"[Cover Repair] No image URLs found for Gallery {latest_id}")
                return
            url = urls[0]
            ext = os.path.splitext(url.split("?")[0])[1]
            if not ext:
                ext = ".jpg"
            target = os.path.join(covers_folder, f"{entry_name}{ext}")
            logger.debug(f"[Cover Repair] Downloading cover from {url} to {target}")
            session = scraperapi.Get.session(referrer="Cover Repair", status="return")
            resp = session.get(url, timeout=(60, 60))
            resp.raise_for_status()
            with open(target, "wb") as f:
                f.write(resp.content)
            logger.info(f"[Cover Repair] Cover updated (downloaded) for Gallery {latest_id}: {target}")
            link_creator_cover(creator_folder, target)
            logger.debug(f"[Cover Repair] Cover linked for {creator_folder}: {target}")
        except Exception as e:
            logger.warning(f"[Cover Repair] Failed to download missing cover for Gallery {latest_id}: {e}")
    except Exception as e:
        logger.debug(f"[Cover Repair] Failed to restore cover file in {creator_folder}: {e}")
        
def repair_covers_hook(download_path, referrer="Extension Manager"):
    orchestrator.refresh_globals()
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {referrer}: Repair covers hook inactive.")
        return
    if not download_path or not os.path.isdir(download_path):
        logger.debug(f"[Cover Repair] No valid download path: {download_path}")
        return
    repaired = 0
    for name in os.listdir(download_path):
        creator_folder = os.path.join(download_path, name)
        if os.path.isdir(creator_folder):
            before = any(
                f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
                for f in os.listdir(creator_folder)
            )
            repair_creator_cover(creator_folder)
            after = any(
                f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
                for f in os.listdir(creator_folder)
            )
            if not before and after:
                repaired += 1
    logger.debug(f"{referrer}: Cover update pass complete. Restored {repaired} cover(s).")

def cleanup_download_tree(
    download_path: str,
    remove_empty_artist_folder: bool = True,
    log_scan_summary: bool = False,
):
    orchestrator.refresh_globals()

    log_clarification("debug")

    if not download_path or not os.path.isdir(download_path):
        log("No valid download path set, skipping cleanup.", "debug")
        return

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would remove empty directories under {download_path}")
        return

    broken_symlinks_removed = 0

    # Combined single walk for both directory cleanup and symlink removal
    for dirpath, dirnames, filenames in os.walk(download_path, topdown=False):
        if dirpath == download_path:
            continue

        # Remove empty directories
        try:
            if remove_empty_artist_folder:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
            else:
                if not dirnames and not filenames:
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
        except Exception as e:
            logger.warning(f"Could not remove empty directory: {dirpath}: {e}")

        # Check and remove broken symlinks
        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            if os.path.islink(full_path) and not os.path.exists(os.readlink(full_path)):
                try:
                    os.unlink(full_path)
                    logger.info(f"Removed broken symlink: {full_path}")
                    broken_symlinks_removed += 1
                except Exception as e:
                    logger.warning(f"Failed to remove broken symlink {full_path}: {e}")

        # Restore missing cover file for creator folders
        if os.path.dirname(dirpath) == download_path:
            ensure_creator_cover(dirpath)

    if log_scan_summary:
        logger.info("Removed empty directories.")
        log_clarification()

    if broken_symlinks_removed > 0:
        logger.info(f"Fixed {broken_symlinks_removed} broken symlink(s).")

    if log_scan_summary:
        logger.info("Scan complete.")