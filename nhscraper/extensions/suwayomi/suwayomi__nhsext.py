#!/usr/bin/env python3
# nhscraper/extensions/skeleton/skeleton__nhsext.py
import os, sys, time, random, argparse, re, subprocess, urllib.parse # 'Default' imports

import threading, asyncio, aiohttp, aiohttp_socks, json, shutil, tarfile # Module-specific imports

from requests.auth import HTTPBasicAuth
from tqdm import tqdm

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *
from nhscraper.core.api import get_session, get_meta_tags, make_filesystem_safe, clean_title

"""
MODULE DESCRIPTION
Example extension for nhentai-scraper. It is also used as the default extension if none is specified.
"""

"""
# ENSURE THAT THIS FILE IS THE *EXACT SAME* IN BOTH THE NHENTAI-SCRAPER REPO AND THE NHENTAI-SCRAPER-EXTENSIONS REPO.
# PLEASE UPDATE THIS FILE IN THE NHENTAI-SCRAPER REPO FIRST, THEN COPY IT OVER TO THE NHENTAI-SCRAPER-EXTENSIONS REPO.
ALL FUNCTIONS MUST BE THREAD SAFE. IF A FUNCTION MANIPULATES A GLOBAL VARIABLE, STORE AND UPDATE IT LOCALLY IF POSSIBLE.
"""

####################################################################################################################
# Global variables
####################################################################################################################

EXTENSION_NAME = "suwayomi" # Must be fully lowercase
EXTENSION_NAME_CAPITALISED = EXTENSION_NAME.capitalize()
EXTENSION_REFERRER = f"{EXTENSION_NAME_CAPITALISED} Extension" # Used for printing the extension's name.
_module_referrer=f"{EXTENSION_NAME}" # Used in executor.* / cross-module calls

EXTENSION_INSTALL_PATH = "/opt/suwayomi-server/" # Use this if extension installs external programs (like Suwayomi-Server)
REQUESTED_DOWNLOAD_PATH = "/opt/suwayomi-server/local/"
#DEDICATED_DOWNLOAD_PATH = None # In case it tweaks out.

LOCAL_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "local_manifest.json"
)

with open(os.path.abspath(LOCAL_MANIFEST_PATH), "r", encoding="utf-8") as f:
    manifest = json.load(f)

DEDICATED_DOWNLOAD_PATH = None # In case it tweaks out.
for ext in manifest.get("extensions", []):
    if ext.get("name") == EXTENSION_NAME:
        DEDICATED_DOWNLOAD_PATH = ext.get("image_download_path")
        break

# Optional fallback
if DEDICATED_DOWNLOAD_PATH is None: # Default download folder here.
    DEDICATED_DOWNLOAD_PATH = REQUESTED_DOWNLOAD_PATH

# What metadata key maps to what subdirectory
# Example: creator = SUBDIR_1, title = SUBDIR_2, etc
SUBFOLDER_STRUCTURE = ["creator", "title"]

####################################################################

# Thread locks for I/O operations
_clean_directories_lock = asyncio.Lock()

# NOTE: _gallery_meta_lock and _deferred_creators_lock remain threading.Lock() because they are used
# in synchronous contexts; the file metadata lock is async (asyncio.Lock) because metadata read/write functions are async.
_collected_gallery_metas = []
_gallery_meta_lock = threading.Lock()

_deferred_creators_lock = threading.Lock()

_creators_metadata_file_lock = asyncio.Lock()

# Lock for remove_from_deferred async modifications (async)
_remove_from_deferred_lock = asyncio.Lock()

SUWAYOMI_TARBALL_URL = "https://github.com/Suwayomi/Suwayomi-Server/releases/download/v2.1.1867/Suwayomi-Server-v2.1.1867-linux-x64.tar.gz"
TARBALL_FILENAME = SUWAYOMI_TARBALL_URL.split("/")[-1]

GRAPHQL_URL = "http://127.0.0.1:4567/api/graphql"

LOCAL_SOURCE_ID = None  # Local source is usually "0"
SUWAYOMI_CATEGORY_NAME = "NHentai Scraped"
CATEGORY_ID = None

AUTH_USERNAME = config.get("BASIC_AUTH_USERNAME", None) # Must be manually set for now. # NOTE: TEST
AUTH_PASSWORD = config.get("BASIC_AUTH_PASSWORD", None) # Must be manually set for now.

# Max number of genres stored in a creator's details.json
MAX_GENRES_STORED = 25
# Max number of genres parsed from a gallery and stored in a creator's "genre_count" field in creators_metadata.json.
MAX_GENRES_PARSED = 100

# Keep a persistent session for cookie-based login
graphql_session = None

creators_metadata_file = os.path.join(DEDICATED_DOWNLOAD_PATH, "creators_metadata.json")

async def load_creators_metadata() -> dict:
    """
    Async load creators_metadata.json (thread-safe via _creators_metadata_file_lock).
    Returns dictionary with default shape if file missing or unreadable.
    """
    async with _creators_metadata_file_lock:
        if os.path.exists(creators_metadata_file):
            try:
                # use executor.read_json() to run blocking open/read/json load
                return await executor.read_json(creators_metadata_file)
            except Exception as e:
                # Use log as requested
                log(f"Could not load creators_metadata.json: {e}", "warning")
        # Initialise dictionaries if missing
        return {
            "collected_manga_ids": [],
            "deferred_creators": [],
            "creators": {}
        }

async def save_creators_metadata(metadata: dict):
    """
    Async save creators_metadata.json using thread offload and protected by async lock.
    """
    async with _creators_metadata_file_lock:
        try:
            await executor.write_json(creators_metadata_file, metadata)
        except Exception as e:
            log(f"Could not save creators_metadata.json: {e}", "warning")

