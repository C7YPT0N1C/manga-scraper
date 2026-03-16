#!/usr/bin/env python3
# mangascraper/dashboard/routes/data_routes.py
#
# Merged replacement for database_routes.py + gallery_routes.py.
# Exposes two blueprints (db_bp, gallery_bp) so URL prefixes are unchanged.

import os
import tempfile
import threading
import time
import io
import zipfile
import posixpath

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


@gallery_bp.route("/list_creators", methods=["GET"])
def list_creators():
    requested = _requested_root()
    creators = set()

    if requested:
        base = _resolve_root_path()
        if not base:
            return jsonify({"creators": [], "root_path": ""})
        for row in scraperapi.DB.list_gallery_locations(root_path=base):
            download_path = row.get("download_path", "")
            # Ignore stale DB rows whose gallery path no longer exists on disk.
            if not (os.path.isdir(download_path) or _is_archive(download_path)):
                continue
            creator, _gallery = _creator_and_gallery_from_location(base, download_path)
            if creator:
                creators.add(creator)
        return jsonify({"creators": sorted(creators), "root_path": base})

    for item in _available_roots():
        base = item.get("root_path")
        if not base:
            continue
        for row in scraperapi.DB.list_gallery_locations(root_path=base):
            download_path = row.get("download_path", "")
            # Ignore stale DB rows whose gallery path no longer exists on disk.
            if not (os.path.isdir(download_path) or _is_archive(download_path)):
                continue
            creator, _gallery = _creator_and_gallery_from_location(base, download_path)
            if creator:
                creators.add(creator)
    return jsonify({"creators": sorted(creators), "root_path": ""})


@gallery_bp.route("/list_galleries/<path:creator>", methods=["GET"])
def list_galleries(creator):
    requested = _requested_root()
    galleries = set()

    if requested:
        base = _resolve_root_path()
        if not base:
            abort(404)
        for row in scraperapi.DB.list_gallery_locations(root_path=base):
            download_path = row.get("download_path", "")
            # Ignore stale DB rows whose gallery path no longer exists on disk.
            if not (os.path.isdir(download_path) or _is_archive(download_path)):
                continue
            creator_name, gallery = _creator_and_gallery_from_location(base, download_path)
            if creator_name == creator and gallery:
                galleries.add(gallery)
        if not galleries:
            abort(404)
        return jsonify({"creator": creator, "galleries": sorted(galleries), "root_path": base})

    for item in _available_roots():
        base = item.get("root_path")
        if not base:
            continue
        for row in scraperapi.DB.list_gallery_locations(root_path=base):
            download_path = row.get("download_path", "")
            # Ignore stale DB rows whose gallery path no longer exists on disk.
            if not (os.path.isdir(download_path) or _is_archive(download_path)):
                continue
            creator_name, gallery = _creator_and_gallery_from_location(base, download_path)
            if creator_name == creator and gallery:
                galleries.add(gallery)
    if not galleries:
        abort(404)
    return jsonify({"creator": creator, "galleries": sorted(galleries), "root_path": ""})


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
    ext_used = "jpg"
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
