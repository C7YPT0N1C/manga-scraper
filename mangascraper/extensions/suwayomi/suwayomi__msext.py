#!/usr/bin/env python3
# mangascraper/extensions/suwayomi/suwayomi__msext.py

import os, time, json, requests, threading, subprocess, shutil, tarfile, math, re

from requests.auth import HTTPBasicAuth
from tqdm import tqdm

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.core.api import (
    get_session,
    get_meta_tags,
    make_filesystem_safe,
    clean_title,
    dynamic_sleep,
    fetch_gallery_metadata,
    fetch_image_urls,
)

####################################################################################################################
# Global variables
####################################################################################################################

EXTENSION_NAME = "suwayomi" # Must be fully lowercase
EXTENSION_NAME_CAPITALISED = EXTENSION_NAME.capitalize()
EXTENSION_REFERRER = f"{EXTENSION_NAME_CAPITALISED} Extension" # Used for printing the extension's name.

EXTENSION_INSTALL_PATH = "/opt/suwayomi-server/" # Use this if extension installs external programs (like Suwayomi-Server)

LOCAL_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "local_manifest.json"
)

with open(os.path.abspath(LOCAL_MANIFEST_PATH), "r", encoding="utf-8") as f:
    manifest = json.load(f)

DEDICATED_DOWNLOAD_PATH = None
manifest_download_path = None
for ext in manifest.get("extensions", []):
    if ext.get("name") == EXTENSION_NAME:
        manifest_download_path = ext.get("image_download_path")
        break

orchestrator.refresh_globals()
override_download_path = getattr(orchestrator, "extension_download_path", None)
if override_download_path and override_download_path != DEFAULT_EXTENSION_DOWNLOAD_PATH:
    DEDICATED_DOWNLOAD_PATH = override_download_path
elif manifest_download_path:
    DEDICATED_DOWNLOAD_PATH = manifest_download_path
else:
    DEDICATED_DOWNLOAD_PATH = DEFAULT_EXTENSION_DOWNLOAD_PATH

SUBFOLDER_STRUCTURE = ["creator", "title"] # SUBDIR_1, SUBDIR_2, etc

# Used to optionally run stuff in hooks (for example, cleaning the download directory) roughly "RUNS_PER_X_BATCHES" times every "EVERY_X_BATCHES" batches.
# Increase this if the operations in your post batch / run hooks get increasingly demanding the larger the library is.
MAX_X_BATCHES = 50
EVERY_X_BATCHES = 10
RUNS_PER_X_BATCHES = 1

####################################################################

GRAPHQL_URL = "http://127.0.0.1:4567/api/graphql"

LOCAL_SOURCE_ID = None  # Local source is usually "0"
SUWAYOMI_CATEGORY_NAME = "ScrapedMangas"
CATEGORY_ID = None
SUWAYOMI_POPULATION_TIME = 2 # Suwayomi update ticks every ~2 secs.

ARCHIVE_WAIT_SECONDS = 120
ARCHIVE_POLL_INTERVAL = 0.5

# NOTE: TEST
AUTH_USERNAME = config.get("BASIC_AUTH_USERNAME", None) # Must be manually set for now.
AUTH_PASSWORD = config.get("BASIC_AUTH_PASSWORD", None) # Must be manually set for now.

# Max number of genres stored in a creator's details.json
MAX_GENRES_STORED = 50
# Max number of genres parsed from a gallery and stored in a creator's "genre_count" field in creators_metadata.json.
MAX_GENRES_PARSED = 1000

# Keep a persistent session for cookie-based login
graphql_session = None

# Thread locks for file operations
_gallery_meta_lock = threading.Lock()
_collected_gallery_metas = []

creators_metadata_file = os.path.join(DEDICATED_DOWNLOAD_PATH, "creators_metadata.json")
_creators_metadata_lock = threading.Lock()

