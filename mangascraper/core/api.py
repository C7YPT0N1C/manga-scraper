#!/usr/bin/env python3
# mangascraper/core/api.py

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

# Cache TTL: 3 hours (runtime-configured)
TTL = getattr(orchestrator, "metadata_ttl", 3 * 60 * 60)
SEARCH_HISTORY_FILENAME = "(search_history).json"
SEARCH_HISTORY_MAX = 10

possible_broken_symbols_lock = threading.Lock()

# Pre-compile regex patterns for title cleaning (avoid recompilation on every call)
_BRACKET_PATTERN = re.compile(r"(\[.*?\]|\{.*?\})")
_DASH_PATTERN = re.compile(r"\s*[–—-]\s*")
_UNDERSCORE_PATTERN = re.compile(r"_+")

# Pre-build symbol translation table for faster replacements
_SYMBOL_TRANSLATION_TABLE = None

session = None
session_lock = threading.Lock()

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
# GALLERY UPDATES
####################################################################################################################

# GENERIC FIELD UPDATE HELPERS
def update_field(table, key_field, key_value, field, value):
    """
    Update a single field in a table for a given key.
    Example: update_field('Creators', 'name', 'John Doe', 'display_name', 'John D.')
    """
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with lock, _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET {field}=? WHERE {key_field}=?",
            (value, key_value)
        )
        conn.commit()

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
        conn.commit()

    # Now process all main tables for this gallery
    cache = load_cache_metadata_for_ids([gallery_id])
    creators = {}
    tags = {}
    languages = {}
    galleries = {}
    gallery_tags = {}
    gallery_languages = {}

    for gid, entry in cache.items():
        logger.debug(f"[DATABASE] Processing gallery {gid} with metadata: {entry}")
        
        meta = entry.get("clean_metadata") or {}
        raw_title = meta.get("raw_title") or meta.get("title") or f"Gallery_{gid}"
        clean_title = meta.get("clean_title") or meta.get("title") or f"Gallery_{gid}"
        num_pages = meta.get("num_pages") or meta.get("pages") or 0
        
        # Creator Names
        creator_names = []
        if "artists" in meta and isinstance(meta["artists"], list):
            creator_names.extend(meta["artists"])
        if "groups" in meta and isinstance(meta["groups"], list):
            creator_names.extend(meta["groups"])
        
        # Tags
        tag_names = meta.get("tags") or []
        if isinstance(tag_names, str):
            tag_names = [tag_names]
        
        # Languages
        language_names = meta.get("languages") or meta.get("language") or []
        if isinstance(language_names, str):
            language_names = [language_names]
        
        status = meta.get("status")
        started_at = meta.get("started_at")
        completed_at = meta.get("completed_at")
        download_path = meta.get("download_path")
        cover_path = meta.get("cover_path")
        extension_used = meta.get("extension_used")

        logger.debug(f"[DATABASE] Gallery fields: raw_title={raw_title}, clean_title={clean_title}, num_pages={num_pages}, creators={creator_names}, tags={tag_names}, languages={language_names}")

        for cname in creator_names:
            creators.setdefault(cname, {"display_name": cname, "first_seen": None, "last_updated": None, "total_galleries": 0, "most_popular_tags": []})
        for tname in tag_names:
            tags.setdefault(tname, {"count": 0})
        for lname in language_names:
            languages.setdefault(lname, {"count": 0})

        galleries[gid] = {
            "id": gid,
            "raw_title": raw_title,
            "clean_title": clean_title,
            "num_pages": num_pages,
            "creator_names": creator_names,
            "language_names": language_names,
            "tag_names": tag_names,
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "download_path": download_path,
            "cover_path": cover_path,
            "extension_used": extension_used
        }
        gallery_tags[gid] = tag_names
        gallery_languages[gid] = language_names

    with lock, _connect() as conn:
        cursor = conn.cursor()
        creator_id_map = {}
        tag_id_map = {}
        lang_id_map = {}
        now = datetime.now(timezone.utc).isoformat()

        for cname, cdata in creators.items():
            cursor.execute("INSERT OR IGNORE INTO Creators (name, display_name, first_seen, last_updated, total_galleries, most_popular_tags) VALUES (?, ?, ?, ?, ?, ?)",
                (cname, cdata["display_name"], now, now, 0, json.dumps([])))
            cursor.execute("SELECT id FROM Creators WHERE name=?", (cname,))
            creator_id_map[cname] = cursor.fetchone()[0]

        for tname, tdata in tags.items():
            cursor.execute("INSERT OR IGNORE INTO Tags (name, count) VALUES (?, ?)", (tname, 0))
            cursor.execute("SELECT id FROM Tags WHERE name=?", (tname,))
            tag_id_map[tname] = cursor.fetchone()[0]

        for lname, ldata in languages.items():
            cursor.execute("INSERT OR IGNORE INTO Languages (name, count) VALUES (?, ?)", (lname, 0))
            cursor.execute("SELECT id FROM Languages WHERE name=?", (lname,))
            lang_id_map[lname] = cursor.fetchone()[0]

        for gid, gdata in galleries.items():
            logger.debug(f"[DATABASE] Writing to Galleries: id={gid}, raw_title={gdata['raw_title']}, clean_title={gdata['clean_title']}, num_pages={gdata['num_pages']}, creator_ids={creator_ids}, language_ids={language_ids}, tag_ids={tag_ids}")
            
            creator_ids = [creator_id_map[c] for c in gdata["creator_names"] if c in creator_id_map]
            tag_ids = [tag_id_map[t] for t in gdata["tag_names"] if t in tag_id_map]
            language_ids = [lang_id_map[l] for l in gdata["language_names"] if l in lang_id_map]
            cursor.execute(
                "INSERT OR REPLACE INTO Galleries (id, raw_title, clean_title, num_pages, creator_ids, language_ids, tag_ids, status, started_at, completed_at, download_path, cover_path, extension_used) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(gid),
                    gdata["raw_title"],
                    gdata["clean_title"],
                    gdata["num_pages"],
                    json.dumps(creator_ids),
                    json.dumps(language_ids),
                    json.dumps(tag_ids),
                    gdata["status"],
                    gdata["started_at"],
                    gdata["completed_at"],
                    gdata["download_path"],
                    gdata["cover_path"],
                    gdata["extension_used"]
                )
            )
            cursor.execute("INSERT OR REPLACE INTO GalleryTags (gallery_id, tag_ids) VALUES (?, ?)", (int(gid), json.dumps(tag_ids)))
            cursor.execute("INSERT OR REPLACE INTO GalleryLanguages (gallery_id, language_ids) VALUES (?, ?)", (int(gid), json.dumps(language_ids)))

        for cname, cid in creator_id_map.items():
            logger.debug(f"[DATABASE] Updating Creator {cname} (id={cid}): total_galleries={total_galleries}, most_popular_tags={most_popular_tag_ids}")
            
            cursor.execute("SELECT id FROM Galleries WHERE json_each.value = ? AND json_valid(creator_ids)", (cid,))
            gallery_ids = [row[0] for row in cursor.fetchall()]
            total_galleries = len(gallery_ids)
            tag_counter = {}
            for gid in gallery_ids:
                cursor.execute("SELECT tag_ids FROM GalleryTags WHERE gallery_id=?", (gid,))
                row = cursor.fetchone()
                if row and row[0]:
                    try:
                        tag_ids = json.loads(row[0])
                        for tid in tag_ids:
                            tag_counter[tid] = tag_counter.get(tid, 0) + 1
                    except Exception:
                        continue
            most_popular_tag_ids = [tid for tid, _ in sorted(tag_counter.items(), key=lambda x: x[1], reverse=True)[:15]]
            cursor.execute("UPDATE Creators SET total_galleries=?, most_popular_tags=?, last_updated=? WHERE id=?", (total_galleries, json.dumps(most_popular_tag_ids), now, cid))

        for tname, tid in tag_id_map.items():
            logger.debug(f"[DATABASE] Updating Tag {tname} (id={tid}): count={count}")

            cursor.execute("SELECT tag_ids FROM GalleryTags")
            count = 0
            for (tag_ids_json,) in cursor.fetchall():
                if tag_ids_json:
                    try:
                        tag_ids = json.loads(tag_ids_json)
                        count += tag_ids.count(tid)
                    except Exception:
                        continue
            cursor.execute("UPDATE Tags SET count=? WHERE id=?", (count, tid))

        for lname, lid in lang_id_map.items():
            logger.debug(f"[DATABASE] Updating Language {lname} (id={lid}): count={count}")
            
            cursor.execute("SELECT language_ids FROM GalleryLanguages")
            count = 0
            for (lang_ids_json,) in cursor.fetchall():
                if lang_ids_json:
                    try:
                        lang_ids = json.loads(lang_ids_json)
                        count += lang_ids.count(lid)
                    except Exception:
                        continue
            cursor.execute("UPDATE Languages SET count=? WHERE id=?", (count, lid))

        conn.commit()

