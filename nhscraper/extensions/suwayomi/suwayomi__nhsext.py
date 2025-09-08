#!/usr/bin/env python3
# extensions/suwayomi/suwayomi.py
# ENSURE THAT THIS FILE IS THE *EXACT SAME* IN BOTH THE NHENTAI-SCRAPER REPO AND THE NHENTAI-SCRAPER-EXTENSIONS REPO.
# PLEASE UPDATE THIS FILE IN THE NHENTAI-SCRAPER REPO FIRST, THEN COPY IT OVER TO THE NHENTAI-SCRAPER-EXTENSIONS REPO.

import os, time, json, requests, threading, subprocess, shutil, tarfile

from nhscraper.core.config import *
from nhscraper.core.api import get_meta_tags, safe_name, clean_title

####################################################################################################################
# Global variables
####################################################################################################################
EXTENSION_NAME = "suwayomi" # Must be fully lowercase
EXTENSION_INSTALL_PATH = "/opt/suwayomi-server/" # Use this if extension installs external programs (like Suwayomi-Server)
REQUESTED_DOWNLOAD_PATH = "/opt/suwayomi-server/local/"
#DEDICATED_DOWNLOAD_PATH = None # In case it tweaks out.

LOCAL_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "local_manifest.json"
)

with open(os.path.abspath(LOCAL_MANIFEST_PATH), "r", encoding="utf-8") as f:
    manifest = json.load(f)

for ext in manifest.get("extensions", []):
    if ext.get("name") == EXTENSION_NAME:
        DEDICATED_DOWNLOAD_PATH = ext.get("image_download_path")
        break

if DEDICATED_DOWNLOAD_PATH is None:
    DEDICATED_DOWNLOAD_PATH = REQUESTED_DOWNLOAD_PATH

SUBFOLDER_STRUCTURE = ["creator", "title"]

dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)

############################################

GRAPHQL_URL = "http://127.0.0.1:4567/api/graphql"
LOCAL_SOURCE_ID = "0"  # Local source is usually "0"
SUWAYOMI_CATEGORY_NAME = "NHentai Scraper"

# Max number of genres stored in a creator's details.json
MAX_GENRES_STORED = 15
# Max number of genres parsed from a gallery and stored in a creator's "most_popular_genres.json" field.
MAX_GENRES_PARSED = 100

############################################

# Thread locks for file operations
_file_lock = threading.Lock()

_collected_gallery_metas = []
_gallery_meta_lock = threading.Lock()

_collected_manga_ids = set()
_manga_ids_lock = threading.Lock()

_deferred_creators = set()
_deferred_lock = threading.Lock()

####################################################################################################################
# CORE
####################################################################################################################
def update_extension_download_path():
    log_clarification()
    if dry_run:
        logger.info(f"[DRY-RUN] Would ensure download path exists: {DEDICATED_DOWNLOAD_PATH}")
        return
    try:
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        logger.info(f"Extension: {EXTENSION_NAME}: Download path ready at '{DEDICATED_DOWNLOAD_PATH}'.")
    except Exception as e:
        logger.error(f"Extension: {EXTENSION_NAME}: Failed to create download path '{DEDICATED_DOWNLOAD_PATH}': {e}")
    logger.info(f"Extension: {EXTENSION_NAME}: Ready.")
    log(f"Extension: {EXTENSION_NAME}: Debugging started.", "debug")
    update_env("EXTENSION_DOWNLOAD_PATH", DEDICATED_DOWNLOAD_PATH)

def return_gallery_metas(meta):
    artists = get_meta_tags(f"{EXTENSION_NAME}: Return_gallery_metas", meta, "artist")
    groups = get_meta_tags(f"{EXTENSION_NAME}: Return_gallery_metas", meta, "group")
    creators = artists or groups or ["Unknown Creator"]
    
    title = clean_title(meta)
    id = str(meta.get("id", "Unknown ID"))
    full_title = f"({id}) {title}"
    
    language = get_meta_tags(f"{EXTENSION_NAME}: Return_gallery_metas", meta, "language") or ["Unknown Language"]
    
    log_clarification()
    return {
        "creator": creators,
        "title": full_title,
        "short_title": title,
        "id": id,
        "language": language,
    }

SUWAYOMI_TARBALL_URL = "https://github.com/Suwayomi/Suwayomi-Server/releases/download/v2.1.1867/Suwayomi-Server-v2.1.1867-linux-x64.tar.gz"
TARBALL_FILENAME = SUWAYOMI_TARBALL_URL.split("/")[-1]

