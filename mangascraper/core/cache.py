#!/usr/bin/env python3
# mangascraper/core/cache.py
"""
Caching utilities for metadata and search results.
Stores cache by search criteria (artist, tag, group, etc.) rather than individual galleries.
"""

import json
import time
from pathlib import Path

# Cache TTL: 3 hours
TTL = 3 * 60 * 60
SEARCH_HISTORY_FILENAME = "search_history.json"
MASTER_CACHE_FILENAME = "master_cache.json"


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
        return {"entries": {}}
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"entries": {}}
        entries = data.get("entries")
        if not isinstance(entries, dict):
            return {"entries": {}}
        return {"entries": entries}
    except Exception:
        return {"entries": {}}


def _save_master_cache(data: dict):
    try:
        cache_file = get_cache_dir() / MASTER_CACHE_FILENAME
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _update_master_cache(entry_key: str, entry: dict):
    data = _load_master_cache()
    data["entries"][entry_key] = entry
    _save_master_cache(data)


def _remove_master_cache_entry(entry_key: str):
    data = _load_master_cache()
    if entry_key in data["entries"]:
        del data["entries"][entry_key]
        _save_master_cache(data)


def _build_master_entry(
    cache_type: str,
    key: str,
    cache_file: Path,
    ttl_seconds: int | None,
    last_read: float | None = None,
    last_write: float | None = None,
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
    return {
        "type": cache_type,
        "key": key,
        "path": str(cache_file),
        "size": size,
        "last_read": last_read,
        "last_write": write_time,
        "ttl": ttl_seconds,
        "expires_at": expires_at,
    }


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
                    _update_master_cache(
                        f"metadata:{cache_key}",
                        _build_master_entry(
                            "metadata",
                            cache_key,
                            cache_file,
                            TTL,
                            last_read=time.time(),
                            last_write=data.get('timestamp', None),
                        ),
                    )
                    return data.get('metadata', {})
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
        data = {
            'timestamp': timestamp,
            'metadata': {str(k): v for k, v in metadata.items()}  # Ensure keys are strings
        }
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _update_master_cache(
            f"metadata:{cache_key}",
            _build_master_entry("metadata", cache_key, cache_file, TTL, last_write=timestamp),
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
