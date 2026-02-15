#!/usr/bin/env python3
# mangascraper/core/cache.py
"""
Caching utilities for metadata and search results.
Stores cache by search criteria (artist, tag, group, etc.) rather than individual galleries.
"""

import json
import time
from pathlib import Path

from mangascraper.core import orchestrator

# Cache TTL: 3 hours (runtime-configured)
TTL = getattr(orchestrator, "metadata_ttl", 3 * 60 * 60)
SEARCH_HISTORY_FILENAME = "(search_history).json"
SELECTED_GALLERIES_FILENAME = "(selected_galleries).json"
MASTER_CACHE_FILENAME = "(master_cache).json"


def get_cache_dir() -> Path:
    """
    Get or create cache directory for metadata.
    Uses /opt/manga-scraper/mangascraper/core/cache/ if available,
    otherwise uses package-relative path as fallback.
    """
    # Try primary location
    primary_cache = Path("/opt/manga-scraper/mangascraper/core/cache")
    if primary_cache.parent.exists():
        primary_cache.mkdir(parents=True, exist_ok=True)
        return primary_cache
    
    # Fallback to package-relative path
    fallback_cache = Path(__file__).parent / "cache"
    fallback_cache.mkdir(parents=True, exist_ok=True)
    return fallback_cache


def ensure_cache_files_exist():
    """Ensure cache directory and core cache files exist."""
    cache_dir = get_cache_dir()
    _prune_cache_files(cache_dir)
    for name in (SEARCH_HISTORY_FILENAME, MASTER_CACHE_FILENAME, SELECTED_GALLERIES_FILENAME):
        cache_file = cache_dir / name
        if cache_file.exists():
            continue
        try:
            if name == MASTER_CACHE_FILENAME:
                data = {
                    "references": {},
                    "metadata": {},
                }
            elif name == SEARCH_HISTORY_FILENAME:
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
        MASTER_CACHE_FILENAME,
        SELECTED_GALLERIES_FILENAME,
    }
    for cache_file in cache_dir.glob("*.json"):
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
    cache_file = get_cache_dir() / MASTER_CACHE_FILENAME
    if not cache_file.exists():
        return {
            "references": {},
            "metadata": {},
        }
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {
                "references": {},
                "metadata": {},
            }
        references = data.get("references")
        if references is None:
            references = data.get("entries")
        if not isinstance(references, dict):
            references = {}

        metadata_block = data.get("metadata")
        if not isinstance(metadata_block, dict):
            metadata_block = {}

        def _ensure_entry(gid: str) -> dict:
            entry = metadata_block.get(gid)
            if not isinstance(entry, dict):
                entry = {"timestamp": None, "clean_metadata": {}, "raw_metadata": {}}
            if not isinstance(entry.get("clean_metadata"), dict):
                entry["clean_metadata"] = {}
            if not isinstance(entry.get("raw_metadata"), dict):
                entry["raw_metadata"] = {}
            metadata_block[gid] = entry
            return entry

        legacy_general = data.get("general_metadata")
        legacy_raw = data.get("general_raw_metadata")
        if isinstance(legacy_general, dict) and isinstance(legacy_general.get("metadata"), dict):
            for gid, entry in legacy_general.get("metadata", {}).items():
                if not isinstance(entry, dict):
                    continue
                target = _ensure_entry(str(gid))
                target["clean_metadata"].update(entry)
                if target.get("timestamp") is None:
                    target["timestamp"] = legacy_general.get("timestamp")
        if isinstance(legacy_raw, dict) and isinstance(legacy_raw.get("metadata"), dict):
            for gid, entry in legacy_raw.get("metadata", {}).items():
                target = _ensure_entry(str(gid))
                target["raw_metadata"] = entry
                if target.get("timestamp") is None:
                    target["timestamp"] = legacy_raw.get("timestamp")

        if isinstance(metadata_block.get("entries"), dict):
            legacy_entries = metadata_block.pop("entries")
            for gid, entry in legacy_entries.items():
                if not isinstance(entry, dict):
                    continue
                target = _ensure_entry(str(gid))
                target["clean_metadata"].update({k: v for k, v in entry.items() if k != "raw_metadata"})
                if "raw_metadata" in entry:
                    target["raw_metadata"] = entry.get("raw_metadata")

        if isinstance(metadata_block.get("metadata"), dict):
            legacy_summary = metadata_block.pop("metadata")
            for gid, entry in legacy_summary.items():
                if not isinstance(entry, dict):
                    continue
                target = _ensure_entry(str(gid))
                target["clean_metadata"].update(entry)

        if isinstance(metadata_block.get("raw_metadata"), dict):
            legacy_raw_map = metadata_block.pop("raw_metadata")
            for gid, entry in legacy_raw_map.items():
                target = _ensure_entry(str(gid))
                target["raw_metadata"] = entry

        data = {
            "references": references,
            "metadata": metadata_block,
        }
        return _prune_master_cache(data, save_if_changed=True)
    except Exception:
        return {
            "references": {},
            "metadata": {},
        }


