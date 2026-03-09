#!/usr/bin/env python3
# mangascraper/core/database.py

import os, sqlite3, threading, atexit, json, time, random, cloudscraper, requests, re, socket, urllib.parse
from datetime import datetime, timezone
from urllib.parse import urljoin
from pathlib import Path
from tqdm import tqdm

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *

################################################################################################################
# GLOBAL VARIABLES
################################################################################################################

lock = threading.Lock()
_thread_local = threading.local()

DATA_DIR = os.path.join(SCRAPER_DIR, "mangascraper/core/data")
DB_PATH = os.path.join(DATA_DIR, "mangascraper.db")

# Cache TTL: 3 hours (runtime-configured)
TTL = getattr(orchestrator, "metadata_ttl", 3 * 60 * 60)
SEARCH_HISTORY_FILENAME = "(search_history).json"
SEARCH_HISTORY_MAX = 10

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
    """Loads and returns the Cached Metadata keyed by IDs in a given list."""
    
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
    """Write a list of Gallery IDs into the database gallery queue"""
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
    """Get the status of a Gallery keyed by its ID"""
    init_db()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM Galleries WHERE id=?", (gallery_id,))
        row = cursor.fetchone()
        return row[0] if row else None

def list_galleries(status=None):
    """
    List all Galleries that match a certain status.
    
    Statues:
    -   started
    -   skipped
    -   completed
    """
    
    init_db()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries WHERE status=?", (status,))
        else:
            cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries")
        return cursor.fetchall()


################################################################################################################
# CACHING HELPERS
################################################################################################################

def upsert_cache_metadata(gallery_id: str, timestamp: float, clean_metadata=None, raw_metadata=None):
    init_db()
    entry = cached_metadata_entry(gallery_id) or {
        "timestamp": None,
        "clean_metadata": {},
        "raw_metadata": {},
    }
    if isinstance(clean_metadata, dict):
        entry["clean_metadata"].update(clean_metadata)
    if raw_metadata is not None:
        entry["raw_metadata"] = raw_metadata
    entry["timestamp"] = timestamp

    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO CachedMetadata (gallery_id, timestamp, clean_metadata, raw_metadata) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(gallery_id) DO UPDATE SET "
            "timestamp=excluded.timestamp, "
            "clean_metadata=excluded.clean_metadata, "
            "raw_metadata=excluded.raw_metadata",
            (
                str(gallery_id),
                entry["timestamp"],
                json.dumps(entry["clean_metadata"], ensure_ascii=False),
                json.dumps(entry["raw_metadata"], ensure_ascii=False),
            ),
        )
        conn.commit()

def prune_cache_metadata(cutoff: float):
    init_db()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM CachedMetadata WHERE timestamp IS NULL OR timestamp < ?",
            (cutoff,),
        )
        conn.commit()

# ===============================
# CACHE REFERENCES
# ===============================

def load_cache_references() -> dict:
    init_db()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT entry_key, cache_type, cache_key, path, size, last_read, last_write, ttl, expires_at, ids "
            "FROM CacheReferences"
        )
        rows = cursor.fetchall()
    result = {}
    for row in rows:
        (
            entry_key,
            cache_type,
            cache_key,
            path,
            size,
            last_read,
            last_write,
            ttl,
            expires_at,
            ids_json,
        ) = row
        entry = {
            "type": cache_type,
            "key": cache_key,
            "path": path,
            "size": size,
            "last_read": last_read,
            "last_write": last_write,
            "ttl": ttl,
            "expires_at": expires_at,
        }
        if ids_json:
            try:
                entry["ids"] = json.loads(ids_json)
            except Exception:
                entry["ids"] = []
        result[str(entry_key)] = entry
    return result

def upsert_cache_reference(entry_key: str, entry: dict):
    init_db()
    ids = entry.get("ids")
    if not isinstance(ids, list):
        ids = [] if ids is None else [ids]
    ids_json = json.dumps(ids)
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO CacheReferences (entry_key, cache_type, cache_key, path, size, last_read, last_write, ttl, expires_at, ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(entry_key) DO UPDATE SET "
            "cache_type=excluded.cache_type, "
            "cache_key=excluded.cache_key, "
            "path=excluded.path, "
            "size=excluded.size, "
            "last_read=excluded.last_read, "
            "last_write=excluded.last_write, "
            "ttl=excluded.ttl, "
            "expires_at=excluded.expires_at, "
            "ids=excluded.ids",
            (
                str(entry_key),
                entry.get("type"),
                entry.get("key"),
                entry.get("path"),
                entry.get("size"),
                entry.get("last_read"),
                entry.get("last_write"),
                entry.get("ttl"),
                entry.get("expires_at"),
                ids_json,
            ),
        )
        conn.commit()

