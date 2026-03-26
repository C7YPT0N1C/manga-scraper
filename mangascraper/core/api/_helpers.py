# mangascraper/core/api/_helpers.py

from __future__ import annotations
import os, time, threading, sqlite3, json, re
from datetime import datetime, timezone

from mangascraper.core import orchestrator
from mangascraper.core.api._constants import (
    db_lock,
    _thread_local,
    _BRACKET_PATTERN,
    _DASH_PATTERN,
    _UNDERSCORE_PATTERN,
    CACHE_REFERENCES_TTL_SECONDS,
    CACHED_METADATA_TTL_SECONDS,
    DOWNLOAD_ROOT_MARKER_FILE,
)

# These are imported for use inside Helpers.sanitise — they live in orchestrator
from mangascraper.core.orchestrator import (
    ALLOWED_SYMBOLS,
    BROKEN_SYMBOL_REPLACEMENTS,
    BROKEN_SYMBOL_BLACKLIST,
)

# _SYMBOL_TRANSLATION_TABLE is mutated at runtime, so we import the module
# and reference it as _constants._SYMBOL_TRANSLATION_TABLE
import mangascraper.core.api._constants as _constants

####################################################################################################################
# STANDALONE CACHE HELPERS
####################################################################################################################

def prune_all_caches():
    """Remove expired entries in cache tables based on expires_at. Returns count of deleted entries."""
    # Import here to avoid circular: _db imports from _helpers
    from mangascraper.core.api._db import DB
    DB.init_db()
    now = time.time()
    from mangascraper.core.api._db import DB
    with db_lock, DB.dbconnect() as conn:
        cursor = conn.cursor()
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


def read_cached_metadata_entry(cache_key=None, gallery_id=None, cutoff=None, ids=None):
    """
    Loads and returns metadata from CachedMetadata or entries from CacheReferences.
    - If no parameter is given, prunes expired entries and returns {"references": ..., "metadata": ...}.
    - If cache_key is given, returns the CacheReferences entry for that key (or None if not found).
    - If ids is given (list of gallery IDs), returns metadata for those galleries as a dict.
    - If gallery_id is given, returns metadata for that gallery (or None if not found).
    - If cutoff is given, returns all metadata entries newer than cutoff as a dict keyed by gallery ID.
    """
    from mangascraper.core.api._db import DB
    DB.init_db()

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

    if cache_key is not None:
        lookup_key = Helpers.safe_text(cache_key, "")
        lookup_now = time.time()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT cache_key, cache_type, cache_target, ids, expires_at
                FROM CacheReferences
                WHERE cache_key = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (lookup_key, lookup_now),
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


def clear_cached_items(cache_key=None, gallery_id=None):
    """Clear all cache, one CacheReferences row, or one CachedMetadata row."""
    from mangascraper.core.api._db import DB
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


####################################################################################################################
# HELPERS CLASS
####################################################################################################################

class Helpers:
    """Stateless helper utilities."""

    _WINDOWS_RESERVED_NAMES = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }

    @staticmethod
    def infer_location_root(download_path: str) -> str:
        from mangascraper.core.api._db import DB
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
                return max(matches, key=len)
        except (sqlite3.OperationalError, sqlite3.DatabaseError, Exception):
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
        # Avoid circular import: Get lives in _get.py, import lazily
        from mangascraper.core.api._get import Get
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

        # Lazy import to avoid circular: Cache.Load.broken_symbols -> _db
        from mangascraper.core.api._cache import Cache
        possible_broken_symbols = Cache.Load.broken_symbols()

        if isinstance(meta_or_title, dict):
            meta = meta_or_title
            title_obj = meta.get("title", {}) or {}
            desired_title_type = orchestrator.title_type.lower()
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
            from mangascraper.core.api._cache import Cache
            Cache.Save.broken_symbols(possible_broken_symbols)
            Helpers.build_symbol_translation_table()

        title = _BRACKET_PATTERN.sub("", title)

        if _constants._SYMBOL_TRANSLATION_TABLE is None:
            Helpers.build_symbol_translation_table()
        title = title.translate(_constants._SYMBOL_TRANSLATION_TABLE)

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

        # Replace problematic filesystem characters: keep slashes turned to dashes,
        # backslashes to dashes, and convert colons to underscores (important for creators)
        title = title.replace("/", "-").replace("\\", "-").replace(":", "_")
        return title.strip()

    @staticmethod
    def summary(meta, referrer: str):
        orchestrator.refresh_globals()

        from mangascraper.core.api._get import Get
        from mangascraper.core.api._cache import Cache

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
            from mangascraper.core.orchestrator import logger
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
        _constants._SYMBOL_TRANSLATION_TABLE = {
            ord(symbol): replacement
            for symbol, replacement in BROKEN_SYMBOL_REPLACEMENTS.items()
        }