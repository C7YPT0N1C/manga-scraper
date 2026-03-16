#!/usr/bin/env python3
# mangascraper/dashboard/routes/data_routes.py

import os
import tempfile
import threading
import time
import io
import zipfile
import posixpath
import json

import requests
from flask import Blueprint, abort, jsonify, request, send_file, send_from_directory

from mangascraper.core import api as scraperapi
from mangascraper.core import orchestrator

# ── Blueprints ────────────────────────────────────────────────────────────────

db_bp = Blueprint("database", __name__)
gallery_bp = Blueprint("gallery", __name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_path(base: str, *parts: str) -> str | None:
    """
    Join parts onto base and return the real path only if it stays inside base.
    Returns None if the result would escape the base directory.
    """
    joined = os.path.realpath(os.path.join(base, *parts))
    base_real = os.path.realpath(base)
    if not joined.startswith(base_real + os.sep) and joined != base_real:
        return None
    return joined


def _download_path() -> str:
    orchestrator.refresh_globals()
    return orchestrator.download_path or orchestrator.DEFAULT_DOWNLOAD_PATH


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
    fallback = _download_path()
    if fallback:
        return [{"root_path": fallback, "extension_used": "", "count": 0}]
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
    return roots[0]["root_path"] if roots else _download_path()


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


def _build_filter_options(items: list[dict], item_type: str) -> dict:
    languages = set()
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


def _prefer_gallery_item(existing: dict | None, candidate: dict) -> dict:
    if not existing:
        return candidate

    existing_id = existing.get("gallery_id")
    candidate_id = candidate.get("gallery_id")
    if existing_id is None and candidate_id is not None:
        return candidate
    if existing_id is not None and candidate_id is None:
        return existing

    existing_score = len(existing.get("tags") or []) + len(existing.get("languages") or [])
    candidate_score = len(candidate.get("tags") or []) + len(candidate.get("languages") or [])
    if candidate_score > existing_score:
        return candidate

    if not existing.get("favourite") and candidate.get("favourite"):
        return candidate

    return existing


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
        valid_rows.append(row)
        if row.get("gallery_id") is not None:
            gallery_ids.add(int(row["gallery_id"]))

    gallery_meta = {}
    tag_name_map = {}
    language_name_map = {}
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

        cursor.execute("SELECT id, name FROM Tags")
        tag_name_map = {int(row[0]): str(row[1]) for row in cursor.fetchall() if row[0] is not None and row[1]}

        cursor.execute("SELECT id, name FROM Languages")
        language_name_map = {int(row[0]): str(row[1]) for row in cursor.fetchall() if row[0] is not None and row[1]}

        if gallery_ids:
            placeholders = ",".join("?" for _ in gallery_ids)
            cursor.execute(
                f"""
                SELECT id, clean_title, raw_title, num_pages, tag_ids, language_ids, status, favourite, rating
                FROM Galleries
                WHERE id IN ({placeholders})
                """,
                tuple(sorted(gallery_ids)),
            )
            for row in cursor.fetchall():
                gallery_id = int(row[0])
                tag_ids = _parse_json_int_list(row[4])
                language_ids = _parse_json_int_list(row[5])
                gallery_meta[gallery_id] = {
                    "clean_title": str(row[1] or ""),
                    "raw_title": str(row[2] or ""),
                    "num_pages": int(row[3]) if row[3] is not None else None,
                    "tags": [tag_name_map[tag_id] for tag_id in tag_ids if tag_id in tag_name_map],
                    "languages": [language_name_map[language_id] for language_id in language_ids if language_id in language_name_map],
                    "status": str(row[6] or ""),
                    "favourite": bool(row[7]),
                    "rating": float(row[8]) if row[8] is not None else None,
                }

    for row in valid_rows:
        creator_name = row["creator_name"]
        gallery_name = row["gallery_name"]
        gallery_id = row.get("gallery_id")
        meta = gallery_meta.get(int(gallery_id)) if gallery_id is not None else {}
        page_count = meta.get("num_pages") if meta else None
        if page_count is None:
            page_count = _count_pages_on_disk(row.get("download_path", ""))

        gallery_item = {
            "label": gallery_name,
            "name": gallery_name,
            "gallery_id": gallery_id,
            "page_count": page_count,
            "languages": list(meta.get("languages") or []),
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
                "tags": set(),
                "tag_count": 0,
                "min_page_count": None,
                "max_page_count": None,
            },
        )
        creator_item["favourite"] = bool(creator_item.get("favourite")) or bool(gallery_item.get("favourite"))
        creator_item["gallery_count"] += 1
        creator_item["languages"].update(gallery_item["languages"])
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
                "tags": set(),
                "tag_count": 0,
                "min_page_count": None,
                "max_page_count": None,
            },
        )
        existing_names = {item.get("name") for item in galleries_by_creator.get(creator_name, [])}
        for gallery_name in _scan_galleries_from_filesystem(root_path, creator_name):
            if gallery_name in existing_names:
                continue
            gallery_path = _safe_path(root_path, creator_name, gallery_name)
            page_count = _count_pages_on_disk(gallery_path or "")
            gallery_item = {
                "label": gallery_name,
                "name": gallery_name,
                "gallery_id": None,
                "page_count": page_count,
                "languages": [],
                "tags": [],
                "tag_count": 0,
                "status": "",
                "favourite": False,
                "rating": None,
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
    Query params: search, limit (default 500), offset (default 0).
    """
    search = request.args.get("search", "").strip()
    limit = min(int(request.args.get("limit", 500)), 2000)
    offset = int(request.args.get("offset", 0))
    result = scraperapi.DB.query_table(table_name, search=search or None, limit=limit, offset=offset)
    if result.get("error"):
        return jsonify(result), 404
    return jsonify(result)


# ── Gallery routes — /api/gallery/... ────────────────────────────────────────

@gallery_bp.route("/list_locations", methods=["GET"])
def list_locations():
    roots = _available_roots()
    return jsonify({"locations": roots, "selected_root": _resolve_root_path()})


@gallery_bp.route("/files", methods=["GET"])
def list_files():
    requested_root = str(request.args.get("root") or "").strip()
    roots = _available_roots()
    root = None
    if requested_root:
        for item in roots:
            if item["root_path"] == requested_root:
                root = requested_root
                break
    if not root:
        root = roots[0]["root_path"] if roots else _download_path()
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
        if not name or name.startswith("."):
            continue
        entry_abs = _safe_path(current_abs, name)
        if not entry_abs:
            continue

        is_dir = os.path.isdir(entry_abs)
        is_file = os.path.isfile(entry_abs)
        if not is_dir and not is_file:
            continue

        size = None
        if is_file:
            try:
                size = int(os.path.getsize(entry_abs))
            except OSError:
                size = None

        entries.append(
            {
                "name": name,
                "is_dir": is_dir,
                "is_file": is_file,
                "size": size,
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
    roots = _available_roots()
    root = None
    for item in roots:
        if item["root_path"] == requested_root:
            root = requested_root
            break
    if not root:
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


@gallery_bp.route("/files/delete", methods=["POST"])
def delete_file():
    import shutil
    payload = request.get_json(silent=True) or {}
    requested_root = str(payload.get("root") or "").strip()
    rel_path = str(payload.get("path") or "").strip().replace("\\", "/")
    if not rel_path or rel_path in (".", "/"):
        return jsonify({"error": "Cannot delete root."}), 400
    roots = _available_roots()
    root = None
    for item in roots:
        if item["root_path"] == requested_root:
            root = requested_root
            break
    if not root:
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
    return jsonify({"ok": True})


@gallery_bp.route("/files/mkdir", methods=["POST"])
def make_directory():
    payload = request.get_json(silent=True) or {}
    requested_root = str(payload.get("root") or "").strip()
    rel_path = str(payload.get("path") or "").strip().replace("\\", "/")
    name = str(payload.get("name") or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return jsonify({"error": "Invalid folder name."}), 400
    roots = _available_roots()
    root = None
    for item in roots:
        if item["root_path"] == requested_root:
            root = requested_root
            break
    if not root:
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
        favourite_value = scraperapi.DB.Creator.favourite(creator_name, bool(set_value))
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
        return jsonify({"creators": creator_items, "filters": _build_filter_options(creator_items, "creator"), "root_path": base})

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
                creators[creator_name]["tags"] = list(creator_item.get("tags") or [])
                continue

            existing["gallery_count"] = int(existing.get("gallery_count") or 0) + int(creator_item.get("gallery_count") or 0)
            existing_languages = set(existing.get("languages") or [])
            existing_languages.update(creator_item.get("languages") or [])
            existing["languages"] = sorted(existing_languages, key=str.lower)
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
    return jsonify({"creators": creator_items, "filters": _build_filter_options(creator_items, "creator"), "root_path": ""})


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
        if not galleries:
            abort(404)
        return jsonify({"creator": creator, "galleries": galleries, "filters": _build_filter_options(galleries, "gallery"), "root_path": base})

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
    return jsonify({"creator": creator, "galleries": gallery_items, "filters": _build_filter_options(gallery_items, "gallery"), "root_path": ""})


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
