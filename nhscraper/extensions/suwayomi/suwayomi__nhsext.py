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

# Thread lock for file operations
_file_lock = threading.Lock()

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
    global DEDICATED_DOWNLOAD_PATH
    global EXTENSION_INSTALL_PATH

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
    global DEDICATED_DOWNLOAD_PATH
    global EXTENSION_INSTALL_PATH

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

############################################
_collected_gallery_metas = []
_gallery_meta_lock = threading.Lock()

GRAPHQL_URL = "http://127.0.0.1:4567/api/graphql"
LOCAL_SOURCE_ID = "0"  # Local source is usually "0"
SUWAYOMI_CATEGORY_NAME = "NHentai Scraper"

def graphql_request(query: str, variables: dict = None):
    headers = {"Content-Type": "application/json"}
    payload = {"query": query, "variables": variables or {}}

    if dry_run:
        logger.info(f"[DRY-RUN] Would make GraphQL request: {query} with variables {variables}")
        return None

    try:
        response = requests.post(GRAPHQL_URL, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"GraphQL request failed: {e}")
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
        logger.error("GraphQL: Failed to fetch sources")
        return LOCAL_SOURCE_ID

    for node in result["data"]["sources"]["nodes"]:
        if node["name"].lower() == "local source":
            LOCAL_SOURCE_ID = str(node["id"])  # must be a string in queries
            log(f"GraphQL: Local source ID = {LOCAL_SOURCE_ID}", "debug")
            return LOCAL_SOURCE_ID

    logger.error("GraphQL: Could not find 'Local source' in sources")
    return None

# ----------------------------
# Ensure Category Exists
# ----------------------------
def ensure_category(category_name=None):
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
        return int(nodes[0]["id"])  # category ID for mutations must be int

    mutation = """
    mutation ($name: String!) {
      createCategory(input: { name: $name }) {
        category { id name }
      }
    }
    """
    result = graphql_request(mutation, {"name": name})
    return int(result["data"]["createCategory"]["category"]["id"])

# ----------------------------
# Add Manga to Category
# ----------------------------
def add_manga_to_category(manga_id: int, category_id: int):
    mutation = """
    mutation ($id: Int!, $categoryId: Int!) {
      updateMangaCategories(
        input: { id: $id, patch: { addToCategories: [$categoryId] } }
      ) {
        manga { id title categories { nodes { id name } } }
      }
    }
    """
    graphql_request(mutation, {"id": manga_id, "categoryId": category_id})
    logger.info(f"GraphQL: Added category {category_id} to manga {manga_id}")

# ----------------------------
# Add Creators (Manga Titles) to Category
# ----------------------------
def add_creator_to_category(meta):
    if dry_run:
        log(f"[DRY-RUN] Would add gallery '{meta.get('title', 'Unknown')}' to category '{SUWAYOMI_CATEGORY_NAME}'", "debug")
        return

    # Get local source ID
    local_source_id = get_local_source_id()
    # Get category ID
    category_id = ensure_category(SUWAYOMI_CATEGORY_NAME)
    
    if local_source_id is None:
        logger.error("GraphQL: Cannot add gallery because Local source ID could not be resolved")
        return

    gallery_meta = return_gallery_metas(meta)
    creators = [safe_name(c) for c in gallery_meta.get("creator", [])]
    if not creators:
        log("No creators found for gallery, skipping category update", "debug")
        return

    # For each creator, fetch manga by title and add to category
    for creator_name in creators:
        query = """
        query ($title: String!, $sourceId: String!) {
          mangas(
            filter: {
              sourceId: { equalTo: $sourceId },
              title: { equalTo: $title }
            }
          ) {
            nodes { id title categories { nodes { id name } } }
          }
        }
        """
        result = graphql_request(query, {"title": creator_name, "sourceId": local_source_id})
        if not result:
            logger.warning(f"GraphQL: Failed to fetch manga for creator '{creator_name}'")
            continue

        nodes = result.get("data", {}).get("mangas", {}).get("nodes", [])
        if not nodes:
            logger.warning(f"GraphQL: No manga found in Local source with title '{creator_name}'")
            continue

        manga_id = int(nodes[0]["id"])
        existing_categories = [c["name"] for c in nodes[0]["categories"]["nodes"]]
        if SUWAYOMI_CATEGORY_NAME not in existing_categories:
            logger.info(f"GraphQL: Adding manga '{creator_name}' (ID={manga_id}) to category '{SUWAYOMI_CATEGORY_NAME}'")
            add_manga_to_category(manga_id, category_id)
        else:
            log(f"GraphQL: Manga '{creator_name}' already in category '{SUWAYOMI_CATEGORY_NAME}'", "debug")

############################################

# Max number of genres stored in a creator's details.json
MAX_GENRES_STORED = 15
# Max number of genres parsed from a gallery and stored in a creator's "most_popular_genres.json" field.
MAX_GENRES_PARSED = 100

