#!/usr/bin/env python3
# mangascraper/core/api.py

import os, sqlite3, threading, atexit, json, time, random, cloudscraper, requests, re, socket, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib import request as urllib_request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin
from pathlib import Path
from tqdm import tqdm

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *

####################################################################################################################
# GLOBAL VARIABLES
####################################################################################################################

# Locks
db_lock = threading.RLock() # Reentrant lock avoids self-deadlocks when DB code paths re-enter DB helpers.
_thread_local = threading.local()
possible_broken_symbols_lock = threading.Lock()
session_lock = threading.Lock()

DATA_DIR = os.path.join(orchestrator.SCRAPER_DIR, "mangascraper/core")
DB_PATH = os.path.join(DATA_DIR, "mangascraper.db")

atexit.register(lambda: DB.close_connection()) # Ensure DB connections are closed on exit

# Cache expiry windows (seconds)
CACHE_REFERENCES_TTL_SECONDS = 60 * 60 * 24 * 1 # day
CACHED_METADATA_TTL_SECONDS = 60 * 60 * 24 * 31 # days

session = None # Session object

# Pre-compile regex patterns for title cleaning (avoid recompilation on every call)
_BRACKET_PATTERN = re.compile(r"(\[.*?\]|\{.*?\})")
_DASH_PATTERN = re.compile(r"\s*[–—-]\s*")
_UNDERSCORE_PATTERN = re.compile(r"_+")

# Pre-build symbol translation table for faster replacements
_SYMBOL_TRANSLATION_TABLE = None

_runtime_progress_lock = threading.Lock()
_runtime_progress = {
    "current_gallery_number": 0,
    "current_gallery_id": None,
    "total_galleries": 0,
    "pages_processed": 0,
    "total_pages": 0,
    "pages_per_second": 0,
    "eta_seconds": 0,
    "download_speed_bytes": 0,
    "updated_at": 0,
}
_runtime_progress_server = None
_runtime_progress_server_thread = None
_runtime_progress_token = ""

DOWNLOAD_ROOT_MARKER_FILE = ".manga-scraper.dir"
DOWNLOAD_ROOT_MARKER_WARNING = (
    "This folder is managed by manga-scraper.\n"
    "If you remove this file while this folder still contains galleries, it could break things.\n"
)


class _RuntimeProgressRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _runtime_progress_token

        parsed = urlparse(self.path)
        if parsed.path != "/progress":
            self.send_response(404)
            self.end_headers()
            return

        if _runtime_progress_token:
            qs = parse_qs(parsed.query or "")
            provided = str((qs.get("token") or [""])[0])
            if provided != _runtime_progress_token:
                self.send_response(403)
                self.end_headers()
                return

        with _runtime_progress_lock:
            payload = dict(_runtime_progress)

        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class RuntimeProgress:
    """Cross-process runtime progress transport (downloader host + dashboard poll)."""

    @staticmethod
    def start_server(port: int | None = None, token: str | None = None) -> bool:
        global _runtime_progress_server, _runtime_progress_server_thread, _runtime_progress_token

        if _runtime_progress_server is not None:
            return True

        env_port = str(os.getenv("MANGASCRAPER_PROGRESS_PORT", "")).strip()
        selected_port = port
        if selected_port is None and env_port:
            try:
                selected_port = int(env_port)
            except Exception:
                selected_port = None
        if selected_port is None:
            return False

        selected_token = token if token is not None else str(os.getenv("MANGASCRAPER_PROGRESS_TOKEN", "")).strip()
        _runtime_progress_token = selected_token or ""

        try:
            _runtime_progress_server = ThreadingHTTPServer(("127.0.0.1", int(selected_port)), _RuntimeProgressRequestHandler)
            _runtime_progress_server_thread = threading.Thread(target=_runtime_progress_server.serve_forever, daemon=True)
            _runtime_progress_server_thread.start()
            return True
        except Exception:
            _runtime_progress_server = None
            _runtime_progress_server_thread = None
            return False

    @staticmethod
    def stop_server() -> None:
        global _runtime_progress_server, _runtime_progress_server_thread

        server = _runtime_progress_server
        thread = _runtime_progress_server_thread
        _runtime_progress_server = None
        _runtime_progress_server_thread = None

        if server is None:
            return
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        if thread is not None:
            try:
                thread.join(timeout=1)
            except Exception:
                pass

    @staticmethod
    def update(**kwargs) -> None:
        with _runtime_progress_lock:
            _runtime_progress.update(kwargs)
            _runtime_progress["updated_at"] = time.time()

    @staticmethod
    def snapshot() -> dict:
        with _runtime_progress_lock:
            return dict(_runtime_progress)

    @staticmethod
    def fetch(port: int | None, token: str | None, timeout_seconds: float = 0.35) -> dict:
        if not port:
            return {}
        query = urllib.parse.urlencode({"token": token or ""})
        url = f"http://127.0.0.1:{int(port)}/progress?{query}"
        try:
            with urllib_request.urlopen(url, timeout=timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}


atexit.register(lambda: RuntimeProgress.stop_server())

####################################################################################################################
# CACHING HELPERS
####################################################################################################################

