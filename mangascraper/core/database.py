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

DATA_DIR = os.path.join(SCRAPER_DIR, "mangascraper/core")
DB_PATH = os.path.join(DATA_DIR, "mangascraper.db")

# Cache TTL: 3 hours (runtime-configured)
TTL = getattr(orchestrator, "metadata_ttl", 3 * 60 * 60)

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
            cache_key TEXT PRIMARY KEY,
            cache_type TEXT,
            cache_target TEXT,
            ids TEXT,
            ttl INTEGER,
            expires_at REAL
        );
        """)
        conn.commit()

################################################################################################################
# DATABASE HELPERS
################################################################################################################

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

####################################################################################################################
# CACHING HELPERS
####################################################################################################################

def read_cached_metadata_entry(cache_key: str = None, gallery_id: str = None, cutoff: float = None, ids: list = None) -> dict | None:
    """
    Loads and returns metadata from CachedMetadata or entries from CacheReferences.
    - If ids is given (list of gallery IDs), returns metadata for those galleries as a dict.
    - If gallery_id is given, returns metadata for that gallery (or None if not found).
    - If cutoff is given, returns all metadata entries newer than cutoff as a dict keyed by gallery ID.
    - If cache_key is given, returns the CacheReferences entry for that key (or None if not found).
    - If no parameter is given, returns all entries in CacheReferences as a dict.
    """
    init_db()

    # CacheReferences logic
    if cache_key is not None or (gallery_id is None and cutoff is None and ids is None):
        with lock, dbconnect() as conn:
            cursor = conn.cursor()
            if cache_key is not None:
                cursor.execute(
                    "SELECT cache_key, cache_type, cache_target, ids, ttl, expires_at FROM CacheReferences WHERE cache_key = ?",
                    (str(cache_key),),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                (
                    cache_key_val,
                    cache_type,
                    cache_target,
                    ids_json,
                    ttl,
                    expires_at,
                ) = row
                entry = {
                    "cache_type": cache_type,
                    "cache_key": cache_key_val,
                    "cache_target": cache_target,
                    "ttl": ttl,
                    "expires_at": expires_at,
                }
                if ids_json:
                    try:
                        entry["ids"] = json.loads(ids_json)
                    except Exception:
                        entry["ids"] = []
                return entry
            else:
                # Return all CacheReferences entries
                cursor.execute(
                    "SELECT cache_key, cache_type, cache_target, ids, ttl, expires_at FROM CacheReferences"
                )
                rows = cursor.fetchall()
                result = {}
                for row in rows:
                    (
                        cache_key_val,
                        cache_type,
                        cache_target,
                        ids_json,
                        ttl,
                        expires_at,
                    ) = row
                    entry = {
                        "cache_type": cache_type,
                        "cache_key": cache_key_val,
                        "cache_target": cache_target,
                        "ttl": ttl,
                        "expires_at": expires_at,
                    }
                    if ids_json:
                        try:
                            entry["ids"] = json.loads(ids_json)
                        except Exception:
                            entry["ids"] = []
                    result[str(cache_key_val)] = entry
                return result

    # CachedMetadata logic
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        if ids is not None:
            if not ids:
                return {}
            ids_str = [str(gid) for gid in ids]
            placeholders = ",".join("?" for _ in ids_str)
            params = list(ids_str)
            query = (
                "SELECT gallery_id, timestamp, clean_metadata, raw_metadata "
                "FROM CachedMetadata WHERE gallery_id IN (" + placeholders + ")"
            )
            if cutoff is not None:
                query += " AND timestamp >= ?"
                params.append(cutoff)
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
        if gallery_id is not None:
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
        elif cutoff is not None:
            cursor.execute(
                "SELECT gallery_id, timestamp, clean_metadata, raw_metadata FROM CachedMetadata WHERE timestamp >= ?",
                (cutoff,),
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
        
def prune_all_caches():
    """Removes expired entries in CacheReferences and CachedMetadata, and deletes any cache reference whose TTL has expired."""
    init_db()
    now = time.time()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        # Delete expired cache references by expires_at
        cursor.execute(
            "DELETE FROM CacheReferences WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        # Also delete any cache reference whose TTL has expired (redundant with expires_at, but explicit)
        cursor.execute(
            "DELETE FROM CacheReferences WHERE ttl IS NOT NULL AND expires_at IS NOT NULL AND (expires_at - ttl) <= ?",
            (now,),
        )
        conn.commit()

def split_cache_key(cache_key):
    """Splits a cache_key into cache_type and cache_target. E.g., "artist:abc" → ("artist", "abc")"""
    if ":" in cache_key:
        cache_type, cache_target = cache_key.split(":", 1)
    else:
        cache_type, cache_target = cache_key, ""
    return cache_type, cache_target

def upsert_cached_metadata(gallery_id: str, timestamp: float, clean_metadata=None, raw_metadata=None):
    init_db()
    entry = read_cached_metadata_entry(gallery_id) or {
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

def upsert_cache_reference(cache_key: str, entry: dict):
    init_db()
    ids = entry.get("ids")
    if not isinstance(ids, list):
        ids = [] if ids is None else [ids]
    ids_json = json.dumps(ids)
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO CacheReferences (cache_key, cache_type, cache_target, ids, ttl, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET "
            "cache_type=excluded.cache_type, "
            "cache_target=excluded.cache_target, "
            "ids=excluded.ids, "
            "ttl=excluded.ttl, "
            "expires_at=excluded.expires_at",
            (
                str(cache_key),
                entry.get("cache_type"),
                entry.get("cache_target"),
                ids_json,
                entry.get("ttl"),
                entry.get("expires_at"),
            ),
        )
        conn.commit()

####################################################################################################################
# GALLERY STATUS UPDATERS
####################################################################################################################

def mark_gallery_started(gallery_id, download_path=None, extension_used=None):
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        # Check if extension_used is already set for this gallery
        cursor.execute("SELECT extension_used FROM Galleries WHERE id=?", (gallery_id,))
        row = cursor.fetchone()
        if row and row[0]:
            # Preserve existing extension_used
            cursor.execute("""
            INSERT INTO Galleries (id, status, started_at, download_path)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                started_at=excluded.started_at,
                download_path=excluded.download_path
            """, (gallery_id, "started", now, download_path))
            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as started.")
            logger.debug(f"[DATABASE] Data: status=started, started_at={now}, download_path={download_path}, extension_used (preserved)={row[0]}")
        else:
            # Set extension_used if not already set
            cursor.execute("""
            INSERT INTO Galleries (id, status, started_at, download_path, extension_used)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                started_at=excluded.started_at,
                download_path=excluded.download_path,
                extension_used=excluded.extension_used
            """, (gallery_id, "started", now, download_path, extension_used))
            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as started.")
            logger.debug(f"[DATABASE] Data: status=started, started_at={now}, download_path={download_path}, extension_used={extension_used}")
        conn.commit()

def mark_gallery_skipped(gallery_id):
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with lock, dbconnect() as conn:
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
    with lock, dbconnect() as conn:
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
    # Load metadata for this gallery to compute paths and extension
    cache = read_cached_metadata_entry([gallery_id])
    meta = None
    for gid, entry in cache.items():
        meta = entry.get("clean_metadata") or {}
        break
    # Compute download_path, cover_path, extension_used, started_at with robust fallback
    download_path = None
    cover_path = None
    extension_used = None
    started_at = None
    ext_download_path = ""
    cleaned_creator = "Unknown"
    ext = "cbz"
    is_archive = True
    # Always update Galleries table with latest clean_title from clean_metadata if available
    gallery_title = ""
    if meta and meta.get("clean_title"):
        with lock, dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE Galleries SET clean_title=? WHERE id=?", (meta["clean_title"], gallery_id))
            conn.commit()
    # Now fetch clean_title from Galleries table
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT clean_title FROM Galleries WHERE id=?", (gallery_id,))
        row = cursor.fetchone()
        if row and isinstance(row[0], str) and row[0].strip():
            gallery_title = row[0].strip()
    if meta:
        ext_download_path = meta.get("extension_download_path") or meta.get("download_path") or None
        # Always resolve to absolute path
        base_ext_path = None
        try:
            from mangascraper.extensions.extension_manager import calculate_extension_download_path
            ext_name = meta.get("extension_used") or meta.get("extension") or getattr(orchestrator, "extension", "skeleton")
            base_ext_path = calculate_extension_download_path(str(ext_name).lower())
        except Exception:
            base_ext_path = getattr(orchestrator, "extension_download_path", "/opt/manga-scraper/downloads/")
        if not ext_download_path:
            ext_download_path = base_ext_path
        elif not os.path.isabs(ext_download_path):
            ext_download_path = os.path.join(base_ext_path, ext_download_path)
        # Cleaned primary creator name
        primary_creator = None
        if "artists" in meta and isinstance(meta["artists"], list) and meta["artists"]:
            primary_creator = meta["artists"][0]
        elif "groups" in meta and isinstance(meta["groups"], list) and meta["groups"]:
            primary_creator = meta["groups"][0]
        else:
            primary_creator = "Unknown"
        from mangascraper.core.api import sanitise_string
        cleaned_creator = sanitise_string(primary_creator)
        ext = meta.get("archive_ext") or meta.get("ext") or "cbz"
        is_archive = meta.get("is_archive", True)
        started_at = meta.get("started_at")
    # Compose download_path and cover_path with full extension path
    if gallery_title:
        if is_archive:
            download_path = os.path.join(ext_download_path, cleaned_creator, f"{gallery_title}.{ext}")
        else:
            download_path = os.path.join(ext_download_path, cleaned_creator, gallery_title)
        # cover_path: full path, no extension, include gallery id
        cover_path = os.path.join(ext_download_path, cleaned_creator, ".covers", f"({gallery_id}) {gallery_title}")
    else:
        download_path = ""
        cover_path = ""
    # If started_at is still None, try to fetch from Galleries table
    if not started_at:
        with lock, dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT started_at FROM Galleries WHERE id=?", (gallery_id,))
            row = cursor.fetchone()
            if row and row[0]:
                started_at = row[0]
    # Preserve extension_used if already set
    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT extension_used FROM Galleries WHERE id=?", (gallery_id,))
        row = cursor.fetchone()
        if row and row[0]:
            extension_used = row[0]
        else:
            extension_used = meta.get("extension_used") or meta.get("extension") or None
        cursor.execute("""
        UPDATE Galleries
        SET status = ?, completed_at = ?, download_path = ?, cover_path = ?, extension_used = ?, started_at = ?
        WHERE id = ?
        """, ("completed", now, download_path, cover_path, extension_used, started_at, gallery_id))
        logger.debug(f"[DATABASE] Marked gallery {gallery_id} as completed.")
        logger.debug(f"[DATABASE] Data: status=completed, completed_at={now}, download_path={download_path}, cover_path={cover_path}, extension_used={extension_used}, started_at={started_at}")
        conn.commit()

    # Now process all main tables for this gallery
    cache = read_cached_metadata_entry([gallery_id])
    creators = {}
    tags = {}
    languages = {}
    galleries = {}
    gallery_tags = {}
    gallery_languages = {}

    for gid, entry in cache.items():
        #logger.debug(f"[DATABASE] Processing gallery {gid} with metadata: {entry}")
        
        meta = entry.get("clean_metadata") or {}
        raw_title = meta.get("raw_title") or meta.get("title") or f"Gallery_{gid}"
        clean_title = meta.get("clean_title") or meta.get("title") or f"Gallery_{gid}"
        num_pages = meta.get("num_pages") or meta.get("pages") or 0
        
        # Creator Names
        creator_names = []
        creator_types = {}
        if "artists" in meta and isinstance(meta["artists"], list):
            creator_names.extend(meta["artists"])
            for artist in meta["artists"]:
                creator_types[artist] = "artist"
        if "groups" in meta and isinstance(meta["groups"], list):
            creator_names.extend(meta["groups"])
            for group in meta["groups"]:
                creator_types[group] = "group"
        
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

        #logger.debug(f"[DATABASE] Gallery fields: raw_title={raw_title}, clean_title={clean_title}, num_pages={num_pages}, creators={creator_names}, tags={tag_names}, languages={language_names}")

        for cname in creator_names:
            ctype = creator_types.get(cname, None)
            creators.setdefault(cname, {"display_name": cname, "creator_type": ctype, "first_seen": None, "last_updated": None, "total_galleries": 0, "most_popular_tags": []})
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

    with lock, dbconnect() as conn:
        cursor = conn.cursor()
        creator_id_map = {}
        tag_id_map = {}
        lang_id_map = {}
        now = datetime.now(timezone.utc).isoformat()

        for cname, cdata in creators.items():
            cursor.execute("INSERT OR IGNORE INTO Creators (name, display_name, creator_type, first_seen, last_updated, total_galleries, most_popular_tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cname, cdata["display_name"], cdata["creator_type"], now, now, 0, json.dumps([])))
            cursor.execute("UPDATE Creators SET creator_type=? WHERE name=?", (cdata["creator_type"], cname))
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
            creator_ids = [creator_id_map[c] for c in gdata["creator_names"] if c in creator_id_map]
            tag_ids = [tag_id_map[t] for t in gdata["tag_names"] if t in tag_id_map]
            language_ids = [lang_id_map[l] for l in gdata["language_names"] if l in lang_id_map]
            logger.debug(f"[DATABASE] Writing to Galleries (partial update): id={gid}, raw_title={gdata['raw_title']}, clean_title={gdata['clean_title']}, num_pages={gdata['num_pages']}, creator_ids={creator_ids}, language_ids={language_ids}, tag_ids={tag_ids}")
            cursor.execute(
                "UPDATE Galleries SET raw_title=?, clean_title=?, num_pages=?, creator_ids=?, language_ids=?, tag_ids=? WHERE id=?",
                (
                    gdata["raw_title"],
                    gdata["clean_title"],
                    gdata["num_pages"],
                    json.dumps(creator_ids),
                    json.dumps(language_ids),
                    json.dumps(tag_ids),
                    int(gid)
                )
            )
            cursor.execute("INSERT OR REPLACE INTO GalleryTags (gallery_id, tag_ids) VALUES (?, ?)", (int(gid), json.dumps(tag_ids)))
            cursor.execute("INSERT OR REPLACE INTO GalleryLanguages (gallery_id, language_ids) VALUES (?, ?)", (int(gid), json.dumps(language_ids)))

        for cname, cid in creator_id_map.items():
            cursor.execute("SELECT Galleries.id FROM Galleries, json_each(Galleries.creator_ids) WHERE json_each.value = ?", (cid,))
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
            #logger.debug(f"[DATABASE] Updating Creator {cname} (id={cid}): total_galleries={total_galleries}, most_popular_tags={most_popular_tag_ids}")
            cursor.execute("UPDATE Creators SET total_galleries=?, most_popular_tags=?, last_updated=? WHERE id=?", (total_galleries, json.dumps(most_popular_tag_ids), now, cid))

        for tname, tid in tag_id_map.items():
            cursor.execute("SELECT tag_ids FROM GalleryTags")
            count = 0
            for (tag_ids_json,) in cursor.fetchall():
                if tag_ids_json:
                    try:
                        tag_ids = json.loads(tag_ids_json)
                        count += tag_ids.count(tid)
                    except Exception:
                        continue
            #logger.debug(f"[DATABASE] Updating Tag {tname} (id={tid}): count={count}")
            cursor.execute("UPDATE Tags SET count=? WHERE id=?", (count, tid))

        for lname, lid in lang_id_map.items():
            cursor.execute("SELECT language_ids FROM GalleryLanguages")
            count = 0
            for (lang_ids_json,) in cursor.fetchall():
                if lang_ids_json:
                    try:
                        lang_ids = json.loads(lang_ids_json)
                        count += lang_ids.count(lid)
                    except Exception:
                        continue
            #logger.debug(f"[DATABASE] Updating Language {lname} (id={lid}): count={count}")
            cursor.execute("UPDATE Languages SET count=? WHERE id=?", (count, lid))

        conn.commit()