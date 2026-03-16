#!/usr/bin/env python3
# mangascraper/dashboard/routes/scraper_routes.py

import shlex
import subprocess
import sys
import threading
import time
import os

from flask import Blueprint, jsonify, request

from mangascraper.core import api as scraperapi
from mangascraper.core import orchestrator

scraper_bp = Blueprint("scraper", __name__)

_process_lock = threading.Lock()
_scraper_process = None
_started_at = None
_last_args = []

_SEARCH_TYPES = {
    "homepage",
    "id_range",
    "ids",
    "cache_key",
    "search",
    "artist",
    "group",
    "tag",
    "character",
    "parody",
}


def _safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def _normalise_ids(value) -> list[int]:
    ids = []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, list):
        parts = value
    else:
        parts = []

    for part in parts:
        parsed = _safe_int(part)
        if parsed is not None:
            ids.append(parsed)
    return list(dict.fromkeys(ids))


def _queue_rows(ids: list[int], metadata: dict | None = None) -> list[dict]:
    metadata = metadata or {}
    rows = []
    for gid in ids:
        meta = metadata.get(gid) or metadata.get(str(gid)) or {}
        rows.append({
            "id": gid,
            "title": meta.get("title") or f"Gallery {gid}",
            "artists": meta.get("artists") or [],
            "groups": meta.get("groups") or [],
            "tags": meta.get("tags") or [],
            "languages": meta.get("languages") or [],
            "pages": meta.get("pages") or 0,
        })
    return rows


def _current_config() -> dict:
    orchestrator.refresh_globals()
    return {
        "extension": orchestrator.extension,
        "mirrors": ",".join(orchestrator.nhentai_mirrors or []),
        "verify_ssl": bool(orchestrator.verify_ssl),
        "language": ",".join(orchestrator.language or []),
        "title_type": orchestrator.title_type,
        "excluded_tags": ",".join(orchestrator.excluded_tags or []),
        "output_folder": orchestrator.download_path,
        "format": orchestrator.gallery_format,
        "use_tor": bool(orchestrator.use_tor),
        "dry_run": bool(orchestrator.dry_run),
        "threads_galleries": int(orchestrator.threads_galleries),
        "threads_images": int(orchestrator.threads_images),
        "use_daemon_threads": bool(orchestrator.use_daemon_threads),
        "max_retries": int(orchestrator.max_retries),
        "calm": bool(orchestrator.calm),
    }


def _update_config(payload: dict) -> dict:
    mapping = {
        "extension": ("EXTENSION", str),
        "mirrors": ("NHENTAI_MIRRORS", str),
        "verify_ssl": ("VERIFY_SSL", _safe_bool),
        "language": ("LANGUAGE", str),
        "title_type": ("TITLE_TYPE", str),
        "excluded_tags": ("EXCLUDED_TAGS", str),
        "output_folder": ("DOWNLOAD_PATH", str),
        "format": ("GALLERY_FORMAT", str),
        "use_tor": ("USE_TOR", _safe_bool),
        "dry_run": ("DRY_RUN", _safe_bool),
        "threads_galleries": ("THREADS_GALLERIES", _safe_int),
        "threads_images": ("THREADS_IMAGES", _safe_int),
        "use_daemon_threads": ("USE_DAEMON_THREADS", _safe_bool),
        "max_retries": ("MAX_RETRIES", _safe_int),
        "calm": ("CALM", _safe_bool),
    }

    applied = {}
    for key, (env_key, caster) in mapping.items():
        if key not in payload:
            continue
        raw = payload.get(key)
        value = caster(raw)
        if value is None:
            continue
        orchestrator.update_env(env_key, value)
        applied[key] = value

    orchestrator.refresh_globals()
    return applied


def _is_running() -> bool:
    return _scraper_process is not None and _scraper_process.poll() is None


