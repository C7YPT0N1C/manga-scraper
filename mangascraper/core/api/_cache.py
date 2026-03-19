# mangascraper/core/api/_cache.py

import time, json
from datetime import datetime, timezone

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import logger
from mangascraper.core.api._constants import (
    db_lock,
    CACHE_REFERENCES_TTL_SECONDS,
    CACHED_METADATA_TTL_SECONDS,
)
from mangascraper.core.api._helpers import (
    Helpers,
    read_cached_metadata_entry,
    clear_cached_items,
    prune_all_caches,
)

####################################################################################################################
# CACHE CLASS
####################################################################################################################

class Cache:

    @staticmethod
    def cache_keys(search_type: str, search_value: str = None) -> str:
        """
        Generate cache key based on search criteria.
        Alias for Build.cache_keys().
        """
        # Lazy import to avoid circular: Build imports Cache
        from mangascraper.core.api._build import Build
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
        from mangascraper.core.api._db import DB
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
        from mangascraper.core.api._db import DB
        DB.init_db()
        cache_key = Helpers.safe_text(cache_key)
        ids = Helpers.normalise_integer_list(entry.get("ids"))
        cache_type = Helpers.safe_text(entry.get("cache_type"), "")
        cache_target = Helpers.safe_text(entry.get("cache_target"), "")
        expires_at = Helpers.safe_float(entry.get("expires_at"))
        if expires_at <= 0:
            expires_at = time.time() + CACHE_REFERENCES_TTL_SECONDS
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
                    json.dumps(ids),
                    expires_at,
                ),
            )
            conn.commit()

    @staticmethod
    def clear_cache(cache_key: str = None, gallery_id: int = None):
        """Clear cached rows globally or for a specific cache key."""
        return clear_cached_items(cache_key=cache_key, gallery_id=gallery_id)

    ####################################################################################################################
    # CACHE.LOAD
    ####################################################################################################################

    class Load:
        @staticmethod
        def cache(cache_key: str = None, gallery_id: int = None):
            """
            Load from cache by either cache_key or gallery_id.
            - If cache_key is provided, return the list of IDs for that key (from CacheReferences).
            - If gallery_id is provided, return the clean metadata for that ID (from CachedMetadata).
            - If no arguments are provided, return all cache references as a dict.
            """
            if cache_key is not None:
                lookup_key = Helpers.safe_text(cache_key, "")
                references_entry = read_cached_metadata_entry(cache_key=lookup_key)["references"].get(str(lookup_key))
                if not references_entry or not isinstance(references_entry, dict):
                    logger.debug(f"[CacheLoad] cache_key='{lookup_key}' returned no references entry")
                    return []
                ids = references_entry.get("ids")
                if not ids or not isinstance(ids, list):
                    logger.debug(f"[CacheLoad] cache_key='{lookup_key}' had empty/non-list ids payload")
                    return []
                normalised_ids = []
                for gid in ids:
                    try:
                        normalised_ids.append(int(gid))
                    except (TypeError, ValueError):
                        continue
                logger.debug(
                    f"[CacheLoad] cache_key='{lookup_key}' loaded ids_raw={len(ids)} ids_normalised={len(normalised_ids)}"
                )
                return normalised_ids

            elif gallery_id is not None:
                gid = Helpers.normalise_integer(gallery_id)
                cache_entry = read_cached_metadata_entry(gallery_id=gallery_id)["metadata"].get(gid)
                if not cache_entry:
                    return None
                clean_meta = cache_entry.get("clean_metadata")
                return clean_meta if isinstance(clean_meta, dict) else None

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
            from mangascraper.core.api._db import DB
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
            """Fetch selected gallery IDs from DownloadQueue entries with status='selected'."""
            from mangascraper.core.api._db import DB
            DB.init_db()
            merged_ids = []
            seen = set()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT ids_json
                    FROM DownloadQueue
                    WHERE LOWER(COALESCE(status, ''))='selected'
                    ORDER BY download_no ASC
                    """
                )
                for (ids_json,) in cursor.fetchall():
                    try:
                        parsed = json.loads(ids_json) if ids_json else []
                    except Exception:
                        parsed = []
                    for gid in Helpers.normalise_integer_list(parsed):
                        if gid in seen:
                            continue
                        seen.add(gid)
                        merged_ids.append(gid)
            return merged_ids

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
            meta_dict = read_cached_metadata_entry(ids=ids)["metadata"]
            result = {}
            for gid, entry in meta_dict.items():
                clean = entry.get("clean_metadata")
                if isinstance(clean, dict):
                    result[gid] = clean
            return result

    ####################################################################################################################
    # CACHE.SAVE
    ####################################################################################################################

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
            from mangascraper.core.api._get import Get
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

            if cache_key and gallery_ids is not None:
                cache_type, cache_target = Cache.split_key(cache_key)
                ids = list(sorted(set(Helpers.normalise_integer_list(gallery_ids))))
                expires_at = now + CACHE_REFERENCES_TTL_SECONDS
                Cache.upsert_cache_reference(cache_key, {
                    "cache_key": cache_key,
                    "cache_type": cache_type,
                    "cache_target": cache_target,
                    "ids": ids,
                    "expires_at": expires_at,
                })

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
                    Cache.upsert_cached_metadata(gid, now, clean_metadata=entry, raw_metadata=None)
                else:
                    Cache.upsert_cached_metadata(gid, now, clean_metadata=None, raw_metadata=entry)

        @staticmethod
        def broken_symbols(symbol_map: dict[str, str]):
            """Insert or update broken symbols into the database."""
            if not symbol_map:
                return
            # Do NOT call DB.init_db() here to avoid self-recursion.
            from mangascraper.core.api._db import DB
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                c = conn.cursor()
                for symbol in symbol_map.keys():
                    c.execute(
                        """
                        INSERT INTO BrokenSymbols (symbol, date_detected, fixed)
                        VALUES (?, ?, 0)
                        ON CONFLICT(symbol) DO UPDATE SET
                            fixed=0,
                            date_detected=excluded.date_detected
                        """,
                        (symbol, now),
                    )
                conn.commit()

        @staticmethod
        def queued_galleries(ids):
            from mangascraper.core.api._db import DB
            return DB.set_queued_galleries(ids)