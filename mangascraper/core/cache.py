#!/usr/bin/env python3
# mangascraper/core/cache.py
"""
Caching utilities for metadata and search results.
Stores cache by search criteria (artist, tag, group, etc.) rather than individual galleries.
"""

import json
import time
from pathlib import Path

from mangascraper.core import orchestrator, database

# Cache TTL: 3 hours (runtime-configured)
TTL = getattr(orchestrator, "metadata_ttl", 3 * 60 * 60)
SEARCH_HISTORY_FILENAME = "(search_history).json"
SELECTED_GALLERIES_FILENAME = "(selected_galleries).json"
SEARCH_HISTORY_MAX = 10


def get_cache_dir() -> Path:
    """
    Get or create cache directory for metadata.
    Uses /opt/manga-scraper/mangascraper/core/data/ if available,
    otherwise uses package-relative path as fallback.
    """
    # Try primary location
    primary_cache = Path("/opt/manga-scraper/mangascraper/core/data")
    if primary_cache.parent.exists():
        primary_cache.mkdir(parents=True, exist_ok=True)
        return primary_cache
    
    # Fallback to package-relative path
    fallback_cache = Path(__file__).parent / "data"
    fallback_cache.mkdir(parents=True, exist_ok=True)
    return fallback_cache


def ensure_cache_files_exist():
    """Ensure cache directory and core cache files exist."""
    cache_dir = get_cache_dir()
    _prune_cache_files(cache_dir)
    for name in (SEARCH_HISTORY_FILENAME, SELECTED_GALLERIES_FILENAME):
        cache_file = cache_dir / name
        if cache_file.exists():
            if name != SEARCH_HISTORY_FILENAME:
                continue
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
            continue
        try:
            if name == SEARCH_HISTORY_FILENAME:
                data = {"saved_at": None, "items": []}
            elif name == SELECTED_GALLERIES_FILENAME:
                data = {"saved_at": None, "ids": [], "csv": ""}
            else:
                data = {}
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            continue


def _prune_cache_files(cache_dir: Path):
    now = time.time()
    protected = {
        SEARCH_HISTORY_FILENAME,
        SELECTED_GALLERIES_FILENAME,
    }
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
                cache_file.unlink()
        except Exception:
            continue


def get_cache_key(search_type: str, search_value: str = None) -> str:
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


def _load_master_cache() -> dict:
    cutoff = time.time() - TTL
    try:
        database.prune_cache_metadata(cutoff)
        database.prune_cache_references(time.time())
        metadata_block = database.load_cache_metadata_all(cutoff)
        references = database.load_cache_references()
        return {"references": references, "metadata": metadata_block}
    except Exception:
        return {
            "references": {},
            "metadata": {},
        }


def _prune_master_cache(data: dict, save_if_changed: bool = False) -> dict:
    if not isinstance(data, dict):
        return {"references": {}, "metadata": {}}
    if save_if_changed:
        database.prune_cache_metadata(time.time() - TTL)
        database.prune_cache_references(time.time())
    return data


def load_all_cached_metadata() -> dict:
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


def _save_master_cache(data: dict):
    return


def _update_master_cache(entry_key: str, entry: dict):
    database.upsert_cache_reference(entry_key, entry)


def _remove_master_cache_entry(entry_key: str):
    database.delete_cache_reference(entry_key)


def load_general_metadata_cache() -> dict:
    """Load general metadata stored inside master cache."""
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


def save_general_metadata_cache(metadata: dict):
    """Save general metadata inside master cache."""
    if not isinstance(metadata, dict):
        return
    data = _load_master_cache()
    safe_metadata = {str(k): v for k, v in metadata.items()}
    now = time.time()
    for gid, entry in safe_metadata.items():
        if not isinstance(entry, dict):
            continue
        database.upsert_cache_metadata(
            gid,
            now,
            clean_metadata=entry,
            raw_metadata=None,
        )


def load_general_raw_metadata_cache() -> dict:
    """Load raw metadata stored inside master cache."""
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


def save_general_raw_metadata_cache(metadata: dict):
    """Save raw metadata inside master cache."""
    if not isinstance(metadata, dict):
        return
    data = _load_master_cache()
    now = time.time()
    for gid, entry in metadata.items():
        database.upsert_cache_metadata(
            str(gid),
            now,
            clean_metadata=None,
            raw_metadata=entry,
        )


