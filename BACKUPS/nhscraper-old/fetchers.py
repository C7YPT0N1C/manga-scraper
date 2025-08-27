#!/usr/bin/env python3
# nhscraper/fetchers.py
# DESCRIPTION: Contains all NHentai fetching logic.
# Called by: downloader.py
# Calls: None
# FUNCTION: Retrieve gallery metadata and gallery lists from NHentai

import requests

def fetch_gallery_metadata(gallery_id):
    """
    Fetch metadata for a gallery via NHentai API.
    Returns dict or None if failed.
    """
    try:
        r = requests.get(f"https://nhentai.net/api/gallery/{gallery_id}")
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None

def fetch_galleries_by_creator(creator_type, name, start=None, end=None):
    """
    Fetch a list of gallery IDs by artist/group/category.
    """
    # placeholder: implement pagination and actual scraping
    return []