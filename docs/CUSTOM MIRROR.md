**Context:**

I have a manga scraper project (`manga-scraper`, branch `rewrite/nhentai-api`). I'm replacing nhentai as the content source with a self-hosted mirror API. The mirror API runs at an internal URL (e.g. `http://scraper.anthrosys.home:8001`).

**What needs to change:**

**1. New config variable**

Add `MIRROR_API` to the env/config system (wherever `NHENTAI_API` and similar are defined in `orchestrator.py`). Example: `MIRROR_API=http://scraper.anthrosys.home:8001`

**2. Replace `Fetch.gallery_metadata(gallery_id)`** in `mangascraper/core/api/_fetch.py`

Instead of hitting nhentai's API, call:
```
GET {MIRROR_API}/gallery/{gallery_id}/meta
```
- If `200` — parse and return the response JSON as the metadata dict
- If `404` — the gallery isn't mirrored yet; call `POST {MIRROR_API}/gallery/{gallery_id}/fetch`, then poll `GET {MIRROR_API}/gallery/{gallery_id}/status` every 3 seconds until `status == "ready"`, then retry the metadata fetch
- If `202` — already ingesting, poll status the same way
- Respect existing retry/timeout logic where possible

**3. Replace `Fetch.image_urls(meta, page)`** in `mangascraper/core/api/_fetch.py`

Instead of building nhentai CDN URLs from `meta["media_id"]`, call:
```
GET {MIRROR_API}/gallery/{gallery_id}
```
(no `?context` — this is reader mode, returns full-res page URLs)

Return `[pages[page_num - 1]["image"]]` as a single-element list. The existing downloader expects a list of mirror URLs to try in order; we only have one now.

**4. Mirror response shapes for reference:**

`GET /gallery/{id}/meta` returns:
```json
{
  "id": 658956,
  "title": {"english": "...", "japanese": "..."},
  "upload_date": 1782243816,
  "num_pages": 115,
  "num_favorites": 1028,
  "scanlator": "",
  "tags": [{"id": 17249, "type": "language", "name": "translated"}, ...]
}
```

`GET /gallery/{id}` (reader, no context) returns:
```json
{
  "id": 658956,
  "num_pages": 115,
  "pages": [
    {"page": 1, "ext": "jpg", "image": "https://cdn.anthrosys.home/content/hash/{hash}"},
    ...
  ]
}
```

`GET /gallery/{id}?context=cover` returns:
```json
{"id": 658956, "cover": "https://cdn.anthrosys.home/content/hash/{hash}"}
```

`GET /gallery/{id}?context=thumb` returns:
```json
{
  "id": 658956,
  "pages": [
    {"page": 1, "ext": "jpg", "thumb": "https://cdn.anthrosys.home/content/hash/{hash}"},
    ...
  ]
}
```

**5. Pre-warm helper (optional but useful)**

Add a function `Fetch.ensure_mirrored(gallery_ids: list[int])` that iterates a list of gallery IDs, POSTs `/fetch` for any that return 404, then polls `GET /gallery/{id}/status` until all are `ready`. Call this at the start of a batch run before `process_galleries()`.

**What NOT to change:**
- The downloader (`download_manager.py`) — swapping `Fetch.image_urls()` and `Fetch.gallery_metadata()` is enough
- The DB schema, CLI, dashboard, or anything else
- The existing nhentai code paths — keep them, just gate on a config flag (`USE_MIRROR=true`) if you want to keep nhentai as a fallback

**ONLY touch `_fetch.py` and `orchestrator.py`. Everything else stays the same unless explicitly stated.**