# ---- Global deferred list ----
async def load_deferred_creators() -> set[str]:
    """
    Async wrapper that returns a set of deferred creators.
    """
    
    metadata = await load_creators_metadata()
    return set(metadata.get("deferred_creators", []))

async def save_deferred_creators(creators: set[str]):
    """
    Async save deferred creators list into metadata.
    """
    metadata = await load_creators_metadata()
    metadata["deferred_creators"] = sorted(creators)
    await save_creators_metadata(metadata)

# ---- Collected manga IDs ----
async def load_collected_manga_ids() -> set[int]:
    metadata = await load_creators_metadata()
    return set(metadata.get("collected_manga_ids", []))

async def save_collected_manga_ids(ids: set[int]):
    metadata = await load_creators_metadata()
    metadata["collected_manga_ids"] = sorted(ids)
    await save_creators_metadata(metadata)

####################################################################################################################
# CORE
####################################################################################################################

def install_extension():
    """
    Install the extension and ensure the dedicated image download path exists.
    """
    
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH
    
    fetch_env_vars() # Refresh env vars in case config changed.

    if not DEDICATED_DOWNLOAD_PATH:
        # Fallback in case manifest didn't define it
        DEDICATED_DOWNLOAD_PATH = REQUESTED_DOWNLOAD_PATH
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] Would install extension and create paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}", "info")
        return

    try:
        # Ensure extension install path and image download path exists.
        os.makedirs(EXTENSION_INSTALL_PATH, exist_ok=True)
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        
        tarball_path = os.path.join("/tmp", TARBALL_FILENAME)

        if not os.path.exists(tarball_path):
            log(f"Downloading Suwayomi-Server tarball from {SUWAYOMI_TARBALL_URL} to {tarball_path}...", "info")

            async def _download():
                async with aiohttp.ClientSession() as session:
                    async with session.get(SUWAYOMI_TARBALL_URL) as resp:
                        resp.raise_for_status()
                        with open(tarball_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)

            # Run async download from sync code
            executor.run_blocking(_download)

        with tarfile.open(tarball_path, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                path_parts = member.name.split("/", 1)
                member.name = path_parts[1] if len(path_parts) > 1 else ""
            tar.extractall(path=EXTENSION_INSTALL_PATH, members=members)
        log(f"Suwayomi-Server extracted to {EXTENSION_INSTALL_PATH}", "info")

        service_file = "/etc/systemd/system/suwayomi-server.service"
        service_content = f"""[Unit]
Description=Suwayomi Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={EXTENSION_INSTALL_PATH}
ExecStart=/bin/bash ./suwayomi-server.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        with open(service_file, "w") as f:
            f.write(service_content)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", "suwayomi-server"], check=True)
        log("Suwayomi systemd service created and started", "info")
        log(f"\nSuwayomi Web: http://$IP:4567/", "debug")
        log("Suwayomi GraphQL: http://$IP:4567/api/graphql", "debug")
        
        pre_run_hook()
        log(f"{EXTENSION_REFERRER}:  Installed.", "info")
    
    except Exception as e:
        log(f"{EXTENSION_REFERRER}:  Failed to install: {e}", "error")

def uninstall_extension():
    """
    Remove the extension and related paths.
    """
    
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] Would uninstall extension and remove paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}", "info")
        return
    
    try:
        subprocess.run(["systemctl", "stop", "suwayomi-server"], check=False)
        subprocess.run(["systemctl", "disable", "suwayomi-server"], check=False)
        service_file = "/etc/systemd/system/suwayomi-server.service"
        if os.path.exists(service_file):
            os.remove(service_file)
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        
        # Ensure extension install path and image download path is removed.
        if os.path.exists(EXTENSION_INSTALL_PATH):
            os.rmdir(EXTENSION_INSTALL_PATH)
        if os.path.exists(DEDICATED_DOWNLOAD_PATH):
            os.rmdir(DEDICATED_DOWNLOAD_PATH)
        
        log(f"{EXTENSION_REFERRER}:  Uninstalled", "info")
    
    except Exception as e:
        log(f"{EXTENSION_REFERRER}:  Failed to uninstall: {e}", "error")

# Hook for pre-run functionality. Use active_extension.pre_run_hook(ARGS) in downloader.
def pre_run_hook():
    """
    This is one of this module's entrypoints.
    """
    
    log(f"{EXTENSION_NAME}: Ready.", "debug")
    log(f"{EXTENSION_REFERRER}:  Debugging started.", "debug")
    
    fetch_env_vars() # Refresh env vars in case config changed.
    update_env("EXTENSION_DOWNLOAD_PATH", DEDICATED_DOWNLOAD_PATH) # Update download path in env
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] Would ensure download path exists: {DEDICATED_DOWNLOAD_PATH}", "info")
        return
    try:
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        log(f"{EXTENSION_REFERRER}:  Download path ready at '{DEDICATED_DOWNLOAD_PATH}'.", "debug")
    except Exception as e:
        log(f"{EXTENSION_REFERRER}:  Failed to create download path '{DEDICATED_DOWNLOAD_PATH}': {e}", "error")

def return_gallery_metas(meta):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    artists = get_meta_tags(meta, "artist")
    groups = get_meta_tags(meta, "group")
    creators = artists or groups or ["Unknown Creator"]
    
    # Use call_appropriately so this works from both async and sync contexts
    title = executor.call_appropriately(clean_title, meta)
    id = str(meta.get("id", "Unknown ID"))
    full_title = f"({id}) {title}"
    
    gallery_language = get_meta_tags(meta, "language") or ["Unknown Language"]
    
    return {
        "creator": creators,
        "title": full_title,
        "short_title": title,
        "id": id,
        "language": gallery_language,
    }

####################################################################################################################
# CUSTOM HOOKS (Create your custom hooks here, add them into the corresponding CORE HOOK)
####################################################################################################################

# Hook for testing functionality. Use active_extension.test_hook(ARGS) in downloader.
def test_hook():
    """
    Update environment variables used by this module.
    Call this function at the start of any function that uses any these variables to ensure they are up to date.
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}:  Test Hook Called.", "debug")