def load_creators_metadata() -> dict:
    with _creators_metadata_lock:
        if os.path.exists(creators_metadata_file):
            try:
                with open(creators_metadata_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load creators_metadata.json: {e}")
        
        # Initialise dictionaries if missing
        return {
            "collected_manga_ids": [],
            "deferred_creators": [],
            "creators": {}
        }

def save_creators_metadata(metadata: dict):
    with _creators_metadata_lock:
        try:
            os.makedirs(os.path.dirname(creators_metadata_file), exist_ok=True)
            with open(creators_metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Could not save creators_metadata.json: {e}")
        
# Removed redundant wrapper functions. Code now works directly with metadata dict.

####################################################################################################################
# CORE
####################################################################################################################

# Hook for pre-run functionality. Use active_extension.pre_run_hook(ARGS) in downloader.
def pre_run_hook():
    """
    This is one this module's entrypoints.
    """
    
    logger.debug(f"{EXTENSION_REFERRER}: Ready.")
    log(f"{EXTENSION_REFERRER}: Debugging started.", "debug")
    
    orchestrator.refresh_globals()
    update_env("EXTENSION_DOWNLOAD_PATH", DEDICATED_DOWNLOAD_PATH) # Update download path in env
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would ensure download path exists: {DEDICATED_DOWNLOAD_PATH}")
        return
    try:
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        logger.debug(f"{EXTENSION_REFERRER}: Download path ready at '{DEDICATED_DOWNLOAD_PATH}'.")
    except Exception as e:
        logger.error(f"{EXTENSION_REFERRER}: Failed to create download path '{DEDICATED_DOWNLOAD_PATH}': {e}")

def return_gallery_metas(meta):
    orchestrator.refresh_globals()
    
    artists = get_meta_tags(f"{EXTENSION_REFERRER}: Return_gallery_metas", meta, "artist")
    groups = get_meta_tags(f"{EXTENSION_REFERRER}: Return_gallery_metas", meta, "group")
    creators = artists or groups or ["Unknown Creator"]
    
    title = clean_title(meta)
    id = str(meta.get("id", "Unknown ID"))
    full_title = f"({id}) {title}"
    
    gallery_language = get_meta_tags(f"{EXTENSION_REFERRER}: Return_gallery_metas", meta, "language") or ["Unknown Language"]
    
    return {
        "creator": creators,
        "title": full_title,
        "short_title": title,
        "id": id,
        "language": gallery_language,
    }

def _extract_gallery_id(text: str) -> int | None:
    if not text:
        return None
    match = re.search(r"\((\d+)\)", str(text))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None

def _get_latest_id_from_details(creator_folder: str) -> int | None:
    details_file = os.path.join(creator_folder, "details.json")
    if not os.path.exists(details_file):
        return None
    try:
        with open(details_file, "r", encoding="utf-8") as f:
            details = json.load(f)
        return _extract_gallery_id(details.get("description", ""))
    except Exception:
        return None

def _get_latest_gallery_entry(creator_folder: str) -> tuple[int | None, str | None, bool]:
    if not os.path.isdir(creator_folder):
        return None, None, False

    entries = []
    for name in os.listdir(creator_folder):
        if not name.startswith("("):
            continue
        full_path = os.path.join(creator_folder, name)
        is_dir = os.path.isdir(full_path)
        is_archive = name.endswith(".cbz") or name.endswith(".zip")
        if not (is_dir or is_archive):
            continue
        entry_id = _extract_gallery_id(name)
        if entry_id is None:
            continue
        entry_name = name
        if is_archive:
            entry_name = os.path.splitext(name)[0]
        entries.append((entry_id, entry_name, is_dir))

    if not entries:
        return None, None, False

    entries.sort(key=lambda item: item[0], reverse=True)
    return entries[0]

def _get_latest_cover_id(covers_folder: str) -> int | None:
    if not os.path.isdir(covers_folder):
        return None
    cover_ids = []
    for name in os.listdir(covers_folder):
        entry_id = _extract_gallery_id(name)
        if entry_id is not None:
            cover_ids.append(entry_id)
    if not cover_ids:
        return None
    return max(cover_ids)

def _ensure_cover_file(creator_folder: str):
    try:
        if not os.path.isdir(creator_folder):
            return

        latest_id, entry_name, is_dir = _get_latest_gallery_entry(creator_folder)
        if not entry_name:
            return

        logger.debug(f"Cover missing for {creator_folder}; searching for latest gallery cover.")

        def _link_cover(cover_source: str):
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

        covers_folder = os.path.join(creator_folder, ".covers")
        if not os.path.isdir(covers_folder):
            os.makedirs(covers_folder, exist_ok=True)

        candidates = [f for f in os.listdir(covers_folder) if f.startswith(entry_name)]
        if not candidates and latest_id is not None:
            candidates = [
                f for f in os.listdir(covers_folder)
                if _extract_gallery_id(f) == latest_id
            ]
        if candidates:
            candidates.sort()
            cover_source = os.path.join(covers_folder, candidates[0])
            logger.debug(f"Cover found in .covers: {cover_source}")
            _link_cover(cover_source)
            return

        if is_dir:
            gallery_path = os.path.join(creator_folder, entry_name)
            if os.path.isdir(gallery_path):
                logger.debug(f"Latest gallery is a folder; checking page 1 in {gallery_path}")
                candidates = [f for f in os.listdir(gallery_path) if f.startswith("1.")]
                if candidates:
                    page1_file = os.path.join(gallery_path, candidates[0])
                    _, ext = os.path.splitext(page1_file)
                    cover_in_subfolder = os.path.join(covers_folder, f"{entry_name}{ext}")
                    if not os.path.exists(cover_in_subfolder):
                        logger.debug(f"Copying cover into .covers: {cover_in_subfolder}")
                        shutil.copy2(page1_file, cover_in_subfolder)
                    _link_cover(cover_in_subfolder)
                    return

        if latest_id is not None:
            logger.debug(f"Cover not found locally; downloading for Gallery {latest_id}")
            try:
                meta = fetch_gallery_metadata(latest_id)
                if not meta:
                    return
                urls = fetch_image_urls(meta, 1)
                if not urls:
                    return
                url = urls[0]
                ext = os.path.splitext(url.split("?")[0])[1]
                if not ext:
                    ext = ".jpg"
                target = os.path.join(covers_folder, f"{entry_name}{ext}")
                session = get_session(referrer="Cover Repair", status="return")
                resp = session.get(url, timeout=(60, 60))
                resp.raise_for_status()
                with open(target, "wb") as f:
                    f.write(resp.content)
                logger.info(f"Cover updated (downloaded) for Gallery {latest_id}: {target}")
                _link_cover(target)
            except Exception as e:
                logger.warning(f"Failed to download missing cover for Gallery {latest_id}: {e}")
    except Exception as e:
        logger.debug(f"Failed to restore cover file in {creator_folder}: {e}")

SUWAYOMI_TARBALL_URL = "https://github.com/Suwayomi/Suwayomi-Server/releases/download/v2.1.1867/Suwayomi-Server-v2.1.1867-linux-x64.tar.gz"
TARBALL_FILENAME = SUWAYOMI_TARBALL_URL.split("/")[-1]

def install_extension():
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH
    
    orchestrator.refresh_globals()

    if not DEDICATED_DOWNLOAD_PATH:
        DEDICATED_DOWNLOAD_PATH = DEFAULT_EXTENSION_DOWNLOAD_PATH

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would install extension and create paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}")
        return

    try:
        os.makedirs(EXTENSION_INSTALL_PATH, exist_ok=True)
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)

        tarball_path = os.path.join("/tmp", TARBALL_FILENAME)

        if not os.path.exists(tarball_path):
            logger.info(f"Downloading Suwayomi-Server tarball from {SUWAYOMI_TARBALL_URL}...")
            r = requests.get(SUWAYOMI_TARBALL_URL, stream=True)
            with open(tarball_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        with tarfile.open(tarball_path, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                path_parts = member.name.split("/", 1)
                member.name = path_parts[1] if len(path_parts) > 1 else ""
            tar.extractall(path=EXTENSION_INSTALL_PATH, members=members)
        logger.info(f"Suwayomi-Server extracted to {EXTENSION_INSTALL_PATH}")

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
        logger.info("Suwayomi systemd service created and started")
        log(f"\nSuwayomi Web: http://$IP:4567/", "debug")
        log("Suwayomi GraphQL: http://$IP:4567/api/graphql", "debug")
        
        pre_run_hook()
        logger.info(f"{EXTENSION_REFERRER}: Installed.")
    
    except Exception as e:
        logger.error(f"{EXTENSION_REFERRER}: Failed to install: {e}")

def uninstall_extension():
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH
    
    orchestrator.refresh_globals()

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would uninstall extension and remove paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}")
        return

    try:
        subprocess.run(["systemctl", "stop", "suwayomi-server"], check=False)
        subprocess.run(["systemctl", "disable", "suwayomi-server"], check=False)
        service_file = "/etc/systemd/system/suwayomi-server.service"
        if os.path.exists(service_file):
            os.remove(service_file)
        subprocess.run(["systemctl", "daemon-reload"], check=False)

        if os.path.exists(EXTENSION_INSTALL_PATH):
            shutil.rmtree(EXTENSION_INSTALL_PATH, ignore_errors=True)
        if os.path.exists(DEDICATED_DOWNLOAD_PATH):
            shutil.rmtree(DEDICATED_DOWNLOAD_PATH, ignore_errors=True)
        logger.info(f"Extension {EXTENSION_NAME}: Uninstalled successfully")

    except Exception as e:
        logger.error(f"Extension {EXTENSION_NAME}: Failed to uninstall: {e}")


####################################################################################################################
# CUSTOM HOOKS (thread-safe)
####################################################################################################################

# Hook for testing functionality. Use active_extension.test_hook(ARGS) in downloader.
def test_hook():
    """
    Update environment variables used by this module.
    Call this function at the start of any function that uses any these variables to ensure they are up to date.
    """
    
    orchestrator.refresh_globals()
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Test Hook Called.", "debug")

# Remove empty folders inside DEDICATED_DOWNLOAD_PATH without deleting the root folder itself.
def clean_directories(RemoveEmptyArtistFolder: bool = True):
    global DEDICATED_DOWNLOAD_PATH
    
    orchestrator.refresh_globals()
    
    log_clarification("debug")

    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        log("No valid DEDICATED_DOWNLOAD_PATH set, skipping cleanup.", "debug")
        return

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Would remove empty directories under {DEDICATED_DOWNLOAD_PATH}")
        return

    broken_symlinks_removed = 0
    
    # Combined single walk for both directory cleanup and symlink removal
    for dirpath, dirnames, filenames in os.walk(DEDICATED_DOWNLOAD_PATH, topdown=False):
        if dirpath == DEDICATED_DOWNLOAD_PATH:
            continue
        
        # Remove empty directories
        try:
            if RemoveEmptyArtistFolder:
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
        if os.path.dirname(dirpath) == DEDICATED_DOWNLOAD_PATH:
            _ensure_cover_file(dirpath)
    
    logger.info(f"Removed empty directories.")
    log_clarification()
    
    if broken_symlinks_removed > 0:
        logger.info(f"Fixed {broken_symlinks_removed} broken symlink(s).")
    
    logger.info("Scan complete.")

############################################

def graphql_request(request: str, variables: dict = None, gql_debugging: bool = False):
    """
    Framework for making requests to GraphQL
    """
    
    orchestrator.refresh_globals()
    
    if gql_debugging:
        debug = gql_debugging
    else:
        debug = orchestrator.debug
    
    # Forcefully enable or disable detailed debug logs
    #debug = True
    
    headers = {"Content-Type": "application/json"}
    payload = {"query": request, "variables": variables or {}}

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] GraphQL: Would make request: {request} with variables {variables}")
        return None

    try:
        # Serialise payload once, reuse for both debug logging and request
        payload_json = json.dumps(payload)
        
        if debug == True:
            log_clarification("debug")
            log(f"GraphQL Request Payload:\n{json.dumps(payload, indent=2)}", "debug") # NOTE: DEBUGGING
        
        response = requests.post(GRAPHQL_URL, headers=headers, data=payload_json)
        response.raise_for_status()
        result = response.json()
        
        if debug == True:
            log(f"GraphQL Response:\n{json.dumps(result, indent=2)}", "debug") # NOTE: DEBUGGING
        return result
    
    except requests.RequestException as e:
        logger.error(f"GraphQL: Request failed: {e}")
        return None
    
    except ValueError as e:
        logger.error(f"GraphQL: Failed to decode JSON response: {e}")
        logger.error(f"Raw response: {response.text if response else 'No response'}")
        return None

