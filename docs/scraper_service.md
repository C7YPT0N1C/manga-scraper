ScraperService — Overview and migration notes

Purpose
- Centralise scraper-related business logic used by the dashboard and background emitter.
- Provide a small, stable API for status, queue management, process control, search, and configuration.

Key methods
- `get_default_service()` — singleton factory for the application service.
- `get_status(started_after=None, gallery_ids=None, include_progress=True, running=False)` — returns `{counts, queue_total, progress}` (no process-level PID/args).
- `get_counts(started_after=None, gallery_ids=None)` — safe DB-backed counts by status.
- `queue_total()` — number of queued galleries (cache + DB downloads).
- `search(payload)` — runs the dashboard gallery search and returns `{message, cache_key, ids, summary, results}`.
- `get_queue()` — returns `{ids, summary, queue, downloads}` suitable for `/queue` GET.
- `add_to_queue(ids)` — add IDs to queued cache and return resulting queue.
- `remove_from_queue(ids)` — remove IDs from queued cache and return resulting queue.
- `clear_queue()` — clear queued cache.
- `start_queue()` — enqueue current queued IDs and attempt to start next download; returns `{enqueued, started, downloads}`.
- `start_process(cli_args)` / `stop_process()` — start/stop scraper subprocess (returning `(ok, None)` or `(None, (err, status))`).
- `get_config()` / `update_config(payload)` — read/update orchestrator-backed config values.

Migration recommendations
- Prefer calling `get_default_service()` directly from routes and background emitters instead of duplicating DB or runtime logic in multiple places.
- Keep route-level compatibility: routes can continue to mirror service-held process state into module-level globals when needed for legacy callers.
- For background emitters, call `service.get_status()` directly rather than invoking the `/status` route to avoid creating Flask app contexts and to reduce overhead.

Notes and caveats
- `get_status()` intentionally omits process-level fields (`pid`, `args`, `started_at`) — the route should provide those runtime values from its process tracking context.
- The service uses `mangascraper.core.api.api` helpers (`DB`, `Fetch`, `Cache`, `Get`, `RuntimeProgress`) and `mangascraper.core.orchestrator` for config reads/updates.
- Background emitters should implement a small backoff strategy to avoid repeatedly reading the DB when nothing changes (the emitter in `dashboard_utils/socketio_.py` uses an adaptive backoff).

Next steps
- Continue migrating remaining endpoints that reference `scraperapi` directly to the service helpers incrementally (search, queue, config done).
- Document any subtle differences in return payload shapes and keep JSON contracts stable for the frontend.
- Add unit tests later if desired to validate `ScraperService` behaviours.