def install_extension():
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH

    if not DEDICATED_DOWNLOAD_PATH:
        DEDICATED_DOWNLOAD_PATH = REQUESTED_DOWNLOAD_PATH

    if dry_run:
        logger.info(f"[DRY-RUN] Would install extension and create paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}")
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
        
        update_extension_download_path()
        logger.info(f"Extension: {EXTENSION_NAME}: Installed.")
    
    except Exception as e:
        logger.error(f"Extension: {EXTENSION_NAME}: Failed to install: {e}")

def uninstall_extension():
    global DEDICATED_DOWNLOAD_PATH, EXTENSION_INSTALL_PATH

    if dry_run:
        logger.info(f"[DRY-RUN] Would uninstall extension and remove paths: {EXTENSION_INSTALL_PATH}, {DEDICATED_DOWNLOAD_PATH}")
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
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Test hook called.", "debug")
    log_clarification()

# Remove empty folders inside DEDICATED_DOWNLOAD_PATH without deleting the root folder itself.
def remove_empty_directories(RemoveEmptyArtistFolder: bool = True):
    global DEDICATED_DOWNLOAD_PATH

    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        log("No valid DEDICATED_DOWNLOAD_PATH set, skipping cleanup.", "debug")
        return

    if dry_run:
        logger.info(f"[DRY-RUN] Would remove empty directories under {DEDICATED_DOWNLOAD_PATH}")
        return

    if RemoveEmptyArtistFolder:
        for dirpath, dirnames, filenames in os.walk(DEDICATED_DOWNLOAD_PATH, topdown=False):
            if dirpath == DEDICATED_DOWNLOAD_PATH:
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
            except Exception as e:
                logger.warning(f"Could not remove empty directory: {dirpath}: {e}")
    else:
        for dirpath, dirnames, filenames in os.walk(DEDICATED_DOWNLOAD_PATH, topdown=False):
            if dirpath == DEDICATED_DOWNLOAD_PATH:
                continue
            if not dirnames and not filenames:
                try:
                    os.rmdir(dirpath)
                    logger.info(f"Removed empty directory: {dirpath}")
                except Exception as e:
                    logger.warning(f"Could not remove empty directory: {dirpath}: {e}")

    logger.info(f"Removed empty directories.")
    DEDICATED_DOWNLOAD_PATH = ""
    update_env("EXTENSION_DOWNLOAD_PATH", DEDICATED_DOWNLOAD_PATH)

############################################

def graphql_request(query: str, variables: dict = None):
    headers = {"Content-Type": "application/json"}
    payload = {"query": query, "variables": variables or {}}

    if dry_run:
        logger.info(f"[DRY-RUN] GraphQL: Would make request: {query} with variables {variables}")
        return None

    try:
        response = requests.post(GRAPHQL_URL, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"GraphQL: Request failed: {e}")
        return None