####################################################################################################################
# other helpers idfk
####################################################################################################################

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



































################################################################################################################
# INTERNAL CACHING HELPERS
################################################################################################################

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

def _build_cached_metadata_entry(meta: dict, gallery_id: int) -> dict | None:
    if not meta or not isinstance(meta, dict):
        return None
    artists = Get.meta_tags("api", meta, "artist")
    groups = Get.meta_tags("api", meta, "group")
    languages = Get.meta_tags("api", meta, "language")
    return {
        "id": gallery_id,
        "title": meta.get("title", {}).get("english", f"Gallery {gallery_id}"),
        "artists": Get.artists(meta),
        "groups": Get.groups(meta),
        "tags": Get.tags(meta),
        "characters": Get.characters(meta),
        "parodies": Get.parodies(meta),
        "languages": Get.languages(meta),
        "pages": Get.page_count(meta),
    }
    #return {
    #    "id": gallery_id,
    #    "title": meta.get("title", {}).get("english", f"Gallery {gallery_id}"),
    #    "artists": artists or ["Unknown Artist"],
    #    "groups": groups or ["Unknown Group"],
    #    "tags": Get.meta_tags("api", meta, "tag"),
    #    "characters": Get.meta_tags("api", meta, "character"),
    #    "parodies": Get.meta_tags("api", meta, "parody"),
    #    "languages": languages or ["Unknown Language"],
    #    "pages": len(meta.get("images", {}).get("pages", [])),
    #}

def _build_master_cache_entry(
    cache_type: str,
    key: str,
    cache_file: Path,
    ttl_seconds: int | None,
    last_read: float | None = None,
    last_write: float | None = None,
    ids: list[int] | None = None,
) -> dict:
    try:
        size = cache_file.stat().st_size
    except Exception:
        size = None
    now = time.time()
    write_time = last_write if last_write is not None else now
    ttl_default = 10800
    ttl = ttl_seconds if ttl_seconds is not None else ttl_default
    expires_at = write_time + ttl if ttl else None
    entry = {
        "type": cache_type,
        "key": key,
        "path": str(cache_file),
        "size": size,
        "last_read": last_read,
        "last_write": write_time,
        "ttl": ttl_seconds,
        "expires_at": expires_at,
    }
    if ids is not None:
        entry["ids"] = ids
    return entry

def _load_master_cache() -> dict:
    cutoff = time.time() - TTL
    try:
        # Prune metadata entries in the master cache by their own TTL
        prune_cache_metadata(cutoff)
        
        # Prune references to cache files by their own TTL
        prune_cache_references(time.time())
        
        metadata_block = load_cache_metadata_all(cutoff)
        
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

################################################################################################################
# INTERNAL API HELPERS
################################################################################################################

def _build_symbol_translation_table():
    import logging
    logger = logging.getLogger("mangascraper.api")
    """Build a translation table for symbol replacements (called once at module load)."""
    global _SYMBOL_TRANSLATION_TABLE
    trans_dict = {ord(symbol): replacement for symbol, replacement in BROKEN_SYMBOL_REPLACEMENTS.items()}
    _SYMBOL_TRANSLATION_TABLE = trans_dict

# Initial symbol translation table at module load
_build_symbol_translation_table()

def sanitise_string(meta_or_title):
    """
    Clean a gallery/manga title. Accepts either a meta dict or a raw string.
    Detects broken symbols in the title, updates the persisted broken symbols file,
    and applies them when cleaning.

    Args:
        meta_or_title: Either a dict with metadata containing "title" or a string title.

    Returns:
        str: Sanitised title.
    """
    
    def is_cjk(char: str) -> bool:
        """Return True if char is a Chinese/Japanese/Korean character."""
        code = ord(char)
        return (
            0x4E00 <= code <= 0x9FFF   # CJK Unified Ideographs
            or 0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
            or 0x20000 <= code <= 0x2A6DF  # Extension B
            or 0x2A700 <= code <= 0x2B73F  # Extension C
            or 0x2B740 <= code <= 0x2B81F  # Extension D
            or 0x2B820 <= code <= 0x2CEAF  # Extension E
            or 0x2CEB0 <= code <= 0x2EBEF  # Extension F
            or 0x3000 <= code <= 0x303F  # CJK Symbols and Punctuation
            or 0x3040 <= code <= 0x309F  # Hiragana
            or 0x30A0 <= code <= 0x30FF  # Katakana
            or 0x31F0 <= code <= 0x31FF  # Katakana Phonetic Extensions
            or 0xFF65 <= code <= 0xFF9F  # Half-width Katakana
        )
    
    # Load persisted broken symbols (mapping)
    possible_broken_symbols = load_broken_symbols()

    # Determine if input is a dict or string
    if isinstance(meta_or_title, dict):
        meta = meta_or_title
        title_obj = meta.get("title", {}) or {}
        desired_title_type = title_type.lower()
        title = (
            title_obj.get(desired_title_type)
            or title_obj.get("english")
            or title_obj.get("pretty")
            or title_obj.get("japanese")
            or f"Gallery_{meta.get('id', 'UNKNOWN')}"
        )
        if "|" in title:
            title = title.split("|")[-1].strip()
    else:
        title = meta_or_title

    # Detect non-ASCII symbols - cache the known_symbols set to avoid rebuilding
    symbols = {c for c in title if ord(c) > 127 and not is_cjk(c)}
    known_symbols = set(ALLOWED_SYMBOLS).union(
        BROKEN_SYMBOL_REPLACEMENTS.keys(),
        BROKEN_SYMBOL_BLACKLIST,
        possible_broken_symbols.keys()
    )
    new_broken = symbols.difference(known_symbols)

    # Add new symbols to the Database, with logging
    if new_broken:
        for s in new_broken:
            possible_broken_symbols[s] = "_"
        logger.debug(f"[BrokenSymbols] New broken symbols detected: {sorted(new_broken)}. Updating database.")
        save_broken_symbols(possible_broken_symbols)
        _build_symbol_translation_table()
    else:
        logger.debug("[BrokenSymbols] No new broken symbols detected. No database update needed.")

    # Remove content inside [] or {} brackets (use pre-compiled regex)
    title = _BRACKET_PATTERN.sub("", title)

    # Apply explicit replacements using optimd translation (faster than loops)
    if _SYMBOL_TRANSLATION_TABLE is None:
        _build_symbol_translation_table()
    title = title.translate(_SYMBOL_TRANSLATION_TABLE)

    # Apply persisted broken symbol replacements
    for symbol, replacement in possible_broken_symbols.items():
        title = title.replace(symbol, replacement)

    # Normalise dashes (use pre-compiled regex)
    title = _DASH_PATTERN.sub("-", title)

    # Replace blacklisted characters
    for symbol in BROKEN_SYMBOL_BLACKLIST:
        title = title.replace(symbol, "_")

    # Collapse multiple underscores/spaces (use pre-compiled regex)
    title = _UNDERSCORE_PATTERN.sub("_", title)
    title = " ".join(title.split())
    title = title.strip(" _")

    if not title:
        title = f"UNTITLED_{meta.get('id', 'UNKNOWN')}" if isinstance(meta_or_title, dict) else "UNTITLED"

    return title.replace("/", "-").replace("\\", "-").strip()