def new_graphql_request(request: str, variables: dict = None, gql_debugging: bool = False):
    """
    New framework for making requests to GraphQL. Allows for authentication with the server.
    """
    
    global graphql_session, AUTH_USERNAME, AUTH_PASSWORD
    
    orchestrator.refresh_globals()
    
    if gql_debugging:
        debug = gql_debugging
    else:
        debug = orchestrator.debug
    
    # Forcefully enable or disable detailed debug logs
    #debug = True
    
    headers = {"Content-Type": "application/json"}
    payload = {"query": request, "variables": variables or {}}

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] GraphQL: Would make request: {request} with variables {variables}")
        return None

    try:
        if graphql_session is None:
            # Initialise session and login once
            graphql_session = requests.Session()
            login_payload = {
                "username": AUTH_USERNAME,
                "password": AUTH_PASSWORD,
            }
            
            login_url = GRAPHQL_URL.replace("/graphql", "/auth/login")
            
            resp = graphql_session.post(login_url, json=login_payload, headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            if resp.status_code != 200:
                logger.error(f"GraphQL: Login failed with status {resp.status_code}: {resp.text}")
                return None
            
            logger.info("GraphQL: Successfully logged in and obtained session cookie.")

        if debug == True:
            log_clarification("debug")
            log(f"GraphQL Request Payload: {json.dumps(payload, indent=2)}", "debug") # NOTE: DEBUGGING
        
        # Use json parameter (requests serialises internally, no manual serialisation needed)
        response = graphql_session.post(
            GRAPHQL_URL,
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        result = response.json()
        
        if debug == True:
            log(f"GraphQL Request Response: {json.dumps(result, indent=2)}", "debug") # NOTE: DEBUGGING
        
        return result

    except requests.RequestException as e:
        logger.error(f"GraphQL: Request failed: {e}")
        return None
    
    except ValueError as e:
        logger.error(f"GraphQL: Failed to decode JSON response: {e}")
        logger.error(f"Raw response: {response.text if response else 'No response'}")
        return None

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
    result = graphql_request(query)
    if not result:
        log_clarification()
        logger.error("GraphQL: Failed to fetch sources")
        return LOCAL_SOURCE_ID
    
    for node in result["data"]["sources"]["nodes"]:
        #log_clarification("debug")
        #log(f"GraphQL: Checking source node {node}", "debug")
        if node["name"].lower() == "local source":
            LOCAL_SOURCE_ID = str(node["id"])  # must be a string in queries
            #log(f"GraphQL: Local source ID = {LOCAL_SOURCE_ID}", "debug")
            return LOCAL_SOURCE_ID

    logger.error("GraphQL: Could not find 'Local source' in sources")
    LOCAL_SOURCE_ID = None

def ensure_category(category_name=None):
    wait = SUWAYOMI_POPULATION_TIME * 4
    log_clarification("debug")
    log(f"Waiting {wait}s for Suwayomi to populate data...")
    
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
    result = graphql_request(query, variables=query_variables)   
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
    result = graphql_request(mutation, variables=query_variables)
    log(f"GraphQL: Create category result: {result}", "debug")
    CATEGORY_ID = int(result["data"]["createCategory"]["category"]["id"])
    
    time.sleep(wait)
    return CATEGORY_ID

# ----------------------------
# Bulk Update Functions
# ----------------------------

def update_suwayomi(operation: str, category_id, update_suwayomi_debugging: bool = False):
    """
    Turn debug on for the GraphQL queries and the logs will get VERY long.
    """

    LOCAL_SOURCE_ID = get_local_source_id()  # Fetch again in case

    if operation == "category browse":
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
        graphql_request(query, variables=query_variables, gql_debugging=update_suwayomi_debugging)

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
        graphql_request(latest_query, variables=query_variables, gql_debugging=update_suwayomi_debugging)

    if operation == "category":
        # Mutation to trigger the update once
        query = """
        mutation TriggerCategoryUpdate($categoryId: Int!) {
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
        graphql_request(query, variables=query_variables, gql_debugging=update_suwayomi_debugging)

    if operation == "status":
        # Query to check the status repeatedly
        query = """
        query CheckGlobalUpdateStatus {
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
        result = graphql_request(query, gql_debugging=update_suwayomi_debugging)
        return result

def populate_suwayomi(category_id: int, attempt: int, update_library: bool = True):
    log_clarification()
    log(f"Suwayomi Update Triggered. Waiting for completion...")
    
    wait_time = SUWAYOMI_POPULATION_TIME

    try:
        # Load category data
        update_suwayomi("category browse", category_id, update_suwayomi_debugging=False)
        
        # Trigger the global update
        if update_library:
            update_suwayomi("category", category_id, update_suwayomi_debugging=False)

        # Initialise progress bar
        pbar = tqdm(total=0, desc=f"Suwayomi Update (Attempt {attempt}/{orchestrator.max_retries})", unit="job", dynamic_ncols=True)
        last_finished = 0
        total_jobs = None

        while True:
            result = update_suwayomi("status", category_id, update_suwayomi_debugging=True) # NOTE: DEBUGGING
            
            # Wait BEFORE checking status to avoid exiting early.
            time.sleep(wait_time)

            if not result:
                logger.warning("Failed to fetch update status, retrying...")
                time.sleep(wait_time)
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
                    logger.warning("Unexpected jobsInfo format, retrying...")
                    time.sleep(wait_time)
                    continue
            
            except (KeyError, TypeError):
                logger.warning("Unexpected status response format, retrying...")
                time.sleep(wait_time)
                continue

            if not is_running:
                log("GraphQL: Suwayomi Update has finished before GraphQL could check. Exiting Update Loop.", "info")
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
                logger.warning(f"Suwayomi library update for Category ID {category_id} completed.")
                wait = max(wait_time * 5, (1 + total_jobs / 50))
                logger.warning(f"Waiting {wait}s for Suwayomi to reflect all changes...")
                time.sleep(wait)
                break
                
            time.sleep(max(wait_time, (1 + total / 1000))) # Adaptive polling

        pbar.close()

    except Exception as e:
        logger.warning(f"Failed during Suwayomi update for category {category_id}: {e}")

def add_mangas_to_suwayomi(ids: list[int], category_id: int):
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
    result = graphql_request(mutation, variables={"ids": ids}) 
    #log(f"GraphQL: updateMangas result: {result}", "debug")
    logger.debug(f"GraphQL: Updated {len(ids)} mangas as 'In Library'.")
    
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
    result = graphql_request(mutation, variables={"ids": ids, "categoryId": category_id})
    #log(f"GraphQL: updateMangasCategories result: {result}", "debug")
    logger.debug(f"GraphQL: Added {len(ids)} mangas to category {category_id}.")
    
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
    result = graphql_request(query, variables={"title": creator_name})
    if not result:
        return []
    return result.get("data", {}).get("mangas", {}).get("nodes", [])

def remove_from_deferred(creator_name: str, metadata: dict = None):
    """
    Remove a creator from the deferred_creators list in metadata.
    Accept optional metadata parameter to avoid redundant file I/O.
    """
    
    if metadata is None:
        metadata = load_creators_metadata()
    
    deferred_creators = set(metadata.get("deferred_creators", []))

    if creator_name in deferred_creators:
        deferred_creators.discard(creator_name)
        logger.info(f"Removed '{creator_name}' from deferred creators.")
        metadata["deferred_creators"] = sorted(deferred_creators)
    
    return metadata
    
# ------------------------------------------------------------
# Update creator mangas and ensure they are added to Suwayomi
# ------------------------------------------------------------
def update_creator_manga(meta):
    """
    Update a creator's details.json and genre metadata based on a downloaded gallery.
    Also attempt to immediately add the creator's manga to Suwayomi using its ID.
    """
    
    orchestrator.refresh_globals()
        
    log_clarification("debug")
    
    if orchestrator.dry_run:
        log(f"[DRY RUN] Would process gallery {meta.get('id')}", "debug")
        return

    gallery_meta = return_gallery_metas(meta)
    creators = [make_filesystem_safe(c) for c in gallery_meta.get("creator", [])]
    if not creators:
        return

    gallery_title = gallery_meta["title"]
    current_gallery_id = _extract_gallery_id(gallery_title) or int(meta.get("id", 0))
    gallery_tags = meta.get("tags", [])
    gallery_genres = [
        tag["name"] for tag in gallery_tags
        if "name" in tag and tag.get("type") not in ["artist", "group", "language", "category"]
    ]

    # Load all metadata at once
    metadata = load_creators_metadata()
    collected_ids = set(metadata.get("collected_manga_ids", []))
    deferred_creators = set(metadata.get("deferred_creators", []))
    if "creators" not in metadata:
        metadata["creators"] = {}

    for creator_name in creators:
        # --- Try to retrieve manga metadata from Suwayomi ---
        nodes = fetch_creators_suwayomi_metadata(creator_name)
        suwayomi_id = int(nodes[0]["id"]) if nodes else None

        if suwayomi_id is not None:
            collected_ids.add(suwayomi_id)
            try:
                add_mangas_to_suwayomi([suwayomi_id], CATEGORY_ID)
                collected_ids.discard(suwayomi_id)

                # Pass metadata to avoid redundant I/O
                metadata = remove_from_deferred(creator_name, metadata)

            except Exception as e:
                logger.warning(f"Failed to update manga {suwayomi_id} for {creator_name}: {e}")
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
        
        # Single sort with reuse for MAX_GENRES_PARSED
        sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)
        entry["genre_counts"] = dict(sorted_genres[:MAX_GENRES_PARSED])

        # --- Update details.json ---
        creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
        os.makedirs(creator_folder, exist_ok=True)
        details_file = os.path.join(creator_folder, "details.json")

        latest_id, latest_name, _ = _get_latest_gallery_entry(creator_folder)
        if latest_id is None or latest_name is None:
            description = f"Latest Doujin: {gallery_title}"
        else:
            description = f"Latest Doujin: {latest_name}"

        # Reuse sorted_genres for MAX_GENRES_STORED slice
        details = {
            "title": creator_name,
            "author": creator_name,
            "artist": creator_name,
            "description": description,
            "genre": [g for g, _ in sorted_genres[:MAX_GENRES_STORED]],
            "status": "1",
            "_status values": ["0 = Unknown", "1 = Ongoing", "2 = Completed", "3 = Licensed"]
        }

        with open(details_file, "w", encoding="utf-8") as f:
            json.dump(details, f, ensure_ascii=False, indent=2)

    # --- Save all metadata at once ---
    metadata["collected_manga_ids"] = sorted(collected_ids)
    metadata["deferred_creators"] = sorted(deferred_creators)
    save_creators_metadata(metadata)

def process_deferred_creators(populate: bool = True):
    """
    Adds deferred creators to library and updates their category.
    Ensures only existing local creator folders are added.
    Adds all existing local mangas to library + category if they exist on disk.
    Cleans up creators_metadata.json so successful creators are removed from deferred creators.
    """
    
    orchestrator.refresh_globals()
    
    process_creators_attempt = 1
    
    still_deferred = set()
    
    while process_creators_attempt <= orchestrator.max_retries:
        log_clarification()
        logger.info(f"Processing creators (attempt {process_creators_attempt}/{orchestrator.max_retries})...")
        
        populate_suwayomi(CATEGORY_ID, process_creators_attempt, update_library=populate) # Update Suwayomi category first

        # ----------------------------
        # Add mangas not yet in library
        # ----------------------------
        log_clarification()
        logger.info("GraphQL: Fetching mangas not yet in library...")

        query = """
        query FetchMangasNotInLibrary($sourceId: LongString!) {
        mangas(filter: { sourceId: { equalTo: $sourceId }, inLibrary: { equalTo: false } }) {
            nodes { id title }
        }
        }
        """
        result = graphql_request(query, variables={"sourceId": LOCAL_SOURCE_ID})
        nodes = result.get("data", {}).get("mangas", {}).get("nodes", []) if result else []

        new_ids = []

        if not nodes:
            logger.info("GraphQL: No mangas found outside the library.")
        else:
            for node in nodes:
                title = node["title"]
                expected_path = os.path.join(DEDICATED_DOWNLOAD_PATH, title)
                if os.path.exists(expected_path):
                    new_ids.append(int(node["id"]))
                    # remove from deferred if found
                    remove_from_deferred(title)

            if new_ids:
                logger.info(f"GraphQL: Adding {len(new_ids)} mangas to library and category.")
                add_mangas_to_suwayomi(new_ids, CATEGORY_ID)

        # ----------------------------
        # Process deferred creators
        # ----------------------------
        log_clarification()

        # Load metadata directly instead of using wrapper function
        metadata = load_creators_metadata()
        deferred_creators = set(metadata.get("deferred_creators", []))

        if not deferred_creators:
            logger.info("GraphQL: No deferred creators to process.")
            return

        logger.info(f"GraphQL: Processing {len(deferred_creators)} deferred creators...")

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
                logger.warning(f"Skipping deferred creator '{creator_name}': folder does not exist.")
                still_deferred.add(creator_name)
                continue

            result = graphql_request(query, variables={"creatorName": creator_name})
            mangas = result.get("data", {}).get("mangas", {}).get("nodes", []) if result else []

            if not mangas:
                logger.warning(f"Creator manga '{creator_name}' not found in Suwayomi local source.")
                still_deferred.add(creator_name)
                continue

            manga_info = mangas[0]  # title is unique per creator
            if manga_info.get("inLibrary") and CATEGORY_ID in [c["id"] for c in manga_info.get("categories", {}).get("nodes", [])]:
                logger.info(f"Creator manga '{creator_name}' already in library and category. Removing from deferred list.")
                remove_from_deferred(creator_name)
                continue

            new_ids.add(int(manga_info["id"]))
            processed_creators.add(creator_name)
            logger.info(f"Queued manga ID {manga_info['id']} for '{creator_name}'.")

        if new_ids:
            add_mangas_to_suwayomi(list(new_ids), CATEGORY_ID)
            for creator_name in processed_creators:
                remove_from_deferred(creator_name)
        
        # If no creators remain, we're done early
        if not still_deferred:
            logger.info("Successfully processed all deferred creators.")
            return
        
        # Otherwise, try again
        process_creators_attempt += 1

    # After max retries, keep creators still deferred
    # Save metadata directly instead of using wrapper function
    metadata = load_creators_metadata()
    metadata["deferred_creators"] = sorted(still_deferred)
    save_creators_metadata(metadata)
    logger.warning("Unable to process Creators: " + ", ".join(sorted(still_deferred)) if still_deferred else "Sucessfully processed all creators.")

####################################################################################################################
# CORE HOOKS (thread-safe)
####################################################################################################################

# Hook for downloading images. Use active_extension.download_images_hook(ARGS) in downloader.
def download_images_hook(gallery, page, urls, path, downloader_session, pbar=None, creator=None):
    """
    Downloads an image from one of the provided URLs to the given path.
    Tries mirrors in order until one succeeds, with retries per mirror.
    Updates tqdm progress bar with current creator.
    """

    orchestrator.refresh_globals()

    if not urls:
        logger.warning(f"Gallery {gallery}: Page {page}: No URLs, skipping")
        if pbar and creator:
            pbar.set_postfix_str(f"Skipped Creator: {creator}")
        return False

    if os.path.exists(path):
        log(f"Already exists, skipping: {path}", "debug")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] Gallery {gallery}: Would download {urls[0]} -> {path}")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if not isinstance(downloader_session, requests.Session):
        downloader_session = requests.Session()

    def try_download(session, mirrors, retries, tor_rotate=False):
        """Try downloading with a given session and retry count."""
        for url in mirrors:
            for attempt in range(1, retries + 1):
                try:
                    r = session.get(url, timeout=(60, 60), stream=True)
                    if r.status_code == 429:
                        wait = 2 ** attempt
                        logger.warning(f"429 rate limit hit for {url}, waiting {wait}s")
                        time.sleep(wait)
                        continue
                    r.raise_for_status()

                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

                    log(f"Downloaded Gallery {gallery}: Page {page} -> {path}", "debug")
                    if pbar and creator:
                        pbar.set_postfix_str(f"Creator: {creator}")
                    return True

                except Exception as e:
                    wait = dynamic_sleep("image", attempt=attempt)
                    log_clarification()
                    logger.warning(
                        f"Gallery {gallery}: Page {page}: Mirror {url}, attempt {attempt} failed: {e}, retrying in {wait:.2f}s"
                    )
                    time.sleep(wait)

            logger.warning(
                f"Gallery {gallery}: Page {page}: Mirror {url} failed after {retries} attempts, trying next mirror"
            )
        return False

    # First attempt: normal retries
    success = try_download(downloader_session, urls, orchestrator.max_retries)

    # If still failed, rebuild Tor session once and retry
    if not success and orchestrator.use_tor:
        logger.warning(
            f"Gallery {gallery}: Page {page}: All retries failed, rotating Tor node and retrying once more..."
        )
        downloader_session = get_session(referrer=f"{EXTENSION_NAME}", status="rebuild")
        success = try_download(downloader_session, urls, 1, tor_rotate=True)

    if not success:
        log_clarification()
        logger.error(
            f"Gallery {gallery}: Page {page}: All mirrors failed after Tor rotate too: {urls}"
        )
        if pbar and creator:
            pbar.set_postfix_str(f"Failed Creator: {creator}")

    return success

# Hook for pre-batch functionality. Use active_extension.pre_batch_hook(ARGS) in downloader.
def pre_batch_hook(gallery_list):
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Pre-batch Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Pre-batch Hook Called.", "debug")
    
    global LOCAL_SOURCE_ID, CATEGORY_ID
    
    # Initialise globals
    LOCAL_SOURCE_ID = get_local_source_id()
    CATEGORY_ID = ensure_category(SUWAYOMI_CATEGORY_NAME)

    return gallery_list

# Hook for functionality before a gallery download. Use active_extension.pre_gallery_download_hook(ARGS) in downloader.
def pre_gallery_download_hook(gallery_id):
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Pre-download Hook Inactive.")
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Pre-download Hook Called: Gallery: {gallery_id}", "debug")

# Hook for functionality during a gallery download. Use active_extension.during_gallery_download_hook(ARGS) in downloader.
def during_gallery_download_hook(gallery_id):
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: During-download Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: During-download Hook Called: Gallery: {gallery_id}", "debug")

# Hook for functionality after a completed gallery download. Use active_extension.after_completed_gallery_download_hook(ARGS) in downloader.
def after_completed_gallery_download_hook(meta: dict, gallery_id):
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Post-download Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Post-download Hook Called: Gallery: {meta['id']}: Downloaded.", "debug")

    # Thread-safe append
    with _gallery_meta_lock:
        _collected_gallery_metas.append(meta)
    
    # Update creator's popular genres
    update_creator_manga(meta)
    
    # Extract cover and delete original gallery folder after archiving
    try:
        gallery_format = str(orchestrator.gallery_format).lower() # Check if gallery format is valid, if not, treat as "directory" for safety
        valid_formats = {"directory", "zip", "cbz"}
        if gallery_format not in valid_formats:
            logger.warning(
                f"{EXTENSION_REFERRER}: Unknown GALLERY_FORMAT '{orchestrator.gallery_format}', "
                "treating as 'directory' for safety."
            )
            gallery_format = "directory"

        gallery_meta = return_gallery_metas(meta)
        creators = [make_filesystem_safe(c) for c in gallery_meta.get("creator", [])]
        cover_source = None
        cover_gallery_name = None
        cover_ext = None
        gallery_paths = {}
        cover_gallery_id = None
        
        temp_root = "/tmp/manga-scraper/archive_temp"
        for creator_name in creators:
            creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
            temp_creator_folder = os.path.join(temp_root, creator_name)

            search_folders = []
            if os.path.isdir(creator_folder):
                search_folders.append(creator_folder)
            if os.path.isdir(temp_creator_folder):
                search_folders.append(temp_creator_folder)
            if not search_folders:
                continue

            gallery_prefix = f"({gallery_id})"
            for search_folder in search_folders:
                gallery_items = [
                    f for f in os.listdir(search_folder)
                    if os.path.isdir(os.path.join(search_folder, f)) and f.startswith(gallery_prefix)
                ]
                if not gallery_items:
                    continue
                gallery_items.sort()
                gallery_path = os.path.join(search_folder, gallery_items[0])
                if os.path.isdir(gallery_path):
                    gallery_paths[creator_name] = gallery_path

                    if cover_source is None:
                        candidates = [f for f in os.listdir(gallery_path) if f.startswith("1.")]
                        if candidates:
                            page1_file = os.path.join(gallery_path, candidates[0])
                            _, ext = os.path.splitext(page1_file)
                            cover_source = page1_file
                            cover_gallery_name = gallery_items[0]
                            cover_ext = ext
                            cover_gallery_id = _extract_gallery_id(cover_gallery_name)
                else:
                    logger.debug(f"Gallery {gallery_items[0]} is already archived or not a directory, skipping")

        for creator_name in creators:
            creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
            if not os.path.isdir(creator_folder):
                continue

            # Extract cover from the downloaded gallery and store in hidden covers subfolder
            if cover_source and cover_gallery_name and cover_ext:
                covers_folder = os.path.join(creator_folder, ".covers")
                try:
                    os.makedirs(covers_folder, exist_ok=True)
                    latest_cover_id = _get_latest_cover_id(covers_folder)
                    if cover_gallery_id is not None and latest_cover_id is not None:
                        if cover_gallery_id <= latest_cover_id:
                            continue
                            gallery_path = gallery_paths.get(creator_name)
                            if gallery_format == "directory" or not gallery_path:
                                if gallery_format == "directory" and gallery_path:
                                    logger.debug(
                                        f"Gallery format is 'directory'; keeping original gallery folder: {gallery_path}"
                                    )
                                continue

                    cover_in_subfolder = os.path.join(covers_folder, f"{cover_gallery_name}{cover_ext}")
                    shutil.copy2(cover_source, cover_in_subfolder)
                    logger.debug(f"Extracted cover for {creator_name}: {cover_in_subfolder}")

                    # Remove any existing cover files (regardless of extension)
                    for f in os.listdir(creator_folder):
                        if f.startswith("cover") and f != "covers" and f != ".covers":
                            try:
                                os.unlink(os.path.join(creator_folder, f))
                            except Exception as e:
                                logger.debug(f"Could not remove old cover file {f}: {e}")

                    # Symlink cover into creator root
                    cover_link = os.path.join(creator_folder, f"cover{cover_ext}")
                    os.symlink(cover_in_subfolder, cover_link)
                    logger.debug(f"Updated cover symlink for {creator_name}: {cover_link} -> {cover_in_subfolder}")
                except Exception as e:
                    logger.debug(f"Could not extract cover for Gallery {gallery_id}: {e}")

            gallery_path = gallery_paths.get(creator_name)
            if gallery_format == "directory" or not gallery_path:
                if gallery_format == "directory" and gallery_path:
                    logger.debug(
                        f"Gallery format is 'directory'; keeping original gallery folder: {gallery_path}"
                    )
                continue

            archive_ext = ".cbz" if gallery_format == "cbz" else ".zip"
            gallery_name = os.path.basename(gallery_path)
            expected_archive = os.path.join(creator_folder, f"{gallery_name}{archive_ext}")
            if not os.path.exists(expected_archive):
                max_checks = max(1, int(ARCHIVE_WAIT_SECONDS / ARCHIVE_POLL_INTERVAL))
                for _ in range(max_checks):
                    time.sleep(ARCHIVE_POLL_INTERVAL)
                    if os.path.exists(expected_archive):
                        break
                if not os.path.exists(expected_archive):
                    logger.warning(
                        f"Archive not found for Gallery {gallery_id} after {ARCHIVE_WAIT_SECONDS}s: "
                        f"expected {expected_archive}; leaving folder undeleted"
                    )
                    logger.info(
                        f"Leaving original folder in place: {gallery_path}"
                    )
                    continue

            # Delete original gallery folder
            try:
                shutil.rmtree(gallery_path)
                logger.debug(f"Deleted original gallery folder: {gallery_path}")
            except Exception as e:
                logger.error(f"Failed to delete gallery folder {gallery_path}: {e}")
    
    except Exception as e:
        logger.error(f"Failed in post-download processing for Gallery {gallery_id}: {e}")

# Hook for cleaning after downloads
def cleanup_hook():
    clean_directories(True) # Clean up the download folder / directories

def repair_covers_hook():
    orchestrator.refresh_globals()
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Repair covers hook inactive.")
        return
    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        return
    repaired = 0
    for name in os.listdir(DEDICATED_DOWNLOAD_PATH):
        creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, name)
        if os.path.isdir(creator_folder):
            before = any(
                f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
                for f in os.listdir(creator_folder)
            )
            _ensure_cover_file(creator_folder)
            after = any(
                f.startswith("cover") and os.path.isfile(os.path.join(creator_folder, f))
                for f in os.listdir(creator_folder)
            )
            if not before and after:
                repaired += 1
    logger.debug(f"{EXTENSION_REFERRER}: Cover update pass complete. Restored {repaired} cover(s).")