# ----------------------------
# Get Local Source ID
# ----------------------------
def get_local_source_id():
    global LOCAL_SOURCE_ID

    query = """
    query {
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
        if node["name"].lower() == "local source":
            LOCAL_SOURCE_ID = str(node["id"])  # must be a string in queries
            log_clarification()
            log(f"GraphQL: Local source ID = {LOCAL_SOURCE_ID}", "debug")
            return LOCAL_SOURCE_ID

    logger.error("GraphQL: Could not find 'Local source' in sources")
    LOCAL_SOURCE_ID = None

# ----------------------------
# Ensure Category Exists
# ----------------------------
def ensure_category(category_name=None):
    global CATEGORY_ID
    name = category_name or SUWAYOMI_CATEGORY_NAME

    query = """
    query ($name: String!) {
      categories(filter: { name: { equalTo: $name } }) {
        nodes { id name }
      }
    }
    """
    result = graphql_request(query, {"name": name})
    nodes = result.get("data", {}).get("categories", {}).get("nodes", [])
    if nodes:
        CATEGORY_ID = int(nodes[0]["id"])
        return CATEGORY_ID

    mutation = """
    mutation ($name: String!) {
      createCategory(input: { name: $name }) {
        category { id name }
      }
    }
    """
    result = graphql_request(mutation, {"name": name})
    CATEGORY_ID = int(result["data"]["createCategory"]["category"]["id"])
    return CATEGORY_ID

# ----------------------------
# Store The IDs of Creators' Manga
# ----------------------------
def store_creator_manga_IDs(meta: dict):
    global LOCAL_SOURCE_ID
    
    if not LOCAL_SOURCE_ID:
        logger.error("GraphQL: LOCAL_SOURCE_ID not set, cannot store manga IDs.")
        return

    if not dry_run:
        gallery_meta = return_gallery_metas(meta)
        creators = [safe_name(c) for c in gallery_meta.get("creator", [])]
        for creator_name in creators:
            query = """
            query ($title: String!, $sourceId: String!) {
              mangas(
                filter: { sourceId: { equalTo: $sourceId }, title: { equalTo: $title } }
              ) {
                nodes { id title }
              }
            }
            """
            result = graphql_request(query, {"title": creator_name, "sourceId": LOCAL_SOURCE_ID})
            nodes = result.get("data", {}).get("mangas", {}).get("nodes", []) if result else []
            if not nodes:
                logger.warning(f"GraphQL: No manga found for creator '{creator_name}', deferring.")
                with _deferred_lock:
                    _deferred_creators.add(creator_name)
                continue

            manga_id = int(nodes[0]["id"])
            with _manga_ids_lock:
                if manga_id not in _collected_manga_ids:
                    _collected_manga_ids.add(manga_id)
                    log(f"GraphQL: Stored manga ID {manga_id} for creator '{creator_name}'", "debug")
    else:
        log(f"[DRY-RUN] GraphQL: Would store manga ID for creators", "debug")

# ----------------------------
# Retry deferred creators
# ----------------------------
def retry_deferred_creators():
    global LOCAL_SOURCE_ID
    if not _deferred_creators:
        return

    max_attempts = 5
    delay = 2

    for attempt in range(1, max_attempts + 1):
        with _deferred_lock:
            creators_to_retry = list(_deferred_creators)

        if not creators_to_retry:
            return

        logger.info(f"GraphQL: Retrying {len(creators_to_retry)} deferred creators (attempt {attempt}/{max_attempts})")

        for creator_name in creators_to_retry:
            query = """
            query ($title: String!, $sourceId: String!) {
              mangas(
                filter: { sourceId: { equalTo: $sourceId }, title: { equalTo: $title } }
              ) {
                nodes { id title }
              }
            }
            """
            result = graphql_request(query, {"title": creator_name, "sourceId": LOCAL_SOURCE_ID})
            nodes = result.get("data", {}).get("mangas", {}).get("nodes", []) if result else []
            if nodes:
                manga_id = int(nodes[0]["id"])
                with _manga_ids_lock:
                    if manga_id not in _collected_manga_ids:
                        _collected_manga_ids.add(manga_id)
                        log(f"GraphQL: Stored manga ID {manga_id} for creator '{creator_name}' (retried)", "debug")
                with _deferred_lock:
                    _deferred_creators.discard(creator_name)

        if not _deferred_creators:
            logger.info("GraphQL: All deferred creators resolved.")
            return

        time.sleep(delay)
        delay *= 2

    if _deferred_creators:
        logger.warning(f"GraphQL: Some creators could not be resolved after retries: {_deferred_creators}")

# ----------------------------
# Bulk Update Functions
# ----------------------------
def update_mangas(ids: list[int]):
    if not ids:
        return
    mutation = """
    mutation ($ids: [Int!]!) {
      updateMangas(input: { ids: $ids, patch: { inLibrary: true } }) {
        clientMutationId
      }
    }
    """
    graphql_request(mutation, {"ids": ids})
    logger.info(f"GraphQL: Updated {len(ids)} mangas as 'In Library'.")

def update_mangas_categories(ids: list[int], category_id: int):
    if not ids:
        return
    mutation = """
    mutation ($ids: [Int!]!, $categoryId: Int!) {
      updateMangasCategories(
        input: { ids: $ids, patch: { addToCategories: [$categoryId] } }
      ) {
        mangas { id title }
      }
    }
    """
    graphql_request(mutation, {"ids": ids, "categoryId": category_id})
    logger.info(f"GraphQL: Added {len(ids)} mangas to category {category_id}.")

# ----------------------------
# Add Collected Creators to Category
# ----------------------------
def add_creators_to_category():
    global CATEGORY_ID
    
    if not dry_run:
        if CATEGORY_ID is None:
            ensure_category()
        if CATEGORY_ID is None:
            logger.error("GraphQL: CATEGORY_ID not set, cannot add creators.")
            return

        retry_deferred_creators()

        # Get existing mangas in the category
        query = """
        query ($categoryId: Int!) {
          category(id: $categoryId) {
            mangas { nodes { id title } }
          }
        }
        """
        result = graphql_request(query, {"categoryId": CATEGORY_ID})
        existing_ids = {int(n["id"]) for n in result.get("data", {}).get("category", {}).get("mangas", {}).get("nodes", [])}

        with _manga_ids_lock:
            new_ids = list(_collected_manga_ids - existing_ids)

        if not new_ids:
            logger.info("GraphQL: No new mangas to add to category.")
            return

        update_mangas(new_ids)
        update_mangas_categories(new_ids, CATEGORY_ID)
    else:
        log(f"[DRY-RUN] GraphQL: Would add creators to Suwayomi category '{SUWAYOMI_CATEGORY_NAME}'", "debug")

####################################################################################################################
# CORE HOOKS (thread-safe)
####################################################################################################################

# Hook for downloading images. Use active_extension.download_images_hook(ARGS) in downloader.
def download_images_hook(gallery, page, urls, path, session, pbar=None, creator=None, retries=None):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Image Download Hook Called.", "debug")
    
    if not urls:
        logger.warning(f"Gallery {gallery}: Page {page}: No URLs, skipping")
        if pbar and creator:
            pbar.set_postfix_str(f"Skipped Creator: {creator}")
        return False

    if retries is None:
        retries = config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES)

    if os.path.exists(path):
        log(f"Already exists, skipping: {path}", "debug")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if dry_run:
        logger.info(f"[DRY-RUN] Gallery {gallery}: Would download {urls[0]} -> {path}")
        if pbar and creator:
            pbar.set_postfix_str(f"Creator: {creator}")
        return True

    if not isinstance(session, requests.Session):
        session = requests.Session()

    # Loop through mirrors
    for url in urls:
        for attempt in range(1, retries + 1):
            try:
                r = session.get(url, timeout=30, stream=True)
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
                wait = 2 ** attempt
                log_clarification()
                logger.warning(f"Gallery {gallery}: Page {page}: Mirror {url}, attempt {attempt} failed: {e}, retrying in {wait}s")
                time.sleep(wait)
        
        # If all retries for this mirror failed, move to next mirror
        logger.warning(f"Gallery {gallery}: Page {page}: Mirror {url} failed after {retries} attempts, trying next mirror")

    # If no mirrors succeeded
    log_clarification()
    logger.error(f"Gallery {gallery}: Page {page}: All mirrors failed after {retries} retries each: {urls}")
    if pbar and creator:
        pbar.set_postfix_str(f"Failed Creator: {creator}")
    return False

# Hook for pre-run functionality. Use active_extension.pre_run_hook(ARGS) in downloader.
def pre_run_hook(gallery_list):
    global LOCAL_SOURCE_ID, CATEGORY_ID
    
    update_extension_download_path()
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Pre-run Hook Called.", "debug")
    
    # Initialise globals
    LOCAL_SOURCE_ID = get_local_source_id()
    CATEGORY_ID = ensure_category(SUWAYOMI_CATEGORY_NAME)

    return gallery_list

# Hook for functionality before a gallery download. Use active_extension.pre_gallery_download_hook(ARGS) in downloader.
def pre_gallery_download_hook(gallery_id):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Pre-download Hook Called: Gallery: {gallery_id}", "debug")

# Hook for functionality during a gallery download. Use active_extension.during_gallery_download_hook(ARGS) in downloader.
def during_gallery_download_hook(gallery_id):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: During-download Hook Called: Gallery: {gallery_id}", "debug")

# Hook for functionality after a completed gallery download. Use active_extension.after_completed_gallery_download_hook(ARGS) in downloader.
def after_completed_gallery_download_hook(meta: dict, gallery_id):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Post-download Hook Called: Gallery: {meta['id']}: Downloaded.", "debug")

    # Thread-safe append
    with _gallery_meta_lock:
        _collected_gallery_metas.append(meta)

    # Update creator's popular genres and store creator's manga ID (thread safe)
    update_creator_popular_genres(meta)
    store_creator_manga_IDs(meta)

# Hook for post-run functionality. Reset download path. Use active_extension.post_run_hook(ARGS) in downloader.
def post_run_hook():
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Post-run Hook Called.", "debug")

    # Bulk add creators to Suwayomi category
    add_creators_to_category()

    # Clean up empty directories
    remove_empty_directories(True)