# Remove empty folders inside DEDICATED_DOWNLOAD_PATH without deleting the root folder itself.
async def clean_directories(RemoveEmptyArtistFolder: bool = True):
    async with _clean_directories_lock:
        await executor.io_to_thread(_clean_directories_sync, RemoveEmptyArtistFolder)

def _clean_directories_sync(RemoveEmptyArtistFolder: bool = True):
    global DEDICATED_DOWNLOAD_PATH
    
    fetch_env_vars()
    log_clarification("debug")

    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        log("No valid DEDICATED_DOWNLOAD_PATH set, skipping cleanup.", "debug")
        return

    if orchestrator.dry_run:
        log(f"[DRY RUN] Would remove empty directories under {DEDICATED_DOWNLOAD_PATH}", "info")
        return

    # Pass 1: remove empty or nearly-empty dirs
    for dirpath, dirnames, filenames in os.walk(DEDICATED_DOWNLOAD_PATH, topdown=False):
        if dirpath == DEDICATED_DOWNLOAD_PATH:
            continue

        try:
            contents = os.listdir(dirpath)

            # Case A: RemoveEmptyArtistFolder = True → also count dirs with only details.json
            if RemoveEmptyArtistFolder:
                if not contents or (len(contents) == 1 and contents[0].lower() == "details.json"):
                    os.rmdir(dirpath)
                    log(f"Removed empty artist folder: {dirpath}", "info")

            # Case B: RemoveEmptyArtistFolder = False → only remove truly empty dirs
            else:
                if not dirnames and not filenames:
                    os.rmdir(dirpath)
                    log(f"Removed empty directory: {dirpath}", "info")

        except Exception as e:
            log(f"Could not remove empty directory: {dirpath}: {e}", "warning")

    log("Removed empty directories.", "info")
    log_clarification()

    # Pass 2: clean broken symlinks
    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        log("No valid DEDICATED_DOWNLOAD_PATH for symlink check.", "warning")
        return

    removed = 0
    for dirpath, _, filenames in os.walk(DEDICATED_DOWNLOAD_PATH):
        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            if os.path.islink(full_path) and not os.path.exists(os.readlink(full_path)):
                try:
                    os.unlink(full_path)
                    log(f"Removed broken symlink: {full_path}", "info")
                    removed += 1
                except Exception as e:
                    log(f"Failed to remove broken symlink {full_path}: {e}", "warning")

    log(f"Fixed {removed} broken symlink(s).", "info")
    
############################################

def graphql_request(request: str, variables: dict = None, auth_user: str = None, auth_passwd: str = None, auth: bool = False, gql_debugging: bool = False):
    """
    Framework for making requests to GraphQL. Setting "auth" to "True" Allows for authentication with the server (in development).
    """
    
    global graphql_session # Shared GraphQL Session.
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if gql_debugging:
        debugging = gql_debugging
    else:
        debugging = orchestrator.debug
    
    headers = {"Content-Type": "application/json"} # Set header here.

    if orchestrator.dry_run:
        log(f"[DRY RUN] GraphQL: Would make request: {request} with variables {variables}", "info")
        return None

    async def _aiohttp_request():
        if auth:
            payload = { # Set payload here (with authentication).
                "query": request,
                "variables": variables or {},
                "username": auth_user,
                "password": auth_passwd
            }
        else:
            payload = { # Set payload here (no authentication).
                "query": request,
                "variables": variables or {}
            }
        
        resp = None
        try:
            if debugging == True:
                log_clarification("debug")
                log(f"GraphQL Request Payload:\n{json.dumps(payload, indent=2)}", "debug") # NOTE: DEBUGGING

            async with aiohttp.ClientSession() as graphql_session:
                async with graphql_session.post(GRAPHQL_URL, headers=headers, json=payload, timeout=10) as resp:
                    resp.raise_for_status()
                    result = await resp.json()

            if debugging == True:
                log(f"GraphQL Response:\n{json.dumps(result, indent=2)}", "debug") # NOTE: DEBUGGING
            return result

        except aiohttp.ClientResponseError as e:
            log(f"GraphQL: Request failed: {e}", "error")
            return None
        except aiohttp.ContentTypeError as e:
            text = await resp.text() if resp else None
            log(f"GraphQL: Failed to decode JSON response: {e}", "error")
            log(f"Raw response: {text if text else 'No response'}", "error")
            return None
        except Exception as e:
            log(f"GraphQL: Unexpected error: {e}", "error")
            return None

    # Run async request in a blocking manner so the GraphQL requests remain synchronous
    return executor.run_blocking(_aiohttp_request)

