#!/usr/bin/env python3
# mangascraper/core/database.py

import os, sqlite3, threading, atexit, json

from datetime import datetime, timezone

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *

DATA_DIR = os.path.join(SCRAPER_DIR, "mangascraper/core/data")
DB_PATH = os.path.join(DATA_DIR, "mangascraper.db")
lock = threading.Lock()
_thread_local = threading.local()


def _connect():
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

# ===============================
# DB INITIALISATION
# ===============================
def init_db():
    orchestrator.refresh_globals()
    
    os.makedirs(DATA_DIR, exist_ok=True)
    with lock, _connect() as conn:
        c = conn.cursor()

        c.executescript("""
        CREATE TABLE IF NOT EXISTS Creators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            display_name TEXT,
            download_path TEXT,
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
            download_path TEXT,
            cover_path TEXT,
            extension_used TEXT,
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
            count TEXT -- JSON array: [{creator_id: count}, ...]
        );

        CREATE TABLE IF NOT EXISTS Languages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            popularity INTEGER
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
            clean_metadata TEXT,
            raw_metadata TEXT
        );

        CREATE TABLE IF NOT EXISTS CachedReferences (
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

####################################################################################################################
# Consolidated update
####################################################################################################################

# Creator metadata
def update_creator_metadata(creator_name, display_name, download_path=None):
    """
    Update all relevant fields for a creator, using the latest metadata.
    - display_name: cleaned creator name (from latest gallery metadata, fallback to CachedMetadata)
    - download_path: path to creator's download folder
    - first_seen: set if not already set
    - last_updated: always set to now
    - total_galleries: count of galleries linked to this creator
    - most_popular_tags: top 15 tags by count across all galleries for this creator
    """
    now = datetime.now(timezone.utc).isoformat()
    with lock, _connect() as conn:
        cursor = conn.cursor()
        # Upsert creator
        cursor.execute(
            "INSERT INTO Creators (name, display_name, download_path, first_seen, last_updated, total_galleries, most_popular_tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (creator_name, display_name, download_path, now, now, 0, "[]")
        )
        cursor.execute("SELECT id, first_seen FROM Creators WHERE name=?", (creator_name,))
        row = cursor.fetchone()
        if not row:
            return
        creator_id, first_seen = row
        # Count total galleries
        cursor.execute("SELECT COUNT(*) FROM Galleries WHERE creator_id=?", (creator_id,))
        total_galleries = cursor.fetchone()[0]
        # Calculate most popular tags (by tag id)
        cursor.execute("SELECT tag_ids FROM GalleryTags WHERE gallery_id IN (SELECT id FROM Galleries WHERE creator_id=?)", (creator_id,))
        tag_counts = {}
        for (tag_ids_json,) in cursor.fetchall():
            if tag_ids_json:
                try:
                    tag_ids = json.loads(tag_ids_json)
                    for tag_id in tag_ids:
                        tag_counts[tag_id] = tag_counts.get(tag_id, 0) + 1
                except Exception:
                    continue
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        most_popular_tag_ids = [tag_id for tag_id, _ in sorted_tags[:15]]
        # Update all fields except notes
        cursor.execute(
            "UPDATE Creators SET display_name=?, download_path=?, last_updated=?, total_galleries=?, most_popular_tags=?, first_seen=COALESCE(first_seen, ?) WHERE id=?",
            (display_name, download_path, now, total_galleries, json.dumps(most_popular_tag_ids), first_seen or now, creator_id)
        )
        # Update Tags table: update count field for each tag used by this creator
        for tag_id, count in tag_counts.items():
            cursor.execute("SELECT count FROM Tags WHERE id=?", (tag_id,))
            row = cursor.fetchone()
            creator_counts = []
            if row and row[0]:
                try:
                    creator_counts = json.loads(row[0])
                except Exception:
                    creator_counts = []
            # Remove any previous entry for this creator
            creator_counts = [d for d in creator_counts if str(creator_id) not in d]
            creator_counts.append({str(creator_id): count})
            cursor.execute("UPDATE Tags SET count=? WHERE id=?", (json.dumps(creator_counts), tag_id))
        conn.commit()

# Gallery metadata
def update_gallery_metadata(gallery_id, raw_title, clean_title, language, tags, cover_path, creator_name=None, download_path=None, extension_used=None, num_pages=None):
    """
    Update all relevant fields for a gallery, always overwriting with latest values.
    """
    with lock, _connect() as conn:
        cursor = conn.cursor()
        # Upsert creators, tags, languages, and get their ids
        creator_ids = []
        if creator_name:
            # Accepts a single creator or list
            names = creator_name if isinstance(creator_name, list) else [creator_name]
            for name in names:
                cursor.execute("INSERT OR IGNORE INTO Creators (name) VALUES (?)", (name,))
                cursor.execute("SELECT id FROM Creators WHERE name=?", (name,))
                row = cursor.fetchone()
                if row:
                    creator_ids.append(row[0])
        tag_ids = []
        for tag in tags or []:
            cursor.execute("INSERT OR IGNORE INTO Tags (name, count) VALUES (?, ?) ", (tag, "[]"))
            cursor.execute("SELECT id FROM Tags WHERE name=?", (tag,))
            row = cursor.fetchone()
            if row:
                tag_ids.append(row[0])
        language_ids = []
        for lang in language or []:
            cursor.execute("INSERT OR IGNORE INTO Languages (name, popularity) VALUES (?, ?) ", (lang, 0))
            cursor.execute("SELECT id FROM Languages WHERE name=?", (lang,))
            row = cursor.fetchone()
            if row:
                language_ids.append(row[0])
        # Insert/update Galleries
        cursor.execute(
            "INSERT INTO Galleries (id, raw_title, clean_title, num_pages, creator_ids, language_ids, tag_ids, download_path, cover_path, extension_used) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET raw_title=excluded.raw_title, clean_title=excluded.clean_title, num_pages=excluded.num_pages, creator_ids=excluded.creator_ids, language_ids=excluded.language_ids, tag_ids=excluded.tag_ids, download_path=excluded.download_path, cover_path=excluded.cover_path, extension_used=excluded.extension_used",
            (gallery_id, raw_title, clean_title, num_pages, json.dumps(creator_ids), json.dumps(language_ids), json.dumps(tag_ids), download_path, cover_path, extension_used)
        )
        # Update GalleryTags and GalleryLanguages
        cursor.execute("INSERT OR REPLACE INTO GalleryTags (gallery_id, tag_ids) VALUES (?, ?)", (gallery_id, json.dumps(tag_ids)))
        cursor.execute("INSERT OR REPLACE INTO GalleryLanguages (gallery_id, language_ids) VALUES (?, ?)", (gallery_id, json.dumps(language_ids)))
        conn.commit()

# Update or insert a language and its popularity
def update_language_metadata(language_name, popularity=0):
    """
    Update or insert a language and its popularity.
    """
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Languages (name, popularity) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET popularity=excluded.popularity",
            (language_name, popularity)
        )
        conn.commit()

# Update or insert a tag and its count (per creator)
def update_tag_metadata(tag_name, creator_id=None, count=1):
    """
    Update or insert a tag and increment/update its count for a creator.
    """
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Tags (name, count) VALUES (?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (tag_name, "[]")
        )
        cursor.execute("SELECT id, count FROM Tags WHERE name=?", (tag_name,))
        row = cursor.fetchone()
        if not row:
            return
        tag_id, count_json = row
        creator_counts = []
        if count_json:
            try:
                creator_counts = json.loads(count_json)
            except Exception:
                creator_counts = []
        if creator_id is not None:
            # Remove any previous entry for this creator
            creator_counts = [d for d in creator_counts if str(creator_id) not in d]
            creator_counts.append({str(creator_id): count})
            cursor.execute("UPDATE Tags SET count=? WHERE id=?", (json.dumps(creator_counts), tag_id))
        conn.commit()

# GENERIC FIELD UPDATE HELPERS
def update_field(table, key_field, key_value, field, value):
    """
    Update a single field in a table for a given key.
    Example: update_field('Creators', 'name', 'John Doe', 'display_name', 'John D.')
    """
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET {field}=? WHERE {key_field}=?",
            (value, key_value)
        )
        conn.commit()

####################################################################################################################
# GALLERY UPDATES
####################################################################################################################

def mark_gallery_started(gallery_id, download_path=None, extension_used=None):
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with lock, _connect() as conn:
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
    with lock, _connect() as conn:
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
    with lock, _connect() as conn:
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
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE Galleries
        SET status = ?, completed_at = ?
        WHERE id = ?
        """, ("completed", now, gallery_id))
        # Update language, tags, cover_path fields
        # (Assume latest values are passed in via other helpers)
        # This is a placeholder; actual update should be done via a dedicated update_gallery_metadata function
        conn.commit()

