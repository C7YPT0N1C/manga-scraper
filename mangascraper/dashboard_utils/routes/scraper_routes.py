#!/usr/bin/env python3
# mangascraper/dashboard/routes/scraper_routes.py

import os, time, sys, threading, subprocess, socket, secrets, shlex, tempfile, re
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from mangascraper.core.api import api as scraperapi
from mangascraper.core import orchestrator

scraper_bp = Blueprint("scraper", __name__)

_process_lock = threading.Lock()
_scraper_process = None
_started_at = None
_last_args = []
_progress_port = None
_progress_token = None
_last_run_status = "stopped"
_last_exit_code = None
_active_download_no = None
_active_queue_file = None
_active_run_gallery_ids = []

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


def _parse_prefixed_query(value: str) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"^(artist|group|tag|character|parody|search)\s*:\s*(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return None
    query_type = str(match.group(1) or "").strip().lower()
    query_value = str(match.group(2) or "").strip()
    if not query_value:
        return None
    return query_type, query_value


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
            "characters": meta.get("characters") or [],
            "parodies": meta.get("parodies") or [],
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
        "output_folder": orchestrator.extension_download_path,
        "format": orchestrator.gallery_format,
        "use_tor": bool(orchestrator.use_tor),
        "dry_run": bool(orchestrator.dry_run),
        "threads_galleries": int(orchestrator.threads_galleries),
        "threads_images": int(orchestrator.threads_images),
        "use_daemon_threads": bool(orchestrator.use_daemon_threads),
        "max_retries": int(orchestrator.max_retries),
        "calm": bool(orchestrator.calm),
        "debug": bool(orchestrator.debug),
    }


