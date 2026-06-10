# mangascraper/dashboard_utils/socketio_.py
from __future__ import annotations
import threading, time
from typing import Optional

from mangascraper.core.api import api as scraperapi

socketio = None
# Remember the last non-zero total number of galleries being downloaded so the
# dynamic bar can continue showing the previous run's total after the run
# completes (avoids showing e.g. Completed: 7/0).
_last_total = 0
_scraper_namespace_sids = set()

def init_app(app, poll_interval: float = 2.0):
    """Initialise SocketIO for the Flask `app` and start a background emitter that
    publishes scraper status to connected clients. Call this after app creation
    and blueprint registration.
    """
    global socketio
    if socketio is not None:
        return socketio

    try:
        from flask_socketio import SocketIO
    except Exception:
        raise

    # Enable detailed logging to help diagnose client handshake/version issues
    socketio = SocketIO(
        app,
        cors_allowed_origins='*',
        logger=True,
        engineio_logger=True,
        async_mode='eventlet',
        path='/socket.io',
    )

    def _bg_loop():
        last_payload = None
        last_status_payload = None
        last_progress = None
        last_progress_emit = 0.0
        # Adaptive backoff: start at `poll_interval` and back off up to `max_sleep`
        # when no changes are observed to reduce DB load.
        max_sleep = max(10.0, poll_interval * 5.0)
        current_sleep = float(poll_interval)
        while True:
            try:
                # Compute run-scoped counts from the DB so the emitter always
                # reports per-run values (prevents flashing between DB totals
                # and run-scoped totals). "Queued Galleries" is defined as the
                # number of galleries in the currently running download job.
                payload = None
                try:
                    # Find any running download jobs and collect their IDs.
                    jobs = scraperapi.DB.DownloadQueue.list(statuses=["running"]) or []
                except Exception as e:
                    jobs = []
                    try:
                        scraperapi.logger.debug(f"[SocketEmitter] failed to list running jobs: {e}")
                    except Exception:
                        pass

                # Merge IDs from all running jobs (union) so counts reflect every
                # active download rather than only the first job.
                run_ids = []
                if jobs:
                    try:
                        for job in jobs:
                            try:
                                ids = job.get('ids') or []
                                for i in ids:
                                    try:
                                        run_ids.append(int(i))
                                    except Exception:
                                        continue
                            except Exception:
                                continue
                    except Exception as e:
                        run_ids = []
                        try:
                            scraperapi.logger.debug(f"[SocketEmitter] error merging job ids: {e}")
                        except Exception:
                            pass
                # Deduplicate and produce a stable ordering
                run_ids = sorted(set(run_ids))
                try:
                    scraperapi.logger.debug(f"[SocketEmitter] running_jobs={len(jobs)} merged_run_ids_count={len(run_ids)} sample_ids={run_ids[:10]}")
                except Exception:
                    pass

                # If there are running jobs, capture the initial queued total
                # from DownloadQueue (sum of ids across all running jobs) and
                # keep it in-memory as `_last_total`. This value will be used
                # as the denominator for the dynamic bar so it doesn't flash
                # to 0 after the DownloadQueue is emptied.
                try:
                    if jobs:
                        queued_total_from_queue = 0
                        for job in jobs:
                            try:
                                queued_total_from_queue += len(job.get('ids') or [])
                            except Exception:
                                continue
                        if queued_total_from_queue and queued_total_from_queue > 0:
                            # Only update when we observe a positive queued total.
                            try:
                                # Ensure we update the module-level tracker
                                global _last_total
                                _last_total = int(queued_total_from_queue)
                            except Exception:
                                pass
                except Exception:
                    pass

                # Compute counts from Galleries table for the run_ids
                counts = {"total": 0, "started": 0, "completed": 0, "failed": 0, "skipped": 0}
                computed_total = 0
                if run_ids:
                    try:
                        rows = scraperapi.DB.fetch_rows_as_dicts("Galleries", columns=["id", "status"]) or []
                    except Exception:
                        rows = []
                    idset = set(int(x) for x in run_ids)
                    for row in rows:
                        try:
                            gid = int(row.get('id'))
                        except Exception:
                            continue
                        if gid not in idset:
                            continue
                        computed_total += 1
                        status = str(row.get('status') or '').strip().lower()
                        if status in counts:
                            counts[status] += 1
                    # Use the remembered `_last_total` when present; otherwise
                    # fall back to the computed total from DB rows.
                    try:
                        counts['total'] = int(_last_total) if _last_total and _last_total > 0 else int(computed_total)
                    except Exception:
                        counts['total'] = int(computed_total)

                else:
                    # No running jobs: preserve last known total so UI shows
                    # Completed:X/<last_total> instead of Completed:X/0
                    try:
                        if _last_total and _last_total > 0:
                            counts['total'] = int(_last_total)
                    except Exception:
                        pass

                # Live runtime progress (page counts, speeds) - attempt fetch but
                # don't make it mandatory for emitting counts.
                try:
                    # Call fetch with explicit None for port/token so the function
                    # does not treat the timeout value as the port (bugfix).
                    progress = scraperapi.RuntimeProgress.fetch(None, None, timeout_seconds=0.35) or {}
                except Exception:
                    progress = {}

                # Only consider counts and queue_total as the stable status
                # used to refresh the dynamic bar. Progress is transient and
                # can change frequently (causing flashes), so emit it on a
                # separate event at a lower rate.
                status_payload = {
                    'counts': counts,
                    'queue_total': len(run_ids),
                }

                # Emit status only when counts/queue_total change.
                try:
                    if status_payload != last_status_payload:
                        try:
                            scraperapi.logger.debug(f"[SocketEmitter] emitting status counts={status_payload.get('counts')} queue_total={status_payload.get('queue_total')} connected_sids={list(_scraper_namespace_sids)}")
                        except Exception:
                            pass
                        socketio.emit('scraper:status', status_payload, namespace='/scraper')
                        last_status_payload = status_payload
                        current_sleep = float(poll_interval)
                    else:
                        current_sleep = min(max_sleep, current_sleep * 1.5)
                except Exception as e:
                    try:
                        scraperapi.logger.debug(f"[SocketEmitter] status emit failed: {e}")
                    except Exception:
                        pass

                # Emit progress separately at most once per second and only
                # when it changes to avoid flooding/updating the UI too often.
                try:
                    now_t = time.time()
                    if progress and progress != last_progress and (now_t - last_progress_emit) >= 1.0:
                        try:
                            scraperapi.logger.debug(f"[SocketEmitter] emitting progress connected_sids={list(_scraper_namespace_sids)}")
                            socketio.emit('scraper:progress', progress, namespace='/scraper')
                            last_progress = progress
                            last_progress_emit = now_t
                        except Exception:
                            pass
                except Exception:
                    pass
                else:
                    current_sleep = min(max_sleep, current_sleep * 1.5)
            except Exception:
                # swallow background errors and retry
                current_sleep = min(max_sleep, current_sleep * 1.5)
            time.sleep(current_sleep)

    # Ensure the '/scraper' namespace accepts connections by providing
    # lightweight connect/disconnect handlers. Without a registered
    # handler the server may reject namespace connect attempts with
    # CONNECT_ERROR ('Unable to connect'). Register handlers before
    # starting the emitter thread and returning the socketio instance.
    try:
        from flask import request as _fl_request

        @socketio.on('connect', namespace='/scraper')
        def _scraper_connect():
            try:
                sid = getattr(_fl_request, 'sid', None)
                if sid:
                    _scraper_namespace_sids.add(sid)
                scraperapi.logger.debug(f'[SocketIO] client connected to /scraper (sid={sid})')
            except Exception:
                pass
            # Allow connection
            return True

        @socketio.on('disconnect', namespace='/scraper')
        def _scraper_disconnect():
            try:
                sid = getattr(_fl_request, 'sid', None)
                if sid and sid in _scraper_namespace_sids:
                    _scraper_namespace_sids.discard(sid)
                scraperapi.logger.debug(f'[SocketIO] client disconnected from /scraper (sid={sid})')
            except Exception:
                pass
    except Exception:
        # Defensive: if registration fails, continue without crashing.
        pass

    t = threading.Thread(target=_bg_loop, daemon=True, name='socketio-scraper-emitter')
    t.start()

    return socketio


def get_last_total() -> int:
    """Return the last remembered positive queued total, or 0."""
    try:
        return int(_last_total) if _last_total and int(_last_total) > 0 else 0
    except Exception:
        return 0