def _calculate_thread_load_sleep(stage: str, num_items: int, attempt: int, gallery_cap: int = 3750) -> float:
    """
    Helper function to calculate sleep time based on thread load.
    """
    if orchestrator.threads_galleries is None or orchestrator.threads_images is None:
        gallery_threads = max(2, int(num_items / BATCH_SIZE) + 1) if stage == "gallery" else DEFAULT_THREADS_GALLERIES
        image_threads = gallery_threads * (DEFAULT_THREADS_IMAGES / DEFAULT_THREADS_GALLERIES)
        log(f"→ Optimised Threads: {gallery_threads} Gallery, {image_threads} Image", "debug")
    else:
        gallery_threads = orchestrator.threads_galleries
        image_threads = orchestrator.threads_images
        log(f"→ Threads: {gallery_threads} Gallery, {image_threads} Image", "debug")
        log(f"→ Configured Threads: Gallery = {gallery_threads}, Image = {image_threads}", "debug")

    concurrency = (gallery_threads * image_threads) + gallery_threads
    current_load = (concurrency * attempt) * num_items
    log(f"→ Concurrency = {gallery_threads} Gallery Threads * {image_threads} Image Threads = {concurrency}", "debug")
    log(f"→ Current Load = (Concurrency * Attempt) * Num Of {stage.capitalize()}s = ({concurrency} * {attempt}) * {num_items} = {current_load:.2f} Units Of Work", "debug")

    unit_factor = current_load / gallery_cap
    log_clarification("debug")
    log(f"→ Unit Factor = {current_load} (Current Load) / {gallery_cap} (Gallery Cap) = {unit_factor:.2f} Units Per Capped Gallery", "debug")
    
    BASE_GALLERY_THREADS = 2
    BASE_IMAGE_THREADS = 10
    gallery_thread_damper = 0.9
    image_thread_damper = 0.9

    thread_factor = ((gallery_threads / BASE_GALLERY_THREADS) ** gallery_thread_damper) * ((image_threads / BASE_IMAGE_THREADS) ** image_thread_damper)
    scaled_sleep = max(unit_factor / thread_factor, orchestrator.min_retry_sleep)
    
    log(f"→ Thread factor = (({gallery_threads}/{BASE_GALLERY_THREADS})^{gallery_thread_damper}) * (({image_threads}/{BASE_IMAGE_THREADS})^{image_thread_damper}) = {thread_factor:.2f}", "debug")
    log(f"→ Scaled sleep = Unit Factor / Thread Factor = {unit_factor:.2f} / {thread_factor:.2f} = {scaled_sleep:.2f}s", "debug")
    
    jitter_min, jitter_max = 0.9, 1.1
    sleep_time = min(random.uniform(scaled_sleep * jitter_min, scaled_sleep * jitter_max), orchestrator.max_retry_sleep)
    
    log(f"→ Sleep after jitter (Capped at {orchestrator.max_retry_sleep}s) = Random({scaled_sleep:.2f}*{jitter_min}, {scaled_sleep:.2f}*{jitter_max}) = {sleep_time:.2f}s", "debug")
    
    return sleep_time, current_load, gallery_threads, image_threads, concurrency

def dynamic_sleep(stage, attempt: int = 1):
    """
    Adaptive sleep timing based on load and stage, 
    including dynamic thread optimisation with anchor + units scaling.
    """
    
    gallery_cap = 3750

    log_clarification("debug")
    log("------------------------------", "debug")
    log(f"{stage.capitalize()} Attempt: {attempt}", "debug")
    log_clarification("debug")

    # API stage - simpler calculation
    if stage == "api":
        attempt_scale = attempt ** 2
        base_min, base_max = orchestrator.min_api_sleep * attempt_scale, orchestrator.max_api_sleep * attempt_scale
        sleep_time = random.uniform(base_min, base_max)
        log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s", "debug")
        log("------------------------------", "debug")
        log_clarification()
        return sleep_time

    # Gallery and Image stages - use unified calculation
    if stage in ("gallery", "image"):
        num_items = 1 if stage == "gallery" else max(1, orchestrator.total_gallery_images)
        log(f"→ Number of {stage.capitalize()}s: {num_items} (Capped at {gallery_cap})", "debug")
        sleep_time, current_load, gallery_threads, image_threads, concurrency = _calculate_thread_load_sleep(stage, num_items, attempt, gallery_cap)
        
        log_clarification("debug")
        log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Load: {current_load:.2f} Units)", "debug")
        log("------------------------------", "debug")
        log_clarification()
        return sleep_time

################################################################################################################
# NHentai API Handling / Endpoints
################################################################################################################
# Default base URL: https://nhentai.net/api
#
# 1. Homepage
#    GET /galleries
#    - Returns the most recent galleries
#
# 2. Gallery by ID
#    GET /gallery/{id}
#    - Fetch gallery information for a specific gallery ID
#
# 3. Search
#    GET /galleries/search
#    - Parameters:
#        query=<search terms>
#        page=<page number>
#        sort=<date / popular-today / popular-week / popular>
#
# 4. Tag
#    GET /galleries/tag/{tag}
#    - Fetch galleries by a specific tag
#
# 5. Artist
#    GET /galleries/artist/{artist}
#    - Fetch galleries by a specific artist
#
# 6. Group
#    GET /galleries/group/{group}
#    - Fetch galleries by a specific circle/group
#
# 7. Parody
#    GET /galleries/parody/{parody}
#    - Fetch galleries by a specific parody/series
#
# 8. Character
#    GET /galleries/character/{character}
#    - Fetch galleries by a specific character
#
# 9. Popular / Trending (if supported)
#    GET /galleries/popular
#    GET /galleries/trending
#
# Notes:
# - Pagination is typically handled via the `page` query parameter.
# - Responses are in JSON format with metadata, tags, images, and media info.
# - Image URLs are usually served via https://i.nhentai.net/galleries/{media_id}/{page}.{ext}

# ===============================
# BUILD API URLS
# ===============================
def build_url(query_type: str, query_value: str, sort_value: str, page: int) -> str:
    """
    Build the NHentai API URL for a given query type, value, sort type, and page number.

    query_type: homepage, artist, group, tag, character, parody, search
    query_value: string value of query (None for homepage)
    sort_value: date / recent / today / week / popular / all_time
    page: page number (1-based)
    """
    
    orchestrator.refresh_globals()
    
    query_lower = query_type.lower()

    # Homepage
    if query_lower == "homepage":
        if sort_value == "date":
            built_url = f"{nhentai_api_base}/galleries/all?page={page}"
        else:
            built_url = f"{nhentai_api_base}/galleries/all?page={page}&sort={sort_value}"
        return built_url

    # Artist / Group / Tag / Character / Parody
    if query_lower in ("artist", "group", "tag", "character", "parody"):
        search_value = query_value
        
        # Only wrap in quotes if user didn't already do so
        if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
            search_value = f'"{search_value}"'
        
        # Use urllib.parse.quote so spaces become '%20'
        encoded = urllib.parse.quote(f"{query_type}:{search_value}", safe=':"')
        
        if sort_value == "date":
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}"
        else:
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"
        return built_url

    # Search queries
    if query_lower == "search":
        # Strip surrounding quotes if present
        search_value = query_value.strip('"').strip("'")

        # Use urllib.parse.quote_plus so spaces become '+', not '%20'
        encoded = urllib.parse.quote_plus(search_value)

        if sort_value == "date":
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}"
        else:
            built_url = f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"
        return built_url

    raise ValueError(f"Unknown query format: {query_type}='{query_value}'")