def get_local_source_id():
    global LOCAL_SOURCE_ID

    #log("GraphQL: Fetching Local source ID", "debug")
    query = """
    query FetchLocalSourceID {
      sources {
        nodes { id name }
      }
    }
    """
    result = graphql_request(query, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False)
    if not result:
        log_clarification()
        log("GraphQL: Failed to fetch sources", "error")
        return LOCAL_SOURCE_ID
    
    for node in result["data"]["sources"]["nodes"]:
        #log_clarification("debug")
        #log(f"GraphQL: Checking source node {node}", "debug")
        if node["name"].lower() == "local source":
            LOCAL_SOURCE_ID = str(node["id"])  # must be a string in queries
            #log(f"GraphQL: Local source ID = {LOCAL_SOURCE_ID}", "debug")
            return LOCAL_SOURCE_ID

    log("GraphQL: Could not find 'Local source' in sources", "error")
    LOCAL_SOURCE_ID = None

def ensure_category(category_name=None):
    global CATEGORY_ID
    name = category_name or SUWAYOMI_CATEGORY_NAME

    log(f"GraphQL: Ensuring '{name}' category exists", "debug")
    query = """
    query EnsureTargetCategoryExists($name: String!) {
      categories(filter: { name: { equalTo: $name } }) {
        nodes { id name }
      }
    }
    """
    query_variables = {"name": name}
    result = graphql_request(query, query_variables, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False)   
    #log(f"GraphQL: Category query result: {result}", "debug")
    nodes = result.get("data", {}).get("categories", {}).get("nodes", [])
    if nodes:
        CATEGORY_ID = int(nodes[0]["id"])
        log(f"GraphQL: Found existing category {nodes[0]}", "debug")
        return CATEGORY_ID

    log(f"GraphQL: Creating new category {name}", "debug")
    mutation = """
    mutation CreateTargetCategory($name: String!) {
      createCategory(input: { name: $name }) {
        category { id name }
      }
    }
    """
    query_variables = {"name": name}
    result = graphql_request(mutation, query_variables, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False)
    log(f"GraphQL: Create category result: {result}", "debug")
    CATEGORY_ID = int(result["data"]["createCategory"]["category"]["id"])
    return CATEGORY_ID

# ----------------------------
# Bulk Update Functions
# ----------------------------

def update_suwayomi(operation: str, category_id, debugging: bool = False):
    """
    Turn debug on for the GraphQL queries and the logs will get VERY long.
    """

    LOCAL_SOURCE_ID = get_local_source_id()  # Fetch again in case

    if operation == "category":
        # Query to fetch available filters and meta for a source
        query = """
        query FetchSourceBrowse($sourceId: LongString!) {
          source(id: $sourceId) {
            id
            name
            displayName
            lang
            isConfigurable
            supportsLatest
            meta {
              sourceId
              key
              value
            }
            filters {
              ... on CheckBoxFilter { type: __typename CheckBoxFilterDefault: default name }
              ... on HeaderFilter { type: __typename name }
              ... on SelectFilter { type: __typename SelectFilterDefault: default name values }
              ... on TriStateFilter { type: __typename TriStateFilterDefault: default name }
              ... on TextFilter { type: __typename TextFilterDefault: default name }
              ... on SortFilter { type: __typename SortFilterDefault: default { ascending index } name values }
              ... on SeparatorFilter { type: __typename name }
              ... on GroupFilter {
                type: __typename
                name
                filters {
                  ... on CheckBoxFilter { type: __typename CheckBoxFilterDefault: default name }
                  ... on HeaderFilter { type: __typename name }
                  ... on SelectFilter { type: __typename SelectFilterDefault: default name values }
                  ... on TriStateFilter { type: __typename TriStateFilterDefault: default name }
                  ... on TextFilter { type: __typename TextFilterDefault: default name }
                  ... on SortFilter { type: __typename SortFilterDefault: default { ascending index } name values }
                  ... on SeparatorFilter { type: __typename name }
                }
              }
            }
          }
        }
        """
        query_variables = {"sourceId": LOCAL_SOURCE_ID}
        graphql_request(query, query_variables, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False, gql_debugging=debugging)

        # Mutation to fetch source mangas, sorted by latest
        latest_query = """
        mutation TriggerSourceFetchLatest($sourceId: LongString!, $page: Int!) {
          fetchSourceManga(input: { source: $sourceId, page: $page, type: LATEST }) {
            hasNextPage
            mangas {
              id
              title
              thumbnailUrl
              inLibrary
              initialized
              sourceId
            }
          }
        }
        """

        # Mutation to fetch source mangas, sorted by popularity
        popular_query = """
        mutation TriggerSourceFetchPopular($sourceId: LongString!, $page: Int!) {
          fetchSourceManga(input: { source: $sourceId, page: $page, type: POPULAR }) {
            hasNextPage
            mangas {
              id
              title
              thumbnailUrl
              inLibrary
              initialized
              sourceId
            }
          }
        }
        """
        query_variables = {"sourceId": LOCAL_SOURCE_ID, "page": 1}
        graphql_request(popular_query, query_variables, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False, gql_debugging=debugging)

    if operation == "library":
        # Mutation to trigger the update once
        query = """
        mutation TriggerGlobalUpdate($categoryId: Int!) {
          updateLibrary(input: { categories: [$categoryId] }) {
            updateStatus {
              jobsInfo {
                isRunning
                totalJobs
                finishedJobs
                skippedCategoriesCount
                skippedMangasCount
              }
            }
          }
        }
        """
        query_variables = {"categoryId": category_id}
        graphql_request(query, query_variables, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False, gql_debugging=debugging)

    if operation == "status":
        # Query to check the status repeatedly
        query = """
        query CheckLibraryCategoryUpdateStatus {
          libraryUpdateStatus {
            jobsInfo {
              isRunning
              totalJobs
              finishedJobs
              skippedCategoriesCount
              skippedMangasCount
            }
          }
        }
        """
        result = graphql_request(query, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False, gql_debugging=debugging)
        return result

