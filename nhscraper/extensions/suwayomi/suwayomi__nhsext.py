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

############################################

GRAPHQL_URL = "http://127.0.0.1:4567/api/graphql"
LOCAL_SOURCE_ID = None  # Local source is usually "0"
SUWAYOMI_CATEGORY_NAME = "NHentai Scraped"

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
    logger.info(f"Extension: {EXTENSION_NAME}: Ready.")
    log(f"Extension: {EXTENSION_NAME}: Debugging started.", "debug")
    update_env("EXTENSION_DOWNLOAD_PATH", DEDICATED_DOWNLOAD_PATH)
    
    if global_dry_run:
        logger.info(f"[DRY-RUN] Would ensure download path exists: {DEDICATED_DOWNLOAD_PATH}")
        return
    try:
        os.makedirs(DEDICATED_DOWNLOAD_PATH, exist_ok=True)
        logger.info(f"Extension: {EXTENSION_NAME}: Download path ready at '{DEDICATED_DOWNLOAD_PATH}'.")
    except Exception as e:
        logger.error(f"Extension: {EXTENSION_NAME}: Failed to create download path '{DEDICATED_DOWNLOAD_PATH}': {e}")

def return_gallery_metas(meta):
    artists = get_meta_tags(f"Extension: {EXTENSION_NAME}: Return_gallery_metas", meta, "artist")
    groups = get_meta_tags(f"Extension: {EXTENSION_NAME}: Return_gallery_metas", meta, "group")
    creators = artists or groups or ["Unknown Creator"]
    
    title = clean_title(meta)
    id = str(meta.get("id", "Unknown ID"))
    full_title = f"({id}) {title}"
    
    language = get_meta_tags(f"Extension: {EXTENSION_NAME}: Return_gallery_metas", meta, "language") or ["Unknown Language"]
    
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

    if global_dry_run:
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

    if global_dry_run:
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
    
    log_clarification()

    if not DEDICATED_DOWNLOAD_PATH or not os.path.isdir(DEDICATED_DOWNLOAD_PATH):
        log("No valid DEDICATED_DOWNLOAD_PATH set, skipping cleanup.", "debug")
        return

    if global_dry_run:
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

# ------------------------------------------------------------
# Update creator's most popular genres
# ------------------------------------------------------------
def update_creator_popular_genres(meta):
    if not global_dry_run:
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
        log(f"[DRY RUN] Would create details.json for {creator_name}", "debug")
    
    # ----------------------------
    # Save page 1 as cover.[ext]
    # ----------------------------
    if not global_dry_run:
        try:
            gallery_meta = return_gallery_metas(meta)
            creators = [safe_name(c) for c in gallery_meta.get("creator", [])]
            if not creators:
                creators = ["Unknown Creator"]

            for creator_name in creators:
                creator_folder = os.path.join(DEDICATED_DOWNLOAD_PATH, creator_name)
                gallery_folder = os.path.join(creator_folder, gallery_meta["title"])

                if not os.path.exists(gallery_folder):
                    logger.warning(f"Skipping manga cover update: Gallery folder not found: {gallery_folder}")
                    continue

                # Look for page 1 file (assume starts with 1.)
                candidates = [f for f in os.listdir(gallery_folder) if f.startswith("1.")]
                if not candidates:
                    logger.warning(f"Skipping manga cover update: No 'page 1' found in Gallery: {gallery_folder}")
                    continue

                page1_file = os.path.join(gallery_folder, candidates[0])
                _, ext = os.path.splitext(page1_file)
                cover_file = os.path.join(creator_folder, f"cover{ext}")

                shutil.copy2(page1_file, cover_file)
                logger.info(f"Updated manga cover for {creator_name}: {cover_file}")

        except Exception as e:
            logger.error(f"Failed to update manga cover for Gallery {meta['id']}: {e}")
    else:
        log(f"[DRY RUN] Would update manga cover for {creator_name}", "debug")

############################################