def _prune_master_cache(data: dict, save_if_changed: bool = False) -> dict:
    if not isinstance(data, dict):
        return {"references": {}, "metadata": {}}

    references = data.get("references")
    metadata_block = data.get("metadata")
    if not isinstance(references, dict):
        references = {}
    if not isinstance(metadata_block, dict):
        metadata_block = {}

    now = time.time()
    changed = False

    for gid in list(metadata_block.keys()):
        entry = metadata_block.get(gid)
        if not isinstance(entry, dict):
            metadata_block.pop(gid, None)
            changed = True
            continue
        timestamp = entry.get("timestamp") or 0
        if (now - timestamp) >= TTL:
            metadata_block.pop(gid, None)
            changed = True

    for key in list(references.keys()):
        entry = references.get(key)
        if not isinstance(entry, dict):
            references.pop(key, None)
            changed = True
            continue
        expires_at = entry.get("expires_at")
        if expires_at and now >= expires_at:
            references.pop(key, None)
            changed = True

    data["references"] = references
    data["metadata"] = metadata_block

    if changed and save_if_changed:
        _save_master_cache(data)
    return data


def load_all_cached_metadata() -> dict:
    """Load and merge all cached metadata entries from the master cache registry."""
    cache_file = get_cache_dir() / MASTER_CACHE_FILENAME
    if not cache_file.exists():
        return {}
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
    try:
        cache_file = get_cache_dir() / MASTER_CACHE_FILENAME
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _update_master_cache(entry_key: str, entry: dict):
    data = _load_master_cache()
    data["references"][entry_key] = entry
    _save_master_cache(data)


def _remove_master_cache_entry(entry_key: str):
    data = _load_master_cache()
    if entry_key in data["references"]:
        del data["references"][entry_key]
        _save_master_cache(data)


def load_general_metadata_cache() -> dict:
    """Load general metadata stored inside master cache."""
    cache_file = get_cache_dir() / MASTER_CACHE_FILENAME
    if not cache_file.exists():
        return {}
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
    metadata_block = data.get("metadata", {})
    if not isinstance(metadata_block, dict):
        metadata_block = {}
    for gid, entry in safe_metadata.items():
        current = metadata_block.get(gid)
        if not isinstance(current, dict):
            current = {"timestamp": None, "clean_metadata": {}, "raw_metadata": {}}
        if not isinstance(current.get("clean_metadata"), dict):
            current["clean_metadata"] = {}
        if not isinstance(current.get("raw_metadata"), dict):
            current["raw_metadata"] = {}
        if isinstance(entry, dict):
            current["clean_metadata"].update(entry)
        current["timestamp"] = time.time()
        metadata_block[gid] = current
    data["metadata"] = metadata_block
    _save_master_cache(data)


def load_general_raw_metadata_cache() -> dict:
    """Load raw metadata stored inside master cache."""
    cache_file = get_cache_dir() / MASTER_CACHE_FILENAME
    if not cache_file.exists():
        return {}
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
    metadata_block = data.get("metadata", {})
    if not isinstance(metadata_block, dict):
        metadata_block = {}
    for gid, entry in metadata.items():
        key = str(gid)
        current = metadata_block.get(key)
        if not isinstance(current, dict):
            current = {"timestamp": None, "clean_metadata": {}, "raw_metadata": {}}
        if not isinstance(current.get("clean_metadata"), dict):
            current["clean_metadata"] = {}
        current["raw_metadata"] = entry
        current["timestamp"] = time.time()
        metadata_block[key] = current
    data["metadata"] = metadata_block
    _save_master_cache(data)


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


def load_search_history(max_items: int = 10) -> list[dict]:
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
            if max_items and len(cleaned) > max_items:
                cleaned = cleaned[-max_items:]
            _update_master_cache(
                "search_history",
                _build_master_entry("search_history", "search_history", cache_file, None, last_read=time.time()),
            )
            return cleaned
    except Exception:
        return []


def save_search_history(items: list[dict], max_items: int = 10):
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
        if max_items and len(safe_items) > max_items:
            safe_items = safe_items[-max_items:]
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
    cache_file = get_cache_dir() / MASTER_CACHE_FILENAME
    if not cache_file.exists():
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
