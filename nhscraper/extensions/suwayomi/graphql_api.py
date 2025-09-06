# ===============================
# SUWAYOMI GRAPHQL HELPERS
# ===============================
def create_suwayomi_category(name):
    query = """
    mutation($name: String!) {
        createCategory(input: { name: $name }) { id name }
    }
    """
    return graphql_request(query, {"name": name})

def assign_gallery_to_category(gallery_id, category_id):
    query = """
    mutation($galleryId: Int!, $categoryId: Int!) {
        assignGalleryToCategory(galleryId: $galleryId, categoryId: $categoryId)
    }
    """
    return graphql_request(query, {"galleryId": gallery_id, "categoryId": category_id})


#!/usr/bin/env python3
# nhscraper/graphql_api.py
# DESCRIPTION: Handles GraphQL calls to Suwayomi
# Called by: downloader.py
# Calls: None
# FUNCTION: Update gallery metadata in Suwayomi via GraphQL

import time

def update_gallery_graphql(gallery_id, attempt=0):
    """
    Push gallery info to Suwayomi GraphQL API with exponential backoff
    """
    try:
        # placeholder: actual GraphQL call
        pass
    except Exception:
        if attempt < 5:
            time.sleep(2 ** attempt)
            update_gallery_graphql(gallery_id, attempt + 1)