#!/usr/bin/env python3
# mangascraper/core/database.py

import os, sqlite3, threading, atexit, json
from datetime import datetime, timezone

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *

####################################################################################################################
# DB INITIALISATION
####################################################################################################################

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

def init_db():
    orchestrator.refresh_globals()
    os.makedirs(DATA_DIR, exist_ok=True)
    with lock, _connect() as conn:
        c = conn.cursor()
        c.executescript(f"""
        CREATE TABLE IF NOT EXISTS GalleriesQueue (
            id INTEGER PRIMARY KEY
        );
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
# CONSOLIDATED UPDATERS
####################################################################################################################

########################################################################################################
# Upsert gallery and mapping tables
########################################################################################################

def upsert_gallery(
    gallery_id,
    raw_title,
    clean_title,
    num_pages,
    creator_ids,
    language_ids,
    tag_ids,
    status=None,
    started_at=None,
    completed_at=None,
    download_path=None,
    cover_path=None,
    extension_used=None
):
    """
    Insert or update a gallery and its mapping tables (GalleryTags, GalleryLanguages).
    """
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Galleries (id, raw_title, clean_title, num_pages, creator_ids, language_ids, tag_ids, status, started_at, completed_at, download_path, cover_path, extension_used) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET raw_title=excluded.raw_title, clean_title=excluded.clean_title, num_pages=excluded.num_pages, creator_ids=excluded.creator_ids, language_ids=excluded.language_ids, tag_ids=excluded.tag_ids, status=excluded.status, started_at=excluded.started_at, completed_at=excluded.completed_at, download_path=excluded.download_path, cover_path=excluded.cover_path, extension_used=excluded.extension_used",
            (
                gallery_id,
                raw_title,
                clean_title,
                num_pages,
                json.dumps(creator_ids),
                json.dumps(language_ids),
                json.dumps(tag_ids),
                status,
                started_at,
                completed_at,
                download_path,
                cover_path,
                extension_used
            )
        )
        cursor.execute("INSERT OR REPLACE INTO GalleryTags (gallery_id, tag_ids) VALUES (?, ?)", (gallery_id, json.dumps(tag_ids)))
        cursor.execute("INSERT OR REPLACE INTO GalleryLanguages (gallery_id, language_ids) VALUES (?, ?)", (gallery_id, json.dumps(language_ids)))
        conn.commit()

########################################################################################################
# Update stats for creators
########################################################################################################

def update_creator_stats(creator_id=None):
    """
    Update total_galleries, most_popular_tags, display_name, first_seen, last_updated for creators.
    If creator_id is None, update all creators.
    """
    now = datetime.now(timezone.utc).isoformat()
    with lock, _connect() as conn:
        cursor = conn.cursor()
        if creator_id is None:
            cursor.execute("SELECT id, name FROM Creators")
            creators = cursor.fetchall()
        else:
            cursor.execute("SELECT id, name FROM Creators WHERE id=?", (creator_id,))
            creators = cursor.fetchall()
        for cid, name in creators:
            # display_name: cleaned version of name (for now, just use name; replace with cleaning logic if needed)
            display_name = name
            # total_galleries
            cursor.execute("""
                SELECT COUNT(*) FROM Galleries
                WHERE EXISTS (
                    SELECT 1 FROM json_each(Galleries.creator_ids)
                    WHERE json_each.value = ?
                )
            """, (cid,))
            total_galleries = cursor.fetchone()[0]
            # most_popular_tags
            cursor.execute("""
                SELECT tag_ids FROM GalleryTags WHERE gallery_id IN (
                    SELECT id FROM Galleries
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(Galleries.creator_ids)
                        WHERE json_each.value = ?
                    )
                )
            """, (cid,))
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
            # first_seen
            cursor.execute("SELECT first_seen FROM Creators WHERE id=?", (cid,))
            first_seen = cursor.fetchone()[0]
            if not first_seen:
                first_seen = now
            # last_updated
            last_updated = now
            cursor.execute(
                "UPDATE Creators SET display_name=?, total_galleries=?, most_popular_tags=?, first_seen=?, last_updated=? WHERE id=?",
                (display_name, total_galleries, json.dumps(most_popular_tag_ids), first_seen, last_updated, cid)
            )
        conn.commit()

########################################################################################################
# Update tag counts
########################################################################################################

def update_tag_stats(tag_id=None):
    """
    Update count for tags (number of galleries using the tag). If tag_id is None, update all tags.
    """
    with lock, _connect() as conn:
        cursor = conn.cursor()
        if tag_id is None:
            cursor.execute("SELECT id FROM Tags")
            tag_ids = [row[0] for row in cursor.fetchall()]
        else:
            tag_ids = [tag_id]
        for tid in tag_ids:
            cursor.execute("SELECT tag_ids FROM GalleryTags")
            count = 0
            for (tag_ids_json,) in cursor.fetchall():
                if tag_ids_json:
                    try:
                        tag_ids_list = json.loads(tag_ids_json)
                        count += tag_ids_list.count(tid)
                    except Exception:
                        continue
            cursor.execute("UPDATE Tags SET count=? WHERE id=?", (count, tid))
        conn.commit()

########################################################################################################
# Update language counts
########################################################################################################

def update_language_stats(language_id=None):
    """
    Update count for languages (number of galleries using the language). If language_id is None, update all languages.
    """
    with lock, _connect() as conn:
        cursor = conn.cursor()
        if language_id is None:
            cursor.execute("SELECT id FROM Languages")
            lang_ids = [row[0] for row in cursor.fetchall()]
        else:
            lang_ids = [language_id]
        for lid in lang_ids:
            cursor.execute("SELECT language_ids FROM GalleryLanguages")
            count = 0
            for (lang_ids_json,) in cursor.fetchall():
                if lang_ids_json:
                    try:
                        lang_ids_list = json.loads(lang_ids_json)
                        count += lang_ids_list.count(lid)
                    except Exception:
                        continue
            cursor.execute("UPDATE Languages SET count=? WHERE id=?", (count, lid))
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

# ===============================
# DOWNLOAD HELPERS
# ===============================

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

def get_queued_galleries():
    init_db()
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM GalleriesQueue")
        rows = cursor.fetchall()
        return sorted({int(row[0]) for row in rows})

def set_queued_galleries(ids):
    init_db()
    with lock, _connect() as conn:
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