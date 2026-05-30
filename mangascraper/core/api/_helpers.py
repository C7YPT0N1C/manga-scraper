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

# These are imported for use inside Helpers.sanitise() - they live in orchestrator
from mangascraper.core.orchestrator import (
    ALLOWED_SYMBOLS,
    BROKEN_SYMBOL_REPLACEMENTS,
    BROKEN_SYMBOL_BLACKLIST,
)

# _SYMBOL_TRANSLATION_TABLE is mutated at runtime, so we import the module
# and reference it as _constants._SYMBOL_TRANSLATION_TABLE
import mangascraper.core.api._constants as _constants




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
    def normalise_api_metadata(raw_meta: dict) -> dict:
        """Normalise API metadata from multiple possible nhentai API shapes into the
        internal format expected by the rest of the codebase.

        Defensive: unwraps common wrappers and maps alternate field names into the
        canonical keys (`id`, `media_id`, `images.pages`, `tags`, `title`).
        Adds derived fields `pages` (int) and `clean_title` (string).
        """
        if not isinstance(raw_meta, dict):
            return raw_meta

        meta = dict(raw_meta)

        # Unwrap common wrappers
        for key in ("gallery", "data"):
            val = meta.get(key)
            if isinstance(val, dict) and val.get("id"):
                meta = dict(val)
                break

        # Handle 'result' wrapper
        res = meta.get("result")
        if isinstance(res, dict) and res.get("id"):
            meta = dict(res)
        elif isinstance(res, list) and len(res) == 1 and isinstance(res[0], dict) and res[0].get("id"):
            meta = dict(res[0])

        # Normalise id
        if "id" not in meta:
            for alt in ("gallery_id", "gid"):
                if alt in meta:
                    meta["id"] = meta.pop(alt)
                    break
        try:
            if "id" in meta:
                meta["id"] = int(meta["id"])  # coerce when possible
        except Exception:
            pass

        # Normalise media_id
        if "media_id" not in meta:
            for alt in ("mediaId", "media"):
                if alt in meta:
                    meta["media_id"] = meta.pop(alt)
                    break
        # Infer media_id from page path if missing
        if "media_id" not in meta:
            pages_guess = None
            if isinstance(meta.get("pages"), list):
                pages_guess = meta.get("pages")
            elif isinstance(meta.get("images"), dict) and isinstance(meta["images"].get("pages"), list):
                pages_guess = meta["images"]["pages"]
            if isinstance(pages_guess, list) and pages_guess:
                first = pages_guess[0]
                path = first.get("path") if isinstance(first, dict) else None
                if isinstance(path, str) and "/galleries/" in path:
                    try:
                        parts = path.split("/galleries/", 1)[1].split("/", 1)
                        media_candidate = parts[0]
                        if media_candidate:
                            meta["media_id"] = media_candidate
                    except Exception:
                        pass

        # Title -> object
        title = meta.get("title")
        if isinstance(title, str):
            meta["title"] = {"english": title}

        # Tags: support list[str]
        tags = meta.get("tags")
        if isinstance(tags, list) and tags and all(isinstance(t, str) for t in tags):
            meta["tags"] = [{"type": "tag", "name": t} for t in tags]

        # Normalise pages: accept top-level 'pages', images.pages, or num_pages
        pages_list = None
        if isinstance(meta.get("pages"), list):
            pages_list = list(meta.pop("pages"))
        else:
            if isinstance(meta.get("images"), dict) and isinstance(meta["images"].get("pages"), list):
                pages_list = list(meta["images"]["pages"])

        if pages_list is None:
            for alt in ("page_list", "pages_list"):
                if isinstance(meta.get(alt), list):
                    pages_list = list(meta.pop(alt))
                    break

        if pages_list is None:
            num = None
            for alt in ("num_pages", "pages_count", "page_count"):
                if alt in meta:
                    try:
                        num = int(meta.get(alt) or 0)
                    except Exception:
                        num = None
                    break
            if num is not None and num > 0:
                pages_list = [{} for _ in range(num)]

        images = meta.get("images") if isinstance(meta.get("images"), dict) else {}
        if pages_list is not None:
            norm_pages = []
            for i, p in enumerate(pages_list):
                page = dict(p) if isinstance(p, dict) else {}
                try:
                    page_number = int(page.get("number", i + 1))
                except Exception:
                    page_number = i + 1
                page["number"] = page_number

                path = page.get("path") or page.get("file") or page.get("url")
                if isinstance(path, str):
                    page["path"] = path

                # infer type code 't' from extension
                if not page.get("t"):
                    ext = None
                    if isinstance(page.get("path"), str) and "." in page.get("path"):
                        try:
                            ext = page.get("path").rsplit(".", 1)[-1].lower()
                        except Exception:
                            ext = None
                    if not ext and isinstance(page.get("thumbnail"), str) and "." in page.get("thumbnail"):
                        try:
                            ext = page.get("thumbnail").rsplit(".", 1)[-1].lower()
                        except Exception:
                            ext = None
                    if ext in ("jpg", "jpeg"):
                        page["t"] = "j"
                    elif ext == "png":
                        page["t"] = "p"
                    elif ext == "gif":
                        page["t"] = "g"
                    else:
                        page["t"] = "w"

                if page.get("width"):
                    try:
                        page["width"] = int(page.get("width"))
                    except Exception:
                        pass
                if page.get("height"):
                    try:
                        page["height"] = int(page.get("height"))
                    except Exception:
                        pass

                norm_pages.append(page)

            images["pages"] = norm_pages
        else:
            images.setdefault("pages", [])

        meta["images"] = images

        # Ensure cover maps to page 1 if cover.path exists
        cover = meta.get("cover")
        if isinstance(cover, dict) and isinstance(cover.get("path"), str):
            try:
                if meta.get("images") and isinstance(meta["images"].get("pages"), list) and len(meta["images"]["pages"]) >= 1:
                    meta["images"]["pages"][0]["path"] = cover.get("path")
                    p0 = meta["images"]["pages"][0]
                    if p0.get("path") and "." in p0.get("path"):
                        ext = p0.get("path").rsplit(".", 1)[-1].lower()
                        if ext in ("jpg", "jpeg"):
                            p0["t"] = "j"
                        elif ext == "png":
                            p0["t"] = "p"
                        elif ext == "gif":
                            p0["t"] = "g"
                        else:
                            p0["t"] = "w"
            except Exception:
                pass

        # Derived convenience fields
        try:
            page_count = len(meta.get("images", {}).get("pages", []))
        except Exception:
            page_count = 0
        meta["pages"] = page_count

        try:
            meta["clean_title"] = Helpers.sanitise(meta)
        except Exception:
            try:
                t = meta.get("title")
                if isinstance(t, dict):
                    meta["clean_title"] = Helpers.safe_text(t.get("english") or t.get("pretty") or "")
                else:
                    meta["clean_title"] = Helpers.safe_text(t)
            except Exception:
                meta["clean_title"] = ""

        return meta

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
    def archive_filename(base_name: str, archive_ext: str) -> str:
        """Build a safe archive filename from a gallery base name.

        Trims trailing dots/spaces to avoid accidental names like "TITLE..cbz"
        when a title ends with a full stop.
        """
        raw = Helpers.safe_text(base_name, "").strip()
        ext = Helpers.safe_text(archive_ext, "").strip().lower() or ".cbz"
        if not ext.startswith("."):
            ext = f".{ext}"

        stem = os.path.splitext(raw)[0] if raw.lower().endswith((".cbz", ".zip")) else raw
        stem = stem.rstrip(" .")
        if not stem:
            stem = "UNTITLED"
        return f"{stem}{ext}"

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


# --- DB-related helpers (migrated from _db_helpers.py) ---
def prune_all_caches():
    """Remove expired entries in cache tables based on expires_at. Returns count of deleted entries."""
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