def delete_cache_reference(entry_key: str):
    init_db()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM CacheReferences WHERE entry_key = ?", (str(entry_key),))
        conn.commit()

def prune_cache_references(now: float):
    init_db()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM CacheReferences WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        conn.commit()

def get_cache_dir() -> Path:
    """
    Get or create cache directory for metadata.
    Uses /opt/manga-scraper/mangascraper/core/data/ if available,
    otherwise uses package-relative path as fallback.
    """
    primary_cache = Path("/opt/manga-scraper/mangascraper/core/data")
    if primary_cache.parent.exists():
        primary_cache.mkdir(parents=True, exist_ok=True)
        return primary_cache
    fallback_cache = Path(__file__).parent / "data"
    fallback_cache.mkdir(parents=True, exist_ok=True)
    return fallback_cache

def _load_master_cache() -> dict:
    cutoff = time.time() - TTL
    try:
        # Prune metadata entries in the master cache by their own TTL
        prune_cache_metadata(cutoff)
        
        # Prune references to cache files by their own TTL
        prune_cache_references(time.time())
        
        metadata_block = all_cached_metadata(cutoff)
        
        # Remove expired entries from metadata_block (in-memory prune)
        if isinstance(metadata_block, dict):
            expired_keys = []
            for gid, entry in metadata_block.items():
                if not isinstance(entry, dict):
                    continue
                timestamp = entry.get("timestamp") or 0
                if (time.time() - timestamp) >= TTL:
                    expired_keys.append(gid)
            for gid in expired_keys:
                metadata_block.pop(gid, None)
        references = load_cache_references()
        return {"references": references, "metadata": metadata_block}
    except Exception:
        return {"references": {}, "metadata": {}}

def _save_master_cache(data: dict):
    return

def _update_master_cache(entry_key: str, entry: dict):
    upsert_cache_reference(entry_key, entry)

def _remove_master_cache_entry(entry_key: str):
    delete_cache_reference(entry_key)
    
def prune_all_caches():
    """Centralised cache pruning for metadata, references, and files."""
    now = time.time()
    # Prune metadata entries in the master cache by their own TTL
    prune_cache_metadata(now - TTL)
    # Prune references to cache files by their own TTL
    prune_cache_references(now)

    cache_dir = get_cache_dir()
    protected = {SEARCH_HISTORY_FILENAME}
    for cache_file in cache_dir.glob("*.json"):
        if cache_file.name == "(master_cache).json":
            try:
                cache_file.unlink()
            except Exception:
                pass
            continue
        if cache_file.name in protected:
            continue
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            timestamp = data.get("timestamp") or 0
            if (now - timestamp) >= TTL:
                # Remove master cache reference if present
                cache_key = cache_file.stem
                _remove_master_cache_entry(f"metadata:{cache_key}")
                cache_file.unlink()
        except Exception:
            continue

def cached_metadata_entry(gallery_id: str) -> dict | None:
        """Loads and returns metadata for a single gallery ID, or None if it doesn't exist."""
        init_db()
        with lock, dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT timestamp, clean_metadata, raw_metadata FROM CachedMetadata WHERE gallery_id = ?",
                (str(gallery_id),),
            )
            row = cursor.fetchone()
        if not row:
            return None
        timestamp, clean_json, raw_json = row
        clean = json.loads(clean_json) if clean_json else {}
        raw = json.loads(raw_json) if raw_json else {}
        return {"timestamp": timestamp, "clean_metadata": clean, "raw_metadata": raw}

def all_cached_metadata(cutoff: float | None = None) -> dict:
        """Loads and returns metadata for all (or recent) galleries as a dictionary keyed by gallery ID."""
        init_db()
        with lock, dbconnect() as conn:
            cursor = conn.cursor()
            if cutoff is not None:
                cursor.execute(
                    "SELECT gallery_id, timestamp, clean_metadata, raw_metadata "
                    "FROM CachedMetadata WHERE timestamp >= ?",
                    (cutoff,),
                )
            else:
                cursor.execute(
                    "SELECT gallery_id, timestamp, clean_metadata, raw_metadata FROM CachedMetadata"
                )
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