def _normalise_cli_args(payload) -> list[str]:
    raw_args = payload.get("args", [])
    if isinstance(raw_args, str):
        return shlex.split(raw_args)
    if isinstance(raw_args, list):
        return [str(arg) for arg in raw_args]
    return []


def _status_counts() -> dict:
    rows = scraperapi.DB.Gallery.list()
    counts = {
        "total": len(rows),
        "started": 0,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
    }

    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        status = str(row[1] or "").lower()
        if status in counts:
            counts[status] += 1

    return counts


def _start_process(cli_args: list[str]):
    global _scraper_process, _started_at, _last_args

    with _process_lock:
        if _is_running():
            return None, ({"message": "Scraper is already running.", "pid": _scraper_process.pid}, 409)

        cmd = [sys.executable, "-m", "mangascraper.cli", *cli_args]
        _scraper_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _started_at = time.time()
        _last_args = cli_args

    return {"message": "Scraper started.", "args": cli_args}, None


@scraper_bp.route("/extensions", methods=["GET"])
def list_extensions():
    """Return installed extensions from the local manifest."""
    try:
        from mangascraper.extensions.extension_manager import calculate_extension_download_path, load_local_manifest
        manifest = load_local_manifest()
        extensions = [
            {
                "name": ext.get("name", ""),
                "label": ext.get("name", ""),
                "description": ext.get("description", ""),
                "version": ext.get("version", ""),
                "installed": ext.get("installed", False),
                "default_output_folder": calculate_extension_download_path(ext.get("name", "")) if ext.get("name") else "",
            }
            for ext in (manifest.get("extensions") or [])
            if ext.get("name")
        ]
    except Exception:
        extensions = []
    return jsonify({"extensions": extensions})


@scraper_bp.route("/config", methods=["GET"])
def config_get():
    return jsonify({"config": _current_config()})


@scraper_bp.route("/config", methods=["POST"])
def config_update():
    payload = request.get_json(silent=True) or {}
    applied = _update_config(payload)
    return jsonify({"message": "Configuration updated.", "updated": applied, "config": _current_config()})


@scraper_bp.route("/search", methods=["POST"])
def search_galleries():
    payload = request.get_json(silent=True) or {}

    query_type = str(payload.get("query_type") or "homepage").strip().lower()
    if query_type not in _SEARCH_TYPES:
        return jsonify({"message": f"Unsupported query_type '{query_type}'."}), 400

    query_value = str(payload.get("query_value") or "").strip()
    sort_value = str(payload.get("sort") or orchestrator.DEFAULT_PAGE_SORT).strip()
    sort_value = orchestrator.get_valid_sort_value(sort_value)
    start_page = _safe_int(payload.get("start_page"), orchestrator.DEFAULT_PAGE_RANGE_START)
    end_page_input = payload.get("end_page")
    end_page = None if end_page_input in (None, "", "all") else _safe_int(end_page_input, orchestrator.DEFAULT_PAGE_RANGE_END)
    fetch_all_pages = _safe_bool(payload.get("fetch_all_pages"), end_page is None)

    cache_key = None
    ids = []

    if query_type == "cache_key":
        if not query_value:
            return jsonify({"message": "cache_key is required for query_type=cache_key."}), 400
        cache_key = query_value
        ids = scraperapi.Cache.Load.cache(cache_key=cache_key)
    elif query_type == "id_range":
        start_id = _safe_int(payload.get("start_id"), None)
        end_id = _safe_int(payload.get("end_id"), None)
        if start_id is None:
            return jsonify({"message": "start_id is required for id_range searches."}), 400
        if end_id is None:
            end_id = scraperapi.Fetch.latest_gallery_id() or start_id
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        ids = list(range(start_id, end_id + 1))
    elif query_type == "ids":
        ids = _normalise_ids(payload.get("ids") or query_value)
        if not ids:
            return jsonify({"message": "Provide IDs for query_type=ids."}), 400
    else:
        search_value = sort_value if query_type == "homepage" else query_value
        cache_key, ids = scraperapi.Fetch.gallery_ids(
            query_type,
            search_value,
            sort_value,
            start_page,
            end_page,
            fetch_as_archival=fetch_all_pages,
        )

    if not ids:
        return jsonify({
            "message": "No galleries found.",
            "cache_key": cache_key,
            "ids": [],
            "summary": {},
            "results": [],
        })

    # Hydrate metadata so the dashboard can show proper titles/artists instead of placeholders.
    metadata = scraperapi.Fetch.all_galleries_metadata(ids, cache_key=None)
    rows = _queue_rows(sorted(ids, reverse=True), metadata)
    summary = scraperapi.Get.metadata_summary(metadata) if metadata else {}

    return jsonify({
        "message": f"Loaded {len(ids)} galleries.",
        "cache_key": cache_key,
        "ids": ids,
        "summary": summary,
        "results": rows,
    })