def get_gallery_status(gallery_id):
    init_db()
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM Galleries WHERE id=?", (gallery_id,))
        row = cursor.fetchone()
        return row[0] if row else None

def list_galleries(status=None):
    init_db()
    with lock, _connect() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries WHERE status=?", (status,))
        else:
            cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries")
        return cursor.fetchall()
    
# ===============================
# BROKEN SYMBOLS MANAGEMENT
# ===============================
def load_broken_symbols() -> dict[str, str]:
    """Load all detected broken symbols as { symbol: '_' }."""
    init_db()
    with lock, _connect() as conn:
        c = conn.cursor()
        c.execute("SELECT symbol FROM BrokenSymbols WHERE fixed=0")
        rows = c.fetchall()
        return {row[0]: "_" for row in rows if row[0].strip()}

def save_broken_symbols(symbol_map: dict[str, str]):
    """Insert or update broken symbols into the database, keeping the mapping (symbol -> replacement)."""
    if not symbol_map:
        return
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with lock, _connect() as conn:
        c = conn.cursor()
        for symbol in symbol_map.keys():
            c.execute("""
                INSERT INTO BrokenSymbols (symbol, date_detected, fixed)
                VALUES (?, ?, 0)
                ON CONFLICT(symbol) DO UPDATE SET
                    fixed=0,
                    date_detected=excluded.date_detected
            """, (symbol, now))
        conn.commit()