def _update_config(payload: dict) -> dict:
    mapping = {
        "extension": ("EXTENSION", str),
        "mirrors": ("NHENTAI_MIRRORS", str),
        "verify_ssl": ("VERIFY_SSL", _safe_bool),
        "language": ("LANGUAGE", str),
        "title_type": ("TITLE_TYPE", str),
        "excluded_tags": ("EXCLUDED_TAGS", str),
        "output_folder": ("EXTENSION_DOWNLOAD_PATH", str),
        "format": ("GALLERY_FORMAT", str),
        "use_tor": ("USE_TOR", _safe_bool),
        "dry_run": ("DRY_RUN", _safe_bool),
        "threads_galleries": ("THREADS_GALLERIES", _safe_int),
        "threads_images": ("THREADS_IMAGES", _safe_int),
        "use_daemon_threads": ("USE_DAEMON_THREADS", _safe_bool),
        "max_retries": ("MAX_RETRIES", _safe_int),
        "calm": ("CALM", _safe_bool),
        "debug": ("DEBUG", _safe_bool),
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


def _status_counts(started_after: str | None = None, gallery_ids: list[int] | None = None) -> dict:
    # Use fetch_rows_as_dicts() to read rows as dicts to avoid positional index errors.
    rows = []
    for attempt in range(1, 4):
        try:
            rows = scraperapi.DB.fetch_rows_as_dicts("Galleries", columns=["id", "status", "started_at", "completed_at"]) or []
            break
        except Exception as exc:
            if "locked" not in str(exc).lower() or attempt >= 3:
                rows = []
                break
            time.sleep(0.2 * attempt)

    counts = {
        "total": 0,
        "started": 0,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
    }

    id_filter = set(_normalise_ids(gallery_ids or [])) if gallery_ids else None

    for row in rows:
        if not isinstance(row, dict):
            continue
        gid = _safe_int(row.get("id"), None)
        if id_filter is not None and (gid is None or gid not in id_filter):
            continue
        if started_after:
            started_at = str(row.get("started_at") or "").strip()
            if not started_at or started_at < started_after:
                continue
        counts["total"] += 1
        status = str(row.get("status") or "").strip().lower()
        if status in counts:
            counts[status] += 1

    return counts


def _current_run_gallery_ids() -> list[int]:
    with _process_lock:
        in_memory_ids = list(_active_run_gallery_ids or [])
    if in_memory_ids:
        return _normalise_ids(in_memory_ids)

    try:
        jobs = scraperapi.DB.DownloadQueue.list(statuses=["running", "queued"])
    except Exception:
        jobs = []

    with _process_lock:
        active_no = _active_download_no

    if active_no is not None:
        for job in jobs:
            if _safe_int(job.get("download_no"), None) == active_no:
                return _normalise_ids(job.get("ids") or [])

    for job in jobs:
        if str(job.get("status") or "").strip().lower() == "running":
            return _normalise_ids(job.get("ids") or [])

    return []


def _queue_total() -> int:
    selected_ids = []
    download_ids = []
    try:
        selected_ids = _normalise_ids(scraperapi.Cache.Load.queued_galleries() or [])
    except Exception:
        selected_ids = []
    try:
        jobs = scraperapi.DB.DownloadQueue.list(statuses=["queued", "running"])
        for job in jobs:
            download_ids.extend(_normalise_ids(job.get("ids") or []))
    except Exception:
        download_ids = []
    return len(set(selected_ids + download_ids))


def _write_queue_ids_file(ids: list[int]) -> str:
    # The CLI caps --ids at 25 entries. Use a temporary file for reliable large batches.
    temp_root = getattr(orchestrator, "TEMP_DIR", None) or tempfile.gettempdir()
    os.makedirs(temp_root, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", prefix="ms_queue_ids_", dir=temp_root, delete=False) as handle:
        handle.write("\n".join(str(i) for i in ids))
        return handle.name


def _finalise_completed_download_job() -> int | None:
    global _active_download_no, _active_queue_file

    with _process_lock:
        if _is_running() or _active_download_no is None:
            return None
        completed_no = _active_download_no
        queue_file = _active_queue_file
        _active_download_no = None
        _active_queue_file = None

    try:
        scraperapi.DB.DownloadQueue.remove(completed_no)
    except Exception:
        pass

    if queue_file:
        try:
            if os.path.isfile(queue_file):
                os.remove(queue_file)
        except Exception:
            pass

    return completed_no


def _start_next_queued_download() -> dict | None:
    global _active_download_no, _active_queue_file, _active_run_gallery_ids

    with _process_lock:
        if _is_running() or _active_download_no is not None:
            return None

    job = scraperapi.DB.DownloadQueue.next_queued()
    if not job:
        return None

    download_no = _safe_int(job.get("download_no"), None)
    ids = _normalise_ids(job.get("ids") or [])
    if download_no is None or not ids:
        if download_no is not None:
            scraperapi.DB.DownloadQueue.remove(download_no)
        return None

    queue_file = _write_queue_ids_file(ids)
    scraperapi.DB.DownloadQueue.set_status(download_no, "running")
    ok, err = _start_process(["--file", queue_file])
    if err:
        scraperapi.DB.DownloadQueue.set_status(download_no, "queued")
        try:
            if os.path.isfile(queue_file):
                os.remove(queue_file)
        except Exception:
            pass
        return None

    with _process_lock:
        _active_download_no = int(download_no)
        _active_queue_file = queue_file
        _active_run_gallery_ids = list(ids)

    return {
        "download_no": int(download_no),
        "status": "running",
        "ids": ids,
    }


def _allocate_localhost_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_runtime_progress() -> dict:
    global _progress_port, _progress_token

    with _process_lock:
        port = _progress_port
        token = _progress_token

    return scraperapi.RuntimeProgress.fetch(port=port, token=token, timeout_seconds=0.35)


def _start_process(cli_args: list[str]):
    global _scraper_process, _started_at, _last_args, _progress_port, _progress_token, _last_run_status, _last_exit_code

    with _process_lock:
        if _is_running():
            return None, ({"message": "Scraper is already running.", "pid": _scraper_process.pid}, 409)

        # Dashboard launches should always be non-interactive and verbose for diagnostics.
        normalised_args = [str(arg) for arg in (cli_args or [])]
        if "--unattended" not in normalised_args:
            normalised_args.append("--unattended")
        if "--debug" not in normalised_args:
            normalised_args.append("--debug")
        # Avoid conflicting logging mode when debug is enforced.
        normalised_args = [arg for arg in normalised_args if arg != "--calm"]

        cmd = [sys.executable, "-m", "mangascraper.cli", *normalised_args]
        child_env = os.environ.copy()
        _progress_port = _allocate_localhost_port()
        _progress_token = secrets.token_urlsafe(24)
        child_env["MANGASCRAPER_PROGRESS_PORT"] = str(_progress_port)
        child_env["MANGASCRAPER_PROGRESS_TOKEN"] = _progress_token
        _scraper_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env,
        )
        _started_at = time.time()
        _last_args = normalised_args
        _last_run_status = "running"
        _last_exit_code = None

    return {"message": "Scraper started.", "args": cli_args}, None


@scraper_bp.route("/extensions", methods=["GET"])
def list_extensions():
    """Return installed extensions from the local manifest with remote version comparison."""
    import json as _json
    import re as _re

    def _ver(v):
        parts = _re.findall(r'\d+', str(v))
        return tuple(int(p) for p in parts) if parts else (0,)

    try:
        from mangascraper.extensions.extension_manager import load_local_manifest
        manifest = load_local_manifest()

        # Try local sibling repo for version comparison (no network required)
        master_map = {}
        try:
            _base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
            _mpath = os.path.join(_base, '..', 'manga-scraper-extensions', 'master_manifest.json')
            if os.path.isfile(_mpath):
                with open(_mpath, 'r', encoding='utf-8') as _f:
                    _remote = _json.load(_f)
                for _e in (_remote.get('extensions') or []):
                    if _e.get('name'):
                        master_map[_e['name']] = _e.get('version', '0')
        except Exception:
            pass

        extensions = []
        for ext in (manifest.get("extensions") or []):
            name = ext.get("name", "")
            if not name:
                continue
            local_v = ext.get("version", "0")
            remote_v = master_map.get(name, local_v)
            update_available = _ver(remote_v) > _ver(local_v)
            extensions.append({
                "name": name,
                "label": name,
                "description": ext.get("description", ""),
                "version": local_v,
                "remote_version": remote_v,
                "update_available": update_available,
                "installed": ext.get("installed", False),
                "default_output_folder": str(ext.get("image_download_path") or ""),
            })
    except Exception:
        extensions = []
    return jsonify({"extensions": extensions})


@scraper_bp.route("/extensions/install", methods=["POST"])
def extension_install():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip().lower()
    if not name:
        return jsonify({"message": "Extension name is required."}), 400

    try:
        from mangascraper.extensions.extension_manager import install_selected_extension
        install_selected_extension(name, reinstall=False, prompt_for_update=False)
        return jsonify({"message": f"Install requested for extension '{name}'."})
    except Exception as e:
        return jsonify({"message": f"Failed to install extension '{name}': {e}"}), 500


@scraper_bp.route("/extensions/uninstall", methods=["POST"])
def extension_uninstall():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip().lower()
    if not name:
        return jsonify({"message": "Extension name is required."}), 400

    try:
        from mangascraper.extensions.extension_manager import uninstall_selected_extension
        uninstall_selected_extension(name)
        return jsonify({"message": f"Uninstall requested for extension '{name}'."})
    except Exception as e:
        return jsonify({"message": f"Failed to uninstall extension '{name}': {e}"}), 500


@scraper_bp.route("/extensions/update", methods=["POST"])
def extension_update():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip().lower()
    if not name:
        return jsonify({"message": "Extension name is required."}), 400

    try:
        from mangascraper.extensions.extension_manager import install_selected_extension
        install_selected_extension(name, reinstall=True, prompt_for_update=False)
        return jsonify({"message": f"Update requested for extension '{name}'."})
    except Exception as e:
        return jsonify({"message": f"Failed to update extension '{name}': {e}"}), 500


@scraper_bp.route("/self-test", methods=["POST"])
def run_self_test():
    """Run core self-tests in a separate process and return a concise result."""
    try:
        cmd = [sys.executable, "-m", "mangascraper.cli", "--self-test", "--unattended", "--debug"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        lines = [line for line in output.splitlines() if line.strip()]
        tail = "\n".join(lines[-30:]) if lines else "(no output)"
        status = "passed" if result.returncode == 0 else "failed"
        return jsonify({
            "message": f"Self-test {status} (exit code {result.returncode}).",
            "status": status,
            "exit_code": result.returncode,
            "output_tail": tail,
        }), (200 if result.returncode == 0 else 500)
    except subprocess.TimeoutExpired:
        return jsonify({"message": "Self-test timed out."}), 504
    except Exception as e:
        return jsonify({"message": f"Self-test failed to start: {e}"}), 500


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
    parsed_prefixed = _parse_prefixed_query(query_value)
    if parsed_prefixed and query_type in {"search", "artist", "group", "tag", "character", "parody"}:
        query_type, query_value = parsed_prefixed
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
        try:
            scraperapi.logger.debug(
                f"[DashboardSearch] query_type=cache_key request key='{cache_key}' sort='{sort_value}' "
                f"start_page={start_page} end_page={end_page} fetch_all_pages={fetch_all_pages}"
            )
        except Exception:
            pass
        ids = scraperapi.Cache.Load.cache(cache_key=cache_key)
        try:
            scraperapi.logger.info(
                f"[DashboardSearch] cache_key='{cache_key}' resolved {len(ids)} IDs"
            )
            preview = ",".join(str(gid) for gid in ids[:10]) if ids else ""
            scraperapi.logger.debug(
                f"[DashboardSearch] cache_key='{cache_key}' ids_preview='{preview}'"
            )
        except Exception:
            pass
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
        if query_type == "cache_key":
            try:
                scraperapi.logger.debug(
                    f"[DashboardSearch] cache_key='{cache_key}' produced no IDs; returning empty result set"
                )
            except Exception:
                pass
        return jsonify({
            "message": "No galleries found.",
            "cache_key": cache_key,
            "ids": [],
            "summary": {},
            "results": [],
        })

    # Hydrate metadata so the dashboard can show proper titles/artists instead of placeholders.
    metadata = scraperapi.Fetch.all_galleries_metadata(ids, cache_key=None)
    try:
        missing_meta_ids = [int(gid) for gid in ids if int(gid) not in set(int(k) for k in (metadata or {}).keys())]
        scraperapi.logger.info(
            f"[DashboardSearch] query_type='{query_type}' ids={len(ids)} metadata={len(metadata or {})} missing={len(missing_meta_ids)}"
        )
        if missing_meta_ids:
            scraperapi.logger.warning(
                "[DashboardSearch] Missing metadata IDs: " + ",".join(str(gid) for gid in missing_meta_ids[:50])
            )
    except Exception:
        pass
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


@scraper_bp.route("/diagnostics", methods=["GET"])
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
        if size <= 0:
            continue
        files.append({"name": name, "size": size, "mtime": mtime})
    return jsonify({"files": files})


@scraper_bp.route("/diagnostics/<path:filename>", methods=["GET"])
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
    downloads = scraperapi.DB.DownloadQueue.list()
    return jsonify({"ids": ids, "summary": summary, "queue": rows, "downloads": downloads})


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
    global _progress_port, _progress_token, _scraper_process, _started_at, _last_run_status, _last_exit_code

    # Reconcile process state here as well (not only in /status), so callers
    # like the creator streamer can reliably start queued work.
    with _process_lock:
        running = _is_running()
        if not running and _scraper_process is not None:
            polled_code = _scraper_process.poll()
            if polled_code is not None:
                _last_exit_code = int(polled_code)
                if _last_run_status != "stopped":
                    _last_run_status = "completed" if _last_exit_code == 0 else "stopped"
                _scraper_process = None
                _started_at = None
                _progress_port = None
                _progress_token = None

        if not _is_running() and _active_download_no is None:
            try:
                scraperapi.DB.DownloadQueue.remove_by_status("running")
            except Exception:
                pass

    _finalise_completed_download_job()

    ids = scraperapi.Cache.Load.queued_galleries()
    enqueued_job = None
    if ids:
        enqueued_job = scraperapi.DB.DownloadQueue.enqueue(ids)
        scraperapi.Cache.Save.queued_galleries([])

    started_job = _start_next_queued_download()
    all_jobs = scraperapi.DB.DownloadQueue.list()
    with _process_lock:
        running_now = _is_running()

    if not all_jobs and not running_now and not enqueued_job and not started_job:
        return jsonify({"message": "Queue is empty."}), 400

    if enqueued_job and started_job and started_job.get("download_no") == enqueued_job.get("download_no"):
        message = f"Download #{enqueued_job.get('download_no')} started."
    elif enqueued_job:
        message = f"Download #{enqueued_job.get('download_no')} queued."
    elif started_job:
        message = f"Download #{started_job.get('download_no')} started."
    else:
        message = "Download already running."

    return jsonify({
        "message": message,
        "enqueued": enqueued_job,
        "started": started_job,
        "downloads": all_jobs,
    })

@scraper_bp.route("/status", methods=["GET"])
def status():
    """Return current scraper status."""
    global _progress_port, _progress_token, _scraper_process, _started_at, _last_run_status, _last_exit_code, _active_download_no

    with _process_lock:
        running = _is_running()
        if not running and _scraper_process is not None:
            polled_code = _scraper_process.poll()
            if polled_code is not None:
                _last_exit_code = int(polled_code)
                if _last_run_status != "stopped":
                    _last_run_status = "completed" if _last_exit_code == 0 else "stopped"
                _scraper_process = None
                _started_at = None
                _progress_port = None
                _progress_token = None

        if not _is_running() and _active_download_no is None:
            try:
                scraperapi.DB.DownloadQueue.remove_by_status("running")
            except Exception:
                pass

    _finalise_completed_download_job()
    _start_next_queued_download()

    with _process_lock:
        running = _is_running()
        pid = _scraper_process.pid if running else None
        started_at = _started_at
        args = list(_last_args)
        if not running:
            _progress_port = None
            _progress_token = None

    run_started_iso = None
    if started_at:
        try:
            run_started_iso = datetime.fromtimestamp(float(started_at), timezone.utc).isoformat()
        except Exception:
            run_started_iso = None

    run_gallery_ids = _current_run_gallery_ids()

    # DB-derived counts should not depend on live progress endpoint reliability.
    counts = {"total": 0, "started": 0, "completed": 0, "failed": 0, "skipped": 0}
    try:
        if run_gallery_ids:
            counts = _status_counts(gallery_ids=run_gallery_ids)
        else:
            counts = _status_counts(started_after=run_started_iso)
    except Exception:
        counts = {"total": 0, "started": 0, "completed": 0, "failed": 0, "skipped": 0}

    # Live runtime progress (pages/sec, pages processed, ETA) with retry/backoff.
    progress = {}
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            progress = _read_runtime_progress() if running else {}
            break
        except Exception:
            if attempt >= max_attempts:
                try:
                    scraperapi.logger.exception("[DashboardStatus] Failed to read runtime progress after retries.")
                except Exception:
                    pass
                progress = {}
                break
            try:
                wait = float(scraperapi.Sleep.dynamic("api", attempt))
            except Exception:
                wait = 0.5
            time.sleep(wait)

    return jsonify({
        "status": "running" if running else _last_run_status,
        "pid": pid,
        "exit_code": _last_exit_code,
        "started_at": started_at,
        "uptime_seconds": int(time.time() - started_at) if running and started_at else 0,
        "args": args,
        "counts": counts,
        "queue_total": _queue_total(),
        "progress": progress,
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
    global _scraper_process, _started_at, _progress_port, _progress_token, _last_run_status, _last_exit_code

    with _process_lock:
        if not _is_running():
            _scraper_process = None
            _started_at = None
            _progress_port = None
            _progress_token = None
            return jsonify({"message": "Scraper is not running."}), 409

        _scraper_process.terminate()
        try:
            _scraper_process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _scraper_process.kill()

        _last_exit_code = _scraper_process.poll()
        _last_run_status = "stopped"

        _scraper_process = None
        _started_at = None
        _progress_port = None
        _progress_token = None

    return jsonify({"message": "Scraper stopped."})

@scraper_bp.route("/database/cleanup", methods=["POST"])
def database_cleanup():
    """Trigger comprehensive database cleanup and maintenance."""
    try:
        stats = scraperapi.DB.database_cleanup()
        message = (
            f"Database cleanup complete: "
            f"{stats['removed_galleries']} orphaned galleries removed, "
            f"{stats['pruned_roots']} unmanaged locations pruned, "
            f"{stats['covers_repaired']} covers repaired."
        )
        return jsonify({
            "message": message,
            "success": True,
            "stats": stats,
        })
    except Exception as e:
        return jsonify({
            "message": f"Database cleanup failed: {e}",
            "success": False,
            "error": str(e),
        }), 500