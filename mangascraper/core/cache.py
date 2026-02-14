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
        # Sanitise for use as filename
        safe_value = "".join(c for c in search_value if c.isalnum() or c in ('-', '_')).lower()
        return f"{search_type}_{safe_value}"
    return search_type


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
                    return data.get('metadata', {})
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
        data = {
            'timestamp': time.time(),
            'metadata': {str(k): v for k, v in metadata.items()}  # Ensure keys are strings
        }
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # Silently fail


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
        else:
            # Clear all cache files
            for cache_file in cache_dir.glob("*.json"):
                cache_file.unlink()
    except Exception:
        pass  # Silently fail