def graphql_request(query: str, variables: dict = None):
    headers = {"Content-Type": "application/json"}
    payload = {"query": query, "variables": variables or {}}

    if global_dry_run:
        logger.info(f"[DRY-RUN] GraphQL: Would make request: {query} with variables {variables}")
        return None

    try:
        #log(f"GraphQL Request Payload:\n{json.dumps(payload, indent=2)}", "debug") # Only needed for ACTUAL code debugging.
        response = requests.post(GRAPHQL_URL, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        result = response.json()
        #log(f"GraphQL Response:\n{json.dumps(result, indent=2)}", "debug") # Only needed for ACTUAL code debugging.
        return result
    except requests.RequestException as e:
        logger.error(f"GraphQL: Request failed: {e}")
        return None
    except ValueError as e:
        logger.error(f"GraphQL: Failed to decode JSON response: {e}")
        logger.error(f"Raw response: {response.text if response else 'No response'}")
        return None

# ----------------------------
# Get Local Source ID
# ----------------------------
def get_local_source_id():
    global LOCAL_SOURCE_ID

    log("GraphQL: Fetching Local source ID", "debug")
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
        #log(f"GraphQL: Checking source node {node}", "debug")
        if node["name"].lower() == "local source":
            LOCAL_SOURCE_ID = str(node["id"])  # must be a string in queries
            log_clarification()
            #log(f"GraphQL: Local source ID = {LOCAL_SOURCE_ID}", "debug")
            return LOCAL_SOURCE_ID

    logger.error("GraphQL: Could not find 'Local source' in sources")
    LOCAL_SOURCE_ID = None

# ----------------------------
# Ensure Category Exists
# ----------------------------
def ensure_category(category_name=None):
    global CATEGORY_ID
    name = category_name or SUWAYOMI_CATEGORY_NAME

    log(f"GraphQL: Ensuring '{name}' category exists", "debug")
    query = """
    query ($name: String!) {
      categories(filter: { name: { equalTo: $name } }) {
        nodes { id name }
      }
    }
    """
    result = graphql_request(query, {
        "name": name
    })
    
    #log(f"GraphQL: Category query result: {result}", "debug")
    nodes = result.get("data", {}).get("categories", {}).get("nodes", [])
    if nodes:
        CATEGORY_ID = int(nodes[0]["id"])
        log(f"GraphQL: Found existing category {nodes[0]}", "debug")
        return CATEGORY_ID

    log(f"GraphQL: Creating new category {name}", "debug")
    mutation = """
    mutation ($name: String!) {
      createCategory(input: { name: $name }) {
        category { id name }
      }
    }
    """
    result = graphql_request(mutation, {"name": name})
    log(f"GraphQL: Create category result: {result}", "debug")
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

    if not global_dry_run:
        gallery_meta = return_gallery_metas(meta)
        creators = [safe_name(c) for c in gallery_meta.get("creator", [])]
        log(f"GraphQL: Gallery Metadata:{gallery_meta}\nProcessing creators {creators}", "debug")
        for creator_name in creators:
            query = """
            query ($title: String!, $sourceId: LongString!) {
              mangas(
                filter: { sourceId: { equalTo: $sourceId }, title: { equalTo: $title } }
              ) {
                nodes { id title }
              }
            }
            """
            log(f"GraphQL: Looking up manga ID for creator '{creator_name}' in source {LOCAL_SOURCE_ID}", "debug")
            result = graphql_request(query, {
                "title": creator_name,
                "sourceId": LOCAL_SOURCE_ID
            })
            
            #log(f"GraphQL: Manga lookup result for '{creator_name}': {result}", "debug")
            nodes = result.get("data", {}).get("mangas", {}).get("nodes", []) if result else []
            if not nodes:
                logger.warning(f"GraphQL: No manga found for creator '{creator_name}', deferring.")
                with _deferred_lock:
                    _deferred_creators.add(creator_name)
                continue

            manga_id = int(nodes[0]["id"])
            log(f"GraphQL: Found manga for creator '{creator_name}': {nodes[0]}", "debug")
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

    max_attempts = config.get("MAX_RETRIES", DEFAULT_MAX_RETRIES)
    delay = 2

    for attempt in range(1, max_attempts + 1):
        with _deferred_lock:
            creators_to_retry = list(_deferred_creators)

        if not creators_to_retry:
            return

        logger.info(f"GraphQL: Retrying {len(creators_to_retry)} deferred creators (attempt {attempt}/{max_attempts})")

        for creator_name in creators_to_retry:
            log(f"GraphQL: Retrying creator '{creator_name}' with source {LOCAL_SOURCE_ID}", "debug")
            query = """
            query ($title: String!, $sourceId: LongString!) {
              mangas(
                filter: { sourceId: { equalTo: $sourceId }, title: { equalTo: $title } }
              ) {
                nodes { id title }
              }
            }
            """
            result = graphql_request(query, {
                "title": creator_name,
                "sourceId": LOCAL_SOURCE_ID
            })
            log(f"GraphQL: Retry result for '{creator_name}': {result}", "debug")
            nodes = result.get("data", {}).get("mangas", {}).get("nodes", []) if result else []
            if nodes:
                manga_id = int(nodes[0]["id"])
                log(f"GraphQL: Found manga {nodes[0]} for creator '{creator_name}' on retry", "debug")
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
    log(f"GraphQL: Updating mangas {ids} as 'In Library", "debug")
    mutation = """
    mutation ($ids: [Int!]!) {
      updateMangas(input: { ids: $ids, patch: { inLibrary: true } }) {
        clientMutationId
      }
    }
    """
    result = graphql_request(mutation, {"ids": ids})
    log(f"GraphQL: updateMangas result: {result}", "debug")
    logger.info(f"GraphQL: Updated {len(ids)} mangas as 'In Library'.")

def update_mangas_categories(ids: list[int], category_id: int):
    if not ids:
        return
    log(f"GraphQL: Adding mangas {ids} to category {category_id}", "debug")
    mutation = """
    mutation ($ids: [Int!]!, $categoryId: Int!) {
      updateMangasCategories(
        input: { ids: $ids, patch: { addToCategories: [$categoryId] } }
      ) {
        mangas { id title }
      }
    }
    """
    result = graphql_request(mutation, {"ids": ids, "categoryId": category_id})
    log(f"GraphQL: updateMangasCategories result: {result}", "debug")
    logger.info(f"GraphQL: Added {len(ids)} mangas to category {category_id}.")

# ----------------------------
# Add Single Creator to Category (defer if needed)
# ----------------------------
def add_creator_to_category(meta: dict):
    global LOCAL_SOURCE_ID

    if not LOCAL_SOURCE_ID:
        logger.error("GraphQL: LOCAL_SOURCE_ID not set, cannot add creator.")
        return

    gallery_meta = return_gallery_metas(meta)
    creators = [safe_name(c) for c in gallery_meta.get("creator", [])]
    if not creators:
        return

    for creator_name in creators:
        query = """
        query ($title: String!, $sourceId: LongString!) {
          mangas(
            filter: { sourceId: { equalTo: $sourceId }, title: { equalTo: $title } }
          ) {
            nodes { id title }
          }
        }
        """
        result = graphql_request(query, {
            "title": creator_name,
            "sourceId": LOCAL_SOURCE_ID
        })
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

# ----------------------------
# Process Deferred Creators & Add to Category
# ----------------------------
def add_deferred_creators_to_category():
    global CATEGORY_ID

    if CATEGORY_ID is None:
        CATEGORY_ID = ensure_category(SUWAYOMI_CATEGORY_NAME)
        if CATEGORY_ID is None:
            logger.error(f"GraphQL: Category '{SUWAYOMI_CATEGORY_NAME}' not set, cannot add creators.")
            return

    retry_deferred_creators()

    # Fetch existing IDs in category
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
        logger.info(f"GraphQL: No new mangas to add to category '{SUWAYOMI_CATEGORY_NAME}'.")
        return

    update_mangas(new_ids)
    update_mangas_categories(new_ids, CATEGORY_ID)

####################################################################################################################
# CORE HOOKS (thread-safe)
####################################################################################################################

# Hook for downloading images. Use active_extension.download_images_hook(ARGS) in downloader.
def download_images_hook(gallery, page, urls, path, session, pbar=None, creator=None, retries=None):
    #log_clarification()
    #log(f"Extension: {EXTENSION_NAME}: Image Download Hook Called.", "debug")
    
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

    if global_dry_run:
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
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Pre-run Hook Called.", "debug")
    
    global LOCAL_SOURCE_ID, CATEGORY_ID  
    
    update_extension_download_path()
    
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

    # Update creator's popular genres
    update_creator_popular_genres(meta)
    
    # Store creator's manga ID, then add creator's manga to Suwayomi Category (thread safe)
    store_creator_manga_IDs(meta)
    add_creator_to_category(meta)

# Hook for post-run functionality. Reset download path. Use active_extension.post_run_hook(ARGS) in downloader.
def post_run_hook():
    log_clarification()
    log(f"Extension: {EXTENSION_NAME}: Post-run Hook Called.", "debug")

    # Add deferred creators to Suwayomi category
    add_deferred_creators_to_category()

    # Clean up empty directories
    remove_empty_directories(True)