def estimate_gallery_size(meta: dict, use_head_requests: bool = False) -> tuple:
    """
    Estimate total download size for a gallery in bytes.
    Returns (estimated_size_bytes, actual_size_bytes, image_count).
    
    Args:
        meta: Gallery metadata dict with 'images' field
        use_head_requests: If True, make HEAD requests for accurate filesize; if False, estimate by type
    
    Returns:
        (estimated_size_bytes, actual_size_bytes or 0, image_count)
    """
    
    orchestrator.refresh_globals()
    
    pages = meta.get("images", {}).get("pages", [])
    image_count = len(pages)
    
    if image_count == 0:
        return 0, 0, 0
    
    # Type-based size estimation (light, ~85% accurate)
    type_sizes = {"j": 85000, "p": 180000, "g": 520000, "w": 65000}  # bytes
    estimated_total = 0
    actual_total = 0
    fetched_count = 0
    
    for i, page_info in enumerate(pages):
        type_code = page_info.get("t", "w") if page_info else "w"
        estimated_total += type_sizes.get(type_code, 65000)  # default webp
        
        # Optional: use HEAD requests for 100% accuracy (slow)
        if use_head_requests and page_info and meta.get("media_id"):
            try:
                ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
                ext = ext_map.get(type_code, "webp")
                filename = f"{i + 1}.{ext}"
                urls = [
                    f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
                    for mirror in orchestrator.nhentai_mirrors[:1]  # try first mirror only
                ]
                
                if urls:
                    resp = Get.session(referrer="API", status="return").head(urls[0], timeout=(10, 10))
                    if resp.status_code == 200:
                        actual_total += int(resp.headers.get("content-length", type_sizes.get(type_code, 65000)))
                        fetched_count += 1
            except Exception:
                pass  # Fall back to estimate if HEAD fails
    
    # If we got some head request data, interpolate the rest
    if fetched_count > 0 and fetched_count < image_count:
        avg_actual = actual_total / fetched_count
        remaining = image_count - fetched_count
        actual_total += int(avg_actual * remaining)
    elif fetched_count == 0:
        actual_total = estimated_total
    
    return estimated_total, actual_total, image_count

################################################################################################################
# NAMESPACED API
################################################################################################################
class LoadCache:
    @staticmethod
    def load(cache_key: str) -> dict:
        cache_file = get_cache_dir() / f"{cache_key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if time.time() - data.get('timestamp', 0) < TTL:
                        metadata = data.get('metadata', {})
                        ids = []
                        if isinstance(metadata, dict):
                            for gid in metadata.keys():
                                try:
                                    ids.append(int(gid))
                                except Exception:
                                    continue
                            ids = sorted(set(ids))
                        _update_master_cache(
                            f"metadata:{cache_key}",
                            _build_master_cache_entry(
                                "metadata",
                                cache_key,
                                cache_file,
                                TTL,
                                last_read=time.time(),
                                last_write=data.get('timestamp', None),
                                ids=ids,
                            ),
                        )
                        return metadata
                    try:
                        cache_file.unlink()
                    except Exception:
                        pass
                    _remove_master_cache_entry(f"metadata:{cache_key}")
            except Exception:
                pass
        return {}
    
    @staticmethod
    def general_metadata() -> dict:
        data = _load_master_cache()
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            return {}
        cleaned = {}
        for gid, entry in metadata.items():
            if not isinstance(entry, dict):
                continue
            timestamp = entry.get("timestamp") or 0
            if (time.time() - timestamp) >= TTL:
                continue
            clean = entry.get("clean_metadata")
            if isinstance(clean, dict):
                cleaned[gid] = clean
        return cleaned

    @staticmethod
    def raw_metadata() -> dict:
        data = _load_master_cache()
        raw_block = data.get("metadata", {})
        if not isinstance(raw_block, dict):
            return {}
        metadata = {}
        for gid, entry in raw_block.items():
            if not isinstance(entry, dict):
                continue
            timestamp = entry.get("timestamp") or 0
            if (time.time() - timestamp) >= TTL:
                continue
            if "raw_metadata" in entry:
                metadata[gid] = entry.get("raw_metadata")
        return metadata

    @staticmethod
    def search_history(max_items: int = SEARCH_HISTORY_MAX) -> list[dict]:
        cache_file = get_cache_dir() / SEARCH_HISTORY_FILENAME
        if not cache_file.exists():
            return []
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                items = data.get('items', [])
                if not isinstance(items, list):
                    return []
                cleaned = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    search_type = item.get('type')
                    search_value = item.get('value')
                    if not search_type or search_value is None:
                        continue
                    cleaned.append({
                        'type': str(search_type),
                        'value': str(search_value),
                        'cache_key': item.get('cache_key'),
                        'sort': item.get('sort'),
                        'start_page': item.get('start_page'),
                        'end_page': item.get('end_page'),
                        'archive_mode': bool(item.get('archive_mode', False)),
                    })
                capped = SEARCH_HISTORY_MAX
                if max_items is not None:
                    capped = min(int(max_items), SEARCH_HISTORY_MAX)
                if capped and len(cleaned) > capped:
                    cleaned = cleaned[-capped:]
                _update_master_cache(
                    "search_history",
                    _build_master_cache_entry("search_history", "search_history", cache_file, None, last_read=time.time()),
                )
                return cleaned
        except Exception:
            return []

    @staticmethod
    def all_metadata() -> dict:
        """Load and merge all cached metadata entries from the master cache registry."""
        data = _load_master_cache()
        references = data.get("references", {})
        if not isinstance(references, dict):
            return {}
        merged = {}
        general_block = data.get("metadata", {})
        if isinstance(general_block, dict):
            for gid, entry in general_block.items():
                if not isinstance(entry, dict):
                    continue
                timestamp = entry.get("timestamp") or 0
                if (time.time() - timestamp) >= TTL:
                    continue
                clean = entry.get("clean_metadata")
                if isinstance(clean, dict):
                    merged[gid] = clean
        for entry in references.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "metadata":
                continue
            path = entry.get("path")
            if not path:
                continue
            try:
                cache_file = Path(path)
                if not cache_file.exists():
                    continue
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                metadata = data.get("metadata", {})
                if isinstance(metadata, dict):
                    merged.update(metadata)
            except Exception:
                continue
        return merged

    @staticmethod
    def id_metadata(ids: list[int]) -> dict:
        if not ids:
            return {}
        wanted = set()
        for gid in ids:
            try:
                wanted.add(int(gid))
            except Exception:
                continue
        if not wanted:
            return {}
        data = _load_master_cache()
        references = data.get("references", {})
        if not isinstance(references, dict):
            return {}
        merged = {}
        general_block = data.get("metadata", {})
        if isinstance(general_block, dict):
            for gid in wanted:
                gid_str = str(gid)
                entry = general_block.get(gid_str)
                if not isinstance(entry, dict):
                    continue
                timestamp = entry.get("timestamp") or 0
                if (time.time() - timestamp) >= TTL:
                    continue
                clean = entry.get("clean_metadata")
                if isinstance(clean, dict):
                    merged[gid] = clean
        for entry in references.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "metadata":
                continue
            entry_ids = entry.get("ids")
            if not isinstance(entry_ids, list):
                continue
            try:
                entry_set = {int(gid) for gid in entry_ids}
            except Exception:
                continue
            if not (wanted & entry_set):
                continue
            path = entry.get("path")
            if not path:
                continue
            try:
                cache_file = Path(path)
                if not cache_file.exists():
                    continue
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                metadata = data.get("metadata", {})
                if not isinstance(metadata, dict):
                    continue
                for gid in wanted:
                    if gid in merged:
                        continue
                    gid_str = str(gid)
                    if gid_str in metadata:
                        merged[gid] = metadata[gid_str]
            except Exception:
                continue
        return merged
    
    @staticmethod
    def queued_galleries() -> list:
        """Fetch queued galleries from GalleriesQueue table in the database."""
        return get_queued_galleries()

