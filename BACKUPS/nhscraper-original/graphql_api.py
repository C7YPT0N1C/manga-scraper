#!/usr/bin/env python3
# nhscraper/graphql-api.py

import requests
import logging
from nhscraper.config import logger, config, SUWAYOMI_DIR

GRAPHQL_URL = config.get("GRAPHQL_URL", "http://127.0.0.1:4567/api/graphql")

##################################################################################################################################
# LOGGING
##################################################################################################################################
def log_clarification():  # Print new line for readability.
    print()
    logger.debug("")

# ===============================
# GLOBAL STUB CONTROL
# ===============================
STUB_ONLY = False  # If True, no real GraphQL calls are made, only stubs

# ===============================
# GRAPHQL UTILS
# ===============================
def graphql_request(query, variables, USE_STUB=False):
    # Generic GraphQL request handler with stub support and success verification.
    # Returns the "data" dictionary on success, None on failure.
    if STUB_ONLY or USE_STUB:
        log_clarification()
        logger.info(f"[*] [STUB] Skipping GraphQL call. Query: {query}, Variables: {variables}")
        return {"data": "stub"}
    
    if config.get("dry_run", False):
        log_clarification()
        logger.info("[+] [DRY-RUN] Skipping GraphQL request")
        return None

    try:
        resp = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Check for GraphQL errors
        if "errors" in data and data["errors"]:
            log_clarification()
            logger.error(f"[!] GraphQL request returned errors: {data['errors']}")
            return None

        # Basic check that data is non-empty
        if "data" not in data or data["data"] is None:
            log_clarification()
            logger.warning(f"[!] GraphQL request returned no data: {data}")
            return None

        # Log successful request
        log_clarification()
        logger.info(f"[+] GraphQL request successful. Query: {query}, Variables: {variables}, Response: {data['data']}")
        return data["data"]
    
    except Exception as e:
        log_clarification()
        logger.error(f"[!] GraphQL request failed: {e}")
        return None

# ===============================
# SKELETON CALL
# ===============================
def skeleton_call():  # Skeleton GraphQL example call
    query = "query example { exampleField }"
    variables = {"someVar": 123}
    graphql_request(query, variables, USE_STUB=True)

# ===============================
# SUWAYOMI LIBRARY
# ===============================
def set_local_directory():  # Set Suwayomi's local directory to match SUWAYOMI_DIR
    query = """
    mutation setLocalDir($path: String!) {
      setLocalDirectory(path: $path)
    }
    """
    variables = {"path": SUWAYOMI_DIR}
    result = graphql_request(query, variables)
    if result and result.get("setLocalDirectory") is True:
        logger.info("[+] Suwayomi local directory updated successfully")
    else:
        logger.warning("[!] Failed to update Suwayomi local directory")

def create_category(tag_name):  # Create a category for a tag if it doesn't exist
    query = """
    mutation createCategory($input: CategoryInput!) {
      createCategory(input: $input) { id name }
    }
    """
    variables = {"input": {"name": tag_name}}
    result = graphql_request(query, variables)
    if result and result.get("createCategory"):
        logger.info(f"[+] Category '{tag_name}' created or already exists")
    else:
        logger.warning(f"[!] Failed to create category '{tag_name}'")

def add_gallery_to_category(gallery_id, category_name):  # Add a downloaded gallery to a category
    query = """
    mutation addGalleryToCategory($galleryId: Int!, $category: String!) {
      addGalleryToCategory(galleryId: $galleryId, category: $category)
    }
    """
    variables = {"galleryId": gallery_id, "category": category_name}
    result = graphql_request(query, variables)
    if result and result.get("addGalleryToCategory") is True:
        logger.info(f"[+] Gallery {gallery_id} added to category '{category_name}'")
    else:
        logger.warning(f"[!] Failed to add gallery {gallery_id} to category '{category_name}'")

def update_library():  # Trigger Suwayomi library update
    query = """
    mutation updateLibrary {
      updateLibrary
    }
    """
    variables = {}
    result = graphql_request(query, variables)
    if result and result.get("updateLibrary") is True:
        logger.info("[+] Suwayomi library updated successfully")
    else:
        logger.warning("[!] Failed to update Suwayomi library")