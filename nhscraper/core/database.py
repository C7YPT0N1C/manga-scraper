#!/usr/bin/env python3
# nhscraper/core/database.py

import os, sqlite3, threading

from datetime import datetime, timezone

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *

DB_PATH = os.path.join(SCRAPER_DIR, "nhscraper/core/nhscraper.db")
lock = threading.Lock()

# ===============================
# DB INITIALISATION
# ===============================
def init_db():
    fetch_env_vars() # Refresh env vars in case config changed.
    
    os.makedirs(SCRAPER_DIR, exist_ok=True)
    with lock, sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()

        c.executescript("""
        CREATE TABLE IF NOT EXISTS Creators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            display_name TEXT,
            most_popular_tags TEXT,
            first_seen TEXT,
            last_updated TEXT,
            download_path TEXT,
            total_galleries INTEGER,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS Galleries (
            id INTEGER PRIMARY KEY,
            creator_id INTEGER,
            raw_title TEXT,
            clean_title TEXT,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            extension_used TEXT,
            num_pages INTEGER,
            language TEXT,
            tags TEXT,
            download_path TEXT,
            cover_path TEXT,
            favourite INTEGER DEFAULT 0,
            rating REAL,
            FOREIGN KEY (creator_id) REFERENCES Creators(id)
        );

        CREATE TABLE IF NOT EXISTS Tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            type TEXT,
            popularity INTEGER
        );

        CREATE TABLE IF NOT EXISTS GalleryTags (
            gallery_id INTEGER,
            tag_id INTEGER,
            PRIMARY KEY (gallery_id, tag_id),
            FOREIGN KEY (gallery_id) REFERENCES Galleries(id),
            FOREIGN KEY (tag_id) REFERENCES Tags(id)
        );

        CREATE TABLE IF NOT EXISTS BrokenSymbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE,
            example_occurrences TEXT,
            date_detected TEXT,
            fixed INTEGER DEFAULT 0
        );
        """)

        conn.commit()

# ===============================
# UTILITY FUNCTIONS
# ===============================
def mark_gallery_started(gallery_id, download_path=None, extension_used=None):
    init_db()
    now = datetime.utcnow().isoformat()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO Galleries (id, status, started_at, download_path, extension_used)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            started_at=excluded.started_at,
            download_path=excluded.download_path,
            extension_used=excluded.extension_used
        """, (gallery_id, "started", now, download_path, extension_used))
        conn.commit()

def mark_gallery_skipped(gallery_id):
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE Galleries
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
        UPDATE Galleries
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
        UPDATE Galleries
        SET status = ?, completed_at = ?
        WHERE id = ?
        """, ("completed", now, gallery_id))
        conn.commit()

def get_gallery_status(gallery_id):
    init_db()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM Galleries WHERE id=?", (gallery_id,))
        row = cursor.fetchone()
        return row[0] if row else None

def list_galleries(status=None):
    init_db()
    with lock, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries WHERE status=?", (status,))
        else:
            cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries")
        return cursor.fetchall()

log_clarification("debug")
logger.debug("Database: Ready.")
log("Database: Debugging Started.", "debug")