class SaveCache:
    @staticmethod
    def save(cache_key: str, metadata: dict):
        try:
            cache_file = get_cache_dir() / f"{cache_key}.json"
            timestamp = time.time()
            safe_metadata = {str(k): v for k, v in metadata.items()}
            ids = []
            for gid in safe_metadata.keys():
                try:
                    ids.append(int(gid))
                except Exception:
                    continue
            ids = sorted(set(ids))
            data = {
                'timestamp': timestamp,
                'metadata': safe_metadata
            }
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            from mangascraper.core import database
            now = time.time()
            prune_cache_references(now)
            _update_master_cache(
                f"metadata:{cache_key}",
                _build_master_cache_entry(
                    "metadata",
                    cache_key,
                    cache_file,
                    TTL,
                    last_write=timestamp,
                    ids=ids,
                ),
            )
        except Exception:
            pass
    
    @staticmethod
    def general_metadata(metadata: dict):
        if not isinstance(metadata, dict):
            return
        data = _load_master_cache()
        safe_metadata = {str(k): v for k, v in metadata.items()}
        now = time.time()
        for gid, entry in safe_metadata.items():
            if not isinstance(entry, dict):
                continue
            upsert_cache_metadata(
                gid,
                now,
                clean_metadata=entry,
                raw_metadata=None,
            )

    @staticmethod
    def raw_metadata(metadata: dict):
        if not isinstance(metadata, dict):
            return
        data = _load_master_cache()
        now = time.time()
        for gid, entry in metadata.items():
            upsert_cache_metadata(
                str(gid),
                now,
                clean_metadata=None,
                raw_metadata=entry,
            )

    @staticmethod
    def search_history(items: list[dict], max_items: int = SEARCH_HISTORY_MAX):
        try:
            if not isinstance(items, list):
                return
            safe_items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                search_type = item.get('type')
                search_value = item.get('value')
                if not search_type or search_value is None:
                    continue
                safe_items.append({
                    'type': str(search_type),
                    'value': str(search_value),
                    'cache_key': item.get('cache_key'),
                    'sort': item.get('sort'),
                    'start_page': item.get('start_page'),
                    'end_page': item.get('end_page'),
                    'archive_mode': bool(item.get('archive_mode', False)),
                })
            capped = SEARCH_HISTORY_MAX
            if max_items is not None:
                capped = min(int(max_items), SEARCH_HISTORY_MAX)
            if capped and len(safe_items) > capped:
                safe_items = safe_items[-capped:]
            cache_file = get_cache_dir() / SEARCH_HISTORY_FILENAME
            saved_at = time.time()
            data = {
                'saved_at': saved_at,
                'items': safe_items,
            }
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            _update_master_cache(
                "search_history",
                _build_master_cache_entry("search_history", "search_history", cache_file, None, last_write=saved_at),
            )
        except Exception:
            pass

class ClearCache:
    @staticmethod
    def clear(cache_key: str = None):
        try:
            cache_dir = get_cache_dir()
            if cache_key:
                cache_file = cache_dir / f"{cache_key}.json"
                if cache_file.exists():
                    cache_file.unlink()
                _remove_master_cache_entry(f"metadata:{cache_key}")
            else:
                for cache_file in cache_dir.glob("*.json"):
                    cache_file.unlink()
                MASTER_CACHE_FILENAME = "(master_cache).json"
                master_file = cache_dir / MASTER_CACHE_FILENAME
                if master_file.exists():
                    master_file.unlink()
        except Exception:
            pass

class CacheUtil:
    @staticmethod
    def ensure_files():
        prune_all_caches()
        cache_dir = get_cache_dir()
        cache_file = cache_dir / SEARCH_HISTORY_FILENAME
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                items = data.get("items", []) if isinstance(data, dict) else []
                if isinstance(items, list) and len(items) > SEARCH_HISTORY_MAX:
                    data["items"] = items[-SEARCH_HISTORY_MAX:]
                    data["saved_at"] = time.time()
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        else:
            try:
                data = {"saved_at": None, "items": []}
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