@scraper_bp.route("/cache/clear", methods=["POST"])
def cache_clear():
    payload = request.get_json(silent=True) or {}
    cache_key = str(payload.get("cache_key") or "").strip() or None
    gallery_id = _safe_int(payload.get("gallery_id"), None)
    cleared = scraperapi.Cache.clear_cache(cache_key=cache_key, gallery_id=gallery_id)
    return jsonify({"message": "Cache cleared.", "cleared": cleared})


@scraper_bp.route("/logs", methods=["GET"])
def logs_list():
    log_dir = getattr(orchestrator, "LOG_DIR", None)
    if not log_dir or not os.path.isdir(log_dir):
        return jsonify({"files": []})

    files = []
    for name in sorted(os.listdir(log_dir), reverse=True):
        full = os.path.join(log_dir, name)
        if not os.path.isfile(full):
            continue
        if not name.endswith(".log"):
            continue
        try:
            size = os.path.getsize(full)
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        files.append({"name": name, "size": size, "mtime": mtime})
    return jsonify({"files": files})


@scraper_bp.route("/logs/<path:filename>", methods=["GET"])
def logs_read(filename):
    log_dir = getattr(orchestrator, "LOG_DIR", None)
    if not log_dir or not os.path.isdir(log_dir):
        return jsonify({"message": "Log directory not found."}), 404

    safe_name = os.path.basename(filename)
    if safe_name != filename:
        return jsonify({"message": "Invalid log filename."}), 400

    full = os.path.realpath(os.path.join(log_dir, safe_name))
    log_dir_real = os.path.realpath(log_dir)
    if not full.startswith(log_dir_real + os.sep):
        return jsonify({"message": "Invalid log filename."}), 400
    if not os.path.isfile(full):
        return jsonify({"message": "Log file not found."}), 404

    lines = _safe_int(request.args.get("lines"), 400)
    if lines is None or lines < 1:
        lines = 400
    lines = min(lines, 5000)

    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content_lines = f.readlines()
        tail = "".join(content_lines[-lines:])
    except OSError as e:
        return jsonify({"message": f"Could not read log: {e}"}), 500

    return jsonify({
        "name": safe_name,
        "lines": lines,
        "content": tail,
    })


@scraper_bp.route("/search/history", methods=["GET"])
def search_history_list():
    references = scraperapi.Cache.Load.cache() or {}
    rows = []
    for key, entry in references.items():
        if not isinstance(entry, dict):
            continue
        ids = entry.get("ids") or []
        cache_type = str(entry.get("cache_type") or "")
        cache_target = str(entry.get("cache_target") or "")
        rows.append({
            "cache_key": str(key),
            "cache_type": cache_type,
            "cache_target": cache_target,
            "ids_count": len(ids),
            "expires_at": entry.get("expires_at"),
            "label": f"{cache_type}:{cache_target}" if cache_target else str(key),
        })

    rows.sort(key=lambda r: str(r.get("cache_key") or ""))
    return jsonify({"history": rows})


