"""High-level scraper service layer.

Provides a small, testable facade for scraper-related business operations
that composes the lower-level helpers in `mangascraper.core.api`.

This initial prototype implements status-related helpers used by the
dashboard and background emitters. It intentionally keeps responsibilities
small so the existing route logic can incrementally adopt the service.
"""

from __future__ import annotations
import time
from typing import Any, Dict, Iterable, List, Optional

from mangascraper.core.api import api as scraperapi
import os
import sys
import socket
import secrets
import subprocess
import tempfile
import shlex
import time as _time
from mangascraper.core import orchestrator


class ScraperService:
    """Encapsulate high-level scraper/business operations.

    Dependencies (DB, Cache, RuntimeProgress, Sleep) are taken from
    `mangascraper.core.api.api` by default but can be injected for tests.
    """

    def __init__(self, api_module=None):
        self.api = api_module or scraperapi
        # process state managed by the service when used as the central controller
        self._process = None
        self._started_at = None
        self._last_args = []
        self._progress_port = None
        self._progress_token = None

    def get_counts(self, started_after: Optional[str] = None, gallery_ids: Optional[Iterable[int]] = None) -> Dict[str, int]:
        """Return counts of galleries by status.

        Mirrors the previous internal logic used by the dashboard but is
        safe to call from other places (background emitters, tests).
        """
        rows: List[Dict[str, Any]] = []
        # tolerate transient DB lock errors by retrying a few times
        for attempt in range(1, 4):
            try:
                rows = self.api.DB.fetch_rows_as_dicts("Galleries", columns=["id", "status", "started_at", "completed_at"]) or []
                break
            except Exception:
                if attempt >= 3:
                    rows = []
                    break
                time.sleep(0.2 * attempt)

        counts = {"total": 0, "started": 0, "completed": 0, "failed": 0, "skipped": 0}

        id_filter = set(int(x) for x in (gallery_ids or [])) if gallery_ids else None

        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                gid = int(row.get("id"))
            except Exception:
                gid = None
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

    def queue_total(self) -> int:
        """Return number of queued galleries (cache + DB downloads)."""
        selected_ids: List[int] = []
        download_ids: List[int] = []
        try:
            selected_ids = list(map(int, (self.api.Cache.Load.queued_galleries() or [])))
        except Exception:
            selected_ids = []
        try:
            jobs = self.api.DB.DownloadQueue.list(statuses=["queued", "running"]) or []
            for job in jobs:
                ids = job.get("ids") or []
                for i in ids:
                    try:
                        download_ids.append(int(i))
                    except Exception:
                        continue
        except Exception:
            download_ids = []

        return len(set(selected_ids + download_ids))

    def _allocate_localhost_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def start_process(self, cli_args: Optional[Iterable[str]] = None) -> tuple[Optional[dict], Optional[tuple]]:
        """Start the scraper subprocess with given CLI args.

        Returns (info_dict, None) on success or (None, (error_dict, status_code)) on failure.
        """
        if self._process is not None and self._process.poll() is None:
            return None, ({"message": "Scraper is already running.", "pid": self._process.pid}, 409)

        normalised_args = [str(arg) for arg in (cli_args or [])]
        if "--unattended" not in normalised_args:
            normalised_args.append("--unattended")
        if "--debug" not in normalised_args:
            normalised_args.append("--debug")
        normalised_args = [arg for arg in normalised_args if arg != "--calm"]

        cmd = [sys.executable, "-m", "mangascraper.cli", *normalised_args]
        child_env = os.environ.copy()
        try:
            port = self._allocate_localhost_port()
        except Exception:
            port = None
        self._progress_port = port
        self._progress_token = secrets.token_urlsafe(24)
        if port is not None:
            child_env["MANGASCRAPER_PROGRESS_PORT"] = str(port)
        child_env["MANGASCRAPER_PROGRESS_TOKEN"] = self._progress_token

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_env,
            )
        except Exception as e:
            return None, ({"message": f"Failed to start scraper: {e}"}, 500)

        self._started_at = _time.time()
        self._last_args = list(normalised_args)

        return {"message": "Scraper started.", "args": cli_args}, None

    # --- Queue & search helpers ---
    def _write_queue_ids_file(self, ids: list[int]) -> str:
        temp_root = getattr(orchestrator, "TEMP_DIR", None) or tempfile.gettempdir()
        os.makedirs(temp_root, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", prefix="ms_queue_ids_", dir=temp_root, delete=False) as handle:
            handle.write("\n".join(str(i) for i in ids))
            return handle.name

    def _start_next_queued_download(self) -> dict | None:
        """Start the next queued download job from DB.DownloadQueue."""
        job = None
        try:
            job = self.api.DB.DownloadQueue.next_queued()
        except Exception:
            job = None

        if not job:
            return None

        download_no = None
        try:
            download_no = int(job.get("download_no"))
        except Exception:
            download_no = None

        ids = []
        try:
            raw_ids = job.get("ids") or []
            ids = [int(i) for i in raw_ids]
        except Exception:
            ids = []

        if download_no is None or not ids:
            try:
                if download_no is not None:
                    self.api.DB.DownloadQueue.remove(download_no)
            except Exception:
                pass
            return None

        queue_file = self._write_queue_ids_file(ids)
        try:
            self.api.DB.DownloadQueue.set_status(download_no, "running")
        except Exception:
            pass

        ok, err = self.start_process(["--file", queue_file])
        if err:
            try:
                self.api.DB.DownloadQueue.set_status(download_no, "queued")
            except Exception:
                pass
            try:
                if os.path.isfile(queue_file):
                    os.remove(queue_file)
            except Exception:
                pass
            return None

        # track active download info on the service instance
        try:
            self._active_download_no = int(download_no)
            self._active_queue_file = queue_file
            self._active_run_gallery_ids = list(ids)
        except Exception:
            self._active_download_no = None
            self._active_queue_file = None
            self._active_run_gallery_ids = []

        return {
            "download_no": int(download_no),
            "status": "running",
            "ids": ids,
        }

    def start_next_queued_download(self) -> dict | None:
        """Public wrapper to start the next queued download."""
        return self._start_next_queued_download()

    def finalise_completed_download_job(self) -> int | None:
        """Finalize any completed download job previously tracked by the service.

        Returns the completed download number or None.
        """
        try:
            active_no = getattr(self, "_active_download_no", None)
        except Exception:
            active_no = None

        if getattr(self, "_process", None) is not None and getattr(self, "_process", None).poll() is None:
            return None

        completed_no = None
        try:
            if active_no is None:
                return None
            completed_no = int(active_no)
            try:
                self.api.DB.DownloadQueue.remove(completed_no)
            except Exception:
                pass
            queue_file = getattr(self, "_active_queue_file", None)
            try:
                if queue_file and os.path.isfile(queue_file):
                    os.remove(queue_file)
            except Exception:
                pass
        finally:
            try:
                self._active_download_no = None
                self._active_queue_file = None
            except Exception:
                pass

        return completed_no

    def get_current_run_gallery_ids(self) -> list[int]:
        """Return the IDs currently being processed (in-memory or from DB)."""
        in_memory = list(getattr(self, "_active_run_gallery_ids", []) or [])
        if in_memory:
            return [int(x) for x in in_memory]

        try:
            jobs = self.api.DB.DownloadQueue.list(statuses=["running", "queued"]) or []
        except Exception:
            jobs = []

        active_no = getattr(self, "_active_download_no", None)
        if active_no is not None:
            for job in jobs:
                try:
                    if int(job.get("download_no")) == int(active_no):
                        return [int(i) for i in (job.get("ids") or [])]
                except Exception:
                    continue

        for job in jobs:
            if str(job.get("status") or "").strip().lower() == "running":
                return [int(i) for i in (job.get("ids") or [])]

        return []

    def get_queue(self) -> dict:
        ids = self.api.Cache.Load.queued_galleries() or []
        metadata = {}
        try:
            metadata = self.api.Cache.Load.id_metadata(ids) if ids else {}
        except Exception:
            metadata = {}

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

        summary = self.api.Get.metadata_summary(metadata) if metadata else {}
        downloads = []
        try:
            downloads = self.api.DB.DownloadQueue.list() or []
        except Exception:
            downloads = []

        return {"ids": ids, "summary": summary, "queue": rows, "downloads": downloads}

    def add_to_queue(self, ids: list[int]) -> list[int]:
        current = self.api.Cache.Load.queued_galleries() or []
        merged = list(dict.fromkeys(list(current) + list(ids)))
        try:
            self.api.Cache.Save.queued_galleries(merged)
        except Exception:
            pass
        return merged

    def remove_from_queue(self, ids: list[int]) -> list[int]:
        current = self.api.Cache.Load.queued_galleries() or []
        updated = [gid for gid in current if gid not in set(ids)]
        try:
            self.api.Cache.Save.queued_galleries(updated)
        except Exception:
            pass
        return updated

    def clear_queue(self) -> list:
        try:
            self.api.Cache.Save.queued_galleries([])
        except Exception:
            pass
        return []

    def start_queue(self) -> dict:
        """Enqueue current queued IDs and attempt to start the next job."""
        ids = self.api.Cache.Load.queued_galleries() or []
        enqueued_job = None
        if ids:
            try:
                enqueued_job = self.api.DB.DownloadQueue.enqueue(ids)
            except Exception:
                enqueued_job = None
            try:
                self.api.Cache.Save.queued_galleries([])
            except Exception:
                pass

        started_job = self._start_next_queued_download()
        all_jobs = []
        try:
            all_jobs = self.api.DB.DownloadQueue.list() or []
        except Exception:
            all_jobs = []

        return {"enqueued": enqueued_job, "started": started_job, "downloads": all_jobs}

    # --- Config helpers ---
    def get_config(self) -> dict:
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

    def update_config(self, payload: dict) -> dict:
        mapping = {
            "extension": ("EXTENSION", str),
            "mirrors": ("NHENTAI_MIRRORS", str),
            "verify_ssl": ("VERIFY_SSL", lambda v: bool(v) if isinstance(v, bool) else str(v)),
            "language": ("LANGUAGE", str),
            "title_type": ("TITLE_TYPE", str),
            "excluded_tags": ("EXCLUDED_TAGS", str),
            "output_folder": ("EXTENSION_DOWNLOAD_PATH", str),
            "format": ("GALLERY_FORMAT", str),
            "use_tor": ("USE_TOR", lambda v: bool(v) if isinstance(v, bool) else str(v)),
            "dry_run": ("DRY_RUN", lambda v: bool(v) if isinstance(v, bool) else str(v)),
            "threads_galleries": ("THREADS_GALLERIES", int),
            "threads_images": ("THREADS_IMAGES", int),
            "use_daemon_threads": ("USE_DAEMON_THREADS", lambda v: bool(v) if isinstance(v, bool) else str(v)),
            "max_retries": ("MAX_RETRIES", int),
            "calm": ("CALM", lambda v: bool(v) if isinstance(v, bool) else str(v)),
            "debug": ("DEBUG", lambda v: bool(v) if isinstance(v, bool) else str(v)),
        }

        applied = {}
        for key, (env_key, caster) in mapping.items():
            if key not in payload:
                continue
            raw = payload.get(key)
            try:
                value = caster(raw)
            except Exception:
                value = None
            if value is None:
                continue
            try:
                orchestrator.update_env(env_key, value)
                applied[key] = value
            except Exception:
                pass

        orchestrator.refresh_globals()
        return applied

    # --- Cache / search-history / diagnostics helpers ---
    def clear_cache(self, cache_key: str | None = None, gallery_id: int | None = None) -> bool:
        try:
            return bool(self.api.Cache.clear_cache(cache_key=cache_key, gallery_id=gallery_id))
        except Exception:
            return False

    def get_search_history(self) -> list:
        references = self.api.Cache.Load.cache() or {}
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
        return rows

    def remove_search_history(self, cache_key: str) -> bool:
        if not cache_key:
            return False
        try:
            return bool(self.api.Cache.clear_cache(cache_key=cache_key))
        except Exception:
            return False

    def database_cleanup(self) -> dict:
        try:
            return self.api.DB.database_cleanup()
        except Exception as e:
            raise

    def stop_process(self) -> tuple[Optional[dict], Optional[tuple]]:
        """Stop the scraper subprocess started by this service.

        Returns (info_dict, None) on success or (None, (error_dict, status_code)).
        """
        if self._process is None or self._process.poll() is not None:
            # already stopped
            self._process = None
            self._started_at = None
            self._progress_port = None
            self._progress_token = None
            return None, ({"message": "Scraper is not running."}, 409)

        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
            exit_code = self._process.poll()
        except Exception as e:
            return None, ({"message": f"Failed to stop scraper: {e}"}, 500)

        self._process = None
        self._started_at = None
        self._progress_port = None
        self._progress_token = None

        return {"message": "Scraper stopped.", "exit_code": exit_code}, None

    def read_runtime_progress(self, running: bool) -> Dict[str, Any]:
        """Attempt to read runtime progress from the RuntimeProgress helper.

        Returns an empty dict on failure or when not running.
        """
        if not running:
            return {}
        try:
            return self.api.RuntimeProgress.fetch(timeout_seconds=0.35) or {}
        except Exception:
            return {}

    def get_status(self, *, started_after: Optional[str] = None, gallery_ids: Optional[Iterable[int]] = None, include_progress: bool = True, running: bool = False) -> Dict[str, Any]:
        """Return a status payload with counts, queue_total and optional progress.

        Note: process-level fields like `pid`, `started_at`, and `args` are
        owned by the caller (for example the dashboard route) since they
        represent runtime process state maintained elsewhere.
        """
        counts = {}
        try:
            counts = self.get_counts(started_after=started_after, gallery_ids=gallery_ids)
        except Exception:
            counts = {"total": 0, "started": 0, "completed": 0, "failed": 0, "skipped": 0}

        progress = {}
        if include_progress:
            progress = self.read_runtime_progress(running=running)

        payload = {
            "counts": counts,
            "queue_total": self.queue_total(),
            "progress": progress,
        }
        return payload

    def search(self, payload: dict) -> dict:
        """Perform a gallery search based on the dashboard payload and return
        the same JSON structure the dashboard expects.
        """
        query_type = str(payload.get("query_type") or "homepage").strip().lower()
        query_value = str(payload.get("query_value") or "").strip()

        # support prefixed queries like "artist:foo"
        parsed_prefixed = None
        try:
            import re
            match = re.match(r"^(artist|group|tag|character|parody|search)\s*:\s*(.+)$", query_value, flags=re.IGNORECASE)
            if match:
                parsed_prefixed = (str(match.group(1) or "").strip().lower(), str(match.group(2) or "").strip())
        except Exception:
            parsed_prefixed = None

        if parsed_prefixed and query_type in {"search", "artist", "group", "tag", "character", "parody"}:
            query_type, query_value = parsed_prefixed

        sort_value = str(payload.get("sort") or orchestrator.DEFAULT_PAGE_SORT).strip()
        try:
            sort_value = orchestrator.get_valid_sort_value(sort_value)
        except Exception:
            pass

        start_page = int(payload.get("start_page") or orchestrator.DEFAULT_PAGE_RANGE_START)
        end_page_input = payload.get("end_page")
        end_page = None if end_page_input in (None, "", "all") else int(end_page_input)
        fetch_all_pages = bool(payload.get("fetch_all_pages") or (end_page is None))

        cache_key = None
        ids = []

        if query_type == "cache_key":
            cache_key = query_value
            ids = self.api.Cache.Load.cache(cache_key=cache_key)
        elif query_type == "id_range":
            start_id = int(payload.get("start_id") or 0)
            end_id = int(payload.get("end_id") or start_id)
            if start_id > end_id:
                start_id, end_id = end_id, start_id
            ids = list(range(start_id, end_id + 1))
        elif query_type == "ids":
            raw_ids = payload.get("ids") or query_value
            if isinstance(raw_ids, str):
                parts = [p.strip() for p in raw_ids.split(",") if p.strip()]
            elif isinstance(raw_ids, list):
                parts = raw_ids
            else:
                parts = []
            ids = []
            for part in parts:
                try:
                    ids.append(int(part))
                except Exception:
                    continue
            ids = list(dict.fromkeys(ids))
        else:
            search_value = sort_value if query_type == "homepage" else query_value
            cache_key, ids = self.api.Fetch.gallery_ids(
                query_type,
                search_value,
                sort_value,
                start_page,
                end_page,
                fetch_as_archival=fetch_all_pages,
            )

        if not ids:
            return {
                "message": "No galleries found.",
                "cache_key": cache_key,
                "ids": [],
                "summary": {},
                "results": [],
            }

        metadata = self.api.Fetch.all_galleries_metadata(ids, cache_key=None)
        rows = []
        for gid in sorted(ids, reverse=True):
            meta = metadata.get(int(gid)) if isinstance(metadata, dict) else {}
            rows.append({
                "id": int(gid),
                "title": meta.get("title") if isinstance(meta, dict) else f"Gallery {gid}",
                "artists": meta.get("artists") if isinstance(meta, dict) else [],
                "groups": meta.get("groups") if isinstance(meta, dict) else [],
                "tags": meta.get("tags") if isinstance(meta, dict) else [],
                "characters": meta.get("characters") if isinstance(meta, dict) else [],
                "parodies": meta.get("parodies") if isinstance(meta, dict) else [],
                "languages": meta.get("languages") if isinstance(meta, dict) else [],
                "pages": meta.get("pages") if isinstance(meta, dict) else 0,
            })

        summary = self.api.Get.metadata_summary(metadata) if metadata else {}

        return {
            "message": f"Loaded {len(ids)} galleries.",
            "cache_key": cache_key,
            "ids": ids,
            "summary": summary,
            "results": rows,
        }


_DEFAULT_SERVICE: Optional[ScraperService] = None


def get_default_service() -> ScraperService:
    """Singleton factory returning the application default service instance."""
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = ScraperService()
    return _DEFAULT_SERVICE
