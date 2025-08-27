#!/usr/bin/env python3
# nhscraper/downloader.py
# DESCRIPTION: Handles gallery downloading and processing.
# Called by: cli.py
# Calls: fetchers.py, graphql_api.py, db.py, config.py
# FUNCTION: Main scraping engine: downloads galleries, updates DB, calls GraphQL API

import os
import threading
from nhscraper.config import config
from nhscraper.db import get_gallery_state, update_gallery_state
from nhscraper.fetchers import fetch_gallery_metadata
from nhscraper.graphql_api import update_gallery_graphql
from tqdm import tqdm

running_galleries = []

def process_gallery(gallery_id):
    """
    Download gallery pages, update DB, and GraphQL.
    """
    running_galleries.append(gallery_id)
    meta = fetch_gallery_metadata(gallery_id)

    if not meta:
        update_gallery_state(gallery_id, "download_status", "failed")
        running_galleries.remove(gallery_id)
        return

    # process locally (images etc)
    # ... downloading logic here

    # Update DB atomically
    update_gallery_state(gallery_id, "download_status", "completed")
    update_gallery_graphql(gallery_id)

    running_galleries.remove(gallery_id)

def main(cfg):
    """
    Iterate gallery list (from config) and process concurrently.
    """
    gallery_list = cfg.get("galleries", [])

    threads = []
    for gid in gallery_list:
        t = threading.Thread(target=process_gallery, args=(gid,))
        threads.append(t)
        t.start()

        if len(threads) >= cfg.get("threads_galleries", 1):
            for th in threads:
                th.join()
            threads = []

    # join remaining threads
    for th in threads:
        th.join()