class Get:
    @staticmethod
    def cache_keys(search_type: str, search_value: str = None) -> str:
        """Generate cache key based on search criteria.
        Args:
            search_type: Type of search (artist, tag, group, character, parody, search, archive, homepage)
            search_value: The query value (artist name, tag name, etc.)
        Returns:
            Cache key suitable for filename (e.g., "artist_john", "tag_schoolgirl")
        """
        if search_value:
            # For multi-word searches, sort terms alphabetically to ensure order-independence
            # E.g., "THREE TWO ONE" and "ONE TWO THREE" both become "one_three_two"
            terms = search_value.lower().split()
            terms.sort()
            sorted_value = "_".join(terms)
            # Sanitise for use as filename (remove special chars)
            safe_value = "".join(c for c in sorted_value if c.isalnum() or c in ('-', '_')).lower()
            return f"{search_type}_{safe_value}"
        return search_type
    
    ################################################################################################################
    # HTTP SESSION
    ################################################################################################################
    
    @staticmethod
    def session(referrer: str = "Undisclosed Module", status: str = "rebuild"):
        """
        This is one this module's entrypoints.
        
        Ensure and return a ready cloudscraper session.
        - If status="rebuild", rebuilds the session.
        - If return_session=True, returns the current session without rebuilding.
        """

        log_clarification("debug")
        logger.debug("Fetcher: Ready.")
        log("Fetcher: Debugging Started.", "debug")
        
        global session
        
        orchestrator.refresh_globals()

        log_clarification("debug")
        # If status is "none", report that Referrer is requesting to only retrieve session, else report build / rebuild.
        # Session is always returned.
        if status == "none":
            logger.debug(f"{referrer}: Requesting to only retrieve session.")
        else:
            logger.debug(f"{referrer}: Requesting to {status} session.")

        with session_lock:
            # Refresh SSL verification setting on every session access
            if session is not None:
                session.verify = orchestrator.verify_ssl
            
            # Return current session if no build requested
            if status not in ["build", "rebuild"]:
                return session
            
            # Log if building or rebuilding session
            if status == "rebuild":
                log(f"Rebuilding HTTP session with cloudscraper for {referrer}", "debug")
            else:
                log(f"Building HTTP session with cloudscraper for {referrer}", "debug")

            # Random browser profiles (only randomised if flag is True)
            DefaultBrowserProfile = {"browser": "chrome", "platform": "windows", "mobile": False}
            RandomiseBrowserProfile = True
            browsers = [
                {"browser": "chrome", "platform": "windows", "mobile": False},
                {"browser": "chrome", "platform": "windows", "mobile": True},
                {"browser": "chrome", "platform": "linux", "mobile": False},
                {"browser": "chrome", "platform": "linux", "mobile": True},    
                {"browser": "firefox", "platform": "windows", "mobile": False},
                {"browser": "firefox", "platform": "windows", "mobile": True},
                {"browser": "firefox", "platform": "linux", "mobile": False},
                {"browser": "firefox", "platform": "linux", "mobile": True},
            ]
            browser_profile = random.choice(browsers) if RandomiseBrowserProfile else DefaultBrowserProfile

            # Create or rebuild session if needed
            if session is None or status == "rebuild":
                session = cloudscraper.create_scraper(browser=browser_profile)
            
            # Set SSL certificate verification based on config
            session.verify = orchestrator.verify_ssl

            # Random User-Agents (only randomised if flag is True)
            DefaultUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            RandomiseUserAgent = True    
            user_agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15",
                "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1",
            ]
            ua = random.choice(user_agents) if RandomiseUserAgent else DefaultUserAgent

            # Random Referers (only randomised if flag is True)
            DefaultReferer = "https://nhentai.net/"
            RandomiseReferer = False   
            referers = [
                "https://nhentai.net/",
                "https://google.com/",
                "https://duckduckgo.com/",
                "https://bing.com/",
            ]
            referer = random.choice(referers) if RandomiseReferer else DefaultReferer

            # Update headers
            session.headers.update({
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": referer,
            })

            # Update proxies
            if use_tor:
                proxy = "socks5h://127.0.0.1:9050"
                session.proxies = {"http": proxy, "https": proxy}
                logger.info(f"Using Tor proxy: {proxy}")
            else:
                session.proxies = {}
                logger.info("Not using Tor proxy")
                
            # Log completion of building/rebuilding
            if status == "rebuild":
                log("Rebuilt HTTP session.", "debug")
            else:
                log("Built HTTP session.", "debug")
            #logger.debug(f"Session ready: {session}") # NOTE: DEBUGGING, not really needed.

            return session # Return the current session
        
    ################################################################################################################
    # METADATA CLEANING
    ################################################################################################################

    @staticmethod
    def meta_tags(referrer: str, meta, tag_type):
        """
        Extract all tag names of a given type (artist, group, parody, language, etc.).
        - Splits names on "|".
        - Returns [] if none found.
        """
        
        if not meta or "tags" not in meta:
            return []

        names = []
        for tag in meta["tags"]:
            if tag.get("type") == tag_type and tag.get("name"):
                parts = [t.strip() for t in tag["name"].split("|") if t.strip()]
                names.extend(parts)
        
        #log(f"Fetcher: '{referrer}' Requested Tag Type '{tag_type}', returning {names}", "debug") # NOTE: DEBUGGING
        return names
    
    # Utility functions for metadata extraction
    @staticmethod
    def artists(meta):
        return Get.meta_tags("api", meta, "artist") or ["Unknown Artist"]

    @staticmethod
    def groups(meta):
        return Get.meta_tags("api", meta, "group") or ["Unknown Group"]

    @staticmethod
    def tags(meta):
        return Get.meta_tags("api", meta, "tag") or []

    @staticmethod
    def characters(meta):
        return Get.meta_tags("api", meta, "character") or []

    @staticmethod
    def parodies(meta):
        return Get.meta_tags("api", meta, "parody") or []

    @staticmethod
    def languages(meta):
        return Get.meta_tags("api", meta, "language") or ["Unknown Language"]

    @staticmethod
    def page_count(meta):
        return len(meta.get("images", {}).get("pages", []))
    
    ################################################################################################################
    # METADATA SUMMARIES
    ################################################################################################################
    
    def metadata_summary(metadata: dict) -> dict:
        """
        Generate a summary of metadata statistics.

        Returns:
            dict with counts of artists, groups, tags, languages, min/max pages, etc.
        """

        if not metadata:
            return {}

        all_artists = set()
        all_groups = set()
        all_tags = set()
        all_characters = set()
        all_parodies = set()
        all_languages = set()
        pages_list = []

        for meta in metadata.values():
            all_artists.update(meta.get("artists", []))
            all_groups.update(meta.get("groups", []))
            all_tags.update(meta.get("tags", []))
            all_characters.update(meta.get("characters", []))
            all_parodies.update(meta.get("parodies", []))
            all_languages.update(meta.get("languages", []))
            pages_list.append(meta.get("pages", 0))

        return {
            "total_galleries": len(metadata),
            "unique_artists": len(all_artists),
            "unique_groups": len(all_groups),
            "unique_tags": len(all_tags),
            "unique_characters": len(all_characters),
            "unique_parodies": len(all_parodies),
            "unique_languages": len(all_languages),
            "artists": sorted(all_artists),
            "groups": sorted(all_groups),
            "tags": sorted(all_tags),
            "characters": sorted(all_characters),
            "parodies": sorted(all_parodies),
            "languages": sorted(all_languages),
            "min_pages": min(pages_list) if pages_list else 0,
            "max_pages": max(pages_list) if pages_list else 0,
            "avg_pages": sum(pages_list) / len(pages_list) if pages_list else 0,
        }