# ===============================
# CACHE METADATA
# ===============================
def load_cache_metadata_all(cutoff: float | None = None) -> dict:
    init_db()
    with lock, _connect() as conn:
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

def load_cache_metadata_for_ids(ids: list[int], cutoff: float | None = None) -> dict:
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
    with lock, _connect() as conn:
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

def load_cache_metadata_entry(gallery_id: str) -> dict | None:
    init_db()
    with lock, _connect() as conn:
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

def upsert_cache_metadata(gallery_id: str, timestamp: float, clean_metadata=None, raw_metadata=None):
    init_db()
    entry = load_cache_metadata_entry(gallery_id) or {
        "timestamp": None,
        "clean_metadata": {},
        "raw_metadata": {},
    }
    if isinstance(clean_metadata, dict):
        entry["clean_metadata"].update(clean_metadata)
    if raw_metadata is not None:
        entry["raw_metadata"] = raw_metadata
    entry["timestamp"] = timestamp

    with lock, _connect() as conn:
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
    with lock, _connect() as conn:
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
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT entry_key, cache_type, cache_key, path, size, last_read, last_write, ttl, expires_at, ids "
            "FROM CachedReferences"
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
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO CachedReferences (entry_key, cache_type, cache_key, path, size, last_read, last_write, ttl, expires_at, ids) "
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
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM CachedReferences WHERE entry_key = ?", (str(entry_key),))
        conn.commit()

def prune_cache_references(now: float):
    init_db()
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM CachedReferences WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        conn.commit()