# Hook for post-batch functionality. Use active_extension.post_batch_hook(ARGS) in downloader.
def post_batch_hook(current_batch_number: int, total_batch_numbers: int):
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Post-batch Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Post-batch Hook Called.", "debug")

    def _should_run_post_batch():
        # --- If Total Batches higher than MAX_X_BATCHES, do not run ---
        if total_batch_numbers > MAX_X_BATCHES:
            return False
        
        # --- Calculate when to trigger cleanup ---
        interval = max(1, round(RUNS_PER_X_BATCHES * total_batch_numbers / EVERY_X_BATCHES))
        is_last_batch = current_batch_number == total_batch_numbers
        
        # --- Only run if conditions are met ---
        return (
            not orchestrator.skip_post_batch # If NOT skipping post batch
            and not orchestrator.archiving # If NOT in archival mode
            and not is_last_batch # If not last batch
            and (current_batch_number % interval == 0) # If current batch hits interval
        )
    
    if _should_run_post_batch():
        cleanup_hook() # Call the cleanup hook
        
        # Add all creators to Suwayomi
        process_deferred_creators(populate=False)

# Hook for post-run functionality. Use active_extension.post_run_hook(ARGS) in downloader.
def post_run_hook():
    orchestrator.refresh_globals()
    
    if orchestrator.dry_run:
        logger.info(f"[DRY RUN] {EXTENSION_REFERRER}: Post-run Hook Inactive.")
        return
    
    log_clarification("debug")
    log(f"{EXTENSION_REFERRER}: Post-run Hook Called.", "debug")
    
    if orchestrator.skip_post_run:
        log_clarification("debug")
        log(f"{EXTENSION_REFERRER}: Post-run Hook Skipped.", "debug")
    else:
        repair_covers_hook()
        cleanup_hook() # Call the cleanup hook
        
        # Add all creators to Suwayomi
        process_deferred_creators(populate=True)
                
        # Update Suwayomi category at end
        log_clarification()
        log("Please update the library manually and / or run a small download to reflect any changes.")