#!/usr/bin/env python3
# mangascraper/core/database.py

import os, sqlite3, threading, atexit, json

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *

################################################################################################################
# GLOBAL VARIABLES
################################################################################################################

DATA_DIR = os.path.join(SCRAPER_DIR, "mangascraper/core/data")
DB_PATH = os.path.join(DATA_DIR, "mangascraper.db")
lock = threading.Lock()
_thread_local = threading.local()

####################################################################################################################
# DB INITIALISATION
####################################################################################################################

def dbconnect():
    conn = getattr(_thread_local, "connection", None)
    if conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        _thread_local.connection = conn
    return conn

def close_connection():
    conn = getattr(_thread_local, "connection", None)
    if conn is not None:
        conn.close()
        _thread_local.connection = None

atexit.register(close_connection)

def init_db():
    orchestrator.refresh_globals()
    os.makedirs(DATA_DIR, exist_ok=True)
    with lock, dbconnect() as conn:
        c = conn.cursor()
        c.executescript(f"""
        CREATE TABLE IF NOT EXISTS GalleriesQueue (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS Creators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            display_name TEXT,
            creator_type TEXT,
            first_seen TEXT,
            last_updated TEXT,
            total_galleries INTEGER,
            most_popular_tags TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS Galleries (
            id INTEGER PRIMARY KEY,
            raw_title TEXT,
            clean_title TEXT,
            num_pages INTEGER,
            creator_ids TEXT, -- JSON array of creator ids
            language_ids TEXT, -- JSON array of language ids
            tag_ids TEXT, -- JSON array of tag ids
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            extension_used TEXT,
            download_path TEXT,
            cover_path TEXT,
            favourite INTEGER DEFAULT 0,
            rating REAL
        );

        CREATE TABLE IF NOT EXISTS GalleryTags (
            gallery_id INTEGER PRIMARY KEY,
            tag_ids TEXT,
            FOREIGN KEY (gallery_id) REFERENCES Galleries(id)
        );

        CREATE TABLE IF NOT EXISTS GalleryLanguages (
            gallery_id INTEGER PRIMARY KEY,
            language_ids TEXT,
            FOREIGN KEY (gallery_id) REFERENCES Galleries(id)
        );

        CREATE TABLE IF NOT EXISTS Tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            count INTEGER
        );

        CREATE TABLE IF NOT EXISTS Languages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            count INTEGER
        );

        CREATE TABLE IF NOT EXISTS BrokenSymbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE,
            date_detected TEXT,
            fixed INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS CachedMetadata (
            gallery_id TEXT PRIMARY KEY,
            timestamp REAL,
            raw_metadata TEXT,
            clean_metadata TEXT
        );

        CREATE TABLE IF NOT EXISTS CacheReferences (
            entry_key TEXT PRIMARY KEY,
            cache_type TEXT,
            cache_key TEXT,
            path TEXT,
            size INTEGER,
            last_read REAL,
            last_write REAL,
            ttl INTEGER,
            expires_at REAL,
            ids TEXT
        );
        """)
        conn.commit()

################################################################################################################
# DATABASE HELPERS
################################################################################################################

def load_cached_metadata_for_ids(ids: list[int], cutoff: float | None = None) -> dict:
    """Loads and returns the Cached Metadata corresponding to each ID in a given list"""
    if not ids:
        return {}
    init_db()
    ids = [str(gid) for gid in ids]
    placeholders = ",".join("?" for _ in ids)
    params = list(ids)
    query = (
        "SELECT gallery_id, timestamp, clean_metadata, raw_metadata "
        "FROM CachedMetadata WHERE gallery_id IN (" + placeholders + ")"
    )
    if cutoff is not None:
        query += " AND timestamp >= ?"
        params.append(cutoff)
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
    result = {}
    for gallery_id, timestamp, clean_json, raw_json in rows:
        clean = json.loads(clean_json) if clean_json else {}
        raw = json.loads(raw_json) if raw_json else {}
        result[str(gallery_id)] = {
            "timestamp": timestamp,
            "clean_metadata": clean,
            "raw_metadata": raw,
        }
    return result

####################################################################################################################
# OTHER DATABASE HELPERS
####################################################################################################################

def set_queued_galleries(ids):
    init_db()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM GalleriesQueue")
        for gid in set(ids or []):
            try:
                cursor.execute("INSERT INTO GalleriesQueue (id) VALUES (?)", (int(gid),))
            except Exception:
                continue
        conn.commit()

def get_gallery_status(gallery_id):
    init_db()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM Galleries WHERE id=?", (gallery_id,))
        row = cursor.fetchone()
        return row[0] if row else None

def list_galleries(status=None):
    init_db()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries WHERE status=?", (status,))
        else:
            cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries")
        return cursor.fetchall()