def _build_master_entry(
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
    expires_at = None
    if ttl_seconds:
        expires_at = write_time + ttl_seconds
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


def load_cache(cache_key: str) -> dict:
    """Load cached metadata for a search criteria.
    
    Args:
        cache_key: Cache key (e.g., "artist_john")
    
    Returns:
        dict: {gallery_id: metadata_dict} or empty dict if not found/expired
    """
    cache_file = get_cache_dir() / f"{cache_key}.json"
    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Check if cache is fresh (within TTL)
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
                        _build_master_entry(
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
        except Exception as e:
            pass  # Silently fail, return empty dict
    return {}


def save_cache(cache_key: str, metadata: dict):
    """Save metadata to cache.
    
    Args:
        cache_key: Cache key (e.g., "artist_john")
        metadata: {gallery_id: metadata_dict} mapping to cache
    """
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
            'metadata': safe_metadata  # Ensure keys are strings
        }
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _update_master_cache(
            f"metadata:{cache_key}",
            _build_master_entry(
                "metadata",
                cache_key,
                cache_file,
                TTL,
                last_write=timestamp,
                ids=ids,
            ),
        )
    except Exception:
        pass  # Silently fail


def load_search_history(max_items: int = SEARCH_HISTORY_MAX) -> list[dict]:
    """Load recent search history from cache.
    
    Args:
        max_items: Maximum number of items to return.
    
    Returns:
        list of dicts with keys: type, value, cache_key, sort, start_page, end_page
    """
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
                _build_master_entry("search_history", "search_history", cache_file, None, last_read=time.time()),
            )
            return cleaned
    except Exception:
        return []


def save_search_history(items: list[dict], max_items: int = SEARCH_HISTORY_MAX):
    """Save recent search history to cache.
    
    Args:
        items: List of search history dicts.
        max_items: Maximum number of items to persist.
    """
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
            _build_master_entry("search_history", "search_history", cache_file, None, last_write=saved_at),
        )
    except Exception:
        pass


def load_selected_galleries() -> list[int]:
    """Load selected galleries from cache.

    Returns:
        list of gallery IDs (ints), sorted ascending
    """
    cache_file = get_cache_dir() / SELECTED_GALLERIES_FILENAME
    if not cache_file.exists():
        return []
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        ids = data.get("ids", [])
        if not isinstance(ids, list):
            return []
        cleaned = []
        for gid in ids:
            try:
                cleaned.append(int(gid))
            except Exception:
                continue
        cleaned = sorted(set(cleaned))
        _update_master_cache(
            "selected_galleries",
            _build_master_entry(
                "selected_galleries",
                "selected_galleries",
                cache_file,
                None,
                last_read=time.time(),
            ),
        )
        return cleaned
    except Exception:
        return []


def save_selected_galleries(ids: list[int]):
    """Save selected gallery IDs as a sorted list and comma-separated string."""
    try:
        if not isinstance(ids, list):
            return
        cleaned = []
        for gid in ids:
            try:
                cleaned.append(int(gid))
            except Exception:
                continue
        cleaned = sorted(set(cleaned))
        cache_file = get_cache_dir() / SELECTED_GALLERIES_FILENAME
        saved_at = time.time()
        data = {
            "saved_at": saved_at,
            "ids": cleaned,
            "csv": ",".join(str(gid) for gid in cleaned),
        }
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _update_master_cache(
            "selected_galleries",
            _build_master_entry(
                "selected_galleries",
                "selected_galleries",
                cache_file,
                None,
                last_write=saved_at,
            ),
        )
    except Exception:
        pass


def load_cached_metadata_for_ids(ids: list[int]) -> dict:
    """Load cached metadata for a set of IDs from master cache entries."""
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


def clear_cache(cache_key: str = None):
    """Clear cache for a specific key or all cache.
    
    Args:
        cache_key: Specific cache to clear, or None to clear all
    """
    try:
        cache_dir = get_cache_dir()
        if cache_key:
            cache_file = cache_dir / f"{cache_key}.json"
            if cache_file.exists():
                cache_file.unlink()
            _remove_master_cache_entry(f"metadata:{cache_key}")
        else:
            # Clear all cache files
            for cache_file in cache_dir.glob("*.json"):
                cache_file.unlink()
            master_file = cache_dir / MASTER_CACHE_FILENAME
            if master_file.exists():
                master_file.unlink()
    except Exception:
        pass  # Silently fail
