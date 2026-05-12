#!/usr/bin/env python3
# mangascraper/extensions/skeleton/skeleton__api.msext.py

"""Skeleton extension API module example.

This module illustrates the small interface an extension may expose to
control per-site rate limits, CDN behaviour and archive support. The
extension manager will import any `*__api.msext.py` file found in an
extension folder and attach it to the extension's entry module as
`extension.api`.
"""

from typing import Dict, Any
import requests

# Local default for nhentai API used by this extension when calculating CDN info.
_LOCAL_NHENTAI_API_BASE = "https://nhentai.net/api/v2"


def get_rate_limit_config() -> Dict[str, Any]:
    """Return per-stage token-bucket parameters for this extension.

    Returned dict keys (example):
    - api: {"rate_per_min": 60, "capacity": 60}
    - cdn_images: {"rate_per_min": 300, "capacity": 600}
    - archives: {"rate_per_min": 5, "capacity": 5}

    The core `Sleep` logic will translate these into per-second rates and
    TokenBucket capacity.
    """
    return {
        "api": {"rate_per_min": 60, "capacity": 60},
        "cdn_images": {"rate_per_min": 600, "capacity": 1200},
        "archives": {"rate_per_min": 5, "capacity": 5},
    }


def get_cdn_policy() -> Dict[str, Any]:
    """Return CDN usage policy hints for the core scraper.

    Keys (example):
    - prefer_api_detail: bool  # prefer gallery detail API for paths
    - use_signed_archives: bool
    - cache_cdn_list_ttl: int  # seconds
    """
    mirrors = None
    base = _LOCAL_NHENTAI_API_BASE.rstrip("/")
    urls = [f"{base}/cdn", f"{base}/config"]
    for url in urls:
        try:
            r = requests.get(url, timeout=(5, 5))
            if r.status_code != 200:
                continue
            payload = r.json()
            if isinstance(payload, dict):
                maybe = payload.get("image_servers") or payload.get("imageServers")
                if isinstance(maybe, (list, tuple)) and maybe:
                    mirrors = [str(m).rstrip("/") for m in maybe if m]
                    break
        except Exception:
            continue

    policy = {
        "prefer_api_detail": True,
        "use_signed_archives": True,
        "cache_cdn_list_ttl": 600,
    }
    if mirrors:
        policy["mirrors"] = mirrors
    return policy


def archive_supported() -> bool:
    """Return True if this extension supports archive endpoint downloads."""
    return True
