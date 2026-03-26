#!/usr/bin/env python3
# mangascraper/dashboard/routes/data_routes.py

import os, time, threading, io, requests, json, re, zipfile, tempfile, posixpath, mimetypes
from flask import Blueprint, abort, jsonify, request, send_file, send_from_directory

from mangascraper.core.api import api as scraperapi
from mangascraper.core import orchestrator

# ── Blueprints ────────────────────────────────────────────────────────────────

db_bp = Blueprint("database", __name__)
gallery_bp = Blueprint("gallery", __name__)
collections_bp = Blueprint("collections", __name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_path(base: str, *parts: str) -> str | None:
    """
    Join parts onto base and return the real path only if it stays inside base.
    Returns None if the result would escape the base directory.
    """
    joined = os.path.realpath(os.path.join(base, *parts))
    base_real = os.path.realpath(base)
    if base_real == os.sep:
        return joined
    if not joined.startswith(base_real + os.sep) and joined != base_real:
        return None
    return joined


def _requested_root() -> str:
    return str(request.args.get("root") or "").strip()


def _extension_short_name(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "." in text:
        text = text.split(".")[-1]
    if text.endswith("__msext"):
        text = text[:-7]
    return text


def _prioritise_roots(roots: list[dict]) -> list[dict]:
    orchestrator.refresh_globals()
    selected_extension = _extension_short_name(getattr(orchestrator, "extension", ""))

    def rank(item: dict) -> tuple:
        ext = _extension_short_name(item.get("extension_used", ""))
        if ext == "skeleton":
            pri = 0
        elif selected_extension and ext == selected_extension:
            pri = 1
        else:
            pri = 2
        return (pri, ext, str(item.get("root_path") or ""))

    return sorted(roots, key=rank)


def _available_roots() -> list[dict]:
    roots = scraperapi.DB.list_download_locations()
    if roots:
        return _prioritise_roots(roots)
    return []


def _resolve_gallery_path(creator: str, gallery: str) -> tuple[str | None, str | None]:
    requested = _requested_root()
    roots = _available_roots()
    if requested and any(item.get("root_path") == requested for item in roots):
        roots = [item for item in roots if item.get("root_path") == requested]

    for item in roots:
        root = item.get("root_path")
        if not root:
            continue
        path = _safe_path(root, creator, gallery)
        if not path:
            continue
        if os.path.isdir(path) or _is_archive(path):
            return root, path
    return None, None


def _resolve_root_path() -> str:
    requested = _requested_root()
    roots = _available_roots()
    if requested:
        for item in roots:
            if item["root_path"] == requested:
                return requested
    return roots[0]["root_path"] if roots else ""


def _file_browser_roots() -> list[dict]:
    """Return roots shown in file browser, including full filesystem roots."""
    roots: list[dict] = []
    seen: set[str] = set()

    if os.name == "nt":
        try:
            import ctypes
            mask = int(ctypes.windll.kernel32.GetLogicalDrives())
            for letter_ord in range(ord("A"), ord("Z") + 1):
                bit = 1 << (letter_ord - ord("A"))
                if not (mask & bit):
                    continue
                drive = f"{chr(letter_ord)}:{os.sep}"
                drive_real = os.path.realpath(drive)
                if drive_real in seen:
                    continue
                seen.add(drive_real)
                roots.append({"root_path": drive_real, "extension_used": "system", "count": 0})
        except Exception:
            pass
    else:
        root_real = os.path.realpath(os.sep)
        seen.add(root_real)
        roots.append({"root_path": root_real, "extension_used": "system", "count": 0})

    if not roots:
        root_real = os.path.realpath(os.sep)
        seen.add(root_real)
        roots.append({"root_path": root_real, "extension_used": "system", "count": 0})

    for item in _available_roots():
        root_path = os.path.realpath(str(item.get("root_path") or "").strip())
        if not root_path or root_path in seen:
            continue
        seen.add(root_path)
        roots.append(
            {
                "root_path": root_path,
                "extension_used": str(item.get("extension_used") or ""),
                "count": int(item.get("count") or 0),
            }
        )
    return roots


def _resolve_file_browser_root(requested_root: str | None = None) -> str:
    """Resolve a browsable root; accepts any absolute existing directory."""
    requested = str(requested_root or "").strip()
    if requested:
        candidate = os.path.realpath(requested)
        if os.path.isabs(candidate) and os.path.isdir(candidate):
            return candidate

    # Default to DB-managed download roots first (ordered with skeleton priority).
    managed_roots = _available_roots()
    if managed_roots:
        preferred = os.path.realpath(str(managed_roots[0].get("root_path") or "").strip())
        if preferred and os.path.isdir(preferred):
            return preferred

    roots = _file_browser_roots()
    if roots:
        fallback = str(roots[0].get("root_path") or os.sep)
        return os.path.realpath(fallback)
    return os.path.realpath(os.sep)


def _creator_and_gallery_from_location(root_path: str, download_path: str) -> tuple[str, str] | tuple[None, None]:
    root_real = os.path.realpath(str(root_path or ""))
    file_real = os.path.realpath(str(download_path or ""))
    if not root_real or not file_real:
        return None, None
    if file_real != root_real and not file_real.startswith(root_real + os.sep):
        return None, None
    rel = os.path.relpath(file_real, root_real)
    if rel in {".", ""}:
        return None, None
    parts = [part for part in rel.split(os.sep) if part and part != "."]
    if len(parts) < 2:
        return None, None
    creator = parts[0]
    gallery = parts[1]
    if creator.startswith(".") or gallery.startswith("."):
        return None, None
    return creator, gallery


def _is_archive(path: str) -> bool:
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() in {".cbz", ".zip"}


def _display_gallery_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    stem, ext = os.path.splitext(text)
    if ext.lower() in {".cbz", ".zip"} and stem:
        return stem
    return text


def _gallery_ids_for_deleted_path(root_path: str, relative_parts: list[str]) -> list[int]:
    """Resolve gallery IDs affected by a filesystem delete path."""
    if not root_path or not relative_parts:
        return []

    creator_target = str(relative_parts[0] or "").strip().lower()
    gallery_target = str(relative_parts[1] or "").strip().lower() if len(relative_parts) >= 2 else ""
    matched = set()

    for row in scraperapi.DB.list_gallery_locations(root_path=root_path):
        gid = row.get("gallery_id")
        dpath = row.get("download_path")
        if gid is None or not dpath:
            continue
        creator_name, gallery_name = _creator_and_gallery_from_location(root_path, dpath)
        creator_key = str(creator_name or "").strip().lower()
        gallery_key = str(gallery_name or "").strip().lower()
        if not creator_key:
            continue

        if len(relative_parts) == 1:
            if creator_key == creator_target:
                matched.add(int(gid))
            continue

        if creator_key == creator_target and gallery_key == gallery_target:
            matched.add(int(gid))

    return sorted(matched)


def _scan_creators_from_filesystem(root_path: str) -> set[str]:
    creators = set()
    if not root_path or not os.path.isdir(root_path):
        return creators

    try:
        for creator_name in os.listdir(root_path):
            if not creator_name or creator_name.startswith("."):
                continue
            creator_path = _safe_path(root_path, creator_name)
            if not creator_path or not os.path.isdir(creator_path):
                continue

            has_gallery = False
            for entry_name in os.listdir(creator_path):
                if not entry_name or entry_name.startswith("."):
                    continue
                entry_path = os.path.join(creator_path, entry_name)
                if os.path.isdir(entry_path) or _is_archive(entry_path):
                    has_gallery = True
                    break

            if has_gallery:
                creators.add(creator_name)
    except OSError:
        return creators

    return creators


def _scan_galleries_from_filesystem(root_path: str, creator: str) -> set[str]:
    galleries = set()
    if not root_path:
        return galleries

    creator_path = _safe_path(root_path, creator)
    if not creator_path or not os.path.isdir(creator_path):
        return galleries

    try:
        for entry_name in os.listdir(creator_path):
            if not entry_name or entry_name.startswith("."):
                continue
            entry_path = os.path.join(creator_path, entry_name)
            if os.path.isdir(entry_path) or _is_archive(entry_path):
                galleries.add(entry_name)
    except OSError:
        return galleries

    return galleries


def _archive_pages(archive_path: str) -> list[str]:
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
    pages = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        for name in zf.namelist():
            normalised = posixpath.normpath(name)
            if normalised.startswith("../") or normalised.startswith("/"):
                continue
            if normalised.endswith("/"):
                continue
            if os.path.splitext(normalised)[1].lower() in IMAGE_EXTS:
                pages.append(normalised)
    return sorted(pages)


def _parse_json_int_list(value) -> list[int]:
    try:
        data = json.loads(value) if value else []
    except Exception:
        data = []
    if not isinstance(data, list):
        return []

    items = []
    for item in data:
        try:
            items.append(int(item))
        except Exception:
            continue
    return items


def _gallery_id_from_name(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"^\((\d+)\)", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _count_pages_on_disk(path: str) -> int | None:
    if not path:
        return None
    try:
        if os.path.isdir(path):
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
            return len(
                [
                    name for name in os.listdir(path)
                    if os.path.splitext(name)[1].lower() in image_exts
                ]
            )
        if _is_archive(path):
            return len(_archive_pages(path))
    except Exception:
        return None
    return None

def _gallery_paths_from_galleries_table(gallery_id: int) -> dict:
    """Return download_path and cover_path from the Galleries table for a gallery id.
    Useful as an authoritative fallback when GalleryLocations entries don't resolve correctly.
    """
    scraperapi.DB.init_db()
    download_path = ""
    cover_path = ""
    try:
        with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT download_path, cover_path FROM Galleries WHERE id=?", (int(gallery_id),))
            row = cursor.fetchone()
            if row:
                download_path = str(row[0] or "").strip()
                cover_path = str(row[1] or "").strip()
    except Exception:
        pass
    return {"download_path": download_path, "cover_path": cover_path}


def _tolerant_gallery_path(gallery_id: int, candidate: str) -> str | None:
    """Given a candidate download_path, try tolerant variants and return a path that exists.

    Tries, in order:
    - the candidate itself
    - prefixing the basename with "(<id>) "
    - URL-decoding the basename and variants
    - a best-effort substring match inside the candidate directory
    Returns the first existing filesystem path or None.
    """
    if not candidate:
        return None
    try:
        if os.path.exists(candidate):
            return candidate
        dirp = os.path.dirname(candidate) or os.path.dirname(os.path.realpath(candidate))
        base = os.path.basename(candidate)
        # try prefixing with (id)
        try:
            pid = int(gallery_id)
            pref = f"({pid}) {base}"
            alt = os.path.join(dirp, pref)
            if os.path.exists(alt):
                return alt
        except Exception:
            pass

        # try URL-decoded basename variants
        try:
            from urllib.parse import unquote
            decoded = unquote(base)
            if decoded and decoded != base:
                alt2 = os.path.join(dirp, decoded)
                if os.path.exists(alt2):
                    return alt2
                try:
                    pref2 = f"({int(gallery_id)}) {decoded}"
                    alt3 = os.path.join(dirp, pref2)
                    if os.path.exists(alt3):
                        return alt3
                except Exception:
                    pass
        except Exception:
            pass

        # fuzzy: look for an entry in the directory that contains the base as substring
        if os.path.isdir(dirp):
            for name in os.listdir(dirp):
                if not name:
                    continue
                if base and base in name:
                    candidate2 = os.path.join(dirp, name)
                    if os.path.exists(candidate2):
                        return candidate2
                # also accept prefixed names like (id) ...
                if base and name.startswith(f"({gallery_id})") and base.split()[0] in name:
                    candidate2 = os.path.join(dirp, name)
                    if os.path.exists(candidate2):
                        return candidate2
    except Exception:
        return None
    return None


def _build_filter_options(items: list[dict], item_type: str) -> dict:
    languages = set()
    parodies = set()
    tags = set()
    statuses = set()
    favourites = set()
    page_values = []
    tag_counts = []
    gallery_counts = []

    for item in items:
        if not isinstance(item, dict):
            continue
        for language in item.get("languages") or []:
            if language:
                languages.add(str(language))
        for parody in item.get("parodies") or []:
            if parody:
                parodies.add(str(parody))
        for tag in item.get("tags") or []:
            if tag:
                tags.add(str(tag))

        page_count = item.get("page_count")
        if isinstance(page_count, (int, float)):
            page_values.append(int(page_count))

        tag_count = item.get("tag_count")
        if isinstance(tag_count, (int, float)):
            tag_counts.append(int(tag_count))

        favourites.add("yes" if bool(item.get("favourite")) else "no")

        if item_type == "gallery":
            status = str(item.get("status") or "").strip()
            if status:
                statuses.add(status)
        else:
            gallery_count = item.get("gallery_count")
            if isinstance(gallery_count, (int, float)):
                gallery_counts.append(int(gallery_count))

    result = {
        "languages": sorted(languages, key=str.lower),
        "parodies": sorted(parodies, key=str.lower),
        "tags": sorted(tags, key=str.lower),
        "favourites": sorted(favourites),
        "page_count": {
            "min": min(page_values) if page_values else 0,
            "max": max(page_values) if page_values else 0,
        },
        "tag_count": {
            "min": min(tag_counts) if tag_counts else 0,
            "max": max(tag_counts) if tag_counts else 0,
        },
    }
    if item_type == "gallery":
        result["statuses"] = sorted(statuses, key=str.lower)
    else:
        result["gallery_count"] = {
            "min": min(gallery_counts) if gallery_counts else 0,
            "max": max(gallery_counts) if gallery_counts else 0,
        }
    return result


def _table_filter_options() -> dict:
    """Return filter options directly from Languages, Parodies and Tags tables."""
    scraperapi.DB.init_db()
    with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM Languages WHERE name IS NOT NULL AND TRIM(name) != ''")
        languages = sorted({str(row[0]).strip() for row in cursor.fetchall() if row and row[0]}, key=str.lower)
        cursor.execute("SELECT name FROM Parodies WHERE name IS NOT NULL AND TRIM(name) != ''")
        parodies = sorted({str(row[0]).strip() for row in cursor.fetchall() if row and row[0]}, key=str.lower)
        cursor.execute("SELECT name FROM Tags WHERE name IS NOT NULL AND TRIM(name) != ''")
        tags = sorted({str(row[0]).strip() for row in cursor.fetchall() if row and row[0]}, key=str.lower)
    return {"languages": languages, "parodies": parodies, "tags": tags}


def _gallery_meta_by_ids(gallery_ids: list[int]) -> dict[int, dict]:
    """Hydrate gallery metadata directly from Galleries table by GalleryId."""
    ids = sorted({int(gid) for gid in (gallery_ids or []) if gid is not None})
    if not ids:
        return {}

    scraperapi.DB.init_db()
    with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, name FROM Languages")
        language_name_map = {int(language_id): str(name) for language_id, name in cursor.fetchall() if language_id is not None and name}
        
        cursor.execute("SELECT id, name FROM Parodies")
        parody_name_map = {int(parody_id): str(name) for parody_id, name in cursor.fetchall() if parody_id is not None and name}

        cursor.execute("SELECT id, name FROM Tags")
        tag_name_map = {int(tag_id): str(name) for tag_id, name in cursor.fetchall() if tag_id is not None and name}

        placeholders = ",".join("?" for _ in ids)
        cursor.execute(
            f"""
            SELECT id, clean_title, raw_title, num_pages, language_ids, parody_ids, tag_ids, status, favourite, rating
            FROM Galleries
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
        )

        result = {}
        for row in cursor.fetchall():
            gid = int(row[0])
            clean_title = str(row[1] or "").strip()
            raw_title = str(row[2] or "").strip()
            language_ids = _parse_json_int_list(row[4])
            parody_ids = _parse_json_int_list(row[5])
            tag_ids = _parse_json_int_list(row[6])
            result[gid] = {
                "title": clean_title or raw_title or f"Gallery {gid}",
                "clean_title": clean_title,
                "raw_title": raw_title,
                "page_count": int(row[3]) if row[3] is not None else 0,
                "languages": [language_name_map[language_id] for language_id in language_ids if language_id in language_name_map],
                "parodies": [parody_name_map[parody_id] for parody_id in parody_ids if parody_id in parody_name_map],
                "tags": [tag_name_map[tag_id] for tag_id in tag_ids if tag_id in tag_name_map],
                "status": str(row[6] or ""),
                "favourite": bool(row[7]),
                "rating": float(row[8]) if row[8] is not None else None,
            }
        return result


def _apply_gallery_db_meta(items: list[dict]) -> list[dict]:
    """Overlay gallery rows with authoritative DB values from Galleries table."""
    gallery_ids = []
    for item in items or []:
        gid = item.get("gallery_id")
        if gid is None:
            continue
        try:
            gallery_ids.append(int(gid))
        except Exception:
            continue

    meta_by_id = _gallery_meta_by_ids(gallery_ids)
    if not meta_by_id:
        return items

    for item in items:
        gid = item.get("gallery_id")
        if gid is None:
            continue
        try:
            gid_int = int(gid)
        except Exception:
            continue
        meta = meta_by_id.get(gid_int)
        if not meta:
            continue
        # Prefer DB-clean title for display when available
        if isinstance(meta.get("title"), str) and meta.get("title"):
            item["title"] = meta.get("title")
            item["name"] = meta.get("title")
        elif isinstance(meta.get("clean_title"), str) and meta.get("clean_title"):
            item["title"] = meta.get("clean_title")
            item["name"] = meta.get("clean_title")
        item["page_count"] = meta["page_count"]
        item["languages"] = list(meta["languages"])
        item["parodies"] = list(meta["parodies"])
        item["tags"] = list(meta["tags"])
        item["tag_count"] = len(meta["tags"])
        item["status"] = meta["status"]
        item["favourite"] = bool(meta["favourite"])
        item["rating"] = meta["rating"]

    return items


def _apply_creator_db_meta(items: list[dict]) -> list[dict]:
    """Overlay creator rows with DB-backed tags/languages/favourite using Creators + Galleries."""
    if not items:
        return items

    scraperapi.DB.init_db()
    with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT id, name FROM Languages")
        language_name_map = {int(language_id): str(name) for language_id, name in cursor.fetchall() if language_id is not None and name}
        
        cursor.execute("SELECT id, name FROM Tags")
        tag_name_map = {int(tag_id): str(name) for tag_id, name in cursor.fetchall() if tag_id is not None and name}

        cursor.execute("SELECT id, name, display_name, most_popular_tags, favourite FROM Creators")
        creator_rows = cursor.fetchall()

        creator_match: dict[str, dict] = {}
        creator_ids: set[int] = set()
        for item in items:
            creator_name = str(item.get("label") or item.get("name") or "").strip()
            if not creator_name:
                continue

            creator_name_lower = creator_name.lower()
            matched = None
            for row in creator_rows:
                creator_id = int(row[0])
                raw_name = str(row[1] or "").strip()
                display_name = str(row[2] or "").strip()

                if display_name and display_name.lower() == creator_name_lower:
                    matched = row
                    break

            if matched is None:
                for row in creator_rows:
                    raw_name = str(row[1] or "").strip()
                    if raw_name and raw_name.lower() == creator_name_lower:
                        matched = row
                        break

            if matched is None:
                continue

            creator_id = int(matched[0])
            creator_match[creator_name] = {
                "creator_id": creator_id,
                "most_popular_tags": _parse_json_int_list(matched[3]),
                "favourite": bool(matched[4]),
            }
            creator_ids.add(creator_id)

        creator_languages: dict[int, set[str]] = {creator_id: set() for creator_id in creator_ids}
        if creator_ids:
            cursor.execute("SELECT creator_ids, language_ids FROM Galleries")
            for row in cursor.fetchall():
                gallery_creator_ids = set(_parse_json_int_list(row[0]))
                if not gallery_creator_ids:
                    continue
                gallery_language_ids = _parse_json_int_list(row[1])
                gallery_languages = {language_name_map[language_id] for language_id in gallery_language_ids if language_id in language_name_map}
                for creator_id in gallery_creator_ids.intersection(creator_ids):
                    creator_languages.setdefault(int(creator_id), set()).update(gallery_languages)

    for item in items:
        creator_name = str(item.get("label") or item.get("name") or "").strip()
        matched = creator_match.get(creator_name)
        if not matched:
            continue

        creator_id = int(matched["creator_id"])
        tag_names = [tag_name_map[tag_id] for tag_id in matched["most_popular_tags"] if tag_id in tag_name_map]
        language_names = sorted(creator_languages.get(creator_id, set()), key=str.lower)

        item["tags"] = sorted(set(tag_names), key=str.lower)
        item["tag_count"] = len(item["tags"])
        item["languages"] = language_names
        item["favourite"] = bool(matched["favourite"]) or bool(item.get("favourite"))

    return items


def _db_filter_options_for_creator(creator_name: str, roots: list[str] | None = None) -> dict:
    """Return language/parody/tag filter values from DB via GalleryLocations path mapping."""
    creator_key = str(creator_name or "").strip().lower()
    if not creator_key:
        return {"languages": [], "parodies": [], "tags": []}

    root_candidates = [str(root or "").strip() for root in (roots or []) if str(root or "").strip()]
    if not root_candidates:
        root_candidates = [item.get("root_path") for item in _available_roots() if item.get("root_path")]

    gallery_ids: set[int] = set()
    for root in root_candidates:
        for row in scraperapi.DB.list_gallery_locations(root_path=root):
            gid = row.get("gallery_id")
            dpath = row.get("download_path")
            if gid is None or not dpath:
                continue
            found_creator, _ = _creator_and_gallery_from_location(root, dpath)
            if str(found_creator or "").strip().lower() == creator_key:
                try:
                    gallery_ids.add(int(gid))
                except Exception:
                    continue

    if not gallery_ids:
        return {"languages": [], "parodies": [], "tags": []}

    scraperapi.DB.init_db()
    with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT id, name FROM Languages")
        language_name_map = {
            int(language_id): str(name)
            for language_id, name in cursor.fetchall()
            if language_id is not None and name
        }
        
        cursor.execute("SELECT id, name FROM Parodies")
        parody_name_map = {
            int(parody_id): str(name)
            for parody_id, name in cursor.fetchall()
            if parody_id is not None and name
        }
        
        cursor.execute("SELECT id, name FROM Tags")
        tag_name_map = {
            int(tag_id): str(name)
            for tag_id, name in cursor.fetchall()
            if tag_id is not None and name
        }

        placeholders = ",".join("?" for _ in gallery_ids)
        cursor.execute(
            f"SELECT language_ids, parody_ids, tag_ids FROM Galleries WHERE id IN ({placeholders})",
            tuple(sorted(gallery_ids)),
        )

        language_ids = set()
        parody_ids = set()
        tag_ids = set()
        for language_ids_json, parody_ids_json, tag_ids_json in cursor.fetchall():
            language_ids.update(_parse_json_int_list(language_ids_json))
            parody_ids.update(_parse_json_int_list(parody_ids_json))
            tag_ids.update(_parse_json_int_list(tag_ids_json))

    languages = sorted(
        [language_name_map[language_id] for language_id in language_ids if language_id in language_name_map],
        key=str.lower,
    )
    parodies = sorted(
        [parody_name_map[parody_id] for parody_id in parody_ids if parody_id in parody_name_map],
        key=str.lower,
    )
    tags = sorted(
        [tag_name_map[tag_id] for tag_id in tag_ids if tag_id in tag_name_map],
        key=str.lower,
    )
    return {"languages": languages, "tags": tags}


def _prefer_gallery_item(existing: dict | None, candidate: dict) -> dict:
    if not existing:
        return candidate

    existing_id = existing.get("gallery_id")
    candidate_id = candidate.get("gallery_id")
    if existing_id is None and candidate_id is not None:
        return candidate
    if existing_id is not None and candidate_id is None:
        return existing

    existing_score = (len(existing.get("languages") or []) + len(existing.get("parodies") or []) + len(existing.get("tags") or []))
    candidate_score = (len(candidate.get("languages") or []) + len(candidate.get("parodies") or []) + len(candidate.get("tags") or []))
    if candidate_score > existing_score:
        return candidate

    if not existing.get("favourite") and candidate.get("favourite"):
        return candidate

    return existing


def _merge_gallery_filter_options(primary: dict, fallback: dict) -> dict:
    merged = dict(primary or {})
    primary_languages = list(merged.get("languages") or [])
    primary_parodies = list(merged.get("parodies") or [])
    primary_tags = list(merged.get("tags") or [])
    fallback_languages = list((fallback or {}).get("languages") or [])
    fallback_parodies = list((fallback or {}).get("parodies") or [])
    fallback_tags = list((fallback or {}).get("tags") or [])

    merged["languages"] = sorted(set(primary_languages).union(fallback_languages), key=str.lower)
    merged["parodies"] = sorted(set(primary_parodies).union(fallback_parodies), key=str.lower)
    merged["tags"] = sorted(set(primary_tags).union(fallback_tags), key=str.lower)
    return merged


def _gallery_id_from_location(root_path: str, creator: str, gallery: str) -> int | None:
    root_real = os.path.realpath(str(root_path or ""))
    if not root_real:
        return None
    creator_key = str(creator or "").strip().lower()
    gallery_key = str(gallery or "").strip().lower()

    for row in scraperapi.DB.list_gallery_locations(root_path=root_path):
        gid = row.get("gallery_id")
        dpath = row.get("download_path")
        if gid is None or not dpath:
            continue
        row_creator, row_gallery = _creator_and_gallery_from_location(root_real, dpath)
        if str(row_creator or "").strip().lower() != creator_key:
            continue
        if str(row_gallery or "").strip().lower() != gallery_key:
            continue
        try:
            return int(gid)
        except Exception:
            continue
    return None


def _gallery_meta_by_id(gallery_id: int) -> dict:
    meta = {
        "gallery_id": int(gallery_id),
        "title": "",
        "page_count": 0,
        "creators": [],
        "languages": [],
        "parodies": [],
        "tags": [],
        "status": "",
        "favourite": False,
        "rating": None,
    }

    scraperapi.DB.init_db()
    with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, name FROM Languages")
        language_name_map = {int(language_id): str(name) for language_id, name in cursor.fetchall() if language_id is not None and name}
        
        cursor.execute("SELECT id, name FROM Parodies")
        parody_name_map = {int(parody_id): str(name) for parody_id, name in cursor.fetchall() if parody_id is not None and name}

        cursor.execute("SELECT id, name FROM Tags")
        tag_name_map = {int(tag_id): str(name) for tag_id, name in cursor.fetchall() if tag_id is not None and name}

        cursor.execute(
            """
            SELECT clean_title, raw_title, num_pages, language_ids, parody_ids, tag_ids, status, favourite, rating, creator_ids
            FROM Galleries
            WHERE id = ?
            """,
            (int(gallery_id),),
        )
        row = cursor.fetchone()
        if not row:
            return meta

        language_ids = _parse_json_int_list(row[3])
        parody_ids = _parse_json_int_list(row[4])
        tag_ids = _parse_json_int_list(row[5])
        creator_ids = _parse_json_int_list(row[9])

        cursor.execute("SELECT id, name, display_name FROM Creators")
        creator_name_map = {}
        for creator_id, name, display_name in cursor.fetchall():
            if creator_id is None:
                continue
            label = str(display_name or name or "").strip()
            if label:
                creator_name_map[int(creator_id)] = label

        meta["title"] = _display_gallery_name(str(row[0] or row[1] or ""))
        meta["page_count"] = int(row[2]) if row[2] is not None else 0
        meta["creators"] = [creator_name_map[cid] for cid in creator_ids if cid in creator_name_map]
        meta["languages"] = [language_name_map[language_id] for language_id in language_ids if language_id in language_name_map]
        meta["parodies"] = [parody_name_map[parody_id] for parody_id in parody_ids if parody_id in parody_name_map]
        meta["tags"] = [tag_name_map[tag_id] for tag_id in tag_ids if tag_id in tag_name_map]
        meta["status"] = str(row[6] or "")
        meta["favourite"] = bool(row[7])
        meta["rating"] = float(row[8]) if row[8] is not None else None

    return meta


def _build_gallery_lookup_by_creator_and_title(
    cursor,
    language_name_map: dict[int, str],
    parody_name_map: dict[int, str],
    tag_name_map: dict[int, str],
) -> dict[tuple[str, str], dict]:
    """Build fallback lookup for gallery metadata by (creator, title)."""
    creator_names_by_id: dict[int, set[str]] = {}
    cursor.execute("SELECT id, name, display_name FROM Creators")
    for creator_id, name, display_name in cursor.fetchall():
        cid = int(creator_id)
        names = set()
        for raw in (name, display_name):
            text = str(raw or "").strip().lower()
            if text:
                names.add(text)
        if names:
            creator_names_by_id[cid] = names

    lookup: dict[tuple[str, str], dict] = {}
    cursor.execute(
        """
        SELECT id, clean_title, raw_title, num_pages, language_ids, parody_ids, tag_ids, status, favourite, rating, creator_ids
        FROM Galleries
        """
    )
    for row in cursor.fetchall():
        gallery_id = int(row[0])
        clean_title = str(row[1] or "").strip()
        raw_title = str(row[2] or "").strip()
        language_ids = _parse_json_int_list(row[4])
        parody_ids = _parse_json_int_list(row[5])
        tag_ids = _parse_json_int_list(row[6])
        creator_ids = _parse_json_int_list(row[10])

        creator_names: set[str] = set()
        for creator_id in creator_ids:
            creator_names.update(creator_names_by_id.get(int(creator_id), set()))
        if not creator_names:
            continue

        title_candidates = {
            text.lower()
            for text in (clean_title, raw_title)
            if text
        }
        for text in (clean_title, raw_title):
            display_text = _display_gallery_name(text)
            if display_text:
                title_candidates.add(display_text.lower())
        if not title_candidates:
            continue

        meta = {
            "gallery_id": gallery_id,
            "clean_title": clean_title,
            "raw_title": raw_title,
            "num_pages": int(row[3]) if row[3] is not None else None,
            "tags": [tag_name_map[tag_id] for tag_id in tag_ids if tag_id in tag_name_map],
            "languages": [language_name_map[language_id] for language_id in language_ids if language_id in language_name_map],
            "status": str(row[7] or ""),
            "favourite": bool(row[8]),
            "rating": float(row[9]) if row[9] is not None else None,
        }

        for creator_name in creator_names:
            for title in title_candidates:
                key = (creator_name, title)
                existing = lookup.get(key)
                if not existing:
                    lookup[key] = meta
                    continue
                existing_score = len(existing.get("languages") or []) + len(existing.get("parodies") or []) + len(existing.get("tags") or [])
                meta_score = len(meta.get("languages") or []) + len(meta.get("parodies") or []) + len(meta.get("tags") or [])
                if meta_score > existing_score:
                    lookup[key] = meta

    return lookup


def _load_gallery_browser_root_dataset(root_path: str) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    creators: dict[str, dict] = {}
    galleries_by_creator: dict[str, list[dict]] = {}

    scraperapi.DB.init_db()
    location_rows = scraperapi.DB.list_gallery_locations(root_path=root_path)
    valid_rows = []
    gallery_ids = set()

    for row in location_rows:
        download_path = row.get("download_path", "")
        if not (os.path.isdir(download_path) or _is_archive(download_path)):
            continue
        creator_name, gallery_name = _creator_and_gallery_from_location(root_path, download_path)
        if not creator_name or not gallery_name:
            continue
        row = dict(row)
        row["creator_name"] = creator_name
        row["gallery_name"] = gallery_name
        if row.get("gallery_id") is None:
            row["gallery_id"] = _gallery_id_from_name(gallery_name)
        valid_rows.append(row)
        if row.get("gallery_id") is not None:
            gallery_ids.add(int(row["gallery_id"]))

    gallery_meta = {}
    gallery_lookup = {}
    language_name_map = {}
    parody_name_map = {}
    tag_name_map = {}
    creator_favourite_map = {}

    with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(Creators)")
        creator_columns = {str(row[1] or "") for row in cursor.fetchall()}
        if "favourite" in creator_columns:
            cursor.execute("SELECT name, display_name, favourite FROM Creators")
            for name, display_name, favourite in cursor.fetchall():
                for key in (str(name or "").strip().lower(), str(display_name or "").strip().lower()):
                    if key:
                        creator_favourite_map[key] = bool(favourite)
        
        cursor.execute("SELECT id, name FROM Languages")
        language_name_map = {int(row[0]): str(row[1]) for row in cursor.fetchall() if row[0] is not None and row[1]}
        
        cursor.execute("SELECT id, name FROM Parodies")
        parody_name_map = {int(row[0]): str(row[1]) for row in cursor.fetchall() if row[0] is not None and row[1]}

        cursor.execute("SELECT id, name FROM Tags")
        tag_name_map = {int(row[0]): str(row[1]) for row in cursor.fetchall() if row[0] is not None and row[1]}

        if gallery_ids:
            placeholders = ",".join("?" for _ in gallery_ids)
            cursor.execute(
                f"""
                SELECT id, clean_title, raw_title, num_pages, language_ids, parody_ids, tag_ids, status, favourite, rating
                FROM Galleries
                WHERE id IN ({placeholders})
                """,
                tuple(sorted(gallery_ids)),
            )
            for row in cursor.fetchall():
                gallery_id = int(row[0])
                language_ids = _parse_json_int_list(row[4])
                parody_ids = _parse_json_int_list(row[5])
                tag_ids = _parse_json_int_list(row[6])
                gallery_meta[gallery_id] = {
                    "gallery_id": gallery_id,
                    "clean_title": str(row[1] or ""),
                    "raw_title": str(row[2] or ""),
                    "num_pages": int(row[3]) if row[3] is not None else None,
                    "languages": [language_name_map[language_id] for language_id in language_ids if language_id in language_name_map],
                    "parodies": [parody_name_map[parody_id] for parody_id in parody_ids if parody_id in parody_name_map],
                    "tags": [tag_name_map[tag_id] for tag_id in tag_ids if tag_id in tag_name_map],
                    "status": str(row[7] or ""),
                    "favourite": bool(row[8]),
                    "rating": float(row[9]) if row[9] is not None else None,
                }

        gallery_lookup = _build_gallery_lookup_by_creator_and_title(cursor, language_name_map, parody_name_map, tag_name_map)

    for row in valid_rows:
        creator_name = row["creator_name"]
        gallery_name = row["gallery_name"]
        gallery_display_name = _display_gallery_name(gallery_name)
        gallery_id = row.get("gallery_id")
        meta = gallery_meta.get(int(gallery_id)) if gallery_id is not None else {}
        if not meta:
            meta = gallery_lookup.get((str(creator_name or "").strip().lower(), str(gallery_name or "").strip().lower()), {})
            if not meta:
                meta = gallery_lookup.get((str(creator_name or "").strip().lower(), str(gallery_display_name or "").strip().lower()), {})
            if meta and gallery_id is None:
                gallery_id = meta.get("gallery_id")
        page_count = meta.get("num_pages") if meta else None
        if page_count is None:
            page_count = _count_pages_on_disk(row.get("download_path", ""))

        gallery_item = {
            "label": gallery_display_name,
            "name": gallery_display_name,
            "path_name": gallery_name,
            "gallery_id": gallery_id,
            "page_count": page_count,
            "languages": list(meta.get("languages") or []),
            "parodies": list(meta.get("parodies") or []),
            "tags": list(meta.get("tags") or []),
            "tag_count": len(meta.get("tags") or []),
            "status": str(meta.get("status") or ""),
            "favourite": bool(meta.get("favourite")),
            "rating": meta.get("rating"),
        }

        galleries_by_creator.setdefault(creator_name, []).append(gallery_item)

        creator_key = str(creator_name or "").strip().lower()
        creator_item = creators.setdefault(
            creator_name,
            {
                "label": creator_name,
                "name": creator_name,
                "favourite": bool(creator_favourite_map.get(creator_key, False)),
                "gallery_count": 0,
                "languages": set(),
                "parodies": set(),
                "tags": set(),
                "tag_count": 0,
                "min_page_count": None,
                "max_page_count": None,
            },
        )
        # A gallery being favourited should not automatically mark its creator as favourited.
        # Preserve only the creator-level favourite flag (from DB or explicit creator actions).
        creator_item["favourite"] = bool(creator_item.get("favourite"))
        creator_item["gallery_count"] += 1
        creator_item["languages"].update(gallery_item["languages"])
        creator_item["parodies"].update(gallery_item["parodies"])
        creator_item["tags"].update(gallery_item["tags"])
        creator_item["tag_count"] = len(creator_item["tags"])
        if isinstance(page_count, int):
            if creator_item["min_page_count"] is None or page_count < creator_item["min_page_count"]:
                creator_item["min_page_count"] = page_count
            if creator_item["max_page_count"] is None or page_count > creator_item["max_page_count"]:
                creator_item["max_page_count"] = page_count

    filesystem_creators = _scan_creators_from_filesystem(root_path)
    for creator_name in filesystem_creators:
        creator_key = str(creator_name or "").strip().lower()
        creators.setdefault(
            creator_name,
            {
                "label": creator_name,
                "name": creator_name,
                "favourite": bool(creator_favourite_map.get(creator_key, False)),
                "gallery_count": 0,
                "languages": set(),
                "parodies": set(),
                "tags": set(),
                "tag_count": 0,
                "min_page_count": None,
                "max_page_count": None,
            },
        )
        existing_names = {item.get("name") for item in galleries_by_creator.get(creator_name, [])}
        for gallery_name in _scan_galleries_from_filesystem(root_path, creator_name):
            gallery_display_name = _display_gallery_name(gallery_name)
            if gallery_display_name in existing_names:
                continue
            gallery_path = _safe_path(root_path, creator_name, gallery_name)
            meta = gallery_lookup.get((str(creator_name or "").strip().lower(), str(gallery_name or "").strip().lower()), {})
            if not meta:
                meta = gallery_lookup.get((str(creator_name or "").strip().lower(), str(gallery_display_name or "").strip().lower()), {})
            gallery_id = meta.get("gallery_id") or _gallery_id_from_name(gallery_name)
            page_count = meta.get("num_pages") if meta else None
            if page_count is None:
                page_count = _count_pages_on_disk(gallery_path or "")
            gallery_item = {
                "label": gallery_display_name,
                "name": gallery_display_name,
                "path_name": gallery_name,
                "gallery_id": gallery_id,
                "page_count": page_count,
                "languages": list(meta.get("languages") or []),
                "parodies": list(meta.get("parodies") or []),
                "tags": list(meta.get("tags") or []),
                "tag_count": len(meta.get("tags") or []),
                "status": str(meta.get("status") or ""),
                "favourite": bool(meta.get("favourite")),
                "rating": meta.get("rating"),
            }
            galleries_by_creator.setdefault(creator_name, []).append(gallery_item)
            creators[creator_name]["gallery_count"] += 1
            if isinstance(page_count, int):
                current_min = creators[creator_name]["min_page_count"]
                current_max = creators[creator_name]["max_page_count"]
                if current_min is None or page_count < current_min:
                    creators[creator_name]["min_page_count"] = page_count
                if current_max is None or page_count > current_max:
                    creators[creator_name]["max_page_count"] = page_count

    for creator_name, creator_item in creators.items():
        creator_item["languages"] = sorted(creator_item["languages"], key=str.lower)
        creator_item["parodies"] = sorted(creator_item["parodies"], key=str.lower)
        creator_item["tags"] = sorted(creator_item["tags"], key=str.lower)
        creator_item["tag_count"] = len(creator_item["tags"])
        creator_item["min_page_count"] = creator_item["min_page_count"] or 0
        creator_item["max_page_count"] = creator_item["max_page_count"] or 0

    for creator_name, gallery_items in galleries_by_creator.items():
        gallery_items.sort(
            key=lambda item: (
                -(int(item["gallery_id"]) if item.get("gallery_id") is not None else -1),
                str(item.get("name") or "").lower(),
            )
        )

    return creators, galleries_by_creator


# ── DB routes — /api/db/... ───────────────────────────────────────────────────

@db_bp.route("/list", methods=["GET"])
def list_all():
    status = request.args.get("status")
    galleries = scraperapi.DB.Gallery.list_as_dicts(status=status or None)
    return jsonify({"galleries": galleries})


@db_bp.route("/get/<int:gallery_id>", methods=["GET"])
def get_gallery(gallery_id):
    status = scraperapi.Get.gallery_status(gallery_id)
    return jsonify({"gallery_id": gallery_id, "status": status})


@db_bp.route("/tables", methods=["GET"])
def list_tables():
    """Return all table names in the SQLite database."""
    tables = scraperapi.DB.list_table_names()
    return jsonify({"tables": tables})


@db_bp.route("/table/<table_name>", methods=["GET"])
def query_table(table_name):
    """
    Return paginated rows for any named table.
    Query params: search, limit (default 500), offset (default 0), sort_by, sort_dir.
    """
    search = request.args.get("search", "").strip()
    limit = min(int(request.args.get("limit", 500)), 2000)
    offset = int(request.args.get("offset", 0))
    sort_by = request.args.get("sort_by", "").strip() or None
    sort_dir = request.args.get("sort_dir", "asc").strip().lower()
    result = scraperapi.DB.query_table(
        table_name,
        search=search or None,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    if result.get("error"):
        return jsonify(result), 404
    return jsonify(result)


# ── Gallery routes — /api/gallery/... ────────────────────────────────────────

@gallery_bp.route("/list_locations", methods=["GET"])
def list_locations():
    roots = _file_browser_roots()
    requested_root = str(request.args.get("root") or "").strip()
    selected_root = _resolve_file_browser_root(requested_root)
    return jsonify({"locations": roots, "selected_root": selected_root})


@gallery_bp.route("/files", methods=["GET"])
def list_files():
    requested_root = str(request.args.get("root") or "").strip()
    root = _resolve_file_browser_root(requested_root)
    if not root or not os.path.isdir(root):
        return jsonify({"error": "Root path not found.", "root_path": root or "", "current_path": "", "entries": []}), 404

    raw_path = str(request.args.get("path") or "").strip().replace("\\", "/")
    path_parts = [part for part in raw_path.split("/") if part and part != "."]
    current_abs = _safe_path(root, *path_parts)
    if not current_abs or not os.path.isdir(current_abs):
        return jsonify({"error": "Path not found.", "root_path": root, "current_path": "/".join(path_parts), "entries": []}), 404

    entries = []
    try:
        names = sorted(os.listdir(current_abs), key=str.lower)
    except OSError as exc:
        return jsonify({"error": f"Failed to read directory: {exc}", "root_path": root, "current_path": "/".join(path_parts), "entries": []}), 500

    for name in names:
        if not name:
            continue
        entry_abs = _safe_path(current_abs, name)
        if not entry_abs:
            continue

        is_dir = os.path.isdir(entry_abs)
        is_file = os.path.isfile(entry_abs)
        if not is_dir and not is_file:
            continue

        size = None
        modified_at = None
        if is_file:
            try:
                size = int(os.path.getsize(entry_abs))
            except OSError:
                size = None
        try:
            modified_at = float(os.path.getmtime(entry_abs))
        except OSError:
            modified_at = None

        entries.append(
            {
                "name": name,
                "is_dir": is_dir,
                "is_file": is_file,
                "size": size,
                "modified_at": modified_at,
            }
        )

    current_path = "/".join(path_parts)
    parent_path = "/".join(path_parts[:-1]) if path_parts else ""
    return jsonify(
        {
            "root_path": root,
            "current_path": current_path,
            "parent_path": parent_path,
            "entries": entries,
        }
    )


@gallery_bp.route("/files/rename", methods=["POST"])
def rename_file():
    payload = request.get_json(silent=True) or {}
    requested_root = str(payload.get("root") or "").strip()
    rel_path = str(payload.get("path") or "").strip().replace("\\", "/")
    new_name = str(payload.get("new_name") or "").strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        return jsonify({"error": "Invalid name."}), 400
    root = _resolve_file_browser_root(requested_root)
    if not root or not os.path.isdir(root):
        return jsonify({"error": "Invalid root."}), 400
    parts = [p for p in rel_path.split("/") if p and p != "."]
    target = _safe_path(root, *parts)
    if not target or not os.path.exists(target):
        return jsonify({"error": "Path not found."}), 404
    dest = os.path.join(os.path.dirname(target), new_name)
    if os.path.exists(dest):
        return jsonify({"error": "A file or folder with that name already exists."}), 409
    try:
        os.rename(target, dest)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@gallery_bp.route("/files/download", methods=["GET"])
def download_file_entry():
    requested_root = str(request.args.get("root") or "").strip()
    rel_path = str(request.args.get("path") or "").strip().replace("\\", "/")
    inline = str(request.args.get("inline") or "").strip().lower() in {"1", "true", "yes"}

    if not rel_path or rel_path in {".", "/"}:
        return jsonify({"error": "Invalid file path."}), 400

    root = _resolve_file_browser_root(requested_root)
    if not root or not os.path.isdir(root):
        return jsonify({"error": "Invalid root."}), 400

    parts = [p for p in rel_path.split("/") if p and p != "."]
    target = _safe_path(root, *parts)
    if not target or not os.path.isfile(target):
        return jsonify({"error": "File not found."}), 404

    guessed_type, _ = mimetypes.guess_type(target)
    return send_file(
        target,
        mimetype=guessed_type or "application/octet-stream",
        as_attachment=not inline,
        download_name=os.path.basename(target),
    )


@gallery_bp.route("/files/read", methods=["GET"])
def read_file_entry():
    requested_root = str(request.args.get("root") or "").strip()
    rel_path = str(request.args.get("path") or "").strip().replace("\\", "/")
    max_bytes = int(request.args.get("max_bytes", 200000) or 200000)
    max_bytes = max(1024, min(max_bytes, 2_000_000))

    if not rel_path or rel_path in {".", "/"}:
        return jsonify({"error": "Invalid file path."}), 400

    root = _resolve_file_browser_root(requested_root)
    if not root or not os.path.isdir(root):
        return jsonify({"error": "Invalid root."}), 400

    parts = [p for p in rel_path.split("/") if p and p != "."]
    target = _safe_path(root, *parts)
    if not target or not os.path.isfile(target):
        return jsonify({"error": "File not found."}), 404

    guessed_type, _ = mimetypes.guess_type(target)
    text_like = bool(guessed_type and (
        guessed_type.startswith("text/")
        or guessed_type in {"application/json", "application/xml", "application/javascript", "application/x-javascript"}
    ))

    try:
        with open(target, "rb") as f:
            payload = f.read(max_bytes + 1)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    truncated = len(payload) > max_bytes
    if truncated:
        payload = payload[:max_bytes]

    # If not recognised as text MIME, do a simple binary heuristic.
    if not text_like and b"\x00" in payload:
        return jsonify({"error": "Binary file cannot be previewed as text."}), 400

    content = payload.decode("utf-8", errors="replace")
    return jsonify(
        {
            "path": rel_path,
            "content": content,
            "truncated": truncated,
            "max_bytes": max_bytes,
            "mime_type": guessed_type or "application/octet-stream",
        }
    )


@gallery_bp.route("/files/upload", methods=["POST"])
def upload_file_entry():
    requested_root = str(request.form.get("root") or "").strip()
    rel_path = str(request.form.get("path") or "").strip().replace("\\", "/")
    upload = request.files.get("file")

    if upload is None:
        return jsonify({"error": "No file uploaded."}), 400

    file_name = os.path.basename(str(upload.filename or "").strip())
    if not file_name or file_name in {".", ".."}:
        return jsonify({"error": "Invalid filename."}), 400
    if "/" in file_name or "\\" in file_name:
        return jsonify({"error": "Invalid filename."}), 400

    root = _resolve_file_browser_root(requested_root)
    if not root or not os.path.isdir(root):
        return jsonify({"error": "Invalid root."}), 400

    parts = [p for p in rel_path.split("/") if p and p != "."]
    parent = _safe_path(root, *parts) if parts else root
    if not parent or not os.path.isdir(parent):
        return jsonify({"error": "Destination folder not found."}), 404

    target = _safe_path(parent, file_name)
    if not target:
        return jsonify({"error": "Invalid destination."}), 400
    if os.path.exists(target):
        return jsonify({"error": "A file with that name already exists."}), 409

    try:
        upload.save(target)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "name": file_name})


@gallery_bp.route("/files/delete", methods=["POST"])
def delete_file():
    import shutil
    payload = request.get_json(silent=True) or {}
    requested_root = str(payload.get("root") or "").strip()
    rel_path = str(payload.get("path") or "").strip().replace("\\", "/")
    if not rel_path or rel_path in (".", "/"):
        return jsonify({"error": "Cannot delete root."}), 400
    root = _resolve_file_browser_root(requested_root)
    if not root or not os.path.isdir(root):
        return jsonify({"error": "Invalid root."}), 400
    parts = [p for p in rel_path.split("/") if p and p != "."]
    if not parts:
        return jsonify({"error": "Cannot delete root."}), 400
    target = _safe_path(root, *parts)
    if not target or not os.path.exists(target):
        return jsonify({"error": "Path not found."}), 404
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    removed_gallery_ids = _gallery_ids_for_deleted_path(root, parts)
    removed_from_db = 0
    for gallery_id in removed_gallery_ids:
        result = scraperapi.DB.remove_gallery_from_database(gallery_id)
        if result.get("removed"):
            removed_from_db += 1

    return jsonify({"ok": True})


# ── Collections routes — /api/collections/... ───────────────────────────────

@collections_bp.route("/list", methods=["GET"])
def collections_list():
    return jsonify({"collections": scraperapi.DB.Collection.list()})


@collections_bp.route("/<int:collection_id>", methods=["GET"])
def collections_get(collection_id):
    item = scraperapi.DB.Collection.get(collection_id)
    if not item:
        return jsonify({"error": "Collection not found."}), 404
    return jsonify(item)


@collections_bp.route("/create", methods=["POST"])
def collections_create():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Collection name is required."}), 400
    description = str(payload.get("description") or "")
    collection_type = str(payload.get("collection_type") or "normal")
    sort_mode = str(payload.get("sort_mode") or "id_desc")
    try:
        item = scraperapi.DB.Collection.create(name=name, description=description, collection_type=collection_type, sort_mode=sort_mode)
        return jsonify(item)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@collections_bp.route("/<int:collection_id>/update", methods=["POST"])
def collections_update(collection_id):
    payload = request.get_json(silent=True) or {}
    try:
        item = scraperapi.DB.Collection.update(
            collection_id=collection_id,
            name=payload.get("name"),
            description=payload.get("description"),
            sort_mode=payload.get("sort_mode"),
        )
        if not item:
            return jsonify({"error": "Collection not found."}), 404
        return jsonify(item)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@collections_bp.route("/<int:collection_id>/delete", methods=["POST"])
def collections_delete(collection_id):
    deleted = scraperapi.DB.Collection.delete(collection_id)
    if not deleted:
        return jsonify({"error": "Collection not found."}), 404
    return jsonify({"ok": True, "id": int(collection_id)})


@collections_bp.route("/<int:collection_id>/filters", methods=["POST"])
def collections_set_filters(collection_id):
    payload = request.get_json(silent=True) or {}
    expressions = payload.get("expressions") if isinstance(payload.get("expressions"), list) else []
    try:
        item = scraperapi.DB.Collection.set_filters(collection_id, expressions)
        if not item:
            return jsonify({"error": "Collection not found."}), 404
        return jsonify(item)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@collections_bp.route("/<int:collection_id>/rule", methods=["POST"])
def collections_set_rule(collection_id):
    payload = request.get_json(silent=True) or {}
    expression = str(payload.get("smart_expression") or "").strip()
    confirm_rebuild = bool(payload.get("confirm_rebuild", False))

    existing_items = scraperapi.DB.Collection.items(collection_id)
    if existing_items and not confirm_rebuild:
        return jsonify(
            {
                "error": "Updating the smart rule will clear all current collection items before rebuilding.",
                "requires_confirmation": True,
            }
        ), 409

    try:
        item = scraperapi.DB.Collection.set_smart_rule(collection_id, expression, clear_existing_items=True)
        if not item:
            return jsonify({"error": "Collection not found."}), 404
        return jsonify(item)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@collections_bp.route("/<int:collection_id>/items", methods=["GET"])
def collections_items(collection_id):
    collection = scraperapi.DB.Collection.get(collection_id)
    if not collection:
        return jsonify({"error": "Collection not found."}), 404
    items = scraperapi.DB.Collection.items(collection_id)
    return jsonify({"collection": collection, "items": items})


@collections_bp.route("/<int:collection_id>/add_galleries", methods=["POST"])
def collections_add_galleries(collection_id):
    payload = request.get_json(silent=True) or {}
    ids = payload.get("gallery_ids") if isinstance(payload.get("gallery_ids"), list) else []
    result = scraperapi.DB.Collection.add_galleries(collection_id, ids, manual=True)
    return jsonify(result)


@collections_bp.route("/<int:collection_id>/add_creator", methods=["POST"])
def collections_add_creator(collection_id):
    payload = request.get_json(silent=True) or {}
    creator = str(payload.get("creator") or "").strip()
    if not creator:
        return jsonify({"error": "creator is required."}), 400
    result = scraperapi.DB.Collection.add_creator_snapshot(collection_id, creator)
    return jsonify(result)


@collections_bp.route("/<int:collection_id>/remove_gallery", methods=["POST"])
def collections_remove_gallery(collection_id):
    payload = request.get_json(silent=True) or {}
    gallery_id = payload.get("gallery_id")
    removed = scraperapi.DB.Collection.remove_gallery(collection_id, gallery_id)
    if not removed:
        return jsonify({"error": "Gallery not found in collection."}), 404
    return jsonify({"ok": True, "gallery_id": gallery_id})


@collections_bp.route("/<int:collection_id>/clear_items", methods=["POST"])
def collections_clear_items(collection_id):
    changed = scraperapi.DB.Collection.clear_items(collection_id)
    return jsonify({"ok": True, "cleared": bool(changed)})


@collections_bp.route("/<int:collection_id>/refresh", methods=["POST"])
def collections_refresh(collection_id):
    try:
        result = scraperapi.DB.Collection.refresh_smart(collection_id)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@collections_bp.route("/refresh_smart_all", methods=["POST"])
def collections_refresh_smart_all():
    result = scraperapi.DB.Collection.refresh_all_smart()
    return jsonify(result)


@gallery_bp.route("/files/mkdir", methods=["POST"])
def make_directory():
    payload = request.get_json(silent=True) or {}
    requested_root = str(payload.get("root") or "").strip()
    rel_path = str(payload.get("path") or "").strip().replace("\\", "/")
    name = str(payload.get("name") or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return jsonify({"error": "Invalid folder name."}), 400
    root = _resolve_file_browser_root(requested_root)
    if not root or not os.path.isdir(root):
        return jsonify({"error": "Invalid root."}), 400
    parts = [p for p in rel_path.split("/") if p and p != "."]
    parent = _safe_path(root, *parts) if parts else root
    if not parent or not os.path.isdir(parent):
        return jsonify({"error": "Parent path not found."}), 404
    new_dir = os.path.join(parent, name)
    if os.path.exists(new_dir):
        return jsonify({"error": "Already exists."}), 409
    try:
        os.makedirs(new_dir)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@gallery_bp.route("/favourite/gallery/<int:gallery_id>", methods=["POST"])
def favourite_gallery(gallery_id):
    payload = request.get_json(silent=True) or {}
    set_value = payload.get("favourite") if isinstance(payload, dict) else None
    if set_value is None:
        favourite_value = scraperapi.DB.Gallery.favourite(gallery_id)
    else:
        favourite_value = scraperapi.DB.Gallery.favourite(gallery_id, bool(set_value))
    return jsonify({"gallery_id": int(gallery_id), "favourite": int(favourite_value)})


@gallery_bp.route("/favourite/creator", methods=["POST"])
def favourite_creator():
    payload = request.get_json(silent=True) or {}
    creator_name = str(payload.get("creator") or "").strip()
    if not creator_name:
        return jsonify({"error": "creator is required."}), 400
    set_value = payload.get("favourite") if isinstance(payload, dict) else None
    if set_value is None:
        favourite_value = scraperapi.DB.Creator.favourite(creator_name)
    else:
        # Set creator favourite flag
        favourite_value = scraperapi.DB.Creator.favourite(creator_name, bool(set_value))

        # Propagate the creator favourite state to all known galleries for that creator.
        try:
            target_val = bool(set_value)
            gallery_ids_to_update: set[int] = set()

            # 1) Find matching creator IDs in Creators table (match name or display_name)
            scraperapi.DB.init_db()
            with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name, display_name FROM Creators")
                creator_lower = creator_name.lower()
                matched_creator_ids = {int(row[0]) for row in cursor.fetchall() if (str(row[1] or "").strip().lower() == creator_lower) or (str(row[2] or "").strip().lower() == creator_lower)}

                # 2) From Galleries.creator_ids JSON, collect galleries referencing these creator ids
                if matched_creator_ids:
                    cursor.execute("SELECT id, creator_ids FROM Galleries")
                    for gid, creator_ids_json in cursor.fetchall():
                        try:
                            gids = _parse_json_int_list(creator_ids_json)
                        except Exception:
                            gids = []
                        if any(int(cid) in matched_creator_ids for cid in gids):
                            try:
                                gallery_ids_to_update.add(int(gid))
                            except Exception:
                                continue

            # 3) Additionally, scan GalleryLocations to find any gallery_ids mapped from filesystem locations for this creator
            for row in scraperapi.DB.list_gallery_locations():
                gid = row.get("gallery_id")
                dpath = row.get("download_path")
                if gid is None or not dpath:
                    continue
                found_creator, _ = _creator_and_gallery_from_location(row.get("root_path") or "", dpath)
                if str(found_creator or "").strip().lower() == creator_name.lower():
                    try:
                        gallery_ids_to_update.add(int(gid))
                    except Exception:
                        continue

            # 4) Apply favourite change to each gallery id found
            for gid in sorted(gallery_ids_to_update):
                try:
                    scraperapi.DB.Gallery.favourite(gid, target_val)
                except Exception:
                    # best-effort: ignore individual failures
                    continue
        except Exception:
            # If propagation fails, do not fail the creator update request; log silently
            pass
    return jsonify({"creator": creator_name, "favourite": int(favourite_value)})


@gallery_bp.route("/list_creators", methods=["GET"])
def list_creators():
    requested = _requested_root()
    creators: dict[str, dict] = {}

    if requested:
        base = _resolve_root_path()
        if not base:
            return jsonify({"creators": [], "filters": _build_filter_options([], "creator"), "root_path": ""})
        root_creators, _ = _load_gallery_browser_root_dataset(base)
        creators.update(root_creators)
        creator_items = sorted(creators.values(), key=lambda item: str(item.get("name") or "").lower())
        creator_items = _apply_creator_db_meta(creator_items)
        return jsonify({"creators": creator_items, "filters": _table_filter_options(), "root_path": base})

    for item in _available_roots():
        base = item.get("root_path")
        if not base:
            continue
        root_creators, _ = _load_gallery_browser_root_dataset(base)
        for creator_name, creator_item in root_creators.items():
            existing = creators.get(creator_name)
            if not existing:
                creators[creator_name] = dict(creator_item)
                creators[creator_name]["languages"] = list(creator_item.get("languages") or [])
                creators[creator_name]["parodies"] = list(creator_item.get("parodies") or [])
                creators[creator_name]["tags"] = list(creator_item.get("tags") or [])
                continue

            existing["gallery_count"] = int(existing.get("gallery_count") or 0) + int(creator_item.get("gallery_count") or 0)
            existing_languages = set(existing.get("languages") or [])
            existing_languages.update(creator_item.get("languages") or [])
            existing["languages"] = sorted(existing_languages, key=str.lower)
            existing_parodies = set(existing.get("parodies") or [])
            existing_parodies.update(creator_item.get("parodies") or [])
            existing["parodies"] = sorted(existing_parodies, key=str.lower)
            existing_tags = set(existing.get("tags") or [])
            existing_tags.update(creator_item.get("tags") or [])
            existing["tags"] = sorted(existing_tags, key=str.lower)
            existing["tag_count"] = len(existing["tags"])
            existing["favourite"] = bool(existing.get("favourite")) or bool(creator_item.get("favourite"))

            min_pages = int(existing.get("min_page_count") or 0)
            other_min = int(creator_item.get("min_page_count") or 0)
            existing["min_page_count"] = min(value for value in [min_pages, other_min] if value > 0) if any(value > 0 for value in [min_pages, other_min]) else 0
            existing["max_page_count"] = max(int(existing.get("max_page_count") or 0), int(creator_item.get("max_page_count") or 0))

    creator_items = sorted(creators.values(), key=lambda item: str(item.get("name") or "").lower())
    creator_items = _apply_creator_db_meta(creator_items)
    return jsonify({"creators": creator_items, "filters": _table_filter_options(), "root_path": ""})


@gallery_bp.route("/list_galleries/<path:creator>", methods=["GET"])
def list_galleries(creator):
    requested = _requested_root()
    galleries: list[dict] = []

    if requested:
        base = _resolve_root_path()
        if not base:
            abort(404)
        _, galleries_by_creator = _load_gallery_browser_root_dataset(base)
        galleries = galleries_by_creator.get(creator, [])
        galleries = _apply_gallery_db_meta(galleries)
        if not galleries:
            abort(404)
        filters = _table_filter_options()
        return jsonify({"creator": creator, "galleries": galleries, "filters": filters, "root_path": base})

    for item in _available_roots():
        base = item.get("root_path")
        if not base:
            continue
        _, galleries_by_creator = _load_gallery_browser_root_dataset(base)
        galleries.extend(galleries_by_creator.get(creator, []))

    if not galleries:
        abort(404)

    deduped = {}
    for item in galleries:
        key = str(item.get("name") or "")
        deduped[key] = _prefer_gallery_item(deduped.get(key), item)
    gallery_items = sorted(deduped.values(), key=lambda item: (-(int(item["gallery_id"]) if item.get("gallery_id") is not None else -1), str(item.get("name") or "").lower()))
    gallery_items = _apply_gallery_db_meta(gallery_items)
    filters = _table_filter_options()
    return jsonify({"creator": creator, "galleries": gallery_items, "filters": filters, "root_path": ""})


@gallery_bp.route("/list_pages/<path:creator>/<path:gallery>", methods=["GET"])
def list_pages(creator, gallery):
    """Return sorted list of image filenames for a local gallery folder."""
    base, gallery_path = _resolve_gallery_path(creator, gallery)
    if not gallery_path:
        abort(404)

    if os.path.isdir(gallery_path):
        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
        pages = sorted(
            name for name in os.listdir(gallery_path)
            if os.path.splitext(name)[1].lower() in IMAGE_EXTS
        )
        return jsonify({"creator": creator, "gallery": gallery, "pages": pages, "mode": "directory", "root_path": base})

    if _is_archive(gallery_path):
        pages = _archive_pages(gallery_path)
        return jsonify({"creator": creator, "gallery": gallery, "pages": pages, "mode": "archive", "root_path": base})

    abort(404)


@gallery_bp.route("/list_pages_by_id/<int:gallery_id>", methods=["GET"])
def list_pages_by_id(gallery_id):
    """Return sorted list of image filenames for a gallery identified by ID."""
    locations = scraperapi.DB.list_gallery_locations(gallery_id=int(gallery_id))
    if not locations:
        abort(404)

    # Try to include a friendly gallery title if available
    try:
        meta = _gallery_meta_by_id(int(gallery_id)) or {}
        friendly_title = str(meta.get("title") or "")
    except Exception:
        friendly_title = ""

    requested_root = _requested_root()
    for row in locations:
        root = str(row.get("root_path") or "")
        if requested_root and root != requested_root:
            continue

        gallery_path = str(row.get("download_path") or "")
        if not gallery_path:
            continue

        resolved_path = _tolerant_gallery_path(gallery_id, gallery_path) or gallery_path

        if os.path.isdir(resolved_path):
            IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
            pages = sorted(
                name for name in os.listdir(resolved_path)
                if os.path.splitext(name)[1].lower() in IMAGE_EXTS
            )
            return jsonify({"gallery_id": int(gallery_id), "pages": pages, "mode": "directory", "root_path": root, "title": friendly_title})

        if _is_archive(resolved_path):
            pages = _archive_pages(resolved_path)
            return jsonify({"gallery_id": int(gallery_id), "pages": pages, "mode": "archive", "root_path": root, "title": friendly_title})

    # Fallback: consult Galleries.download_path if GalleryLocations didn't resolve
    try:
        tbl = _gallery_paths_from_galleries_table(gallery_id)
        gp = str(tbl.get("download_path") or "").strip()
        if gp:
            resolved_gp = _tolerant_gallery_path(gallery_id, gp) or gp
            if os.path.isdir(resolved_gp):
                IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
                pages = sorted(
                    name for name in os.listdir(resolved_gp)
                    if os.path.splitext(name)[1].lower() in IMAGE_EXTS
                )
                return jsonify({"gallery_id": int(gallery_id), "pages": pages, "mode": "directory", "root_path": "", "title": friendly_title})
            if _is_archive(resolved_gp):
                pages = _archive_pages(resolved_gp)
                return jsonify({"gallery_id": int(gallery_id), "pages": pages, "mode": "archive", "root_path": "", "title": friendly_title})
    except Exception:
        pass

    abort(404)


@gallery_bp.route("/details/<path:creator>/<path:gallery>", methods=["GET"])
def gallery_details(creator, gallery):
    root, gallery_path = _resolve_gallery_path(creator, gallery)
    if not root or not gallery_path:
        abort(404)

    gallery_id = _gallery_id_from_location(root, creator, gallery)
    if gallery_id is not None:
        meta = _gallery_meta_by_id(gallery_id)
    else:
        meta = {
            "gallery_id": None,
            "title": gallery,
            "page_count": _count_pages_on_disk(gallery_path) or 0,
            "languages": [],
            "parodies": [],
            "tags": [],
            "status": "",
            "favourite": False,
            "rating": None,
        }

    if not meta.get("title"):
        meta["title"] = gallery

    return jsonify({
        "creator": creator,
        "gallery": gallery,
        "root_path": root,
        **meta,
    })


@gallery_bp.route("/details_by_id/<int:gallery_id>", methods=["GET"])
def gallery_details_by_id(gallery_id):
    meta = _gallery_meta_by_id(gallery_id)
    if not meta:
        abort(404)
    return jsonify(meta)


@gallery_bp.route("/view/<path:creator>/<path:gallery>/<path:filename>", methods=["GET"])
def view_image(creator, gallery, filename):
    """Serve a local gallery image to the frontend reader."""
    base, gallery_path = _resolve_gallery_path(creator, gallery)
    if not gallery_path:
        abort(404)

    if os.path.isdir(gallery_path):
        # Validate filename stays inside the gallery folder
        file_path = _safe_path(gallery_path, filename)
        if not file_path or not os.path.isfile(file_path):
            abort(404)
        return send_from_directory(gallery_path, filename)

    if _is_archive(gallery_path):
        normalised = posixpath.normpath(filename)
        if normalised.startswith("../") or normalised.startswith("/"):
            abort(404)
        with zipfile.ZipFile(gallery_path, "r") as zf:
            try:
                payload = zf.read(normalised)
            except KeyError:
                abort(404)
        ext = os.path.splitext(normalised)[1].lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".avif": "image/avif",
        }
        return send_file(io.BytesIO(payload), mimetype=mime_map.get(ext, "application/octet-stream"), download_name=os.path.basename(normalised))

    abort(404)


@gallery_bp.route("/view_by_id/<int:gallery_id>/<path:filename>", methods=["GET"])
def view_image_by_id(gallery_id, filename):
    """Serve a gallery image by gallery ID, using GalleryLocations download path."""
    locations = scraperapi.DB.list_gallery_locations(gallery_id=int(gallery_id))
    if not locations:
        abort(404)

    requested_root = _requested_root()
    for row in locations:
        root = str(row.get("root_path") or "")
        if requested_root and root != requested_root:
            continue

        gallery_path = str(row.get("download_path") or "")
        if not gallery_path:
            continue

        resolved_path = _tolerant_gallery_path(gallery_id, gallery_path) or gallery_path
        if not os.path.exists(resolved_path):
            continue

        if os.path.isdir(resolved_path):
            file_path = _safe_path(resolved_path, filename)
            if not file_path or not os.path.isfile(file_path):
                continue
            rel_name = os.path.relpath(file_path, resolved_path)
            return send_from_directory(resolved_path, rel_name)

        if _is_archive(resolved_path):
            normalised = posixpath.normpath(filename)
            if normalised.startswith("../") or normalised.startswith("/"):
                continue
            with zipfile.ZipFile(resolved_path, "r") as zf:
                try:
                    payload = zf.read(normalised)
                except KeyError:
                    continue
            ext = os.path.splitext(normalised)[1].lower()
            mime_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".avif": "image/avif",
            }
            return send_file(io.BytesIO(payload), mimetype=mime_map.get(ext, "application/octet-stream"), download_name=os.path.basename(normalised))

    # Fallback: consult Galleries.download_path
    try:
        tbl = _gallery_paths_from_galleries_table(gallery_id)
        gp = str(tbl.get("download_path") or "").strip()
        if gp:
            resolved_gp = _tolerant_gallery_path(gallery_id, gp) or gp
            if os.path.isdir(resolved_gp):
                file_path = _safe_path(resolved_gp, filename)
                if file_path and os.path.isfile(file_path):
                    rel_name = os.path.relpath(file_path, resolved_gp)
                    return send_from_directory(resolved_gp, rel_name)
            if _is_archive(resolved_gp):
                normalised = posixpath.normpath(filename)
                if not normalised.startswith("../") and not normalised.startswith("/"):
                    with zipfile.ZipFile(resolved_gp, "r") as zf:
                        try:
                            payload = zf.read(normalised)
                        except KeyError:
                            payload = None
                    if payload is not None:
                        ext = os.path.splitext(normalised)[1].lower()
                        mime_map = {
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".png": "image/png",
                            ".gif": "image/gif",
                            ".webp": "image/webp",
                            ".avif": "image/avif",
                        }
                        return send_file(io.BytesIO(payload), mimetype=mime_map.get(ext, "application/octet-stream"), download_name=os.path.basename(normalised))
    except Exception:
        pass

    abort(404)


# ── Cover image endpoints ──────────────────────────────────────────────────────

@gallery_bp.route("/cover/<path:creator>", methods=["GET"])
def get_creator_cover(creator):
    """
    Serve the cover image for a creator.
    Looks for cover.* (jpg, png, gif, webp) in the creator folder.
    """
    roots = _available_roots()
    requested = _requested_root()
    if requested:
        roots = [item for item in roots if item.get("root_path") == requested]
    
    for item in roots:
        root = item.get("root_path")
        if not root:
            continue
        creator_path = _safe_path(root, creator)
        if not creator_path or not os.path.isdir(creator_path):
            continue
        
        # Look for cover.* file
        for ext in ("jpg", "jpeg", "png", "gif", "webp"):
            cover_file = os.path.join(creator_path, f"cover.{ext}")
            if os.path.isfile(cover_file):
                return send_from_directory(creator_path, f"cover.{ext}")
    
    abort(404)


@gallery_bp.route("/cover/<path:creator>/<path:gallery>", methods=["GET"])
def get_gallery_cover(creator, gallery):
    """
    Serve the cover image for a gallery.
    Looks in .covers/ folder for (GalleryTitle).* file, or uses first page of gallery.
    """
    base, gallery_path = _resolve_gallery_path(creator, gallery)
    if not gallery_path:
        abort(404)
    
    creator_path = _safe_path(base, creator) if base else None
    if not creator_path:
        abort(404)
    
    # Try .covers folder first
    covers_folder = os.path.join(creator_path, ".covers")
    if os.path.isdir(covers_folder):
        # Look for files matching the gallery name (without archive extension)
        gallery_base = os.path.splitext(gallery)[0] if gallery.endswith((".cbz", ".zip")) else gallery
        for ext in ("jpg", "jpeg", "png", "gif", "webp"):
            cover_file = os.path.join(covers_folder, f"{gallery_base}.{ext}")
            if os.path.isfile(cover_file):
                return send_from_directory(covers_folder, f"{gallery_base}.{ext}")
    
    # Fallback: try to get first page from gallery
    if os.path.isdir(gallery_path):
        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
        try:
            pages = sorted(
                name for name in os.listdir(gallery_path)
                if os.path.splitext(name)[1].lower() in IMAGE_EXTS
            )
            if pages:
                return send_from_directory(gallery_path, pages[0])
        except OSError:
            pass
    
    elif _is_archive(gallery_path):
        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
        try:
            with zipfile.ZipFile(gallery_path, "r") as zf:
                pages = sorted(
                    name for name in zf.namelist()
                    if not name.endswith("/") and os.path.splitext(name)[1].lower() in IMAGE_EXTS
                )
                if pages:
                    payload = zf.read(pages[0])
                    ext = os.path.splitext(pages[0])[1].lower()
                    mime_map = {
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".png": "image/png",
                        ".gif": "image/gif",
                        ".webp": "image/webp",
                        ".avif": "image/avif",
                    }
                    return send_file(io.BytesIO(payload), mimetype=mime_map.get(ext, "application/octet-stream"))
        except Exception:
            pass
    
    abort(404)


@gallery_bp.route("/cover_by_id/<int:gallery_id>", methods=["GET"])
def get_gallery_cover_by_id(gallery_id):
    """Serve a gallery cover by gallery ID, with fallbacks to first page."""
    locations = scraperapi.DB.list_gallery_locations(gallery_id=int(gallery_id))
    if not locations:
        abort(404)

    requested_root = _requested_root()

    for row in locations:
        root_path = str(row.get("root_path") or "")
        if requested_root and root_path != requested_root:
            continue

        cover_base = str(row.get("cover_path") or "")
        if cover_base:
            cover_candidates = [cover_base]
            if not os.path.splitext(cover_base)[1]:
                cover_candidates.extend(f"{cover_base}.{ext}" for ext in ("jpg", "jpeg", "png", "gif", "webp", "avif"))
            for candidate in cover_candidates:
                if os.path.isfile(candidate):
                    return send_file(candidate)

        gallery_path = str(row.get("download_path") or "")
        if not gallery_path:
            continue

        if os.path.isdir(gallery_path):
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
            try:
                pages = sorted(
                    name for name in os.listdir(gallery_path)
                    if os.path.splitext(name)[1].lower() in image_exts
                )
                if pages:
                    return send_from_directory(gallery_path, pages[0])
            except OSError:
                continue
        elif _is_archive(gallery_path):
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
            try:
                with zipfile.ZipFile(gallery_path, "r") as zf:
                    pages = sorted(
                        name for name in zf.namelist()
                        if not name.endswith("/") and os.path.splitext(name)[1].lower() in image_exts
                    )
                    if pages:
                        payload = zf.read(pages[0])
                        ext = os.path.splitext(pages[0])[1].lower()
                        mime_map = {
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".png": "image/png",
                            ".gif": "image/gif",
                            ".webp": "image/webp",
                            ".avif": "image/avif",
                        }
                        return send_file(io.BytesIO(payload), mimetype=mime_map.get(ext, "application/octet-stream"))
            except Exception:
                continue

    abort(404)


# ── Gallery streaming — fetch pages from nhentai by ID ───────────────────────
#
# Streams are short-lived: images are downloaded to a temp directory and
# served from there.  The temp directory is cleaned up when the session ends
# (i.e. after the final page is marked as done by the client).

_stream_lock = threading.Lock()
_active_streams: dict[int, dict] = {}   # gallery_id → {tmpdir, meta, expires}

_STREAM_TTL = 60 * 30   # 30 minutes before auto-cleanup


def _stream_temp_root() -> str:
    """Use the scraper temp root for stream sessions (e.g. /tmp/manga-scraper/)."""
    root = getattr(orchestrator, "TEMP_DIR", "/tmp/manga-scraper") or "/tmp/manga-scraper"
    os.makedirs(root, exist_ok=True)
    return root


def _cleanup_expired_streams():
    now = time.time()
    with _stream_lock:
        expired = [gid for gid, s in _active_streams.items() if now > s["expires"]]
        for gid in expired:
            tmpdir = _active_streams.pop(gid, {}).get("tmpdir")
            if tmpdir and os.path.isdir(tmpdir):
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)


@gallery_bp.route("/stream/<int:gallery_id>", methods=["GET"])
def stream_info(gallery_id):
    """
    Fetch and cache metadata for a gallery.
    Returns page count and a list of page URLs routed through /stream/<id>/<page>.
    """
    _cleanup_expired_streams()

    meta = scraperapi.Fetch.gallery_metadata(gallery_id)
    if not meta:
        return jsonify({"error": f"Could not fetch metadata for gallery {gallery_id}."}), 404

    pages = len(meta.get("images", {}).get("pages", []))
    if pages == 0:
        return jsonify({"error": "Gallery has no pages."}), 404

    # Prepare temp dir for this stream session
    with _stream_lock:
        if gallery_id not in _active_streams:
            tmpdir = tempfile.mkdtemp(prefix=f"ms_stream_{gallery_id}_", dir=_stream_temp_root())
            _active_streams[gallery_id] = {
                "tmpdir": tmpdir,
                "meta": meta,
                "expires": time.time() + _STREAM_TTL,
            }
        else:
            # Refresh expiry on revisit
            _active_streams[gallery_id]["expires"] = time.time() + _STREAM_TTL

    clean_meta = scraperapi.Cache.Load.cached_metadata(clean=True).get(gallery_id) or {}
    title = clean_meta.get("title") or meta.get("title", {}).get("pretty") or f"Gallery {gallery_id}"

    page_urls = [f"/api/gallery/stream/{gallery_id}/{p}" for p in range(1, pages + 1)]
    return jsonify({
        "gallery_id": gallery_id,
        "title": title,
        "page_count": pages,
        "page_urls": page_urls,
        "temp_dir": _active_streams[gallery_id]["tmpdir"],
        "expires_in_seconds": _STREAM_TTL,
    })


@gallery_bp.route("/stream/<int:gallery_id>/<int:page>", methods=["GET"])
def stream_page(gallery_id, page):
    """
    Serve a single page from a streamed gallery.
    Downloads the image to a temp file the first time; serves from cache on subsequent requests.
    """
    _cleanup_expired_streams()

    with _stream_lock:
        stream = _active_streams.get(gallery_id)

    if not stream:
        # Re-init the stream if it expired or was never started
        meta = scraperapi.Fetch.gallery_metadata(gallery_id)
        if not meta:
            abort(404)
        tmpdir = tempfile.mkdtemp(prefix=f"ms_stream_{gallery_id}_", dir=_stream_temp_root())
        stream = {"tmpdir": tmpdir, "meta": meta, "expires": time.time() + _STREAM_TTL}
        with _stream_lock:
            _active_streams[gallery_id] = stream

    meta = stream["meta"]
    tmpdir = stream["tmpdir"]

    # Check cache first
    for ext in ("jpg", "png", "gif", "webp"):
        cached = os.path.join(tmpdir, f"{page}.{ext}")
        if os.path.isfile(cached):
            return send_file(cached)

    # Fetch the image URL(s) from the API layer
    urls = scraperapi.Fetch.image_urls(meta, page)
    if not urls:
        abort(404)

    # Try each mirror URL
    img_data = None
    content_type = "image/jpeg"
    for url in urls:
        try:
            resp = requests.get(url, timeout=30, stream=True)
            if resp.status_code == 200:
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                ext_used = content_type.split("/")[-1].split(";")[0].strip() or "jpg"
                img_data = resp.content
                break
        except Exception:
            continue

    if img_data is None:
        abort(502)

    # Save to temp dir
    dest = os.path.join(tmpdir, f"{page}.{ext_used}")
    with open(dest, "wb") as f:
        f.write(img_data)

    # Refresh expiry
    with _stream_lock:
        if gallery_id in _active_streams:
            _active_streams[gallery_id]["expires"] = time.time() + _STREAM_TTL

    return send_file(dest, mimetype=content_type)


@gallery_bp.route("/stream/<int:gallery_id>/close", methods=["POST"])
def stream_close(gallery_id):
    """Delete the temp files for a finished stream session."""
    with _stream_lock:
        stream = _active_streams.pop(gallery_id, None)
    if stream and os.path.isdir(stream["tmpdir"]):
        import shutil
        shutil.rmtree(stream["tmpdir"], ignore_errors=True)
    return jsonify({"message": f"Stream {gallery_id} closed."})