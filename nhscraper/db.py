#!/usr/bin/env python3
# nhscraper/db.py
# DESCRIPTION: SQLite database management for gallery state
# Called by: downloader.py, nhscraper_api.py
# Calls: sqlite3
# FUNCTION: Track gallery metadata and download/GraphQL status

import sqlite3
import json
from datetime import datetime

DB_FILE = "/opt/nhentai-scraper/nhscraper.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS galleries (
            id INTEGER PRIMARY KEY,
            meta TEXT,
            download_status TEXT,
            graphql_status TEXT,
            download_attempts INTEGER,
            graphql_attempts INTEGER,
            last_checked TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_gallery_state(gallery_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT meta, download_status, graphql_status FROM galleries WHERE id=?", (gallery_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"meta": json.loads(row[0]), "download_status": row[1], "graphql_status": row[2]}
    return None

def update_gallery_state(gallery_id, key, value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"UPDATE galleries SET {key}=? WHERE id=?", (value, gallery_id))
    conn.commit()
    conn.close()