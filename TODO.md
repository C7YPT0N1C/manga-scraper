# nhentai: Rate limits, CDN usage, and scraper changes

This document summarises the recommendations, rationale and concrete
implementation tasks to make the scraper compliant with nhentai's API/CDN
rules, avoid temporary bans, and optionally support authenticated higher
limits and archive downloads.

---

## Summary (short)
- Walking the CDN at high concurrency risks temporary bans and 429s.
- Use the nhentai API flows when target domain is `nhentai.net`: fetch
  gallery detail and CDN servers, use the exact paths returned, and avoid
  guessing file names.
- Support optional API key authentication (`Authorization: Key <key>`) to
  keep previous, more generous rate limits and to use the `POST
  /api/v2/galleries/{id}/download` archive endpoint.

---

## A — Rate limits: assessment & mitigation

1. Can we tell if we exceed limits?
   - Add logging/metrics to capture per-domain requests/min and HTTP 429
     responses. The API includes rate-limit headers on 429; capture them.
   - Increment a counter (e.g. `events.inc_stat("nhentai_429")`) when a
     429 is observed.

2. Behaviour likely to trigger limits
   - Sustained high-concurrency downloads of many per-page CDN images.
   - Repeated requests to invalid CDN URL patterns (constructed/guessed
     paths).

3. Dynamic sleep tuning
   - Keep the existing dynamic sleep for other sites.
   - When domain == `nhentai.net` and unauthenticated, be conservative:
     - Reduce concurrent thumbnail/image fetches (cap e.g. 4–8).
     - Increase `scaled_sleep` and jitter for `gallery`/`image` stages.
   - Back off immediately on 429: honour `Retry-After` or use exponential
     backoff if header absent.

4. Authentication for higher limits
   - Implement optional API-key config and send `Authorization: Key <key>`
     on API calls.
   - When API key present, use authenticated rate buckets and enable the
     archive endpoint for full-gallery downloads.

---

## B — CDN guidance & scraper changes

Key points to follow from the docs:
- Fetch the CDN servers via `GET /api/v2/cdn` and concatenate a server
  with the returned path exactly.
- Do not guess or alter returned paths; incorrect patterns will be
  silently rejected and may cause bans.
- Use `POST /api/v2/galleries/{id}/download` (authenticated) for full
  gallery archives.

Practical changes (minimal disruption to other sites):

1. Add an nhentai adapter
   - Add a small module (e.g. `mangascraper/extensions/nhentai_adapter.py`) to
     handle nhentai-specific flows. The main scraper uses domain detection
     to route requests to the adapter for `nhentai.net` and otherwise
     retains current logic.

2. Fetch gallery detail & cdn servers
   - Use `GET /api/v2/galleries/{id}` for page list and media paths.
   - Use `GET /api/v2/cdn` to obtain servers and cache for a short TTL
     (10–60 minutes). Build media URLs by concatenating a server + the
     returned path exactly.

3. Archive downloads (authenticated)
   - If `api_key` present, call `POST /api/v2/galleries/{id}/download` to
     obtain a short-lived signed URL and download the archive stream.
   - Respect issuance limits (5 per 5 minutes per user) and per-stream
     throttles (MB/s). Queue or rate-limit archive downloads accordingly.

4. Throttling & token buckets
   - Extend `Sleep.TokenBucket` logic with site/group-specific buckets for
     `nhentai` groups (e.g. `gallery_detail`, `galleries_list`, `cdn_images`).
   - Choose conservative anonymous rates; use more generous buckets when
     `api_key` is configured.

5. 429 handling
   - On 429 responses: look for `Retry-After` and rate-limit headers,
     then sleep accordingly. Treat 429 as authoritative backoff signal
     and pause or reduce concurrency for that session.

6. Avoid invalid CDN patterns
   - Never construct CDN paths for nhentai by guessing extensions or
     numbering. For other sites, keep current behaviour but isolate it
     behind adapters.

7. Observability & User-Agent
   - Add config for a descriptive `User-Agent` (e.g. `MyScraper/1.0
     (https://repo.url)`).
   - Do not log API keys. Store keys in env vars or a restricted config.

---

## Other concerns & improvements

- Cache `GET /api/v2/cdn` and gallery detail responses for short TTL to
  reduce pressure on the API.
- Track per-domain/request-type metrics in the dashboard: requests/min,
  429 counts, concurrent downloads.
- Consider per-stream rate limiting when downloading archives to respect
  MB/s throttles.
- If you need wide-scale archival/preservation, contact nhentai with a
  descriptive User-Agent and expected rate so they can consider raising
  limits for legitimate use.

---

## Concrete implementation tasks (pick any or all)

1. Add `nhentai` adapter and domain switch in the scraper.
2. Add optional `api_key` config and include `Authorization: Key <key>` for
   nhentai API calls; add `User-Agent` config.
3. Implement `POST /api/v2/galleries/{id}/download` archive flow with
   queueing and per-stream throttling.
4. Extend `Sleep.api_wait_for_url()` to use site-specific buckets and
   choose anonymous vs authenticated buckets.
5. Implement 429-aware backoff: parse `Retry-After` and rate-limit headers
   and log/instrument occurrences.
6. Add dashboard counters for nhentai requests and 429s.
7. Add caching for `GET /api/v2/cdn` and gallery detail.

---

## Quick choices

Option A — Implement adapter + API-key + safer nhentai flow now.

Option B — Add monitoring/logging for 429s first, gather telemetry, then
           decide conservative throttling settings.

Recommended: do both incrementally — enable logging first, then add the
adapter + auth+archive flow once you confirm rates/limits.

---

If you want I can implement Option A now (create the adapter, config keys,
429 handling and archive download) or Option B (monitoring only). Reply
with which option to implement and I will start the changes.