class Fetch:
    ################################################################################################################
    # GALLERY ID FETCHING
    ################################################################################################################
    
    @staticmethod
    def gallery_ids(
        query_type: str,
        query_value: str,
        sort_value: str = DEFAULT_PAGE_SORT,
        start_page: int | None = None,
        end_page: int | None = None,
        file_used: bool = False,
        fetch_as_archival: bool = DEFAULT_ARCHIVING,
    ) -> set[int]:
        """
        Fetch gallery IDs from NHentai based on query type, value, and optional sort type.

        query_type: homepage, artist, group, tag, character, parody, search
        query_value: string query value (None for homepage)
        sort_value: date / recent / today / week / popular / all_time (defaults to 'date')
        start_page, end_page: pagination (auto-defaults depend on archival flag)
        archival: if True, crawl until NHentai returns no more results (ignores end_page)
        """
        
        global archiving

        orchestrator.refresh_globals()
        
        query_type = query_type.capitalize()
        query_str = f" ' {query_value}'" if query_value else ""
        sort_str = f"'{sort_value}'" if sort_value != "date" else "date"

        # Apply default ranges depending on flags used.
        if start_page is None:
            start_page = DEFAULT_PAGE_RANGE_START
        
        if file_used:
            if end_page is None:
                end_page = None # Always unlimited in archival mode if no end page provided
        if fetch_as_archival:
            log_clarification("debug") # NOTE: DEBUGGING
            log(f"SWITCHING TO ARCHIVAL MODE", "debug")
            orchestrator.archiving = True # Let scraper know there is archival being done.
            end_page = None # Always unlimited in archival mode
        else:
            if end_page is None:
                end_page = DEFAULT_PAGE_RANGE_END

        ids: set[int] = set()
        page = start_page
        
        #log_clarification("debug") # NOTE: DEBUGGING
        #log(f"START PAGE = {start_page}", "debug")
        #log(f"END PAGE = {end_page}", "debug")
        
        gallery_ids_session = Get.session(referrer="API", status="return")

        try:
            log_clarification("debug")
            if query_value is None:
                log(f"Fetching Gallery IDs from NHentai Homepages {start_page} → {end_page or '∞'}")
            else:
                log(f"Fetching Gallery IDs for {query_type} '{query_value}' (pages {start_page} → {end_page or '∞'}), sorted by {sort_str}")

            while True:
                # Stop at configured end_page (non-archival only)
                if end_page is not None and page > end_page:
                    break

                url = build_url(query_type, query_value, sort_value, page)
                log(f"Fetcher: Requesting URL: {url}", "debug")

                resp = None
                for attempt in range(1, orchestrator.max_retries + 1):
                    try:
                        resp = gallery_ids_session.get(url, timeout=(60, 60))

                        if resp.status_code == 429:
                            wait = dynamic_sleep("api", attempt=attempt)
                            logger.warning(f"{query_type}{query_str}, Page {page}: Attempt {attempt}: 429 rate limit, waiting {wait:.2f}s")
                            time.sleep(wait)
                            continue

                        if resp.status_code == 403:
                            wait = dynamic_sleep("api", attempt=attempt)
                            logger.warning(f"{query_type}{query_str}, Page {page}: Attempt {attempt}: 403 forbidden, retrying in {wait:.2f}s")
                            time.sleep(wait)
                            continue

                        resp.raise_for_status()
                        break  # success

                    except requests.RequestException as e:
                        if attempt >= orchestrator.max_retries:
                            log_clarification("debug")
                            logger.warning(f"{query_type} {f'{query_value}' if query_value == None else ''}, Page {page}: Failed after {attempt} retries: {e}")
                            resp = None

                            # Tor fallback
                            if use_tor:
                                wait = dynamic_sleep("api", attempt=attempt) * 2
                                logger.warning(f"{query_type}{query_str}, Page {page}: Retrying with new Tor node in {wait:.2f}s")
                                time.sleep(wait)
                                gallery_ids_session = Get.session(referrer="API", status="rebuild")
                                try:
                                    resp = gallery_ids_session.get(url, timeout=(60, 60))
                                    resp.raise_for_status()
                                except Exception as e2:
                                    logger.warning(f"{query_type}{query_str}, Page {page}: Still failed after Tor rotate: {e2}")
                                    resp = None
                            break

                        wait = dynamic_sleep("api", attempt=attempt)
                        logger.warning(f"{query_type}{query_str}, Page {page}: Attempt {attempt}: Request failed: {e}, retrying in {wait:.2f}s")
                        time.sleep(wait)

                if resp is None:
                    page += 1
                    continue  # skip this page

                try:
                    data = resp.json()
                except Exception as e:
                    logger.warning(f"{query_type}{query_str}, Page {page}: Failed to decode JSON: {e}")
                    break
                
                # ------------------------------------
                # Filtering
                # ------------------------------------
                results = data.get("result", [])
                batch = []

                # --- Excluded Tags ---
                excluded_gallery_tags = [tag.lower() for tag in orchestrator.excluded_tags]
                
                # --- Allowed Languages ---
                allowed_gallery_language = [lang.lower() for lang in orchestrator.language]

                for g in results:
                    # Extract gallery tags
                    gallery_tags = [
                        t["name"].lower()
                        for t in g.get("tags", [])
                        if t.get("type") == "tag"
                    ]

                    # Extract gallery languages
                    gallery_langs = [
                        t["name"].lower()
                        for t in g.get("tags", [])
                        if t.get("type") == "language"
                    ]

                    # --- Tag filter ---
                    blocked_tags = [t for t in gallery_tags if t in excluded_gallery_tags]
                    if blocked_tags:
                        log(f"Skipping Gallery {g['id']} due to excluded tags: {blocked_tags}", "debug") # NOTE: DEBUGGING
                        continue

                    # --- Language filter ---
                    if allowed_gallery_language:
                        has_allowed = any(lang in allowed_gallery_language for lang in gallery_langs)
                        has_translated = "translated" in gallery_langs
                        allow_translated = "translated" in allowed_gallery_language
                        if not (has_allowed or (has_translated and allow_translated)):
                            blocked_langs = gallery_langs[:]
                            log(f"Skipping Gallery {g['id']} due to blocked languages: {blocked_langs}", "debug") # NOTE: DEBUGGING
                            continue

                    # If passed filters → keep
                    batch.append(int(g["id"]))
                    
                    # --- Track total pages ---
                    images = g.get("images", {})
                    num_pages = len(images.get("pages", []))
                    orchestrator.total_gallery_images += num_pages

                log(f"Fetcher: {query_type}{query_str}, Page {page}: Fetched {len(batch)} Gallery IDs", "info")
                log(f"Current Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")
                #log(f"Excluded tags: {excluded_gallery_tags})", "debug") # NOTE: DEBUGGING
                #log(f"Langs allowed: {allowed_gallery_language}", "debug") # NOTE: DEBUGGING

                # Stop only if NHentai itself returns no results
                if not results:
                    logger.info(f"Fetcher: {query_type}{query_str}, Page {page}: No more results from NHentai, stopping.")
                    break

                # If results exist but all were filtered out, just skip to next page
                if not batch:
                    logger.debug(f"Fetcher: {query_type}{query_str}, Page {page}: All galleries filtered out, continuing to next page.")
                    page += 1
                    continue

                ids.update(batch)
                page += 1

            log(f"Fetched total {len(ids)} Galleries for {query_type}{query_str}", "warning")
            log(f"Overall Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")
            return ids

        except Exception as e:
            logger.warning(f"Failed to fetch Galleries for {query_type}{query_str}: {e}")
            return set()

    ################################################################################################################
    # IMAGE URL FETCHING
    ################################################################################################################
    
    @staticmethod
    def image_urls(meta: dict, page: int):
        """
        Returns the full image URL for a gallery page.
        Tries mirrors from NHENTAI_MIRRORS in order until one succeeds.
        Handles missing metadata, unknown types, and defaulting to webp.
        """
        
        orchestrator.refresh_globals()
        
        try:
            #log(f"Fetcher: Building image URLs for Gallery {meta.get('id','?')}: Page {page}", "debug") # NOTE: DEBUGGING

            pages = meta.get("images", {}).get("pages", [])
            if page - 1 >= len(pages):
                logger.warning(f"Gallery {meta.get('id','?')}: Page {page}: Not in metadata")
                return None

            page_info = pages[page - 1]
            if not page_info:
                logger.warning(f"Gallery {meta.get('id','?')}: Page {page}: Metadata is None")
                return None

            # Map type codes to extensions
            ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
            type_code = page_info.get("t", "w")  # default to webp
            if type_code not in ext_map:
                log_clarification()
                logger.warning(
                    f"Unknown image type '{type_code}' for Gallery {meta.get('id','?')}: Page {page}: Defaulting to webp"
                )

            ext = ext_map.get(type_code, "webp")
            filename = f"{page}.{ext}"

            # Try each mirror in order
            #nhentai_mirrors = orchestrator.nhentai_mirrors or DEFAULT_NHENTAI_MIRRORS # Normalised in configurator
            #if isinstance(nhentai_mirrors, str):
            #    nhentai_mirrors = [nhentai_mirrors]
            urls = [
                f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
                for mirror in orchestrator.nhentai_mirrors
            ]

            log(f"Fetcher: Built image URLs for Gallery {meta.get('id','?')}: Page {page}: {urls}", "debug") # NOTE: DEBUGGING
            return urls  # return list so downloader can try them in order

        except Exception as e:
            logger.warning(f"Failed to build image URL for Gallery {meta.get('id','?')}: Page {page}: {e}")
            return None

    ################################################################################################################
    # GALLERY METADATA FETCHING
    ################################################################################################################
    
    @staticmethod
    def gallery_metadata(gallery_id: int):
        orchestrator.refresh_globals()

        raw_cache = LoadCache.raw_metadata()
        cached_meta = raw_cache.get(str(gallery_id))
        if cached_meta and isinstance(cached_meta, dict):
            return cached_meta

        metadata_session = Get.session(referrer="API", status="return")
        
        url = f"{nhentai_api_base}/gallery/{gallery_id}"
        for attempt in range(1, orchestrator.max_retries + 1):
            try:
                log_clarification("debug")
                log(f"Fetcher: Fetching metadata for Gallery: {gallery_id}, URL: {url}", "debug")

                resp = metadata_session.get(url, timeout=(60, 60))
                if resp.status_code == 429:
                    wait = dynamic_sleep("api", attempt=(attempt))
                    logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: 429 rate limit hit, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code == 403:
                    wait = dynamic_sleep("api", attempt=(attempt))
                    time.sleep(wait)
                    continue
                
                resp.raise_for_status()
                
                data = resp.json()

                # Validate the response
                if not isinstance(data, dict):
                    logger.error(f"Unexpected response type for Gallery: {gallery_id}: {type(data)}")
                    return None

                cached_entry = _build_cached_metadata_entry(data, gallery_id)
                if cached_entry:
                    general_metadata = LoadCache.general_metadata()
                    general_metadata[gallery_id] = cached_entry
                    SaveCache.general_metadata(general_metadata)

                raw_cache = LoadCache.raw_metadata()
                raw_cache[str(gallery_id)] = data
                SaveCache.raw_metadata(raw_cache)

                log_clarification("debug")
                log(f"Fetcher: Fetched metadata for Gallery: {gallery_id}", "debug")
                #log(f"Fetcher: Metadata for Gallery: {gallery_id}: {data}", "debug") # NOTE: DEBUGGING
                return data
            except requests.HTTPError as e:
                if "404 Client Error: Not Found for url" in str(e):
                    logger.warning(f"Gallery: {gallery_id}: Not found (404), skipping retries.")
                    return None
                if attempt >= orchestrator.max_retries:
                    logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                    # Rebuild session with Tor and try again once
                    if use_tor:
                        wait = dynamic_sleep("api", attempt=(attempt)) * 2
                        logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: Metadata fetch failed: {e}, retrying with new Tor Node in {wait:.2f}s")
                        time.sleep(wait)
                        metadata_session = Get.session(referrer="API", status="rebuild")
                        try:
                            resp = metadata_session.get(url, timeout=(60, 60))
                            resp.raise_for_status()
                            return resp.json()
                        except Exception as e2:
                            logger.warning(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}")
                    return None
                wait = dynamic_sleep("api", attempt=(attempt))
                logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s")
                time.sleep(wait)
            except requests.RequestException as e:
                if attempt >= orchestrator.max_retries:
                    logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                    # Rebuild session with Tor and try again once
                    if use_tor:
                        wait = dynamic_sleep("api", attempt=(attempt)) * 2
                        logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: Metadata fetch failed: {e}, retrying with new Tor Node in {wait:.2f}s")
                        time.sleep(wait)
                        metadata_session = Get.session(referrer="API", status="rebuild")
                        try:
                            resp = metadata_session.get(url, timeout=(60, 60))
                            resp.raise_for_status()
                            return resp.json()
                        except Exception as e2:
                            logger.warning(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}")
                    return None
                wait = dynamic_sleep("api", attempt=(attempt))
                logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s")
                time.sleep(wait)

    ################################################################################################################
    # BATCH METADATA FETCHING (for pre-filtering)
    ################################################################################################################
    
    @staticmethod
    def fetch_metadata_batch(gallery_ids: list) -> dict:
        """
        Fetch metadata for multiple galleries efficiently using threading.
        Used for pre-fetching before filtering/sizing.
        
        Returns:
            dict: {gallery_id: metadata_dict}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        if not gallery_ids:
            return {}
        
        metadata = {}
        failed_ids = []
        
        # Use thread pool for parallel fetching
        max_workers = min(10, len(gallery_ids))  # Cap at 10 parallel requests
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(Fetch.gallery_metadata, gid): gid for gid in gallery_ids}
            
            # Use tqdm for progress
            for future in tqdm(as_completed(futures), total=len(futures), desc="Fetching metadata", unit="gallery"):
                gallery_id = futures[future]
                try:
                    meta = future.result()
                    if meta and isinstance(meta, dict):
                        metadata[gallery_id] = meta
                except Exception as e:
                    logger.debug(f"Failed to fetch metadata for Gallery {gallery_id}: {e}")
                    failed_ids.append(gallery_id)
        
        if failed_ids:
            logger.warning(f"Failed to fetch metadata for {len(failed_ids)}/{len(gallery_ids)} galleries")
        
        return metadata

    def all_galleries_metadata(gallery_ids: list, cache_key: str = None) -> dict:
        """
        Fetch metadata for all galleries with caching support.
        Uses caching to avoid repeated API calls for the same search criteria.
        Extracts relevant fields (title, artists, tags, etc.) for display/filtering.
        
        Args:
            gallery_ids: List of gallery IDs to fetch
            cache_key: Optional cache key for storing results by search criteria (e.g., "artist_john")
        
        Returns:
            dict: {gallery_id: metadata_dict with title, artists, tags, language, pages}
        """
        
        if not gallery_ids:
            return {}
        
        # Deduplicate gallery IDs to prevent redundant API calls
        normalised_ids = []
        for gid in gallery_ids:
            try:
                gid_int = int(gid)
            except (TypeError, ValueError):
                logger.warning(f"Skipping gallery with invalid ID: {gid}")
                continue
            normalised_ids.append(gid_int)
        gallery_ids = list(dict.fromkeys(normalised_ids))
        
        # Try loading from cache first if cache_key provided
        cached_metadata = {}
        ids_to_fetch = []

        def _is_complete_cached_meta(meta: dict) -> bool:
            if not isinstance(meta, dict):
                return False
            required_keys = {"title", "artists", "tags", "languages", "pages"}
            return required_keys.issubset(meta.keys())

        if cache_key:
            cached_metadata = LoadCache.load(cache_key)
        else:
            cached_metadata = LoadCache.id_metadata(gallery_ids)

        if cached_metadata:
            normalised_cached = {}
            for gid, meta in cached_metadata.items():
                try:
                    gid_int = int(gid)
                except (TypeError, ValueError):
                    continue
                normalised_cached[gid_int] = meta
            cached_metadata = normalised_cached

        if cached_metadata:
            incomplete = [gid for gid, meta in cached_metadata.items() if not _is_complete_cached_meta(meta)]
            if incomplete:
                logger.debug(f"Dropping {len(incomplete)} cached galleries due to incomplete metadata")
            for gid in incomplete:
                cached_metadata.pop(gid, None)
            if incomplete:
                logger.info(f"Refreshing {len(incomplete)} cached galleries with incomplete metadata")

        ids_to_fetch = [gid for gid in gallery_ids if gid not in cached_metadata]
        if cached_metadata:
            logger.info(f"Using {len(cached_metadata)} galleries from cache")
        
        # If all galleries are cached, return immediately
        if not ids_to_fetch:
            logger.info(f"All {len(cached_metadata)} galleries loaded from cache")
            return cached_metadata
        
        logger.info(f"Fetching metadata for {len(ids_to_fetch)} new galleries...")
        if cache_key:
            logger.info(f"({len(cached_metadata)} Cached galleries, fetching {len(ids_to_fetch)} new)")
        log_clarification()
        
        metadata = dict(cached_metadata)  # Start with cached results
        failed_ids = []
        
        # Fetch missing galleries with progress bar
        # Note: Filtering already applied in fetch_gallery_ids(), so we just extract metadata here
        for gallery_id in tqdm(ids_to_fetch, desc="Fetching gallery metadata", unit="gallery"):
            try:
                meta = Fetch.gallery_metadata(gallery_id)
                if meta and isinstance(meta, dict):
                    meta_entry = _build_cached_metadata_entry(meta, gallery_id)
                    if meta_entry:
                        metadata[gallery_id] = meta_entry
                else:
                    failed_ids.append(gallery_id)
            except Exception as e:
                logger.debug(f"Failed to fetch metadata for Gallery {gallery_id}: {e}")
                failed_ids.append(gallery_id)
        
        if failed_ids:
            logger.warning(f"Failed to fetch metadata for {len(failed_ids)} galleries (they will be skipped)")
        
        # Save to cache
        if metadata and cache_key:
            SaveCache.save(cache_key, metadata)
        elif metadata:
            general_metadata = LoadCache.general_metadata()
            general_metadata.update(metadata)
            SaveCache.general_metadata(general_metadata)
        
        return metadata

################################################################################################################
# EXPORTED API
################################################################################################################

load = LoadCache()
save = SaveCache()
clear = ClearCache()
util = CacheUtil()
get = Get()
fetch = Fetch()