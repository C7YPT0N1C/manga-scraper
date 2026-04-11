"""API package for MangaScraper core logic.

This re-exports the high-level convenience instances and selected helpers
so callers can import from `mangascraper.core.api` directly.
"""

# Re-export the main API module contents (cache, db, helpers, fetch, etc.)
from .api import *  # noqa: F401,F403

# Re-export helper functions implemented in _db_helpers so older imports
# that import these directly from the package continue to work.
from ._db_helpers import prune_all_caches, read_cached_metadata_entry, clear_cached_items  # noqa: F401

__all__ = [
	*[n for n in dir() if not n.startswith("_")],
]