def populate_suwayomi(category_id: int, attempt: int):
    log_clarification()
    log(f"Suwayomi Update Triggered. Waiting for completion...", "info")
    
    wait_time = 4

    try:
        # Fetch all mangas in the category update
        update_suwayomi("category", category_id, debugging=False)
        
        # Trigger the global update
        update_suwayomi("library", category_id, debugging=False)

        # Initialise progress bar
        pbar = tqdm(total=0, desc=f"Suwayomi Update (Attempt {attempt}/{orchestrator.max_retries})", unit="job", dynamic_ncols=True)
        last_finished = 0
        total_jobs = None

        while True:
            result = update_suwayomi("status", category_id, debugging=False)

            if not result:
                log("Failed to fetch update status, retrying...", "warning")
                dynamic_sleep(wait=wait_time, dynamic=False)
                continue

            try:
                jobs_info = result.get("data", {}).get("libraryUpdateStatus", {}).get("jobsInfo", {})

                # If it's a list of jobs, keep current logic
                if isinstance(jobs_info, list):
                    is_running = any(job.get("isRunning", False) for job in jobs_info)
                    finished = sum(job.get("finishedJobs", 0) for job in jobs_info)
                    total = sum(job.get("totalJobs", 0) for job in jobs_info)

                # If it's a single dict
                elif isinstance(jobs_info, dict):
                    is_running = jobs_info.get("isRunning", False)
                    finished = jobs_info.get("finishedJobs", 0)
                    total = jobs_info.get("totalJobs", 0)

                else:
                    log("Unexpected jobsInfo format, retrying...", "warning")
                    dynamic_sleep(wait=wait_time, dynamic=False)
                    continue
            
            except (KeyError, TypeError):
                log("Unexpected status response format, retrying...", "warning")
                dynamic_sleep(wait=wait_time, dynamic=False)
                continue

            if not is_running:
                log("GraphQL: Suwayomi Update has been stopped either by the user or Suwayomi. Exiting.", "info")
                break  # Immediate exit if update stopped

            # Set total if available
            if total_jobs is None and total > 0:
                total_jobs = total
                pbar.total = total_jobs
                pbar.refresh()

            # Update progress bar
            pbar.update(finished - last_finished)
            last_finished = finished

            # Exit when all jobs are finished
            if total_jobs is not None and finished >= total_jobs:
                pbar.n = pbar.total
                pbar.refresh()
                log(f"Suwayomi library update for Category ID {category_id} completed.", "warning")
                wait = max(wait_time * 5, (1 + total_jobs / 50))
                log(f"Waiting {wait}s for Suwayomi to reflect all changes...", "warning")
                dynamic_sleep(wait=wait, dynamic=False)
                break

            # Adaptive polling
            dynamic_sleep(wait=max(wait_time, (1 + total / 1000)), dynamic=False) # Adaptive polling

        pbar.close()

    except Exception as e:
        log(f"Failed during Suwayomi update for category {category_id}: {e}", "warning")

async def add_mangas_to_suwayomi(ids: list[int], category_id: int):
    if not ids:
        return
    
    log(f"GraphQL: Updating mangas {ids} as 'In Library'", "debug")
    mutation = """
    mutation AddMangasToLibrary($ids: [Int!]!) {
      updateMangas(input: { ids: $ids, patch: { inLibrary: true } }) {
        clientMutationId
      }
    }
    """
    
    # Call graphql_request (sync) ( use executor.run_blocking() ) from async context using call_appropriately
    result = executor.run_blocking(
        graphql_request, mutation, {"ids": ids}, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False
    )
    #log(f"GraphQL: updateMangas result: {result}", "debug")
    log(f"GraphQL: Updated {len(ids)} mangas as 'In Library'.", "info")
    
    log(f"GraphQL: Adding mangas {ids} to category {category_id}", "debug")
    mutation = """
    mutation AddMangasToCategory($ids: [Int!]!, $categoryId: Int!) {
      updateMangasCategories(
        input: { ids: $ids, patch: { addToCategories: [$categoryId] } }
      ) {
        mangas { id title }
      }
    }
    """
    result = executor.run_blocking(
        graphql_request, mutation, {"ids": ids, "categoryId": category_id}, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False
    )
    #log(f"GraphQL: updateMangasCategories result: {result}", "debug")
    log(f"GraphQL: Added {len(ids)} mangas to category {category_id}.", "info")
    
def fetch_creators_suwayomi_metadata(creator_name: str):
    """
    Retrieve metadata for a creator from Suwayomi's Local Source by exact title match.
    Returns the list of nodes (id, title, chapters, etc).
    """
    
    query = """
    query FetchMangaMetadataFromLocalSource($title: String!) {
      mangas(
        filter: { sourceId: { equalTo: "0" }, title: { equalTo: $title } }
      ) {
        nodes {
          id
          title
          chapters {
            nodes {
              name
            }
          }
        }
      }
    }
    """
    result = graphql_request(query, {"title": creator_name}, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False)
    if not result:
        return []
    return result.get("data", {}).get("mangas", {}).get("nodes", [])

async def remove_from_deferred(creator_name: str):
    """
    Remove a creator from the global deferred_creators list in creators_metadata.json.
    Uses async lock _remove_from_deferred_lock to avoid races across async callers.
    """
    async with _remove_from_deferred_lock:
        metadata = await load_creators_metadata()
        deferred_creators = set(metadata.get("deferred_creators", []))

        if creator_name in deferred_creators:
            deferred_creators.discard(creator_name)
            log(f"Removed '{creator_name}' from deferred creators.", "info")
            metadata["deferred_creators"] = sorted(deferred_creators)
            await save_creators_metadata(metadata)

