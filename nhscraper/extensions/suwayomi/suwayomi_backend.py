#!/usr/bin/env python3
# extensions/suwayomi/custom_hooks.py
# ENSURE THAT THIS FILE IS THE *EXACT SAME* IN BOTH THE NHENTAI-SCRAPER REPO AND THE NHENTAI-SCRAPER-EXTENSIONS REPO.
# PLEASE UPDATE THIS FILE IN THE NHENTAI-SCRAPER REPO FIRST, THEN COPY IT OVER TO THE NHENTAI-SCRAPER-EXTENSIONS REPO.

import os, json, threading, requests

from nhscraper.core.config import *
from nhscraper.core.api import get_meta_tags, safe_name, clean_title

####################################################################################################################
# Global variables
####################################################################################################################
EXTENSION_NAME = "skeleton" # Must be fully lowercase, also update in custom_hooks
EXTENSION_INSTALL_PATH = "/opt/nhentai-scraper/downloads/" # Use this if extension installs external programs (like Suwayomi-Server)
REQUESTED_DOWNLOAD_PATH = "/opt/nhentai-scraper/downloads/"
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

# Optional fallback
if DEDICATED_DOWNLOAD_PATH is None: # Default download folder here.
    DEDICATED_DOWNLOAD_PATH = REQUESTED_DOWNLOAD_PATH

SUBFOLDER_STRUCTURE = ["creator", "title"] # SUBDIR_1, SUBDIR_2, etc

dry_run = config.get("DRY_RUN", DEFAULT_DRY_RUN)

# Thread lock for file operations
_file_lock = threading.Lock()

GRAPHQL_URL = "http://127.0.0.1:4567/api/graphql"
LOCAL_SOURCE_ID = "0"  # Local source is usually "0"
SUWAYOMI_CATEGORY_NAME = "NHentai Scraper"

_collected_gallery_metas = []
_gallery_meta_lock = threading.Lock()

_collected_manga_ids = set()
_manga_ids_lock = threading.Lock()

####################################################################################################################
# CORE
####################################################################################################################
# Remember to reflect in __nhsext file as well as backend.
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

    logger.error("GraphQL: Could not find 'Local source' in sources")
    LOCAL_SOURCE_ID = None

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
                logger.warning(f"GraphQL: No manga found for creator '{creator_name}'") # TEST
                continue

            manga_id = int(nodes[0]["id"])
            with _manga_ids_lock:
                if manga_id not in _collected_manga_ids:
                    _collected_manga_ids.add(manga_id)
                    log(f"GraphQL: Stored manga ID {manga_id} for creator '{creator_name}'", "debug")
    else:
        log(f"[DRY-RUN] GraphQL: Would store manga ID {manga_id} for creator '{creator_name}'", "debug")

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
            logger.error("GraphQL: CATEGORY_ID not set, cannot add creators.")
            return

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
        log(f"[DRY-RUN] Would add creators to Suwayomi category '{SUWAYOMI_CATEGORY_NAME}'", "debug")

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
        log(f"[DRY RUN] Would create details.json for {creator_name}", "debug")