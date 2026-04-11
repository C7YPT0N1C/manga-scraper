# mangascraper/core/api/_db_helpers.py

from __future__ import annotations
import time, json

from mangascraper.core.api._constants import db_lock
from mangascraper.core.api._helpers import Helpers


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
    See the original _helpers implementation for behaviour.
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