def prune_all_caches():
    """Remove expired entries in cache tables based on expires_at. Returns count of deleted entries."""
    DB.init_db()
    now = time.time()
    with db_lock, DB.dbconnect() as conn:
        cursor = conn.cursor()
        # Delete expired cache references by expires_at
        cursor.execute(
            "DELETE FROM CacheReferences WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        cache_refs_deleted = cursor.rowcount if cursor.rowcount is not None else 0
        cursor.execute(
            "DELETE FROM CachedMetadata WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        cache_meta_deleted = cursor.rowcount if cursor.rowcount is not None else 0
        conn.commit()
        return int(cache_refs_deleted) + int(cache_meta_deleted)

def read_cached_metadata_entry(cache_key: str = None, gallery_id: int = None, cutoff: float = None, ids: list = None) -> dict | None:
    """
    Loads and returns metadata from CachedMetadata or entries from CacheReferences.
    - If no parameter is given, prunes expired entries and returns {"references": ..., "metadata": ...}.
    - If cache_key is given, returns the CacheReferences entry for that key (or None if not found).
    - If ids is given (list of gallery IDs), returns metadata for those galleries as a dict.
    - If gallery_id is given, returns metadata for that gallery (or None if not found).
    - If cutoff is given, returns all metadata entries newer than cutoff as a dict keyed by gallery ID.
    """
    DB.init_db()

    # No-args: full cache snapshot (references + metadata)
    if cache_key is None and gallery_id is None and cutoff is None and ids is None:
        try:
            prune_all_caches()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                now = time.time()

                cursor.execute(
                    """
                    SELECT cache_key, cache_type, cache_target, ids, expires_at
                    FROM CacheReferences
                    WHERE expires_at IS NULL OR expires_at > ?
                    """,
                    (now,),
                )
                references = {}
                for row in cursor.fetchall():
                    cache_key_val, cache_type, cache_target, ids_json, expires_at = row
                    parsed_ids = []
                    if ids_json:
                        try:
                            parsed_ids = Helpers.normalise_integer_list(json.loads(ids_json))
                        except Exception:
                            parsed_ids = []
                    references[str(cache_key_val)] = {
                        "cache_type": str(cache_type or ""),
                        "cache_key": str(cache_key_val),
                        "cache_target": str(cache_target or ""),
                        "ids": parsed_ids,
                        "expires_at": Helpers.safe_float(expires_at),
                    }

                cursor.execute(
                    """
                    SELECT gallery_id, timestamp, clean_metadata, raw_metadata, expires_at
                    FROM CachedMetadata
                    WHERE expires_at IS NULL OR expires_at > ?
                    """,
                    (now,),
                )
                metadata = {}
                for gid_val, timestamp, clean_json, raw_json, expires_at in cursor.fetchall():
                    gid = Helpers.normalise_integer(gid_val)
                    if gid is None:
                        continue
                    metadata[gid] = {
                        "timestamp": Helpers.safe_float(timestamp),
                        "expires_at": Helpers.safe_float(expires_at),
                        "clean_metadata": Helpers.safe_json_dict(clean_json),
                        "raw_metadata": Helpers.safe_json_dict(raw_json),
                    }
            return {"references": references, "metadata": metadata}
        except Exception:
            return {"references": {}, "metadata": {}}

    # CacheReferences: single key lookup
    if cache_key is not None:
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT cache_key, cache_type, cache_target, ids, expires_at
                FROM CacheReferences
                WHERE cache_key = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (str(cache_key), time.time()),
            )
            row = cursor.fetchone()
            if not row:
                return {"references": {}, "metadata": {}}
            cache_key_val, cache_type, cache_target, ids_json, expires_at = row
            parsed_ids = []
            if ids_json:
                try:
                    parsed_ids = Helpers.normalise_integer_list(json.loads(ids_json))
                except Exception:
                    parsed_ids = []
            entry = {
                "cache_type": str(cache_type or ""),
                "cache_key": str(cache_key_val),
                "cache_target": str(cache_target or ""),
                "ids": parsed_ids,
                "expires_at": Helpers.safe_float(expires_at),
            }
            return {"references": {str(cache_key_val): entry}, "metadata": {}}

    # CachedMetadata: one or more gallery lookups
    with db_lock, DB.dbconnect() as conn:
        cursor = conn.cursor()
        metadata = {}
        if ids is not None:
            ids_list = Helpers.normalise_integer_list(ids)
            if ids_list:
                placeholders = ",".join("?" for _ in ids_list)
                params = list(ids_list)
                query = (
                    "SELECT gallery_id, timestamp, clean_metadata, raw_metadata, expires_at "
                    "FROM CachedMetadata WHERE gallery_id IN (" + placeholders + ")"
                )
                query += " AND (expires_at IS NULL OR expires_at > ?)"
                params.append(time.time())
                if cutoff is not None:
                    query += " AND timestamp >= ?"
                    params.append(cutoff)
                cursor.execute(query, params)
                for gid_val, timestamp, clean_json, raw_json, expires_at in cursor.fetchall():
                    gid = Helpers.normalise_integer(gid_val)
                    if gid is None:
                        continue
                    metadata[gid] = {
                        "timestamp": Helpers.safe_float(timestamp),
                        "expires_at": Helpers.safe_float(expires_at),
                        "clean_metadata": Helpers.safe_json_dict(clean_json),
                        "raw_metadata": Helpers.safe_json_dict(raw_json),
                    }
        elif gallery_id is not None:
            gid = Helpers.normalise_integer(gallery_id)
            if gid is not None:
                cursor.execute(
                    """
                    SELECT timestamp, clean_metadata, raw_metadata, expires_at
                    FROM CachedMetadata
                    WHERE gallery_id = ?
                      AND (expires_at IS NULL OR expires_at > ?)
                    """,
                    (gid, time.time()),
                )
                row = cursor.fetchone()
                if row:
                    timestamp, clean_json, raw_json, expires_at = row
                    metadata[gid] = {
                        "timestamp": Helpers.safe_float(timestamp),
                        "expires_at": Helpers.safe_float(expires_at),
                        "clean_metadata": Helpers.safe_json_dict(clean_json),
                        "raw_metadata": Helpers.safe_json_dict(raw_json),
                    }
        elif cutoff is not None:
            cursor.execute(
                """
                SELECT gallery_id, timestamp, clean_metadata, raw_metadata, expires_at
                FROM CachedMetadata
                WHERE timestamp >= ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (cutoff, time.time()),
            )
            for gid_val, timestamp, clean_json, raw_json, expires_at in cursor.fetchall():
                gid = Helpers.normalise_integer(gid_val)
                if gid is None:
                    continue
                metadata[gid] = {
                    "timestamp": Helpers.safe_float(timestamp),
                    "expires_at": Helpers.safe_float(expires_at),
                    "clean_metadata": Helpers.safe_json_dict(clean_json),
                    "raw_metadata": Helpers.safe_json_dict(raw_json),
                }
        return {"references": {}, "metadata": metadata}

def clear_cached_items(cache_key: str = None, gallery_id: int = None):
    """Clear all cache, one CacheReferences row, or one CachedMetadata row."""
    DB.init_db()
    prune_all_caches()

    if cache_key is not None and gallery_id is not None:
        raise ValueError("Provide either cache_key or gallery_id, not both.")

    cleared = {
        "cache_keys": 0,
        "gallery_ids": [],
        "mode": "all",
    }

    with db_lock, DB.dbconnect() as conn:
        cursor = conn.cursor()

        if cache_key is None and gallery_id is None:
            cursor.execute("SELECT COUNT(*) FROM CacheReferences")
            row = cursor.fetchone()
            cleared["cache_keys"] = Helpers.normalise_integer(row[0]) or 0

            cursor.execute("DELETE FROM CacheReferences")
            cursor.execute("DELETE FROM CachedMetadata")
        elif cache_key is not None:
            cache_key = Helpers.safe_text(cache_key)
            cursor.execute("DELETE FROM CacheReferences WHERE cache_key=?", (cache_key,))
            cleared["cache_keys"] = cursor.rowcount if cursor.rowcount is not None else 0
            cleared["mode"] = "cache_key"
        else:
            gid = Helpers.normalise_integer(gallery_id)
            if gid is not None:
                cursor.execute("DELETE FROM CachedMetadata WHERE gallery_id=?", (gid,))
                if cursor.rowcount:
                    cleared["gallery_ids"] = [gid]
            cleared["mode"] = "gallery_id"

        conn.commit()

    return cleared

################################################################################################################

class Helpers:
    """Stateless helper utilities."""

    _WINDOWS_RESERVED_NAMES = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }

    @staticmethod
    def infer_location_root(download_path: str) -> str:
        """Infer the extension/root download directory from a stored gallery path.
        Prefers matching known DownloadLocations, then falls back to <path>/../.. .
        """
        safe_path = str(download_path or "").strip()
        if not safe_path:
            return ""
        normalised = os.path.normpath(safe_path)
        try:
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT root_path FROM DownloadLocations WHERE root_path IS NOT NULL AND TRIM(root_path) != ''")
                roots = [Helpers.safe_text(row[0], "") for row in cursor.fetchall()]
            matches = []
            norm_case = os.path.normcase(normalised)
            for root in roots:
                root_norm = os.path.normpath(root)
                root_case = os.path.normcase(root_norm)
                if norm_case == root_case or norm_case.startswith(root_case + os.sep):
                    matches.append(root_norm)
            if matches:
                # Most specific root wins.
                return max(matches, key=len)
        except (sqlite3.OperationalError, sqlite3.DatabaseError, Exception):
            # Best-effort inference only; continue to legacy fallback.
            pass
        parent = os.path.dirname(normalised)
        if not parent:
            return ""
        return os.path.dirname(parent) or parent

    @staticmethod
    def normalise_integer(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def normalise_integer_list(values) -> list[int]:
        if values is None:
            return []
        if not isinstance(values, list):
            values = [values]
        normalised = []
        for value in values:
            gid = Helpers.normalise_integer(value)
            if gid is not None:
                normalised.append(gid)
        return normalised

    @staticmethod
    def safe_json_dict(value) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
                return loaded if isinstance(loaded, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def safe_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def safe_text(value, default: str = "") -> str:
        if value is None:
            return default
        return str(value)

    @staticmethod
    def safe_text_list(values) -> list[str]:
        if values is None:
            return []
        if not isinstance(values, list):
            values = [values]
        result = []
        for value in values:
            text = Helpers.safe_text(value).strip()
            if text:
                result.append(text)
        return result

    @staticmethod
    def creator_candidates(meta: dict) -> list[str]:
        if not isinstance(meta, dict):
            return ["Unknown Creator"]
        creators = Get.meta_tags("api", meta, "artist") or Get.meta_tags("api", meta, "group")
        creators = Helpers.safe_text_list(creators)
        return creators or ["Unknown Creator"]

    @staticmethod
    def _normalise_path_component_key(value: str) -> str:
        text = Helpers.safe_text(value)
        return text.rstrip(" .").casefold()

    @staticmethod
    def _is_windows_reserved_component(value: str) -> bool:
        text = Helpers.safe_text(value).strip(" .")
        if not text:
            return True
        stem = text.split(".", 1)[0].upper()
        return stem in Helpers._WINDOWS_RESERVED_NAMES

    @staticmethod
    def is_unsafe_path_component(value: str) -> bool:
        text = Helpers.safe_text(value)
        if not text or text in {".", ".."}:
            return True
        if text[-1:] in {" ", "."}:
            return True
        if re.search(r'[<>:"/\\|?*\x00-\x1f]', text):
            return True
        if Helpers._is_windows_reserved_component(text):
            return True
        return False

    @staticmethod
    def choose_creator_folder_name(raw_name: str, base_path: str | None = None, fallback_name: str | None = None) -> str:
        raw = Helpers.safe_text(raw_name).strip()
        fallback = Helpers.safe_text(fallback_name or Helpers.sanitise(raw)).strip()

        if not raw:
            raw = "Unknown Creator"
        if not fallback:
            fallback = "Unknown Creator"

        chosen = raw
        if Helpers.is_unsafe_path_component(chosen):
            chosen = fallback

        if base_path:
            try:
                candidate_key = Helpers._normalise_path_component_key(chosen)
                for existing in os.listdir(base_path):
                    existing_path = os.path.join(base_path, existing)
                    if not os.path.isdir(existing_path):
                        continue
                    if Helpers._normalise_path_component_key(existing) != candidate_key:
                        continue
                    if existing != chosen:
                        chosen = fallback
                        break
            except Exception:
                pass

        if Helpers.is_unsafe_path_component(chosen):
            chosen = Helpers.sanitise(chosen) or "Unknown Creator"

        return chosen

    @staticmethod
    def resolve_creator_entries(meta: dict, base_path: str) -> list[dict]:
        """Resolve creator naming context with raw-first folder naming."""

        raw_creators = Helpers.creator_candidates(meta)
        entries = []
        seen_folders = set()

        for raw_name in raw_creators:
            display_name = Helpers.sanitise(raw_name)
            folder_name = Helpers.choose_creator_folder_name(
                raw_name=raw_name,
                base_path=base_path,
                fallback_name=display_name,
            )
            if folder_name in seen_folders:
                continue
            seen_folders.add(folder_name)
            entries.append(
                {
                    "raw_name": raw_name,
                    "display_name": display_name,
                    "folder_name": folder_name,
                }
            )

        return entries

    @staticmethod
    def sanitise(meta_or_title):
        def is_cjk(char: str) -> bool:
            code = ord(char)
            return (
                0x4E00 <= code <= 0x9FFF
                or 0x3400 <= code <= 0x4DBF
                or 0x20000 <= code <= 0x2A6DF
                or 0x2A700 <= code <= 0x2B73F
                or 0x2B740 <= code <= 0x2B81F
                or 0x2B820 <= code <= 0x2CEAF
                or 0x2CEB0 <= code <= 0x2EBEF
                or 0x3000 <= code <= 0x303F
                or 0x3040 <= code <= 0x309F
                or 0x30A0 <= code <= 0x30FF
                or 0x31F0 <= code <= 0x31FF
                or 0xFF65 <= code <= 0xFF9F
            )

        possible_broken_symbols = Cache.Load.broken_symbols()

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

        symbols = {c for c in title if ord(c) > 127 and not is_cjk(c)}
        known_symbols = set(ALLOWED_SYMBOLS).union(
            BROKEN_SYMBOL_REPLACEMENTS.keys(),
            BROKEN_SYMBOL_BLACKLIST,
            possible_broken_symbols.keys()
        )
        new_broken = symbols.difference(known_symbols)

        if new_broken:
            for s in new_broken:
                possible_broken_symbols[s] = "_"
            logger.debug(f"[BrokenSymbols] New broken symbols detected: {sorted(new_broken)}. Updating database.")
            Cache.Save.broken_symbols(possible_broken_symbols)
            Helpers.build_symbol_translation_table()

        title = _BRACKET_PATTERN.sub("", title)

        if _SYMBOL_TRANSLATION_TABLE is None:
            Helpers.build_symbol_translation_table()
        title = title.translate(_SYMBOL_TRANSLATION_TABLE)

        for symbol, replacement in possible_broken_symbols.items():
            title = title.replace(symbol, replacement)

        title = _DASH_PATTERN.sub("-", title)

        for symbol in BROKEN_SYMBOL_BLACKLIST:
            title = title.replace(symbol, "_")

        title = _UNDERSCORE_PATTERN.sub("_", title)
        title = " ".join(title.split())
        title = title.strip(" _")

        if not title:
            title = f"UNTITLED_{meta.get('id', 'UNKNOWN')}" if isinstance(meta_or_title, dict) else "UNTITLED"

        return title.replace("/", "-").replace("\\", "-").strip()

    @staticmethod
    def summary(meta, referrer: str):
        orchestrator.refresh_globals()

        artists = Get.meta_tags(f"{referrer}: Build_gallery_metadata_summary", meta, "artist")
        groups = Get.meta_tags(f"{referrer}: Build_gallery_metadata_summary", meta, "group")
        creators = artists or groups or ["Unknown Creator"]
        creators_clean = [Helpers.sanitise(c) for c in creators]

        title = Helpers.sanitise(meta)
        id = str(meta.get("id", "Unknown ID"))
        full_title = f"({id}) {title}"

        gallery_language = Get.meta_tags(
            f"{referrer}: Build_gallery_metadata_summary", meta, "language"
        ) or ["Unknown Language"]
        gallery_language_clean = [Helpers.sanitise(l) for l in gallery_language]

        clean_metadata = {
            "clean_title": title,
            "creator_names": creators_clean,
            "language_names": gallery_language_clean,
        }

        try:
            gid = int(id) if id.isdigit() else id
            Cache.upsert_cached_metadata(gid, time.time(), clean_metadata=clean_metadata)
        except Exception as e:
            logger.error(f"Failed to upsert cached metadata for Gallery {id}: {e}")

        return {
            "creator": creators_clean,
            "title": full_title,
            "short_title": title,
            "id": id,
            "language": gallery_language_clean,
        }

    @staticmethod
    def build_symbol_translation_table():
        global _SYMBOL_TRANSLATION_TABLE
        trans_dict = {ord(symbol): replacement for symbol, replacement in BROKEN_SYMBOL_REPLACEMENTS.items()}
        _SYMBOL_TRANSLATION_TABLE = trans_dict

class DB:
    """Database access namespace."""

    @staticmethod
    def connect():
        return DB.dbconnect()

    @staticmethod
    def dbconnect():
        conn = getattr(_thread_local, "connection", None)
        if conn is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            conn = sqlite3.connect(DB_PATH)
            conn.execute("PRAGMA foreign_keys = ON")
            _thread_local.connection = conn
        return conn

    @staticmethod
    def init():
        return DB.init_db()

    @staticmethod
    def init_db():
        orchestrator.refresh_globals()
        os.makedirs(DATA_DIR, exist_ok=True)
        with db_lock, DB.dbconnect() as conn:
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

            CREATE TABLE IF NOT EXISTS DownloadLocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_path TEXT NOT NULL UNIQUE,
                extension_used TEXT
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
                gallery_id INTEGER PRIMARY KEY,
                timestamp REAL,
                raw_metadata TEXT,
                clean_metadata TEXT,
                expires_at REAL
            );

            CREATE TABLE IF NOT EXISTS CacheReferences (
                cache_key TEXT PRIMARY KEY,
                cache_type TEXT,
                cache_target TEXT,
                ids TEXT,
                expires_at REAL
            );
            """)

            c.execute("PRAGMA table_info(CacheReferences)")
            cache_ref_columns = [row[1] for row in c.fetchall()]
            if "expires_at" not in cache_ref_columns:
                c.execute("ALTER TABLE CacheReferences ADD COLUMN expires_at REAL")

            c.execute("PRAGMA table_info(CachedMetadata)")
            cached_meta_columns = [row[1] for row in c.fetchall()]
            if "expires_at" not in cached_meta_columns:
                c.execute("ALTER TABLE CachedMetadata ADD COLUMN expires_at REAL")

            c.execute("PRAGMA table_info(Creators)")
            creators_columns = [row[1] for row in c.fetchall()]
            if "favourite" not in creators_columns:
                c.execute("ALTER TABLE Creators ADD COLUMN favourite INTEGER DEFAULT 0")

            now = time.time()
            c.execute(
                "UPDATE CacheReferences SET expires_at = ? WHERE expires_at IS NULL",
                (now + CACHE_REFERENCES_TTL_SECONDS,),
            )
            c.execute(
                """
                UPDATE CachedMetadata
                SET expires_at = CASE
                    WHEN timestamp IS NOT NULL THEN timestamp + ?
                    ELSE ?
                END
                WHERE expires_at IS NULL
                """,
                (CACHED_METADATA_TTL_SECONDS, now + CACHED_METADATA_TTL_SECONDS),
            )

            # ── GalleryLocations schema migration ─────────────────────────────────
            # Three cases:
            #  A) Fresh install – neither GalleryLocations nor DownloadLocations existed
            #     → create GalleryLocations with the new schema.
            #  B) Old single-table schema (has root_path column, no location_id)
            #     → populate DownloadLocations, migrate rows, swap table.
            #  C) New schema already present – nothing to do.
            c.execute("PRAGMA table_info(GalleryLocations)")
            gl_cols = {row[1] for row in c.fetchall()}

            if not gl_cols:
                # Case A: fresh install
                c.execute("""
                    CREATE TABLE GalleryLocations (
                        gallery_id INTEGER NOT NULL,
                        location_id INTEGER NOT NULL,
                        download_path TEXT NOT NULL,
                        cover_path TEXT,
                        first_seen TEXT,
                        last_seen TEXT,
                        PRIMARY KEY (gallery_id, location_id),
                        FOREIGN KEY (gallery_id) REFERENCES Galleries(id),
                        FOREIGN KEY (location_id) REFERENCES DownloadLocations(id)
                    )
                """)
                c.execute("CREATE INDEX IF NOT EXISTS idx_gallerylocations_gallery_id ON GalleryLocations(gallery_id)")

            elif "root_path" in gl_cols and "location_id" not in gl_cols:
                # Case B: migrate old single-table schema
                c.execute("SELECT DISTINCT root_path, extension_used FROM GalleryLocations WHERE root_path IS NOT NULL AND TRIM(root_path) != ''")
                for root_path, ext_used in c.fetchall():
                    c.execute(
                        "INSERT OR IGNORE INTO DownloadLocations (root_path, extension_used) VALUES (?, ?)",
                        (root_path, ext_used or ""),
                    )
                c.execute("""
                    CREATE TABLE GalleryLocations_new (
                        gallery_id INTEGER NOT NULL,
                        location_id INTEGER NOT NULL,
                        download_path TEXT NOT NULL,
                        cover_path TEXT,
                        first_seen TEXT,
                        last_seen TEXT,
                        PRIMARY KEY (gallery_id, location_id),
                        FOREIGN KEY (gallery_id) REFERENCES Galleries(id),
                        FOREIGN KEY (location_id) REFERENCES DownloadLocations(id)
                    )
                """)
                c.execute("SELECT gallery_id, root_path, download_path, cover_path, first_seen, last_seen FROM GalleryLocations WHERE root_path IS NOT NULL AND TRIM(root_path) != ''")
                for g_id, root_path, dl_path, cov_path, f_seen, l_seen in c.fetchall():
                    c.execute("SELECT id FROM DownloadLocations WHERE root_path=?", (root_path,))
                    loc = c.fetchone()
                    if loc:
                        c.execute(
                            "INSERT OR IGNORE INTO GalleryLocations_new (gallery_id, location_id, download_path, cover_path, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                            (g_id, loc[0], dl_path or "", cov_path or "", f_seen or "", l_seen or ""),
                        )
                c.execute("DROP TABLE GalleryLocations")
                c.execute("ALTER TABLE GalleryLocations_new RENAME TO GalleryLocations")
                c.execute("CREATE INDEX IF NOT EXISTS idx_gallerylocations_gallery_id ON GalleryLocations(gallery_id)")
            # Case C: new schema already present, no action needed.

            # ── Backfill GalleryLocations from Galleries.download_path ────────────
            c.execute(
                "SELECT id, extension_used, download_path, cover_path, started_at, completed_at FROM Galleries WHERE download_path IS NOT NULL AND TRIM(download_path) != ''"
            )
            for gallery_id, extension_used, download_path, cover_path, started_at, completed_at in c.fetchall():
                root_path = Helpers.infer_location_root(download_path)
                if not root_path:
                    continue
                first_seen = started_at or completed_at or datetime.now(timezone.utc).isoformat()
                last_seen = completed_at or started_at or datetime.now(timezone.utc).isoformat()
                c.execute(
                    "INSERT OR IGNORE INTO DownloadLocations (root_path, extension_used) VALUES (?, ?)",
                    (root_path, extension_used or ""),
                )
                c.execute("SELECT id FROM DownloadLocations WHERE root_path=?", (root_path,))
                loc = c.fetchone()
                if loc:
                    c.execute(
                        """
                        INSERT INTO GalleryLocations (gallery_id, location_id, download_path, cover_path, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(gallery_id, location_id) DO UPDATE SET
                            download_path=excluded.download_path,
                            cover_path=excluded.cover_path,
                            last_seen=excluded.last_seen
                        """,
                        (gallery_id, loc[0], download_path, cover_path or "", first_seen, last_seen),
                    )

            # Directly enforce creator display_name normalization for all existing rows.
            c.execute("SELECT id, name FROM Creators")
            creator_rows = c.fetchall()
            for creator_id, raw_name in creator_rows:
                display_name = Helpers.sanitise(Helpers.safe_text(raw_name, ""))
                c.execute(
                    "UPDATE Creators SET display_name=? WHERE id=?",
                    (display_name, creator_id),
                )

            conn.commit()

    @staticmethod
    def close():
        return DB.close_connection()

    @staticmethod
    def close_connection():
        conn = getattr(_thread_local, "connection", None)
        if conn is not None:
            conn.close()
            _thread_local.connection = None

    @staticmethod
    def migrate():
        return DB.init_db()

    @staticmethod
    def schema():
        return DB.init_db()

    @staticmethod
    def migrations():
        return DB.init_db()

    @staticmethod
    def list_table_names() -> list[str]:
        """Return all user-created table names in the database."""
        DB.init_db()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [row[0] for row in cursor.fetchall()]

    @staticmethod
    def query_table(table_name: str, search: str = None, limit: int = 500, offset: int = 0) -> dict:
        """
        Return rows and column names for any table in the database.
        table_name is validated against the live table list to prevent injection.
        Optional search filters any column that contains the search string.
        """
        DB.init_db()
        allowed = DB.list_table_names()
        if table_name not in allowed:
            return {"columns": [], "rows": [], "error": f"Unknown table '{table_name}'"}

        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")  # table name already validated above
            columns = [row[1] for row in cursor.fetchall()]

            if search and search.strip():
                like = f"%{search.strip()}%"
                conditions = " OR ".join(f"CAST({col} AS TEXT) LIKE ?" for col in columns)
                params = [like] * len(columns) + [limit, offset]
                cursor.execute(
                    f"SELECT * FROM {table_name} WHERE {conditions} LIMIT ? OFFSET ?",
                    params,
                )
            else:
                cursor.execute(f"SELECT * FROM {table_name} LIMIT ? OFFSET ?", (limit, offset))

            rows = [list(row) for row in cursor.fetchall()]

        return {"columns": columns, "rows": rows}

    @staticmethod
    def set_queued_galleries(ids):
        """Write a list of Gallery IDs into the database gallery queue."""
        DB.init_db()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM GalleriesQueue")
            for gid in set(ids or []):
                try:
                    cursor.execute("INSERT INTO GalleriesQueue (id) VALUES (?)", (int(gid),))
                except Exception:
                    continue
            conn.commit()

    @staticmethod
    def list_galleries(status=None):
        """List all galleries, optionally filtered by status."""
        DB.init_db()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries WHERE status=?", (status,))
            else:
                cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries")
            rows = cursor.fetchall()
            result = []
            for row in rows:
                gid = Helpers.normalise_integer(row[0])
                if gid is None:
                    continue
                result.append((gid, Helpers.safe_text(row[1], ""), Helpers.safe_text(row[2], ""), Helpers.safe_text(row[3], "")))
            return result

    @staticmethod
    def upsert_gallery_location(gallery_id, extension_used=None, download_path=None, cover_path=None, first_seen=None, last_seen=None, root_path=None):
        DB.init_db()
        gallery_id = Helpers.normalise_integer(gallery_id)
        download_path = Helpers.safe_text(download_path, "")
        if gallery_id is None or not download_path:
            return
        extension_used = Helpers.safe_text(extension_used, "")
        cover_path = Helpers.safe_text(cover_path, "")
        explicit_root = Helpers.safe_text(root_path, "")
        if explicit_root:
            explicit_root = os.path.normpath(explicit_root)
        if explicit_root:
            root_path = explicit_root
        else:
            root_path = Helpers.infer_location_root(download_path)
        if not root_path:
            return
        first_seen = Helpers.safe_text(first_seen, "") or datetime.now(timezone.utc).isoformat()
        last_seen = Helpers.safe_text(last_seen, "") or first_seen
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO DownloadLocations (root_path, extension_used) VALUES (?, ?)",
                (root_path, extension_used),
            )
            cursor.execute("SELECT id FROM DownloadLocations WHERE root_path=?", (root_path,))
            loc = cursor.fetchone()
            if not loc:
                return
            cursor.execute(
                """
                INSERT INTO GalleryLocations (gallery_id, location_id, download_path, cover_path, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(gallery_id, location_id) DO UPDATE SET
                    download_path=excluded.download_path,
                    cover_path=excluded.cover_path,
                    last_seen=excluded.last_seen
                """,
                (gallery_id, loc[0], download_path, cover_path, first_seen, last_seen),
            )
            conn.commit()

    @staticmethod
    def list_download_locations() -> list[dict]:
        """Return one dict per distinct download root, with a gallery count."""
        DB.init_db()  # Ensure schema is initialized
        DB.prune_unmanaged_download_locations()  # Prune stale locations (skips init_db)
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT dl.id, dl.root_path, dl.extension_used, COUNT(gl.gallery_id) AS gallery_count
                FROM DownloadLocations dl
                LEFT JOIN GalleryLocations gl ON gl.location_id = dl.id
                GROUP BY dl.id
                ORDER BY dl.extension_used, dl.root_path
            """)
            return [
                {
                    "id": Helpers.normalise_integer(row[0]),
                    "root_path": Helpers.safe_text(row[1], ""),
                    "extension_used": Helpers.safe_text(row[2], ""),
                    "count": Helpers.normalise_integer(row[3]) or 0,
                }
                for row in cursor.fetchall()
            ]

    @staticmethod
    def upsert_download_location(root_path: str, extension_used: str = ""):
        """Ensure a managed download root exists in DownloadLocations."""
        DB.init_db()
        root_path = Helpers.safe_text(root_path, "").strip()
        if not root_path:
            return
        root_path = os.path.normpath(root_path)
        extension_used = Helpers.safe_text(extension_used, "").strip()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO DownloadLocations (root_path, extension_used)
                VALUES (?, ?)
                ON CONFLICT(root_path) DO UPDATE SET
                    extension_used = CASE
                        WHEN excluded.extension_used IS NOT NULL AND TRIM(excluded.extension_used) != ''
                        THEN excluded.extension_used
                        ELSE DownloadLocations.extension_used
                    END
                """,
                (root_path, extension_used),
            )
            conn.commit()

    @staticmethod
    def prune_unmanaged_download_locations() -> dict:
        """Remove DownloadLocations rows whose root path is missing the marker file."""
        # Note: Do NOT call init_db here; called by database_cleanup or by list_download_locations
        removed = {"roots": 0, "gallery_locations": 0}
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, root_path FROM DownloadLocations")
            rows = cursor.fetchall()
            for loc_id, root_path in rows:
                loc_id = Helpers.normalise_integer(loc_id)
                root_text = Helpers.safe_text(root_path, "").strip()
                if loc_id is None or not root_text:
                    continue

                marker_path = os.path.join(root_text, DOWNLOAD_ROOT_MARKER_FILE)
                if os.path.isfile(marker_path):
                    continue

                cursor.execute("DELETE FROM GalleryLocations WHERE location_id=?", (loc_id,))
                gl_deleted = cursor.rowcount if cursor.rowcount is not None else 0
                cursor.execute("DELETE FROM DownloadLocations WHERE id=?", (loc_id,))
                dl_deleted = cursor.rowcount if cursor.rowcount is not None else 0
                if gl_deleted > 0:
                    removed["gallery_locations"] += int(gl_deleted)
                if dl_deleted > 0:
                    removed["roots"] += int(dl_deleted)

            conn.commit()
        return removed

    @staticmethod
    def database_cleanup() -> dict:
        """
        Comprehensive database cleanup and maintenance.
        
        Performs:
        1. Prunes expired cache entries
        2. Prunes unmanaged download locations (missing marker file)
        3. Identifies and removes orphaned galleries (files don't exist on disk)
        4. Checks page counts and identifies missing pages
        
        Returns a dictionary with cleanup statistics.
        Consolidates cache and orphan cleanup operations into a single transaction.
        """
        DB.init_db()
        
        stats = {
            "cache_entries_pruned": 0,
            "pruned_roots": 0,
            "pruned_gallery_locations": 0,
            "removed_galleries": 0,
            "page_checks": 0,
            "pages_downloaded": 0,
            "errors": [],
        }
        
        try:
            # Step 0: Prune expired cache entries
            logger.debug("[DATABASE_CLEANUP] Pruning expired cache entries...")
            stats["cache_entries_pruned"] = prune_all_caches()
            
            # Step 1: Prune unmanaged download locations
            logger.debug("[DATABASE_CLEANUP] Pruning unmanaged download locations...")
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, root_path FROM DownloadLocations")
                dl_rows = cursor.fetchall()
                
                for loc_id, root_path in dl_rows:
                    loc_id = Helpers.normalise_integer(loc_id)
                    root_text = Helpers.safe_text(root_path, "").strip()
                    if loc_id is None or not root_text:
                        continue

                    marker_path = os.path.join(root_text, DOWNLOAD_ROOT_MARKER_FILE)
                    if os.path.isfile(marker_path):
                        continue

                    cursor.execute("DELETE FROM GalleryLocations WHERE location_id=?", (loc_id,))
                    gl_deleted = cursor.rowcount if cursor.rowcount is not None else 0
                    cursor.execute("DELETE FROM DownloadLocations WHERE id=?", (loc_id,))
                    dl_deleted = cursor.rowcount if cursor.rowcount is not None else 0
                    if gl_deleted > 0:
                        stats["pruned_gallery_locations"] += int(gl_deleted)
                    if dl_deleted > 0:
                        stats["pruned_roots"] += int(dl_deleted)
                
                conn.commit()
            
            # Step 2: Check for orphaned galleries (files don't exist on disk)
            logger.debug("[DATABASE_CLEANUP] Scanning for orphaned galleries...")
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT gl.gallery_id, gl.download_path, gl.location_id
                    FROM GalleryLocations gl
                    JOIN DownloadLocations dl ON dl.id = gl.location_id
                """)
                location_rows = cursor.fetchall()
            
            orphaned_galleries = []
            for gallery_id, download_path, location_id in location_rows:
                gid = Helpers.normalise_integer(gallery_id)
                dpath = Helpers.safe_text(download_path, "").strip()
                
                if gid is None or not dpath:
                    continue
                
                if not os.path.exists(dpath):
                    orphaned_galleries.append((gid, location_id, dpath))
                    logger.debug(f"[DATABASE_CLEANUP] Orphaned gallery found: {gid} at missing path {dpath}")
            
            if orphaned_galleries:
                with db_lock, DB.dbconnect() as conn:
                    cursor = conn.cursor()
                    for gid, location_id, dpath in orphaned_galleries:
                        try:
                            cursor.execute("DELETE FROM GalleryLocations WHERE gallery_id=? AND location_id=?", (gid, location_id))
                            cursor.execute("SELECT COUNT(*) FROM GalleryLocations WHERE gallery_id=?", (gid,))
                            remaining = cursor.fetchone()[0] if cursor.fetchone() else 0
                            if remaining == 0:
                                cursor.execute("DELETE FROM Galleries WHERE id=?", (gid,))
                                stats["removed_galleries"] += 1
                                logger.info(f"[DATABASE_CLEANUP] Removed orphaned gallery {gid}")
                        except Exception as e:
                            stats["errors"].append(f"Error removing gallery {gid}: {e}")
                            logger.warning(f"[DATABASE_CLEANUP] Error removing gallery {gid}: {e}")
                    
                    conn.commit()
            
            # Step 3: Check page counts and identify missing pages
            logger.debug("[DATABASE_CLEANUP] Checking gallery page counts...")
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, num_pages, download_path
                    FROM Galleries
                    WHERE status = 'completed' AND num_pages > 0
                """)
                gallery_rows = cursor.fetchall()
            
            missing_pages = []
            for gallery_id, num_pages, download_path in gallery_rows:
                gid = Helpers.normalise_integer(gallery_id)
                num_p = Helpers.normalise_integer(num_pages) or 0
                dpath = Helpers.safe_text(download_path, "").strip()
                
                if gid is None or num_p <= 0 or not dpath or not os.path.exists(dpath):
                    continue
                
                stats["page_checks"] += 1
                
                try:
                    if os.path.isdir(dpath):
                        image_files = [f for f in os.listdir(dpath) 
                                     if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))]
                        actual_count = len(image_files)
                    elif dpath.endswith(('.cbz', '.zip')):
                        try:
                            import zipfile
                            with zipfile.ZipFile(dpath, 'r') as z:
                                actual_count = len([f for f in z.namelist() 
                                                  if not f.endswith('/') and 
                                                  f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))])
                        except Exception:
                            actual_count = -1
                    else:
                        continue
                    
                    if actual_count >= 0 and actual_count < num_p:
                        missing_count = num_p - actual_count
                        missing_pages.append((gid, num_p, actual_count, missing_count, dpath))
                        logger.debug(f"[DATABASE_CLEANUP] Gallery {gid}: {actual_count}/{num_p} pages (missing {missing_count})")
                except Exception as e:
                    logger.debug(f"[DATABASE_CLEANUP] Error checking pages for gallery {gid}: {e}")
            
            stats["page_checks"] = len(missing_pages)
            
            logger.info(f"[DATABASE_CLEANUP] Cleanup complete: {stats}")
        
        except Exception as e:
            logger.error(f"[DATABASE_CLEANUP] Fatal error during cleanup: {e}")
            stats["errors"].append(f"Fatal error: {e}")
        
        return stats

    @staticmethod
    def list_gallery_locations(gallery_id=None, root_path=None) -> list[dict]:
        """Return location rows joined from GalleryLocations + DownloadLocations."""
        DB.init_db()
        query = """
            SELECT gl.gallery_id, dl.extension_used, dl.root_path, gl.download_path,
                   gl.cover_path, gl.first_seen, gl.last_seen, dl.id
            FROM GalleryLocations gl
            JOIN DownloadLocations dl ON dl.id = gl.location_id
        """
        clauses = []
        params = []
        gid = Helpers.normalise_integer(gallery_id)
        root_path_filter = Helpers.safe_text(root_path, "")
        if gid is not None:
            clauses.append("gl.gallery_id=?")
            params.append(gid)
        if root_path_filter:
            clauses.append("dl.root_path=?")
            params.append(root_path_filter)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY dl.root_path, gl.download_path"
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return [
                {
                    "gallery_id": Helpers.normalise_integer(row[0]),
                    "extension_used": Helpers.safe_text(row[1], ""),
                    "root_path": Helpers.safe_text(row[2], ""),
                    "download_path": Helpers.safe_text(row[3], ""),
                    "cover_path": Helpers.safe_text(row[4], ""),
                    "first_seen": Helpers.safe_text(row[5], ""),
                    "last_seen": Helpers.safe_text(row[6], ""),
                    "location_id": Helpers.normalise_integer(row[7]),
                }
                for row in cursor.fetchall()
            ]

    class Gallery:
        @staticmethod
        def status(gallery_id):
            return Get.gallery_status(gallery_id)

        @staticmethod
        def start(gallery_id, download_path=None, extension_used=None):
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return
            download_path = Helpers.safe_text(download_path, "")
            extension_used = Helpers.safe_text(extension_used, "")
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT extension_used FROM Galleries WHERE id=?", (gallery_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    cursor.execute("""
                    INSERT INTO Galleries (id, status, started_at, download_path)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status=excluded.status,
                        started_at=excluded.started_at,
                        download_path=excluded.download_path
                    """, (gallery_id, "started", now, download_path))
                else:
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
            
            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as started.")
            #logger.debug(f"[DATABASE] Data: status=started, started_at={now}, download_path={download_path}, extension_used={extension_used}")

        @staticmethod
        def skip(gallery_id):
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                UPDATE Galleries
                SET status = ?, completed_at = ?
                WHERE id = ?
                """, ("skipped", now, gallery_id))
                conn.commit()
            
            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as skipped.")

        @staticmethod
        def fail(gallery_id):
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                UPDATE Galleries
                SET status = ?, completed_at = ?
                WHERE id = ?
                """, ("failed", now, gallery_id))
                conn.commit()
            
            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as failed.")

        @staticmethod
        def complete(gallery_id):
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return
            now = datetime.now(timezone.utc).isoformat()
            cache = read_cached_metadata_entry(ids=[gallery_id])
            meta = None
            for gid, entry in cache["metadata"].items():
                meta = entry.get("clean_metadata") or {}
                break

            download_path = None
            cover_path = None
            extension_used = None
            started_at = None
            ext_download_path = ""
            cleaned_creator = "Unknown"
            ext = "cbz"
            is_archive = True
            gallery_title = ""

            if meta and meta.get("clean_title"):
                with db_lock, DB.dbconnect() as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE Galleries SET clean_title=? WHERE id=?", (meta["clean_title"], gallery_id))
                    conn.commit()

            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT clean_title FROM Galleries WHERE id=?", (gallery_id,))
                row = cursor.fetchone()
                if row and isinstance(row[0], str) and row[0].strip():
                    gallery_title = row[0].strip()

            if meta:
                ext_download_path = meta.get("extension_download_path") or meta.get("download_path") or None
                base_ext_path = None
                try:
                    from mangascraper.extensions.extension_manager import calculate_extension_download_path
                    ext_name = meta.get("extension_used") or meta.get("extension") or getattr(orchestrator, "extension", "skeleton")
                    base_ext_path = calculate_extension_download_path(ext_name)
                except Exception:
                    base_ext_path = getattr(orchestrator, "extension_download_path", "/opt/manga-scraper/downloads/")
                if not ext_download_path:
                    ext_download_path = base_ext_path
                elif not os.path.isabs(ext_download_path):
                    ext_download_path = os.path.join(base_ext_path, ext_download_path)

                primary_creator = None
                if "artists" in meta and isinstance(meta["artists"], list) and meta["artists"]:
                    primary_creator = meta["artists"][0]
                elif "groups" in meta and isinstance(meta["groups"], list) and meta["groups"]:
                    primary_creator = meta["groups"][0]
                else:
                    primary_creator = "Unknown"
                cleaned_creator = Helpers.choose_creator_folder_name(
                    raw_name=primary_creator,
                    base_path=ext_download_path,
                    fallback_name=Helpers.sanitise(primary_creator),
                )
                ext = meta.get("archive_ext") or meta.get("ext") or "cbz"
                is_archive = meta.get("is_archive", True)
                started_at = meta.get("started_at")

            if gallery_title:
                if is_archive:
                    download_path = os.path.join(ext_download_path, cleaned_creator, f"{gallery_title}.{ext}")
                else:
                    download_path = os.path.join(ext_download_path, cleaned_creator, gallery_title)
                cover_path = os.path.join(ext_download_path, cleaned_creator, ".covers", f"({gallery_id}) {gallery_title}")
            else:
                download_path = ""
                cover_path = ""

            if not started_at:
                with db_lock, DB.dbconnect() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT started_at FROM Galleries WHERE id=?", (gallery_id,))
                    row = cursor.fetchone()
                    if row and row[0]:
                        started_at = row[0]

            with db_lock, DB.dbconnect() as conn:
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
                conn.commit()

            DB.upsert_gallery_location(
                gallery_id,
                extension_used=extension_used,
                download_path=download_path,
                cover_path=cover_path,
                first_seen=started_at or now,
                last_seen=now,
                root_path=ext_download_path,
            )
            
            cache = read_cached_metadata_entry(ids=[gallery_id])
            creators = {}
            tags = {}
            languages = {}
            galleries = {}
            gallery_tags = {}
            gallery_languages = {}

            for gid, entry in cache["metadata"].items():
                meta = entry.get("clean_metadata") or {}
                raw_title = Helpers.safe_text(meta.get("raw_title") or meta.get("title") or f"Gallery_{gid}")
                clean_title = Helpers.safe_text(meta.get("clean_title") or meta.get("title") or f"Gallery_{gid}")
                num_pages = Helpers.normalise_integer(meta.get("num_pages") or meta.get("pages") or 0) or 0

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

                tag_names = meta.get("tags") or []
                if isinstance(tag_names, str):
                    tag_names = [tag_names]

                language_names = meta.get("languages") or meta.get("language") or []
                if isinstance(language_names, str):
                    language_names = [language_names]

                status = Helpers.safe_text(meta.get("status"), "")
                started_at = Helpers.safe_text(meta.get("started_at"), "")
                completed_at = Helpers.safe_text(meta.get("completed_at"), "")
                download_path = Helpers.safe_text(meta.get("download_path"), "")
                cover_path = Helpers.safe_text(meta.get("cover_path"), "")
                extension_used = Helpers.safe_text(meta.get("extension_used"), "")

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

            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                creator_id_map = {}
                tag_id_map = {}
                lang_id_map = {}
                now = datetime.now(timezone.utc).isoformat()

                for cname, cdata in creators.items():
                    display_name = Helpers.sanitise(cname)
                    cursor.execute("INSERT OR IGNORE INTO Creators (name, display_name, creator_type, first_seen, last_updated, total_galleries, most_popular_tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (cname, display_name, cdata["creator_type"], now, now, 0, json.dumps([])))
                    cursor.execute("UPDATE Creators SET creator_type=?, display_name=? WHERE name=?", (cdata["creator_type"], display_name, cname))
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
                    #logger.debug(f"[DATABASE] Writing to Galleries (partial update): id={gid}, raw_title={gdata['raw_title']}, clean_title={gdata['clean_title']}, num_pages={gdata['num_pages']}, creator_ids={creator_ids}, language_ids={language_ids}, tag_ids={tag_ids}")
                    cursor.execute(
                        "UPDATE Galleries SET raw_title=?, clean_title=?, num_pages=?, creator_ids=?, language_ids=?, tag_ids=? WHERE id=?",
                        (
                            gdata["raw_title"],
                            gdata["clean_title"],
                            gdata["num_pages"],
                            json.dumps(creator_ids),
                            json.dumps(language_ids),
                            json.dumps(tag_ids),
                            gid
                        )
                    )
                    cursor.execute("INSERT OR REPLACE INTO GalleryTags (gallery_id, tag_ids) VALUES (?, ?)", (gid, json.dumps(tag_ids)))
                    cursor.execute("INSERT OR REPLACE INTO GalleryLanguages (gallery_id, language_ids) VALUES (?, ?)", (gid, json.dumps(language_ids)))

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
                    cursor.execute("UPDATE Languages SET count=? WHERE id=?", (count, lid))

                conn.commit()
            
            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as completed.")
            #logger.debug(f"[DATABASE] Data: status=completed, completed_at={now}, download_path={download_path}, cover_path={cover_path}, extension_used={extension_used}, started_at={started_at}")

        @staticmethod
        def list():
            return DB.list_galleries()

        @staticmethod
        def list_by_status(status):
            return DB.list_galleries(status=status)

        @staticmethod
        def list_as_dicts(status=None) -> "list[dict]":
            """Return gallery rows as dicts with string-safe fields."""
            rows = DB.list_galleries(status=status)
            result = []
            for row in rows:
                if isinstance(row, dict):
                    gid = row.get("id")
                    payload = {
                        "id": gid,
                        "status": row.get("status"),
                        "started_at": row.get("started_at"),
                        "completed_at": row.get("completed_at"),
                    }
                elif isinstance(row, (list, tuple)) and len(row) >= 4:
                    gid = row[0]
                    payload = {
                        "id": gid,
                        "status": row[1],
                        "started_at": row[2],
                        "completed_at": row[3],
                    }
                else:
                    continue
                payload["locations"] = DB.list_gallery_locations(gallery_id=gid)
                result.append(payload)
            return result

        @staticmethod
        def favourite(gallery_id, value=None):
            """Toggle or set favourite for a gallery and return the new value (0 or 1)."""
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return 0

            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT favourite FROM Galleries WHERE id=?", (gallery_id,))
                row = cursor.fetchone()
                current = int(row[0]) if row and row[0] is not None else 0
                if value is None:
                    new_value = 0 if current else 1
                else:
                    new_value = 1 if bool(value) else 0
                cursor.execute(
                    "INSERT INTO Galleries (id, favourite) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET favourite=excluded.favourite",
                    (gallery_id, new_value),
                )
                conn.commit()
                return int(new_value)

    class Creator:
        @staticmethod
        def favourite(creator_name, value=None):
            """Toggle or set favourite for a creator and return the new value (0 or 1)."""
            DB.init_db()
            creator_name = Helpers.safe_text(creator_name, "").strip()
            if not creator_name:
                return 0

            display_name = Helpers.sanitise(creator_name)
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, favourite
                    FROM Creators
                    WHERE LOWER(name)=LOWER(?) OR LOWER(display_name)=LOWER(?)
                    ORDER BY CASE WHEN LOWER(name)=LOWER(?) THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (creator_name, display_name, creator_name),
                )
                row = cursor.fetchone()

                if row:
                    creator_id = int(row[0])
                    current = int(row[1]) if row[1] is not None else 0
                else:
                    cursor.execute(
                        "INSERT INTO Creators (name, display_name, creator_type, first_seen, last_updated, total_galleries, most_popular_tags, notes, favourite) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (creator_name, display_name, "", "", "", 0, "[]", "", 0),
                    )
                    creator_id = int(cursor.lastrowid)
                    current = 0

                if value is None:
                    new_value = 0 if current else 1
                else:
                    new_value = 1 if bool(value) else 0

                cursor.execute("UPDATE Creators SET favourite=? WHERE id=?", (new_value, creator_id))
                conn.commit()
                return int(new_value)

class Sleep:
    """Adaptive retry sleep calculations."""

    @staticmethod
    def calculate_load(stage: str, num_items: int, attempt: int, gallery_cap: int = 3750):
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

    @staticmethod
    def dynamic(stage, attempt: int = 1):
        gallery_cap = 3750

        log_clarification("debug")
        log("------------------------------", "debug")
        log(f"{stage.capitalize()} Attempt: {attempt}", "debug")
        log_clarification("debug")

        if stage == "api":
            attempt_scale = attempt ** 2
            base_min, base_max = orchestrator.min_api_sleep * attempt_scale, orchestrator.max_api_sleep * attempt_scale
            sleep_time = random.uniform(base_min, base_max)
            log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s", "debug")
            log("------------------------------", "debug")
            log_clarification()
            return sleep_time

        if stage in ("gallery", "image"):
            num_items = 1 if stage == "gallery" else max(1, orchestrator.total_gallery_images)
            log(f"→ Number of {stage.capitalize()}s: {num_items} (Capped at {gallery_cap})", "debug")
            sleep_time, current_load, gallery_threads, image_threads, concurrency = Sleep.calculate_load(stage, num_items, attempt, gallery_cap)

            log_clarification("debug")
            log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Load: {current_load:.2f} Units)", "debug")
            log("------------------------------", "debug")
            log_clarification()
            return sleep_time

class Build:
    """Build URLs and derived query keys."""
    
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

    @staticmethod
    def url(query_type: str, query_value: str, sort_value: str, page: int) -> str:
        orchestrator.refresh_globals()

        query_lower = query_type.lower()

        if query_lower == "homepage":
            if sort_value == "date":
                return f"{nhentai_api_base}/galleries/all?page={page}"
            return f"{nhentai_api_base}/galleries/all?page={page}&sort={sort_value}"

        if query_lower in ("artist", "group", "tag", "character", "parody"):
            search_value = query_value
            if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
                search_value = f'"{search_value}"'
            encoded = urllib.parse.quote(f"{query_type}:{search_value}", safe=':"')

            if sort_value == "date":
                return f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}"
            return f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"

        if query_lower == "search":
            search_value = query_value.strip('"').strip("'")
            encoded = urllib.parse.quote_plus(search_value)

            if sort_value == "date":
                return f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}"
            return f"{nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"

        raise ValueError(f"Unknown query format: {query_type}='{query_value}'")

    @staticmethod
    def estimate_gallery_size(meta: dict, use_head_requests: bool = False) -> tuple:
        orchestrator.refresh_globals()

        pages = meta.get("images", {}).get("pages", [])
        image_count = len(pages)
        if image_count == 0:
            return 0, 0, 0

        type_sizes = {"j": 85000, "p": 180000, "g": 520000, "w": 65000}
        estimated_total = 0
        actual_total = 0
        fetched_count = 0

        for i, page_info in enumerate(pages):
            type_code = page_info.get("t", "w") if page_info else "w"
            estimated_total += type_sizes.get(type_code, 65000)

            if use_head_requests and page_info and meta.get("media_id"):
                try:
                    ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
                    ext = ext_map.get(type_code, "webp")
                    filename = f"{i + 1}.{ext}"
                    urls = [
                        f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
                        for mirror in orchestrator.nhentai_mirrors[:1]
                    ]

                    if urls:
                        resp = Get.session(referrer="API", status="return").head(urls[0], timeout=(10, 10))
                        if resp.status_code == 200:
                            actual_total += int(resp.headers.get("content-length", type_sizes.get(type_code, 65000)))
                            fetched_count += 1
                except Exception:
                    pass

        if fetched_count > 0 and fetched_count < image_count:
            avg_actual = actual_total / fetched_count
            remaining = image_count - fetched_count
            actual_total += int(avg_actual * remaining)
        elif fetched_count == 0:
            actual_total = estimated_total

        return estimated_total, actual_total, image_count

    @staticmethod
    def cache_keys(search_type: str, search_value: str = None) -> str:
        """Generate cache key based on search criteria."""
        search_type = str(search_type or "")
        if search_value is not None:
            search_value = str(search_value)

        if search_value:
            terms = [t for t in search_value.lower().split() if t]
            if len(terms) > 1:
                terms = sorted(terms, key=lambda x: (x.isdigit(), x))
            sorted_value = "_".join(terms)
            safe_value = "".join(c for c in sorted_value if c.isalnum() or c in ('-', '_', '+')).lower()
            logger.debug(f"[DATABASE]: Generated Cache Key '{search_type}:{safe_value}'")
            return f"{search_type}:{safe_value}"
        return search_type

class Get:
    """Get a resource, usually generated."""
    
    @staticmethod
    def gallery_status(gallery_id):
        """Get the status of a Gallery keyed by its ID"""
        gallery_id = Helpers.normalise_integer(gallery_id)
        if gallery_id is None:
            return None
        DB.init_db()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM Galleries WHERE id=?", (gallery_id,))
            row = cursor.fetchone()
            return Helpers.safe_text(row[0], "") if row else None
    
    @staticmethod # HTTP SESSION
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
            
            # Lazily build a session if caller requested the current session but none exists yet.
            if status not in ["build", "rebuild"] and session is not None:
                return session
            if status not in ["build", "rebuild"] and session is None:
                status = "build"
            
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
        
    @staticmethod # METADATA CLEANING
    def meta_tags(referrer: str, meta, tag_type):
        """
        Extract all tag names of a given type (artist, group, parody, language, etc.).
        - Splits names on "|".
        - Returns [] if none found.
        """
        
        if not isinstance(meta, dict):
            return []

        tags = meta.get("tags")
        if not isinstance(tags, list):
            return []

        tag_type = Helpers.safe_text(tag_type)

        names = []
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            if tag.get("type") == tag_type and tag.get("name"):
                parts = [t.strip() for t in tag["name"].split("|") if t.strip()]
                names.extend(parts)
        
        #log(f"Fetcher: '{referrer}' Requested Tag Type '{tag_type}', returning {names}", "debug") # NOTE: DEBUGGING
        return names
    
    # Utility functions for metadata extraction
    @staticmethod
    def artists(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "artist")) or ["Unknown Artist"]

    @staticmethod
    def groups(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "group")) or ["Unknown Group"]

    @staticmethod
    def tags(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "tag"))

    @staticmethod
    def characters(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "character"))

    @staticmethod
    def parodies(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "parody"))

    @staticmethod
    def languages(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "language")) or ["Unknown Language"]

    @staticmethod
    def page_count(meta):
        if not isinstance(meta, dict):
            return 0
        return len(meta.get("images", {}).get("pages", []))
    
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
    """Fetch a resource"""

    @staticmethod
    def latest_gallery_id(timeout: int = 5) -> int | None:
        """Fetch the latest gallery ID directly from nhentai homepage API."""

        orchestrator.refresh_globals()
        try:
            log_clarification("debug")
            log("Fetching latest gallery ID from nhentai homepage...", "debug")

            session = Get.session(referrer="Latest ID Fetch", status="return")
            url = f"{nhentai_api_base}/galleries/all?page=1"

            resp = session.get(url, timeout=(timeout, timeout))
            resp.raise_for_status()
            data = resp.json()

            results = data.get("result", []) if isinstance(data, dict) else []
            if results:
                latest_id = Helpers.normalise_integer(results[0].get("id"))
                if latest_id is not None:
                    log_clarification("debug")
                    log(f"Latest gallery ID fetched: {latest_id}", "debug")
                    return latest_id
        except Exception as e:
            log_clarification("debug")
            logger.warning(f"Could not fetch latest gallery ID: {e}")
        return None
    
    @staticmethod # GALLERY ID FETCHING
    def gallery_ids(
        query_type: str,
        query_value: str,
        sort_value: str = DEFAULT_PAGE_SORT,
        start_page: int | None = None,
        end_page: int | None = None,
        file_used: bool = False,
        fetch_as_archival: bool = DEFAULT_ARCHIVING,
    ) -> tuple[str | None, list[int]]:
        """
        Fetches Gallery IDs. Tries cache key(s) first, then falls back to API if needed.
        Returns a tuple (cache_key, list of IDs).
        """

        cache_target = query_value
        if query_type == "homepage":
            cache_target = sort_value or DEFAULT_PAGE_SORT

        sort_token_map = {
            "date": "date",
            "popular-week": "week",
            "popular-month": "month",
            "popular": "popular",
            "popular-today": "today",
        }
        sort_token = sort_token_map.get(
            Helpers.safe_text(sort_value).strip().lower(),
            Helpers.safe_text(sort_value).strip().lower().replace("-", "_") or "date",
        )

        key_start_page = Helpers.normalise_integer(start_page)
        if key_start_page is None or key_start_page < 1:
            key_start_page = DEFAULT_PAGE_RANGE_START

        key_end_page_num = None if end_page is None else max(key_start_page, Helpers.normalise_integer(end_page) or key_start_page)
        key_end_page = "all" if key_end_page_num is None else str(key_end_page_num)
        cache_modifier = f"{sort_token}_{key_start_page}-{key_end_page}"

        if Helpers.safe_text(cache_target, ""):
            cache_target = f"{cache_target}+{cache_modifier}"
        else:
            cache_target = cache_modifier

        cache_key = Cache.cache_keys(query_type, cache_target)

        # Support superset page-range cache hits (e.g. request 1-5 can reuse cached 1-10).
        def _normalise_target_text(value) -> str:
            return Helpers.safe_text(value, "").strip().lower()

        def _normalise_sort_token(value: str) -> str:
            raw = Helpers.safe_text(value, "").strip().lower()
            aliases = {
                "d": "date",
                "date": "date",
                "p": "popular",
                "popular": "popular",
                "pw": "week",
                "week": "week",
                "popular_week": "week",
                "popular-week": "week",
                "pm": "month",
                "month": "month",
                "popular_month": "month",
                "popular-month": "month",
                "pt": "today",
                "today": "today",
                "popular_today": "today",
                "popular-today": "today",
            }
            return aliases.get(raw, raw)

        def _parse_cache_target(value: str) -> tuple[str, str, int, int | None] | None:
            text = Helpers.safe_text(value, "").strip()
            if not text:
                return None

            base = ""
            modifier = text
            if "+" in text:
                left, right = text.rsplit("+", 1)
                base = left
                modifier = right

            match = re.match(r"^(?P<sort>[a-z0-9_-]+)_(?P<start>\d+)-(?P<end>\d+|all)$", modifier.strip().lower())
            if not match:
                return None

            start_val = Helpers.normalise_integer(match.group("start"))
            if start_val is None or start_val < 1:
                return None

            end_raw = match.group("end")
            end_val = None if end_raw == "all" else Helpers.normalise_integer(end_raw)
            if end_val is not None and end_val < start_val:
                return None

            return (
                _normalise_target_text(base),
                _normalise_sort_token(match.group("sort")),
                start_val,
                end_val,
            )

        requested_range = _parse_cache_target(cache_target)
        
        # 1. Try cache first
        if cache_key:
            references = Cache.Load.cache()
            cache_entry = references.get(cache_key)
            now = time.time()
            if cache_entry:
                expires_at = cache_entry.get("expires_at")
                
                ids = Helpers.normalise_integer_list(cache_entry.get("ids", []))
                
                if expires_at is None or expires_at > now:
                    logger.debug(f"[DATABASE] Using cached Gallery IDs for key '{cache_key}' (count: {len(ids)})")
                    return (cache_key, ids)
                else:
                    logger.debug(f"Cache entry for {cache_key} expired (expires_at={expires_at}, now={now}). Will fetch from API.")
            else:
                logger.debug(f"No valid cache entry for {cache_key}. Will fetch from API.")

            # If no exact match, try to reuse a superset cached range for the same query kind/target/sort.
            if requested_range:
                requested_type = Helpers.safe_text(query_type, "").strip().lower()
                requested_base, requested_sort, requested_start, requested_end = requested_range
                best_key = None
                best_ids = []
                best_rank = None

                for candidate_key, candidate_entry in (references or {}).items():
                    if Helpers.safe_text(candidate_key, "") == Helpers.safe_text(cache_key, ""):
                        continue

                    candidate_type = Helpers.safe_text(candidate_entry.get("cache_type"), "").strip().lower()
                    if candidate_type != requested_type:
                        continue

                    candidate_target = Helpers.safe_text(candidate_entry.get("cache_target"), "")
                    parsed_candidate = _parse_cache_target(candidate_target)
                    if not parsed_candidate:
                        continue

                    candidate_base, candidate_sort, candidate_start, candidate_end = parsed_candidate
                    if candidate_base != requested_base or candidate_sort != requested_sort or candidate_start != requested_start:
                        continue

                    covers_requested = False
                    if requested_end is None:
                        covers_requested = candidate_end is None
                    elif candidate_end is None:
                        covers_requested = True
                    elif candidate_end >= requested_end:
                        covers_requested = True

                    if not covers_requested:
                        continue

                    candidate_ids = Helpers.normalise_integer_list(candidate_entry.get("ids", []))
                    if not candidate_ids:
                        continue

                    candidate_rank = (float("inf") if candidate_end is None else candidate_end)
                    if best_rank is None or candidate_rank < best_rank:
                        best_rank = candidate_rank
                        best_key = candidate_key
                        best_ids = candidate_ids

                if best_key:
                    logger.debug(
                        f"[DATABASE] Using superset cached Gallery IDs for key '{best_key}' to satisfy '{cache_key}' (count: {len(best_ids)})"
                    )
                    return (best_key, best_ids)
        else:
            logger.debug(f"No valid cache entry for {cache_key}. Will fetch from API.")

        # 2. If no valid cache, fetch from API
        max_retries = 2
        attempt = 0
        ids = []
        while attempt < max_retries:
            try:
                global archiving
                orchestrator.refresh_globals()
                qt = query_type.capitalize()
                query_str = f" ' {query_value}'" if query_value else ""
                sort_str = f"'{sort_value}'" if sort_value != "date" else "date"
                if start_page is None:
                    start_page = DEFAULT_PAGE_RANGE_START
                if file_used:
                    if end_page is None:
                        end_page = None
                if fetch_as_archival:
                    log_clarification("debug")
                    log(f"SWITCHING TO ARCHIVAL MODE", "debug")
                    orchestrator.archiving = True
                    end_page = None
                else:
                    if end_page is None:
                        end_page = DEFAULT_PAGE_RANGE_END
                ids_set = set()
                page = start_page
                gallery_ids_session = Get.session(referrer="API", status="return")
                log_clarification("debug")
                if query_value is None:
                    log(f"Fetching Gallery IDs from NHentai Homepages {start_page} → {end_page or '∞'}")
                else:
                    log(f"Fetching Gallery IDs for {qt} '{query_value}' (pages {start_page} → {end_page or '∞'}), sorted by {sort_str}")
                while True:
                    if end_page is not None and page > end_page:
                        break
                    url = Build.url(qt, query_value, sort_value, page)
                    log(f"Fetcher: Requesting URL: {url}", "debug")
                    resp = None
                    for api_attempt in range(1, orchestrator.max_retries + 1):
                        try:
                            resp = gallery_ids_session.get(url, timeout=(60, 60))
                            if resp.status_code == 429:
                                wait = Sleep.dynamic("api", attempt=api_attempt)
                                logger.warning(f"{qt}{query_str}, Page {page}: Attempt {api_attempt}: 429 rate limit, waiting {wait:.2f}s")
                                time.sleep(wait)
                                continue
                            if resp.status_code == 403:
                                wait = Sleep.dynamic("api", attempt=api_attempt)
                                logger.warning(f"{qt}{query_str}, Page {page}: Attempt {api_attempt}: 403 forbidden, retrying in {wait:.2f}s")
                                time.sleep(wait)
                                continue
                            resp.raise_for_status()
                            break
                        except requests.RequestException as e:
                            if api_attempt >= orchestrator.max_retries:
                                log_clarification("debug")
                                logger.warning(f"{qt} {f'{query_value}' if query_value == None else ''}, Page {page}: Failed after {api_attempt} retries: {e}")
                                resp = None
                                if use_tor:
                                    wait = Sleep.dynamic("api", attempt=api_attempt) * 2
                                    logger.warning(f"{qt}{query_str}, Page {page}: Retrying with new Tor node in {wait:.2f}s")
                                    time.sleep(wait)
                                    gallery_ids_session = Get.session(referrer="API", status="rebuild")
                                    try:
                                        resp = gallery_ids_session.get(url, timeout=(60, 60))
                                        resp.raise_for_status()
                                    except Exception as e2:
                                        logger.warning(f"{qt}{query_str}, Page {page}: Still failed after Tor rotate: {e2}")
                                        resp = None
                                break
                            wait = Sleep.dynamic("api", attempt=api_attempt)
                            logger.warning(f"{qt}{query_str}, Page {page}: Attempt {api_attempt}: Request failed: {e}, retrying in {wait:.2f}s")
                            time.sleep(wait)
                    if resp is None:
                        page += 1
                        continue
                    try:
                        data = resp.json()
                    except Exception as e:
                        logger.warning(f"{qt}{query_str}, Page {page}: Failed to decode JSON: {e}")
                        break
                    if not isinstance(data, dict):
                        logger.warning(f"{qt}{query_str}, Page {page}: Unexpected JSON payload type: {type(data).__name__}")
                        break
                    results = data.get("result", [])
                    if not isinstance(results, list):
                        logger.warning(f"{qt}{query_str}, Page {page}: Unexpected result payload type: {type(results).__name__}")
                        break
                    batch = []
                    excluded_gallery_tags = [tag.lower() for tag in orchestrator.excluded_tags]
                    allowed_gallery_language = [lang.lower() for lang in orchestrator.language]
                    exact_match_types = {"artist", "group", "tag", "character", "parody"}

                    def _normalise_term(value: str) -> str:
                        return " ".join(Helpers.safe_text(value).strip().lower().split())

                    query_kind = Helpers.safe_text(query_type).strip().lower()
                    query_exact = _normalise_term(Helpers.safe_text(query_value).strip().strip('"').strip("'"))

                    for g in results:
                        if not isinstance(g, dict):
                            continue
                        gallery_tags = [
                            t["name"].lower()
                            for t in g.get("tags", [])
                            if t.get("type") == "tag"
                        ]
                        gallery_langs = [
                            t["name"].lower()
                            for t in g.get("tags", [])
                            if t.get("type") == "language"
                        ]

                        # Exact matching for typed queries only (artist/group/tag/character/parody).
                        if query_kind in exact_match_types and query_exact:
                            typed_names = []
                            for t in g.get("tags", []):
                                if not isinstance(t, dict):
                                    continue
                                if Helpers.safe_text(t.get("type")).lower() != query_kind:
                                    continue
                                raw_name = Helpers.safe_text(t.get("name"))
                                for part in raw_name.split("|"):
                                    name = _normalise_term(part)
                                    if name:
                                        typed_names.append(name)

                            compact_exact = query_exact.replace(" ", "")
                            if query_exact not in typed_names and compact_exact not in [n.replace(" ", "") for n in typed_names]:
                                log(
                                    f"Skipping Gallery {g.get('id', '?')} due to exact {query_kind} mismatch: expected '{query_exact}', got {typed_names}",
                                    "debug",
                                )
                                continue

                        blocked_tags = [t for t in gallery_tags if t in excluded_gallery_tags]
                        if blocked_tags:
                            log(f"Skipping Gallery {g['id']} due to excluded tags: {blocked_tags}", "debug")
                            continue
                        if allowed_gallery_language:
                            has_allowed = any(lang in allowed_gallery_language for lang in gallery_langs)
                            has_translated = "translated" in gallery_langs
                            allow_translated = "translated" in allowed_gallery_language
                            if not (has_allowed or (has_translated and allow_translated)):
                                blocked_langs = gallery_langs[:]
                                log(f"Skipping Gallery {g['id']} due to blocked languages: {blocked_langs}", "debug")
                                continue
                        gid = Helpers.normalise_integer(g.get("id"))
                        if gid is None:
                            continue
                        batch.append(gid)
                        images = g.get("images", {})
                        num_pages = len(images.get("pages", []))
                        orchestrator.total_gallery_images += num_pages
                    log(f"Fetcher: {qt}{query_str}, Page {page}: Fetched {len(batch)} Gallery IDs", "info")
                    log(f"Current Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")
                    if not results:
                        logger.info(f"Fetcher: {qt}{query_str}, Page {page}: No more results from NHentai, stopping.")
                        break
                    if not batch:
                        logger.debug(f"Fetcher: {qt}{query_str}, Page {page}: All galleries filtered out, continuing to next page.")
                        page += 1
                        continue
                    ids_set.update(batch)
                    page += 1
                log(f"Fetched total {len(ids_set)} Galleries for {qt}{query_str}", "warning")
                log(f"Overall Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")
                ids = list(sorted(ids_set))
                if cache_key and ids:
                    Cache.Save.cache(cache_key=cache_key, gallery_ids=ids)
                return (cache_key, ids)
            except Exception as e:
                attempt += 1
                logger.error(f"Error fetching galleries (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    logger.info("Retrying...")
                    time.sleep(2)
                    continue
                logger.warning(f"Failed to fetch galleries for {query_type}={query_value}. Skipping.")
                return (None, [])
        return (cache_key, ids)

    @staticmethod # IMAGE URL FETCHING
    def image_urls(meta: dict, page: int):
        """
        Returns the full image URL for a gallery page.
        Tries mirrors from NHENTAI_MIRRORS in order until one succeeds.
        Handles missing metadata, unknown types, and defaulting to webp.
        """
        
        orchestrator.refresh_globals()
        if not isinstance(meta, dict):
            return None
        page = Helpers.normalise_integer(page)
        if page is None or page < 1:
            return None
        
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

    @staticmethod # GALLERY METADATA FETCHING
    def gallery_metadata(gallery_id: int):
        orchestrator.refresh_globals()

        gallery_id = Helpers.normalise_integer(gallery_id)
        if gallery_id is None:
            return None

        raw_cache = Cache.Load.cached_metadata()
        cached_meta = raw_cache.get(gallery_id)
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
                    wait = Sleep.dynamic("api", attempt=(attempt))
                    logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: 429 rate limit hit, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code == 403:
                    wait = Sleep.dynamic("api", attempt=(attempt))
                    time.sleep(wait)
                    continue
                
                resp.raise_for_status()
                
                data = resp.json()

                # Validate the response
                if not isinstance(data, dict):
                    logger.error(f"Unexpected response type for Gallery: {gallery_id}: {type(data)}")
                    return None
                
                # Update cache
                cached_entry = Cache.Save.cache(data, gallery_id)
                if cached_entry:
                    general_metadata = Cache.Load.cached_metadata(clean=True)
                    general_metadata[gallery_id] = cached_entry
                    Cache.Save.cached_metadata(general_metadata, clean=True)
                raw_cache = Cache.Load.cached_metadata()
                raw_cache[gallery_id] = data
                Cache.Save.cached_metadata(raw_cache)

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
                        wait = Sleep.dynamic("api", attempt=(attempt)) * 2
                        logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: Metadata fetch failed: {e}, retrying with new Tor Node in {wait:.2f}s")
                        time.sleep(wait)
                        metadata_session = Get.session(referrer="API", status="rebuild")
                        try:
                            resp = metadata_session.get(url, timeout=(60, 60))
                            resp.raise_for_status()
                            retry_data = resp.json()
                            return retry_data if isinstance(retry_data, dict) else None
                        except Exception as e2:
                            logger.warning(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}")
                    return None
                wait = Sleep.dynamic("api", attempt=(attempt))
                logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s")
                time.sleep(wait)
            except requests.RequestException as e:
                if attempt >= orchestrator.max_retries:
                    logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                    # Rebuild session with Tor and try again once
                    if use_tor:
                        wait = Sleep.dynamic("api", attempt=(attempt)) * 2
                        logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: Metadata fetch failed: {e}, retrying with new Tor Node in {wait:.2f}s")
                        time.sleep(wait)
                        metadata_session = Get.session(referrer="API", status="rebuild")
                        try:
                            resp = metadata_session.get(url, timeout=(60, 60))
                            resp.raise_for_status()
                            retry_data = resp.json()
                            return retry_data if isinstance(retry_data, dict) else None
                        except Exception as e2:
                            logger.warning(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}")
                    return None
                wait = Sleep.dynamic("api", attempt=(attempt))
                logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s")
                time.sleep(wait)

    @staticmethod # BATCH METADATA FETCHING (for pre-filtering)
    def fetch_metadata_batch(gallery_ids: list) -> dict:
        """
        Fetch metadata for multiple galleries efficiently using threading.
        Used for pre-fetching before filtering/sizing.
        
        Returns:
            dict: {gallery_id: metadata_dict}
        """
        
        if not gallery_ids:
            return {}

        # Ensure all gallery IDs are integers
        gallery_ids = Helpers.normalise_integer_list(gallery_ids)
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

        # Ensure all gallery IDs are integers and deduplicate
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
            cached_metadata = Cache.Load.cache(cache_key)
        else:
            cached_metadata = Cache.Load.id_metadata(gallery_ids)

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
        for gallery_id in tqdm(ids_to_fetch, desc="Fetching gallery metadata", unit="gallery"):
            try:
                meta = Fetch.gallery_metadata(gallery_id)
                if meta and isinstance(meta, dict):
                    meta_entry = Cache.Save.cache(meta, gallery_id)
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
            Cache.Save.cache(cache_key, gallery_ids)
        elif metadata:
            general_metadata = Cache.Load.cached_metadata(clean=True)
            general_metadata.update(metadata)
            Cache.Save.cached_metadata(general_metadata, clean=True)
        
        return metadata

class Cache:
    @staticmethod
    def cache_keys(search_type: str, search_value: str = None) -> str:
        """
        Generate cache key based on search criteria.
        Alias for Build.cache_keys().
        """
        return Build.cache_keys(search_type, search_value)

    @staticmethod
    def split_key(cache_key):
        cache_key = Helpers.safe_text(cache_key)
        if ":" in cache_key:
            cache_type, cache_target = cache_key.split(":", 1)
        else:
            cache_type, cache_target = cache_key, ""
        return cache_type, cache_target

    @staticmethod
    def upsert_cached_metadata(gallery_id: str, timestamp: float, clean_metadata=None, raw_metadata=None):
        DB.init_db()
        gid = Helpers.normalise_integer(gallery_id)
        if gid is None:
            return

        entry = read_cached_metadata_entry(gallery_id=gid)["metadata"].get(gid) or {
            "timestamp": None,
            "expires_at": None,
            "clean_metadata": {},
            "raw_metadata": {},
        }
        if isinstance(clean_metadata, dict):
            entry["clean_metadata"].update(clean_metadata)
        if raw_metadata is not None:
            entry["raw_metadata"] = Helpers.safe_json_dict(raw_metadata)
        entry["timestamp"] = Helpers.safe_float(timestamp, time.time())
        entry["expires_at"] = entry["timestamp"] + CACHED_METADATA_TTL_SECONDS

        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO CachedMetadata (gallery_id, timestamp, clean_metadata, raw_metadata, expires_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(gallery_id) DO UPDATE SET "
                "timestamp=excluded.timestamp, "
                "clean_metadata=excluded.clean_metadata, "
                "raw_metadata=excluded.raw_metadata, "
                "expires_at=excluded.expires_at",
                (
                    gid,
                    entry["timestamp"],
                    json.dumps(entry["clean_metadata"], ensure_ascii=False),
                    json.dumps(entry["raw_metadata"], ensure_ascii=False),
                    entry["expires_at"],
                ),
            )
            conn.commit()

    @staticmethod
    def upsert_cache_reference(cache_key: str, entry: dict):
        DB.init_db()
        cache_key = Helpers.safe_text(cache_key)
        ids = Helpers.normalise_integer_list(entry.get("ids"))
        cache_type = Helpers.safe_text(entry.get("cache_type"), "")
        cache_target = Helpers.safe_text(entry.get("cache_target"), "")
        expires_at = Helpers.safe_float(entry.get("expires_at"))
        if expires_at <= 0:
            expires_at = time.time() + CACHE_REFERENCES_TTL_SECONDS
        ids_json = json.dumps(ids)
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO CacheReferences (cache_key, cache_type, cache_target, ids, expires_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET "
                "cache_type=excluded.cache_type, "
                "cache_target=excluded.cache_target, "
                "ids=excluded.ids, "
                "expires_at=excluded.expires_at",
                (
                    cache_key,
                    cache_type,
                    cache_target,
                    ids_json,
                    expires_at,
                ),
            )
            conn.commit()

    @staticmethod
    def clear_cache(cache_key: str = None, gallery_id: int = None):
        """Clear cached rows globally or for a specific cache key."""
        return clear_cached_items(cache_key=cache_key, gallery_id=gallery_id)
    
    class Load:
        @staticmethod
        def cache(cache_key: str = None, gallery_id: int = None):
            """
            Load from cache by either cache_key or gallery_id.
            - If cache_key is provided, return the list of IDs for that key (from CacheReferences).
            - If gallery_id is provided, return the clean metadata for that ID (from CachedMetadata).
            - If no arguments are provided, return all cache references as a dict.
            """
            
            # Return the list of Gallery IDs for a specific cache_key
            if cache_key is not None:
                references_entry = read_cached_metadata_entry(cache_key=cache_key)["references"].get(str(cache_key))
                if not references_entry or not isinstance(references_entry, dict):
                    return []
                ids = references_entry.get("ids")
                if not ids or not isinstance(ids, list):
                    return []
                normalised_ids = []
                for gid in ids:
                    try:
                        normalised_ids.append(int(gid))
                    except (TypeError, ValueError):
                        continue
                return normalised_ids
            
            # Return clean metadata for a specific Gallery ID
            elif gallery_id is not None:
                gid = Helpers.normalise_integer(gallery_id)
                cache_entry = read_cached_metadata_entry(gallery_id=gallery_id)["metadata"].get(gid)
                if not cache_entry:
                    return None
                clean_meta = cache_entry.get("clean_metadata")
                return clean_meta if isinstance(clean_meta, dict) else None
            
            # Return all cache references as a dict
            else:
                return read_cached_metadata_entry()["references"]
        
        @staticmethod
        def cached_metadata(clean: bool = False) -> dict:
            """
            Returns cached metadata for all galleries.
            By default returns raw metadata. If clean=True, returns clean metadata.
            """
            
            data = read_cached_metadata_entry()
            metadata_block = data.get("metadata", {})
            if not isinstance(metadata_block, dict):
                return {}
            result = {}
            
            for gid, entry in metadata_block.items():
                if not isinstance(entry, dict):
                    continue
                
                if clean:
                    clean_meta = entry.get("clean_metadata")
                    if isinstance(clean_meta, dict):
                        result[gid] = clean_meta
                
                else:
                    raw_meta = entry.get("raw_metadata")
                    if isinstance(raw_meta, dict):
                        result[gid] = raw_meta
            
            return result
        
        @staticmethod
        def broken_symbols() -> dict[str, str]:
            """Load all detected broken symbols as { symbol: '_' }."""
            # Do NOT call DB.init_db() here to avoid self-recursion.
            with db_lock, DB.dbconnect() as conn:
                c = conn.cursor()
                c.execute("SELECT symbol FROM BrokenSymbols WHERE fixed=0")
                rows = c.fetchall()
                result = {}
                for row in rows:
                    symbol = Helpers.safe_text(row[0], "").strip()
                    if symbol:
                        result[symbol] = "_"
                return result
        
        @staticmethod
        def queued_galleries() -> list:
            """Fetch queued galleries from GalleriesQueue table in the database."""
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM GalleriesQueue")
                rows = cursor.fetchall()
                ids = []
                for row in rows:
                    gid = Helpers.normalise_integer(row[0])
                    if gid is not None:
                        ids.append(gid)
                return sorted(set(ids))

        @staticmethod
        def all_metadata() -> dict:
            """Load all cached metadata entries from the CachedMetadata table."""
            return Cache.Load.cached_metadata(clean=True)

        @staticmethod
        def id_metadata(ids: list[int]) -> dict:
            """Load each metadata entry from the CachedMetadata table corresponding to the IDs in a given list."""
            ids = Helpers.normalise_integer_list(ids)
            if not ids:
                return {}
            # Fetch metadata for all IDs from CachedMetadata
            meta_dict = read_cached_metadata_entry(ids=ids)["metadata"]
            result = {}
            for gid, entry in meta_dict.items():
                clean = entry.get("clean_metadata")
                if isinstance(clean, dict):
                    result[gid] = clean
            return result

    class Save:
        @staticmethod
        def cache(cache_key: str = None, gallery_ids: list = None, meta: dict = None):
            """
            Canonical function for updating CachedMetadata and/or CacheReferences.
            - If only meta is provided, updates CachedMetadata for the ID extracted from meta.
            - If cache_key and gallery_ids are provided, updates CacheReferences for that key with the given IDs.
            - If all are provided, updates both.
            Returns the clean metadata entry if metadata is updated, else None.
            """
            now = time.time()
            entry = None

            # Backward-compatible call shape: cache(meta, gallery_id)
            if isinstance(cache_key, dict) and meta is None:
                meta = cache_key
                cache_key = None
                if gallery_ids is not None and "id" not in meta:
                    gid = Helpers.normalise_integer(gallery_ids)
                    if gid is not None:
                        meta["id"] = gid
                gallery_ids = None

            # If meta is provided, extract gallery_id from meta
            if isinstance(meta, dict):
                gid = Helpers.normalise_integer(meta.get("id"))
                if gid is not None:
                    title_obj = meta.get("title")
                    if isinstance(title_obj, dict):
                        title = Helpers.safe_text(title_obj.get("english"), f"Gallery {gid}")
                    else:
                        title = Helpers.safe_text(title_obj, f"Gallery {gid}")
                    entry = {
                        "id": gid,
                        "title": title,
                        "artists": Get.artists(meta),
                        "groups": Get.groups(meta),
                        "tags": Get.tags(meta),
                        "characters": Get.characters(meta),
                        "parodies": Get.parodies(meta),
                        "languages": Get.languages(meta),
                        "pages": Get.page_count(meta),
                    }
                    Cache.upsert_cached_metadata(
                        gallery_id=gid,
                        timestamp=now,
                        clean_metadata=entry,
                        raw_metadata=meta,
                    )
            
            # If cache_key and gallery_ids are provided, update CacheReferences
            if cache_key and gallery_ids is not None:
                cache_type, cache_target = Cache.split_key(cache_key)
                ids = Helpers.normalise_integer_list(gallery_ids)
                ids = list(sorted(set(ids)))
                expires_at = now + CACHE_REFERENCES_TTL_SECONDS
                entry_ref = {
                    "cache_key": cache_key,
                    "cache_type": cache_type,
                    "cache_target": cache_target,
                    "ids": ids,
                    "expires_at": expires_at,
                }
                Cache.upsert_cache_reference(cache_key, entry_ref)
            
            return entry
        
        @staticmethod
        def cached_metadata(metadata: dict, clean: bool = False):
            """
            Updates cached metadata for all galleries.
            If clean=True, updates clean_metadata. Otherwise, updates raw_metadata.
            """
            
            now = time.time()
            if not isinstance(metadata, dict):
                return
            
            for gid, entry in metadata.items():
                if not isinstance(entry, dict):
                    continue
                
                if clean:
                    Cache.upsert_cached_metadata(
                        gid,
                        now,
                        clean_metadata=entry,
                        raw_metadata=None,
                    )
                
                else:
                    Cache.upsert_cached_metadata(
                        gid,
                        now,
                        clean_metadata=None,
                        raw_metadata=entry,
                    )
        
        @staticmethod
        def broken_symbols(symbol_map: dict[str, str]):
            """Insert or update broken symbols into the database, keeping the mapping (symbol -> replacement)."""
            if not symbol_map:
                return
            # Do NOT call DB.init_db() here to avoid self-recursion.
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
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

        @staticmethod
        def queued_galleries(ids):
            return DB.set_queued_galleries(ids)

################################################################################################################
# EXPORTED API
################################################################################################################

# Initial symbol translation table at module load
Helpers.build_symbol_translation_table()

cache = Cache()
db = DB()
helpers = Helpers()
sleep = Sleep()
get = Get()
fetch = Fetch()