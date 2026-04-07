# Dovetail PoC draft for manga-scraper

Branch target: `feature/dovetail-poc-drafts`

## Goal

Demonstrate sync/async interoperability in manga-scraper without rewriting the scraper architecture.

## Existing model

- Ordered per-gallery stages are required.
- Parallelism is valid across galleries.
- Parallelism is valid across images within a gallery after metadata is prepared.

## Proposed pipeline contract

Per gallery:
1. `fetch_metadata`
2. `clean_metadata`
3. `persist_and_plan`
4. `download_images`
5. `post_process_and_finalize`

Constraint:
- A gallery must execute stages in order.
- Multiple galleries can execute concurrently.

## Minimal PoC scope

1. Add a tiny async coordinator layer (new module) that wraps existing sync functions.
2. Use Dovetail `to_thread` at stage boundaries.
3. Keep all existing downloader and API logic intact.
4. Add one CLI/dev flag to switch PoC mode on for testing.

## Suggested touchpoints

- `mangascraper/core/downloader.py`
- `mangascraper/core/api/_fetch.py`
- `mangascraper/core/orchestrator.py`

## High-level pseudocode

```python
# async coordinator
async def run_gallery_pipeline(gallery_id):
    meta = await d.task.to_thread(fetch_metadata_sync, gallery_id)
    cleaned = await d.task.to_thread(clean_metadata_sync, meta)
    plan = await d.task.to_thread(persist_and_plan_sync, gallery_id, cleaned)
    await run_images_with_limits(plan)
    await d.task.to_thread(finalize_sync, gallery_id)

async def run_batch(gallery_ids, max_gallery_workers):
    sem = asyncio.Semaphore(max_gallery_workers)

    async def guarded(gid):
        async with sem:
            return await run_gallery_pipeline(gid)

    await asyncio.gather(*(guarded(gid) for gid in gallery_ids), return_exceptions=True)
```

## Why this is a good PoC

- Shows dovetail value without large migration risk.
- Preserves current threading model while enabling async orchestration.
- Makes extension and dashboard async integration easier later.

## Validation checklist

- Existing non-PoC mode still works unchanged.
- Per-gallery stage order is preserved.
- Throughput is at least parity with current implementation for moderate runs.
- Graceful shutdown still works (Ctrl+C and process stop).
- Logs preserve gallery stage progression.