@scraper_bp.route("/search/history/remove", methods=["POST"])
def search_history_remove():
    payload = request.get_json(silent=True) or {}
    cache_key = str(payload.get("cache_key") or "").strip()
    if not cache_key:
        return jsonify({"message": "cache_key is required."}), 400

    cleared = scraperapi.Cache.clear_cache(cache_key=cache_key)
    return jsonify({"message": f"Removed search history item '{cache_key}'.", "cleared": cleared})


@scraper_bp.route("/queue", methods=["GET"])
def queue_get():
    ids = scraperapi.Cache.Load.queued_galleries()
    metadata = scraperapi.Cache.Load.id_metadata(ids) if ids else {}
    rows = _queue_rows(ids, metadata)
    summary = scraperapi.Get.metadata_summary(metadata) if metadata else {}
    return jsonify({"ids": ids, "summary": summary, "queue": rows})


@scraper_bp.route("/queue/add", methods=["POST"])
def queue_add():
    payload = request.get_json(silent=True) or {}
    ids = _normalise_ids(payload.get("ids"))
    if not ids:
        return jsonify({"message": "No IDs provided."}), 400

    current = scraperapi.Cache.Load.queued_galleries()
    merged = list(dict.fromkeys(current + ids))
    scraperapi.Cache.Save.queued_galleries(merged)
    return jsonify({"message": f"Added {len(ids)} IDs.", "ids": merged})


@scraper_bp.route("/queue/remove", methods=["POST"])
def queue_remove():
    payload = request.get_json(silent=True) or {}
    ids_to_remove = set(_normalise_ids(payload.get("ids")))
    current = scraperapi.Cache.Load.queued_galleries()
    updated = [gid for gid in current if gid not in ids_to_remove]
    scraperapi.Cache.Save.queued_galleries(updated)
    return jsonify({"message": f"Removed {len(ids_to_remove)} IDs.", "ids": updated})


@scraper_bp.route("/queue/clear", methods=["POST"])
def queue_clear():
    scraperapi.Cache.Save.queued_galleries([])
    return jsonify({"message": "Queue cleared.", "ids": []})


@scraper_bp.route("/queue/start", methods=["POST"])
def queue_start():
    ids = scraperapi.Cache.Load.queued_galleries()
    if not ids:
        return jsonify({"message": "Queue is empty."}), 400

    cli_args = ["--ids", ",".join(str(i) for i in ids)]
    ok, err = _start_process(cli_args)
    if err:
        return jsonify(err[0]), err[1]
    return jsonify(ok)

@scraper_bp.route("/status", methods=["GET"])
def status():
    """Return current scraper status."""
    with _process_lock:
        running = _is_running()
        pid = _scraper_process.pid if running else None
        started_at = _started_at
        args = list(_last_args)

    return jsonify({
        "status": "running" if running else "stopped",
        "pid": pid,
        "started_at": started_at,
        "uptime_seconds": int(time.time() - started_at) if running and started_at else 0,
        "args": args,
        "counts": _status_counts(),
    })

@scraper_bp.route("/start", methods=["POST"])
def start_scraper():
    """Start the scraper (accept CLI-like args as JSON)."""
    payload = request.get_json(silent=True) or {}
    cli_args = _normalise_cli_args(payload)
    ok, err = _start_process(cli_args)
    if err:
        return jsonify(err[0]), err[1]
    return jsonify(ok)

@scraper_bp.route("/stop", methods=["POST"])
def stop_scraper():
    """Stop scraper gracefully."""
    global _scraper_process, _started_at

    with _process_lock:
        if not _is_running():
            _scraper_process = None
            _started_at = None
            return jsonify({"message": "Scraper is not running."}), 409

        _scraper_process.terminate()
        try:
            _scraper_process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _scraper_process.kill()

        _scraper_process = None
        _started_at = None

    return jsonify({"message": "Scraper stopped."})