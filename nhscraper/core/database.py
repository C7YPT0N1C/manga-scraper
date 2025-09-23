#!/usr/bin/env python3
# nhscraper/core/db.py

import os, sqlite3, threading
from datetime import datetime, timezone

from nhscraper.core.config import *

DB_PATH = os.path.join(SCRAPER_DIR, "nhscraper/core/nhscraper.db")
lock = threading.Lock()

# ===============================
# DB INITIALISATION
# ===============================
def init_db():
    """
    This is one this module's entrypoints.
    """

    global session

    log_clarification()
    logger.debug("Database: Ready.")
    log("Database: Debugging Started.", "debug")
    
    fetch_env_vars() # Refresh env vars in case config changed.
    
    os.makedirs(SCRAPER_DIR, exist_ok=True)
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS galleries (
            id INTEGER PRIMARY KEY,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            download_location TEXT,
            extension_used TEXT
        )
        """)
        conn.commit()

# ===============================
# UTILITY FUNCTIONS
# ===============================
def mark_gallery_started(gallery_id, download_location=None, extension_used=None):
    init_db()
    now = datetime.utcnow().isoformat()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO galleries (id, status, started_at, download_location, extension_used)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            started_at=excluded.started_at,
            download_location=excluded.download_location,
            extension_used=excluded.extension_used
        """, (gallery_id, "started", now, download_location, extension_used))
        conn.commit()

def mark_gallery_skipped(gallery_id):
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE galleries
        SET status = ?, completed_at = ?
        WHERE id = ?
        """, ("skipped", now, gallery_id))
        conn.commit()

def mark_gallery_failed(gallery_id):
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE galleries
        SET status = ?, completed_at = ?
        WHERE id = ?
        """, ("failed", now, gallery_id))
        conn.commit()

def mark_gallery_completed(gallery_id):
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE galleries
        SET status = ?, completed_at = ?
        WHERE id = ?
        """, ("completed", now, gallery_id))
        conn.commit()

def get_gallery_status(gallery_id):
    init_db()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM galleries WHERE id=?", (gallery_id,))
        row = cursor.fetchone()
        return row[0] if row else None

def list_galleries(status=None):
    init_db()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT id, status, started_at, completed_at FROM galleries WHERE status=?", (status,))
        else:
            cursor.execute("SELECT id, status, started_at, completed_at FROM galleries")
        return cursor.fetchall()