# ------------------------------------------------------------
# Update creator's most popular genres
# ------------------------------------------------------------
def update_creator_popular_genres(meta):
    if not dry_run:
        gallery_meta = return_gallery_metas(meta)
        creators = [safe_name(c) for c in gallery_meta.get("creator", [])]
        if not creators:
            return
        gallery_title = gallery_meta["title"]
        gallery_tags = meta.get("tags", [])
        gallery_genres = [
            tag["name"] for tag in gallery_tags
            if "name" in tag and tag.get("type") not in ["artist", "group", "language", "category"]
        ]

        top_genres_file = os.path.join(DEDICATED_DOWNLOAD_PATH, "most_popular_genres.json")
        with _file_lock:
            if os.path.exists(top_genres_file):
                with open(top_genres_file, "r", encoding="utf-8") as f:
                    all_genre_counts = json.load(f)
            else:
                all_genre_counts = {}

        for creator_name in creators:
            creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
            details_file = os.path.join(creator_folder, "details.json")
            os.makedirs(creator_folder, exist_ok=True)

            with _file_lock:
                if os.path.exists(details_file):
                    with open(details_file, "r", encoding="utf-8") as f:
                        details = json.load(f)
                else:
                    details = {
                        "title": "",
                        "author": creator_name,
                        "artist": creator_name,
                        "description": "",
                        "genre": [],
                        "status": "1",
                        "_status values": ["0 = Unknown", "1 = Ongoing", "2 = Completed", "3 = Licensed"]
                    }

            details["title"] = creator_name
            details["author"] = creator_name
            details["artist"] = creator_name
            details["description"] = f"Latest Doujin: {gallery_title}"

            with _file_lock:
                if creator_name not in all_genre_counts:
                    all_genre_counts[creator_name] = {}
                creator_counts = all_genre_counts[creator_name]
                for genre in gallery_genres:
                    creator_counts[genre] = creator_counts.get(genre, 0) + 1

                most_popular = sorted(creator_counts.items(), key=lambda x: x[1], reverse=True)[:MAX_GENRES_STORED]
                log_clarification()
                #log(f"Most Popular Genres for {creator_name}:\n{most_popular}", "debug")
                details["genre"] = [g for g, count in most_popular]

                if len(creator_counts) > MAX_GENRES_PARSED:
                    creator_counts = dict(sorted(creator_counts.items(), key=lambda x: x[1], reverse=True)[:MAX_GENRES_PARSED])
                    all_genre_counts[creator_name] = creator_counts

                with open(details_file, "w", encoding="utf-8") as f:
                    json.dump(details, f, ensure_ascii=False, indent=2)

                with open(top_genres_file, "w", encoding="utf-8") as f:
                    json.dump(all_genre_counts, f, ensure_ascii=False, indent=2)
    
    else:
        log(f"[DRY RUN] Would add manga to for {creator_name}", "debug")
        log(f"[DRY RUN] Would create details.json for {creator_name}", "debug")

############################################

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

####################################################################################################################
# CORE HOOKS (thread-safe)
####################################################################################################################

# Hook for downloading images. Use active_extension.download_images_hook(ARGS) in downloader.
def download_images_hook(gallery, page, urls, path, session, pbar=None, creator=None, retries=None):
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
    update_extension_download_path()
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Pre-run hook called.", "debug")
    return gallery_list

# Hook for functionality before a gallery download. Use active_extension.pre_gallery_download_hook(ARGS) in downloader.
def pre_gallery_download_hook(gallery_id):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Pre-download hook called: Gallery: {gallery_id}", "debug")

# Hook for functionality during a gallery download. Use active_extension.during_gallery_download_hook(ARGS) in downloader.
def during_gallery_download_hook(gallery_id):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: During-download hook called: Gallery: {gallery_id}", "debug")

# Hook for functionality after a completed gallery download. Use active_extension.after_completed_gallery_download_hook(ARGS) in downloader.
def after_completed_gallery_download_hook(meta: dict, gallery_id):
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Post-download hook called: Gallery: {meta['id']}: Downloaded.", "debug")

    # Thread-safe append
    with _gallery_meta_lock:
        _collected_gallery_metas.append(meta)

    # Update creator's popular genres (thread safe)
    update_creator_popular_genres(meta)

    if dry_run:
        log(f"[DRY-RUN] Would collect gallery meta for post-run processing: {meta.get('title', 'Unknown')}", "debug")

# Hook for post-run functionality. Reset download path. Use active_extension.post_run_hook(ARGS) in downloader.
def post_run_hook():
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Post-run hook called.", "debug")

    # Thread-safe copy and clear of collected metas
    with _gallery_meta_lock:
        metas_to_process = _collected_gallery_metas.copy()
        _collected_gallery_metas.clear()

    if not metas_to_process:
        log("No gallery metas collected during run, skipping category updates.", "debug")
    else:
        log(f"Processing {len(metas_to_process)} collected gallery metas for Suwayomi category...", "debug")
        for meta in metas_to_process:
            if dry_run:
                log(f"[DRY-RUN] Would add gallery '{meta.get('title', 'Unknown')}' to category '{SUWAYOMI_CATEGORY_NAME}'", "debug")
            else:
                add_creator_to_category(meta)

    # Clean up empty directories
    remove_empty_directories(True)