# ------------------------------------------------------------
# Update creator mangas and ensure they are added to Suwayomi
# ------------------------------------------------------------
async def update_creator_manga(meta):
    """
    Update a creator's details.json and genre metadata based on a downloaded gallery.
    Also attempt to immediately add the creator's manga to Suwayomi using its ID.

    This function is async; file operations are offloaded with io_to_thread. When called from
    sync contexts, callers should use executor.call_appropriately(update_creator_manga, meta, type="TYPE").
    # NOTE: type= 'gallery', 'image', or 'default' (generic task, if default, you don't need to specify type)
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.
        
    log_clarification("debug")
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] Would process gallery {meta.get('id')}", "debug")
        return

    gallery_meta = return_gallery_metas(meta)
    creators = [make_filesystem_safe(c) for c in gallery_meta.get("creator", [])]
    if not creators:
        return

    gallery_title = gallery_meta["title"]
    gallery_tags = meta.get("tags", [])
    gallery_genres = [
        tag["name"] for tag in gallery_tags
        if "name" in tag and tag.get("type") not in ["artist", "group", "language", "category"]
    ]

    # Load all metadata at once (async)
    metadata = await load_creators_metadata()
    collected_ids = set(metadata.get("collected_manga_ids", []))
    deferred_creators = set(metadata.get("deferred_creators", []))
    if "creators" not in metadata:
        metadata["creators"] = {}

    # For writing details.json files we will offload just the file write operations
    for creator_name in creators:
        # --- Try to retrieve manga metadata from Suwayomi ---
        nodes = fetch_creators_suwayomi_metadata(creator_name)
        suwayomi_id = int(nodes[0]["id"]) if nodes else None

        if suwayomi_id is not None:
            collected_ids.add(suwayomi_id)
            try:
                # add_mangas_to_suwayomi is async; using executor.spawn_task() will run it fine. # NOTE: TEST
                executor.spawn_task(
                    add_mangas_to_suwayomi([suwayomi_id], CATEGORY_ID),
                    type="gallery"
                )
                collected_ids.discard(suwayomi_id)

                # Use helper instead of manual discard
                await remove_from_deferred(creator_name)

            except Exception as e:
                log(f"Failed to update manga {suwayomi_id} for {creator_name}: {e}", "warning")
                collected_ids.discard(suwayomi_id)
                deferred_creators.add(creator_name)
        else:
            # No existing manga found, mark creator as deferred
            deferred_creators.add(creator_name)

        # --- Update genre counts ---
        entry = metadata["creators"].setdefault(creator_name, {})
        genre_counts = entry.get("genre_counts", {})
        for genre in gallery_genres:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
        entry["genre_counts"] = dict(sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:MAX_GENRES_PARSED])

        # --- Update details.json ---
        creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
        await executor.io_to_thread(os.makedirs, creator_folder, exist_ok=True) # create folder using io_to_thread
        details_file = os.path.join(creator_folder, "details.json")

        most_popular = sorted(entry["genre_counts"].items(), key=lambda x: x[1], reverse=True)[:MAX_GENRES_STORED]
        details = {
            "title": creator_name,
            "author": creator_name,
            "artist": creator_name,
            "description": f"Latest Doujin: {gallery_title}",
            "genre": [g for g, _ in most_popular],
            "status": "1",
            "_status values": ["0 = Unknown", "1 = Ongoing", "2 = Completed", "3 = Licensed"]
        }

        await executor.io_to_thread(executor.write_json, details_file, details) # Offload JSON write

    # --- Save all metadata at once (async)---
    metadata["collected_manga_ids"] = sorted(collected_ids)
    metadata["deferred_creators"] = sorted(deferred_creators)
    await save_creators_metadata(metadata)

    # --- Update manga cover ---
    if not orchestrator.dry_run:
        try:
            for creator_name in creators:
                creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
                gallery_folder = os.path.join(creator_folder, gallery_meta["title"])
                if not os.path.exists(gallery_folder):
                    log(f"Skipping manga cover update: Gallery folder not found: {gallery_folder}", "info")
                    continue

                candidates = [f for f in os.listdir(gallery_folder) if f.startswith("1.")]
                if not candidates:
                    log(f"Skipping manga cover update: No 'page 1' found in Gallery: {gallery_folder}", "info")
                    continue

                page1_file = os.path.join(gallery_folder, candidates[0])
                _, ext = os.path.splitext(page1_file)

                # Remove old cover - offload removal to thread
                for f in os.listdir(creator_folder):
                    if f.startswith("cover."):
                        try:
                            await executor.io_to_thread(os.remove, os.path.join(creator_folder, f))
                            log(f"Removed old cover file: {os.path.join(creator_folder, f)}", "info")
                        except Exception as e:
                            log(f"Failed to remove old cover file for {creator_folder}: {e}", "info")
                            log("You can safely ignore this. Suwayomi will generate it automatically", "info")

                cover_file = os.path.join(creator_folder, f"cover{ext}")
                
                # copy file in thread
                await executor.io_to_thread(shutil.copy2, page1_file, cover_file)
                log(f"Updated manga cover for {creator_name}: {cover_file}", "info")

        except Exception as e:
            log(f"Failed to update manga cover for Gallery {meta['id']}: {e}", "error")
    else:
        log(f"[DRY RUN] Would update manga cover for creators: {creators}", "debug")

def process_deferred_creators():
    """
    Adds deferred creators to library and updates their category.
    Ensures only existing local creator folders are added.
    Adds all existing local mangas to library + category if they exist on disk.
    Cleans up creators_metadata.json so successful creators are removed from deferred creators.

    This function remains synchronous. It calls async helpers via executor.call_appropriately(..., type="TYPE") where necessary.
    # NOTE: type= 'gallery', 'image', or 'default' (generic task, if default, you don't need to specify type)
    """
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    process_creators_attempt = 1
    
    still_deferred = set()
    
    max_attempts = orchestrator.max_retries

    while process_creators_attempt <= max_attempts:
        log_clarification()
        log(f"Processing creators (attempt {process_creators_attempt}/{orchestrator.max_retries})", "info")
        
        # Update Suwayomi category first (blocking sync)
        populate_suwayomi(CATEGORY_ID, process_creators_attempt)

        # ----------------------------
        # Add mangas not yet in library
        # ----------------------------
        log_clarification()
        log("GraphQL: Fetching mangas not yet in library...", "info")

        query = """
        query FetchMangasNotInLibrary($sourceId: LongString!) {
        mangas(filter: { sourceId: { equalTo: $sourceId }, inLibrary: { equalTo: false } }) {
            nodes { id title }
        }
        }
        """
        result = graphql_request(query, {"sourceId": LOCAL_SOURCE_ID}, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False)
        nodes = result.get("data", {}).get("mangas", {}).get("nodes", []) if result else []

        new_ids = []

        if not nodes:
            log("GraphQL: No mangas found outside the library.", "info")
        else:
            for node in nodes:
                title = node["title"]
                expected_path = os.path.join(DEDICATED_DOWNLOAD_PATH, title)
                if os.path.exists(expected_path):
                    new_ids.append(int(node["id"]))
                    # remove from deferred if found (async) -> use executor.run_blocking()
                    executor.run_blocking(remove_from_deferred, title)

            if new_ids:
                log(f"GraphQL: Adding {len(new_ids)} mangas to library and category.", "info")
                
                # add_mangas_to_suwayomi is async -> call via executor.run_blocking()
                executor.run_blocking(add_mangas_to_suwayomi, new_ids, CATEGORY_ID)

        # ----------------------------
        # Process deferred creators
        # ----------------------------
        log_clarification()

        # Here _deferred_creators_lock is a threading.Lock and used in sync context
        # load_deferred_creators is async: call via executor.run_blocking()
        with _deferred_creators_lock:
            deferred_creators = executor.run_blocking(load_deferred_creators)

        if not deferred_creators:
            log("GraphQL: No deferred creators to process.", "info")
            return

        log(f"GraphQL: Processing {len(deferred_creators)} deferred creators...", "info")

        query = """
        query FindMangaMetadataFromLocalSource($creatorName: String!) {
        mangas(
            filter: { sourceId: { equalTo: "0" }, title: { equalTo: $creatorName } }
        ) {
            nodes {
            id
            title
            inLibrary
            categories {
                nodes {
                id
                }
            }
            }
        }
        }
        """
        
        new_ids = set()
        processed_creators = set()

        for creator_name in sorted(deferred_creators):
            creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
            if not os.path.exists(creator_folder):
                log(f"Skipping deferred creator '{creator_name}': folder does not exist.", "warning")
                still_deferred.add(creator_name)
                continue

            result = graphql_request(query, {"creatorName": creator_name}, auth_user=AUTH_USERNAME, auth_passwd=AUTH_PASSWORD, auth=False)
            mangas = result.get("data", {}).get("mangas", {}).get("nodes", []) if result else []

            if not mangas:
                log(f"Creator manga '{creator_name}' not found in Suwayomi local source.", "warning")
                still_deferred.add(creator_name)
                continue

            manga_info = mangas[0]  # title is unique per creator
            # categories nodes may have id as int or str
            categories_nodes = manga_info.get("categories", {}).get("nodes", [])
            category_ids = [int(c["id"]) for c in categories_nodes] if categories_nodes else []
            if manga_info.get("inLibrary") and CATEGORY_ID in category_ids:
                log(f"Creator manga '{creator_name}' already in library and category. Removing from deferred list.", "info")
                executor.call_appropriately(remove_from_deferred, creator_name)
                continue

            new_ids.add(int(manga_info["id"]))
            processed_creators.add(creator_name)
            log(f"Queued manga ID {manga_info['id']} for '{creator_name}'.", "info")

        if new_ids:
            # add_mangas_to_suwayomi is async -> call via executor.call_appropriately()
            executor.call_appropriately(add_mangas_to_suwayomi, list(new_ids), CATEGORY_ID)
            
            for creator_name in processed_creators:
                executor.call_appropriately(remove_from_deferred, creator_name)
        
        # If no creators remain, we're done early
        if not still_deferred:
            log("Successfully processed all deferred creators.", "info")
            return
        
        # Otherwise, try again
        process_creators_attempt += 1

    # After max retries, keep creators still deferred
    executor.call_appropriately(save_deferred_creators, still_deferred)
    log("Unable to process Creators: " + ", ".join(sorted(still_deferred)) if still_deferred else "Sucessfully processed all creators.", "warning")

####################################################################################################################
# CORE HOOKS (Please add to the functions, do NOT change or remove any function names)
####################################################################################################################

# Hook for downloading images. Use active_extension.download_images_hook(ARGS) in downloader.
def download_images_hook(gallery, page, urls, path, _downloader_session, pbar=None, creator=None):
    """
    Downloads an image from one of the provided URLs to the given path.
    Tries mirrors in order until one succeeds, with retries per mirror.
    Updates tqdm progress bar with current creator.
    """

    fetch_env_vars() # Refresh env vars in case config changed.

    if not urls:
        log(f"Gallery {gallery}: Page {page}: No URLs, skipping", "warning")
        if pbar and creator:
            pbar.set_postfix_str(f"Skipped Creator: {creator}")
        return False

    if os.path.exists(path):
        log(f"Already exists, skipping: {path}", "debug")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if orchestrator.dry_run:
        log(f"[DRY RUN] Gallery {gallery}: Would download {urls[0]} -> {path}", "info")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if _downloader_session is None: # Use executor.run_blocking()
        _downloader_session = executor.run_blocking(get_session, status="rebuild")

    def try_download(session, mirrors, retries, tor_rotate=False):
        """Try downloading with a given session and retry count."""
        
        for url in mirrors:
            for attempt in range(1, retries + 1):
                try:
                    r = session.get(url, timeout=10, stream=True)
                    if r.status_code == 429:
                        dynamic_sleep(stage="api", attempt=attempt)
                        log(f"429 rate limit hit for {url}, backing off (attempt {attempt})", "warning")
                        continue
                    r.raise_for_status()

                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

                    # log(f"Downloaded Gallery {gallery}: Page {page} -> {path}", "debug")
                    log(f"Downloaded Gallery {gallery}: Page {page} -> {path}", "debug")
                    if pbar and creator:
                        pbar.set_postfix_str(f"Creator: {creator}")
                    return True

                except Exception as e:
                    dynamic_sleep(stage="gallery", attempt=attempt)
                    log_clarification()
                    log(f"Gallery {gallery}: Page {page}: Mirror {url}, attempt {attempt} failed: {e}, retrying", "warning")

            log(f"Gallery {gallery}: Page {page}: Mirror {url} failed after {retries} attempts, trying next mirror", "warning")
        return False

    # First attempt: normal retries
    success = try_download(_downloader_session, urls, orchestrator.max_retries)

    # If still failed, rebuild Tor session once and retry
    if not success and orchestrator.use_tor:
        log(f"Gallery {gallery}: Page {page}: All retries failed, rotating Tor node and retrying once more...", "warning")
        
        # Use executor.run_blocking()
        _downloader_session = executor.run_blocking(get_session, status="rebuild")
        success = try_download(_downloader_session, urls, 1, tor_rotate=True)

    if not success:
        log_clarification()
        log(f"Gallery {gallery}: Page {page}: All mirrors failed after Tor rotate too: {urls}", "error")
        if pbar and creator:
            pbar.set_postfix_str(f"Failed Creator: {creator}")

    return success

# Hook for pre-batch functionality. Use active_extension.pre_batch_hook(ARGS) in downloader.
def pre_batch_hook(gallery_list):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] {EXTENSION_REFERRER}:  Pre-batch Hook Inactive.", "info")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}:  Pre-batch Hook Called.", "debug")
    
    global LOCAL_SOURCE_ID, CATEGORY_ID
    
    # Initialise globals
    LOCAL_SOURCE_ID = get_local_source_id()
    CATEGORY_ID = ensure_category(SUWAYOMI_CATEGORY_NAME)
    
    return gallery_list

# Hook for functionality before a gallery download. Use active_extension.pre_gallery_download_hook(ARGS) in downloader.
def pre_gallery_download_hook(gallery_id):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] {EXTENSION_REFERRER}:  Pre-download Hook Inactive.", "info")
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}:  Pre-download Hook Called: Gallery: {gallery_id}", "debug")

# Hook for functionality during a gallery download. Use active_extension.during_gallery_download_hook(ARGS) in downloader.
def during_gallery_download_hook(gallery_id):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] {EXTENSION_REFERRER}:  During-download Hook Inactive.", "info")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}:  During-download Hook Called: Gallery: {gallery_id}", "debug")

# Hook for functionality after a completed gallery download. Use active_extension.after_completed_gallery_download_hook(ARGS) in downloader.
def after_completed_gallery_download_hook(meta: dict, gallery_id):
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] {EXTENSION_REFERRER}:  Post-download Hook Inactive.", "info")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}:  Post-Completed Gallery Download Hook Called: Gallery: {meta['id']}: Downloaded.", "debug")
    
    # Thread-safe append
    with _gallery_meta_lock:
        _collected_gallery_metas.append(meta)
    
    # Update creator's popular genres - update_creator_manga is async, call via executor.call_appropriately()
    executor.call_appropriately(update_creator_manga, meta)

# Hook for post-batch functionality. Use active_extension.post_batch_hook(ARGS) in downloader.
def post_batch_hook():
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] {EXTENSION_REFERRER}:  Post-batch Hook Inactive.", "info")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}:  Post-batch Hook Called.", "debug")

# Hook for post-run functionality. Use active_extension.post_run_hook(ARGS) in downloader.
def post_run_hook():
    fetch_env_vars() # Refresh env vars in case config changed.
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] {EXTENSION_REFERRER}:  Post-run Hook Inactive.", "info")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}:  Post-run Hook Called.", "debug")
    
    clean_directories(True)
    
    if orchestrator.skip_post_run == True:
        log_clarification("debug")
        log(f"{EXTENSION_REFERRER}:  Post-run Hook Skipped.", "debug")
    else:
        log_clarification("debug")
        log(f"{EXTENSION_REFERRER}:  Post-run Hook Active.", "debug")
        
        # Add all creators to Suwayomi
        process_deferred_creators()
        
        # Update Suwayomi category at end
        log_clarification()
        log("Please update the library manually and / or run a small download to reflect any changes.", "info")