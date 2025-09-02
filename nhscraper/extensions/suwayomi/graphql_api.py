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