# mangascraper/core/api/_fetch.py

from __future__ import annotations
import time, requests
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import logger, log, log_clarification
from mangascraper.core.api._helpers import Helpers
from mangascraper.core.api._get import Get
from mangascraper.core.api._cache import Cache

####################################################################################################################
# FETCH CLASS
####################################################################################################################

class Fetch:
    """Fetch a resource."""

    @staticmethod
    def latest_gallery_id(timeout: int = 5) -> int | None:
        """Fetch the latest gallery ID directly from nhentai homepage API."""
        orchestrator.refresh_globals()
        try:
            log_clarification("debug")
            log("Fetching latest gallery ID from nhentai homepage...", "debug")

            session = Get.session(referrer="Latest ID Fetch", status="return")
            url = f"{orchestrator.nhentai_api_base}/galleries/all?page=1"

            resp = session.get(url, timeout=(timeout, timeout))
            resp.raise_for_status()
            data = resp.json()

            results = data.get("result", []) if isinstance(data, dict) else []
            if results:
                latest_id = Helpers.normalise_integer(results[0].get("id"))
                if latest_id is not None:
                    log_clarification("debug")
                    log(f"Latest gallery ID fetched: {latest_id}", "debug")
                    return latest_id
        except Exception as e:
            log_clarification("debug")
            logger.warning(f"Could not fetch latest gallery ID: {e}")
        return None

    @staticmethod
    def gallery_ids(
        query_type: str,
        query_value: str,
        sort_value: str = None,
        start_page: int | None = None,
        end_page: int | None = None,
        file_used: bool = False,
        fetch_as_archival: bool = None,
    ) -> tuple[str | None, list[int]]:
        """
        Fetches Gallery IDs. Tries cache key(s) first, then falls back to API if needed.
        Returns a tuple (cache_key, list of IDs).
        """
        orchestrator.refresh_globals()

        from mangascraper.core.api._build import Build

        # Fall back to orchestrator defaults if not supplied
        if sort_value is None:
            sort_value = orchestrator.DEFAULT_PAGE_SORT
        if fetch_as_archival is None:
            fetch_as_archival = orchestrator.DEFAULT_ARCHIVING

        cache_target = query_value
        if query_type == "homepage":
            cache_target = sort_value or orchestrator.DEFAULT_PAGE_SORT

        sort_token_map = {
            "date":           "date",
            "popular-week":   "week",
            "popular-month":  "month",
            "popular":        "popular",
            "popular-today":  "today",
        }
        sort_token = sort_token_map.get(
            Helpers.safe_text(sort_value).strip().lower(),
            Helpers.safe_text(sort_value).strip().lower().replace("-", "_") or "date",
        )

        key_start_page = Helpers.normalise_integer(start_page)
        if key_start_page is None or key_start_page < 1:
            key_start_page = orchestrator.DEFAULT_PAGE_RANGE_START

        key_end_page_num = None if end_page is None else max(key_start_page, Helpers.normalise_integer(end_page) or key_start_page)
        key_end_page = "all" if key_end_page_num is None else str(key_end_page_num)
        cache_modifier = f"{sort_token}_{key_start_page}-{key_end_page}"

        if Helpers.safe_text(cache_target, ""):
            cache_target = f"{cache_target}+{cache_modifier}"
        else:
            cache_target = cache_modifier

        cache_key = Cache.cache_key(query_type, cache_target)

        def _normalise_target_text(value) -> str:
            return Helpers.safe_text(value, "").strip().lower()

        def _normalise_sort_token(value: str) -> str:
            raw = Helpers.safe_text(value, "").strip().lower()
            aliases = {
                "d": "date", "date": "date",
                "p": "popular", "popular": "popular",
                "pw": "week", "week": "week", "popular_week": "week", "popular-week": "week",
                "pm": "month", "month": "month", "popular_month": "month", "popular-month": "month",
                "pt": "today", "today": "today", "popular_today": "today", "popular-today": "today",
            }
            return aliases.get(raw, raw)

        def _parse_cache_target(value: str) -> tuple[str, str, int, int | None] | None:
            import re
            text = Helpers.safe_text(value, "").strip()
            if not text:
                return None
            base = ""
            modifier = text
            if "+" in text:
                left, right = text.rsplit("+", 1)
                base = left
                modifier = right
            match = re.match(r"^(?P<sort>[a-z0-9_-]+)_(?P<start>\d+)-(?P<end>\d+|all)$", modifier.strip().lower())
            if not match:
                return None
            start_val = Helpers.normalise_integer(match.group("start"))
            if start_val is None or start_val < 1:
                return None
            end_raw = match.group("end")
            end_val = None if end_raw == "all" else Helpers.normalise_integer(end_raw)
            if end_val is not None and end_val < start_val:
                return None
            return (
                _normalise_target_text(base),
                _normalise_sort_token(match.group("sort")),
                start_val,
                end_val,
            )

        requested_range = _parse_cache_target(cache_target)

        # 1. Try cache first
        if cache_key:
            references = Cache.Load.cache()
            cache_entry = references.get(cache_key)
            now = time.time()
            if cache_entry:
                expires_at = cache_entry.get("expires_at")
                ids = Helpers.normalise_integer_list(cache_entry.get("ids", []))
                if expires_at is None or expires_at > now:
                    logger.debug(f"[DATABASE] Using cached Gallery IDs for key '{cache_key}' (count: {len(ids)})")
                    return (cache_key, ids)
                else:
                    logger.debug(f"Cache entry for {cache_key} expired (expires_at={expires_at}, now={now}). Will fetch from API.")
            else:
                logger.debug(f"No valid cache entry for {cache_key}. Will fetch from API.")

            # Try superset cache hit
            if requested_range:
                requested_type = Helpers.safe_text(query_type, "").strip().lower()
                requested_base, requested_sort, requested_start, requested_end = requested_range
                best_key = None
                best_ids = []
                best_rank = None

                for candidate_key, candidate_entry in (references or {}).items():
                    if Helpers.safe_text(candidate_key, "") == Helpers.safe_text(cache_key, ""):
                        continue
                    candidate_type = Helpers.safe_text(candidate_entry.get("cache_type"), "").strip().lower()
                    if candidate_type != requested_type:
                        continue
                    candidate_target = Helpers.safe_text(candidate_entry.get("cache_target"), "")
                    parsed_candidate = _parse_cache_target(candidate_target)
                    if not parsed_candidate:
                        continue
                    candidate_base, candidate_sort, candidate_start, candidate_end = parsed_candidate
                    if candidate_base != requested_base or candidate_sort != requested_sort or candidate_start != requested_start:
                        continue
                    covers_requested = False
                    if requested_end is None:
                        covers_requested = candidate_end is None
                    elif candidate_end is None:
                        covers_requested = True
                    elif candidate_end >= requested_end:
                        covers_requested = True
                    if not covers_requested:
                        continue
                    candidate_ids = Helpers.normalise_integer_list(candidate_entry.get("ids", []))
                    if not candidate_ids:
                        continue
                    candidate_rank = float("inf") if candidate_end is None else candidate_end
                    if best_rank is None or candidate_rank < best_rank:
                        best_rank = candidate_rank
                        best_key = candidate_key
                        best_ids = candidate_ids

                if best_key:
                    logger.debug(
                        f"[DATABASE] Using superset cached Gallery IDs for key '{best_key}' to satisfy '{cache_key}' (count: {len(best_ids)})"
                    )
                    return (best_key, best_ids)
        else:
            logger.debug(f"No valid cache entry for {cache_key}. Will fetch from API.")

        # 2. Fetch from API
        max_retries = 2
        attempt = 0
        ids = []
        while attempt < max_retries:
            try:
                orchestrator.refresh_globals()
                qt = query_type.capitalize()
                query_str = f" ' {query_value}'" if query_value else ""
                sort_str = f"'{sort_value}'" if sort_value != "date" else "date"
                if start_page is None:
                    start_page = orchestrator.DEFAULT_PAGE_RANGE_START
                if file_used:
                    if end_page is None:
                        end_page = None
                if fetch_as_archival:
                    log_clarification("debug")
                    log("SWITCHING TO ARCHIVAL MODE", "debug")
                    orchestrator.archiving = True
                    end_page = None
                else:
                    if end_page is None:
                        end_page = orchestrator.DEFAULT_PAGE_RANGE_END

                ids_set = set()
                page = start_page
                gallery_ids_session = Get.session(referrer="API", status="return")

                log_clarification("debug")
                if query_value is None:
                    log(f"Fetching Gallery IDs from NHentai Homepages {start_page} → {end_page or '∞'}")
                else:
                    log(f"Fetching Gallery IDs for {qt} '{query_value}' (pages {start_page} → {end_page or '∞'}), sorted by {sort_str}")

                while True:
                    if end_page is not None and page > end_page:
                        break
                    url = Build.url(qt, query_value, sort_value, page)
                    log(f"Fetcher: Requesting URL: {url}", "debug")
                    resp = None
                    for api_attempt in range(1, orchestrator.max_retries + 1):
                        try:
                            resp = gallery_ids_session.get(url, timeout=(60, 60))
                            if resp.status_code == 429:
                                from mangascraper.core.api._sleep import Sleep
                                wait = Sleep.dynamic("api", attempt=api_attempt)
                                logger.warning(f"{qt}{query_str}, Page {page}: Attempt {api_attempt}: 429 rate limit, waiting {wait:.2f}s")
                                time.sleep(wait)
                                continue
                            if resp.status_code == 403:
                                from mangascraper.core.api._sleep import Sleep
                                wait = Sleep.dynamic("api", attempt=api_attempt)
                                logger.warning(f"{qt}{query_str}, Page {page}: Attempt {api_attempt}: 403 forbidden, retrying in {wait:.2f}s")
                                time.sleep(wait)
                                continue
                            resp.raise_for_status()
                            break
                        except requests.RequestException as e:
                            if api_attempt >= orchestrator.max_retries:
                                log_clarification("debug")
                                logger.warning(f"{qt} {f'{query_value}' if query_value is None else ''}, Page {page}: Failed after {api_attempt} retries: {e}")
                                resp = None
                                if orchestrator.use_tor:
                                    from mangascraper.core.api._sleep import Sleep
                                    wait = Sleep.dynamic("api", attempt=api_attempt) * 2
                                    logger.warning(f"{qt}{query_str}, Page {page}: Retrying with new Tor node in {wait:.2f}s")
                                    time.sleep(wait)
                                    gallery_ids_session = Get.session(referrer="API", status="rebuild")
                                    try:
                                        resp = gallery_ids_session.get(url, timeout=(60, 60))
                                        resp.raise_for_status()
                                    except Exception as e2:
                                        logger.warning(f"{qt}{query_str}, Page {page}: Still failed after Tor rotate: {e2}")
                                        resp = None
                                break
                            from mangascraper.core.api._sleep import Sleep
                            wait = Sleep.dynamic("api", attempt=api_attempt)
                            logger.warning(f"{qt}{query_str}, Page {page}: Attempt {api_attempt}: Request failed: {e}, retrying in {wait:.2f}s")
                            time.sleep(wait)

                    if resp is None:
                        page += 1
                        continue

                    try:
                        data = resp.json()
                    except Exception as e:
                        logger.warning(f"{qt}{query_str}, Page {page}: Failed to decode JSON: {e}")
                        break

                    if not isinstance(data, dict):
                        logger.warning(f"{qt}{query_str}, Page {page}: Unexpected JSON payload type: {type(data).__name__}")
                        break

                    results = data.get("result", [])
                    if not isinstance(results, list):
                        logger.warning(f"{qt}{query_str}, Page {page}: Unexpected result payload type: {type(results).__name__}")
                        break

                    batch = []
                    excluded_gallery_tags = [tag.lower() for tag in orchestrator.excluded_tags]
                    allowed_gallery_language = [lang.lower() for lang in orchestrator.language]

                    def _normalise_term(value: str) -> str:
                        return " ".join(Helpers.safe_text(value).strip().lower().split())

                    query_kind = Helpers.safe_text(query_type).strip().lower()
                    query_exact = _normalise_term(Helpers.safe_text(query_value).strip().strip('"').strip("'"))
                    exact_match_types = {"artist", "group", "tag", "character", "parody"}

                    for g in results:
                        if not isinstance(g, dict):
                            continue
                        gallery_tags = [
                            t["name"].lower()
                            for t in g.get("tags", [])
                            if t.get("type") == "tag"
                        ]
                        gallery_langs = [
                            t["name"].lower()
                            for t in g.get("tags", [])
                            if t.get("type") == "language"
                        ]

                        if query_kind in exact_match_types and query_exact:
                            typed_names = []
                            for t in g.get("tags", []):
                                if not isinstance(t, dict):
                                    continue
                                if Helpers.safe_text(t.get("type")).lower() != query_kind:
                                    continue
                                raw_name = Helpers.safe_text(t.get("name"))
                                for part in raw_name.split("|"):
                                    name = _normalise_term(part)
                                    if name:
                                        typed_names.append(name)
                            compact_exact = query_exact.replace(" ", "")
                            if query_exact not in typed_names and compact_exact not in [n.replace(" ", "") for n in typed_names]:
                                log(
                                    f"Skipping Gallery {g.get('id', '?')} due to exact {query_kind} mismatch: expected '{query_exact}', got {typed_names}",
                                    "debug",
                                )
                                continue

                        blocked_tags = [t for t in gallery_tags if t in excluded_gallery_tags]
                        if blocked_tags:
                            log(f"Skipping Gallery {g['id']} due to excluded tags: {blocked_tags}", "debug")
                            continue

                        if allowed_gallery_language:
                            has_allowed = any(lang in allowed_gallery_language for lang in gallery_langs)
                            has_translated = "translated" in gallery_langs
                            allow_translated = "translated" in allowed_gallery_language
                            if not (has_allowed or (has_translated and allow_translated)):
                                log(f"Skipping Gallery {g['id']} due to blocked languages: {gallery_langs}", "debug")
                                continue

                        gid = Helpers.normalise_integer(g.get("id"))
                        if gid is None:
                            continue
                        batch.append(gid)
                        images = g.get("images", {})
                        num_pages = len(images.get("pages", []))
                        orchestrator.total_gallery_images += num_pages

                    log(f"Fetcher: {qt}{query_str}, Page {page}: Fetched {len(batch)} Gallery IDs", "info")
                    log(f"Current Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")

                    if not results:
                        logger.info(f"Fetcher: {qt}{query_str}, Page {page}: No more results from NHentai, stopping.")
                        break
                    if not batch:
                        logger.debug(f"Fetcher: {qt}{query_str}, Page {page}: All galleries filtered out, continuing to next page.")
                        page += 1
                        continue

                    ids_set.update(batch)
                    page += 1

                log(f"Fetched total {len(ids_set)} Galleries for {qt}{query_str}", "warning")
                log(f"Overall Total Images across All Galleries: {orchestrator.total_gallery_images}", "debug")
                ids = list(sorted(ids_set))
                if cache_key and ids:
                    Cache.Save.cache(cache_key=cache_key, gallery_ids=ids)
                return (cache_key, ids)

            except Exception as e:
                attempt += 1
                logger.error(f"Error fetching galleries (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    logger.info("Retrying...")
                    time.sleep(2)
                    continue
                logger.warning(f"Failed to fetch galleries for {query_type}={query_value}. Skipping.")
                return (None, [])

        return (cache_key, ids)

    @staticmethod
    def image_urls(meta: dict, page: int):
        """
        Returns the full image URL for a gallery page.
        Tries mirrors from nhentai_mirrors in order until one succeeds.
        """
        orchestrator.refresh_globals()
        if not isinstance(meta, dict):
            return None
        page = Helpers.normalise_integer(page)
        if page is None or page < 1:
            return None

        try:
            pages = meta.get("images", {}).get("pages", [])
            if page - 1 >= len(pages):
                logger.warning(f"Gallery {meta.get('id','?')}: Page {page}: Not in metadata")
                return None

            page_info = pages[page - 1]
            if not page_info:
                logger.warning(f"Gallery {meta.get('id','?')}: Page {page}: Metadata is None")
                return None

            ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
            type_code = page_info.get("t", "w")
            if type_code not in ext_map:
                log_clarification()
                logger.warning(
                    f"Unknown image type '{type_code}' for Gallery {meta.get('id','?')}: Page {page}: Defaulting to webp"
                )

            ext = ext_map.get(type_code, "webp")
            filename = f"{page}.{ext}"

            urls = [
                f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
                for mirror in orchestrator.nhentai_mirrors
            ]

            log(f"Fetcher: Built image URLs for Gallery {meta.get('id','?')}: Page {page}: {urls}", "debug")
            return urls

        except Exception as e:
            logger.warning(f"Failed to build image URL for Gallery {meta.get('id','?')}: Page {page}: {e}")
            return None

    @staticmethod
    def gallery_metadata(gallery_id: int):
        orchestrator.refresh_globals()

        gallery_id = Helpers.normalise_integer(gallery_id)
        if gallery_id is None:
            return None

        raw_cache = Cache.Load.cached_metadata()
        cached_meta = raw_cache.get(gallery_id)
        if cached_meta and isinstance(cached_meta, dict):
            return cached_meta

        metadata_session = Get.session(referrer="API", status="return")
        url = f"{orchestrator.nhentai_api_base}/gallery/{gallery_id}"

        for attempt in range(1, orchestrator.max_retries + 1):
            try:
                log_clarification("debug")
                log(f"Fetcher: Fetching metadata for Gallery: {gallery_id}, URL: {url}", "debug")

                resp = metadata_session.get(url, timeout=(60, 60))
                if resp.status_code == 429:
                    from mangascraper.core.api._sleep import Sleep
                    wait = Sleep.dynamic("api", attempt=attempt)
                    logger.warning(f"Gallery: {gallery_id}: Attempt {attempt}: 429 rate limit hit, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code == 403:
                    from mangascraper.core.api._sleep import Sleep
                    wait = Sleep.dynamic("api", attempt=attempt)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                if not isinstance(data, dict):
                    logger.error(f"Unexpected response type for Gallery: {gallery_id}: {type(data)}")
                    return None

                cached_entry = Cache.Save.cache(data, gallery_id)
                if cached_entry:
                    general_metadata = Cache.Load.cached_metadata(clean=True)
                    general_metadata[gallery_id] = cached_entry
                    Cache.Save.cached_metadata(general_metadata, clean=True)
                raw_cache = Cache.Load.cached_metadata()
                raw_cache[gallery_id] = data
                Cache.Save.cached_metadata(raw_cache)

                log_clarification("debug")
                log(f"Fetcher: Fetched metadata for Gallery: {gallery_id}", "debug")
                return data

            except requests.HTTPError as e:
                if "404 Client Error: Not Found for url" in str(e):
                    logger.warning(f"Gallery: {gallery_id}: Not found (404), skipping retries.")
                    return None
                if attempt >= orchestrator.max_retries:
                    logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                    if orchestrator.use_tor:
                        from mangascraper.core.api._sleep import Sleep
                        wait = Sleep.dynamic("api", attempt=attempt) * 2
                        logger.warning(f"Gallery: {gallery_id}: Retrying with new Tor Node in {wait:.2f}s")
                        time.sleep(wait)
                        metadata_session = Get.session(referrer="API", status="rebuild")
                        try:
                            resp = metadata_session.get(url, timeout=(60, 60))
                            resp.raise_for_status()
                            retry_data = resp.json()
                            return retry_data if isinstance(retry_data, dict) else None
                        except Exception as e2:
                            logger.warning(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}")
                    return None
                from mangascraper.core.api._sleep import Sleep
                wait = Sleep.dynamic("api", attempt=attempt)
                logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s")
                time.sleep(wait)

            except requests.RequestException as e:
                if attempt >= orchestrator.max_retries:
                    logger.warning(f"Failed to fetch metadata for Gallery: {gallery_id} after max retries: {e}")
                    if orchestrator.use_tor:
                        from mangascraper.core.api._sleep import Sleep
                        wait = Sleep.dynamic("api", attempt=attempt) * 2
                        logger.warning(f"Gallery: {gallery_id}: Retrying with new Tor Node in {wait:.2f}s")
                        time.sleep(wait)
                        metadata_session = Get.session(referrer="API", status="rebuild")
                        try:
                            resp = metadata_session.get(url, timeout=(60, 60))
                            resp.raise_for_status()
                            retry_data = resp.json()
                            return retry_data if isinstance(retry_data, dict) else None
                        except Exception as e2:
                            logger.warning(f"Gallery: {gallery_id}: Still failed after Tor rotate: {e2}")
                    return None
                from mangascraper.core.api._sleep import Sleep
                wait = Sleep.dynamic("api", attempt=attempt)
                logger.warning(f"Attempt {attempt} failed for Gallery: {gallery_id}: {e}, retrying in {wait:.2f}s")
                time.sleep(wait)

    @staticmethod
    def fetch_metadata_batch(gallery_ids: list) -> dict:
        """
        Fetch metadata for multiple galleries efficiently using threading.
        Used for pre-fetching before filtering/sizing.
        """
        if not gallery_ids:
            return {}

        gallery_ids = Helpers.normalise_integer_list(gallery_ids)
        if not gallery_ids:
            return {}

        metadata = {}
        failed_ids = []
        max_workers = min(10, len(gallery_ids))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(Fetch.gallery_metadata, gid): gid for gid in gallery_ids}
            for future in tqdm(as_completed(futures), total=len(futures), desc="Fetching metadata", unit="gallery"):
                gallery_id = futures[future]
                try:
                    meta = future.result()
                    if meta and isinstance(meta, dict):
                        metadata[gallery_id] = meta
                except Exception as e:
                    logger.debug(f"Failed to fetch metadata for Gallery {gallery_id}: {e}")
                    failed_ids.append(gallery_id)

        if failed_ids:
            logger.warning(f"Failed to fetch metadata for {len(failed_ids)}/{len(gallery_ids)} galleries")

        return metadata

    @staticmethod
    def all_galleries_metadata(gallery_ids: list, cache_key: str = None) -> dict:
        """
        Fetch metadata for all galleries with caching support.
        Uses caching to avoid repeated API calls for the same search criteria.
        """
        if not gallery_ids:
            return {}

        normalised_ids = []
        for gid in gallery_ids:
            try:
                gid_int = int(gid)
            except (TypeError, ValueError):
                logger.warning(f"Skipping gallery with invalid ID: {gid}")
                continue
            normalised_ids.append(gid_int)
        gallery_ids = list(dict.fromkeys(normalised_ids))

        def _is_complete_cached_meta(meta: dict) -> bool:
            if not isinstance(meta, dict):
                return False
            return {"title", "artists", "tags", "languages", "pages"}.issubset(meta.keys())

        cached_metadata = {}
        if cache_key:
            cached_metadata = Cache.Load.cache(cache_key)
        else:
            cached_metadata = Cache.Load.id_metadata(gallery_ids)

        if cached_metadata:
            normalised_cached = {}
            for gid, meta in cached_metadata.items():
                try:
                    gid_int = int(gid)
                except (TypeError, ValueError):
                    continue
                normalised_cached[gid_int] = meta
            cached_metadata = normalised_cached

        if cached_metadata:
            incomplete = [gid for gid, meta in cached_metadata.items() if not _is_complete_cached_meta(meta)]
            if incomplete:
                logger.debug(f"Dropping {len(incomplete)} cached galleries due to incomplete metadata")
            for gid in incomplete:
                cached_metadata.pop(gid, None)
            if incomplete:
                logger.info(f"Refreshing {len(incomplete)} cached galleries with incomplete metadata")

        ids_to_fetch = [gid for gid in gallery_ids if gid not in cached_metadata]
        if cached_metadata:
            logger.info(f"Using {len(cached_metadata)} galleries from cache")

        if not ids_to_fetch:
            logger.info(f"All {len(cached_metadata)} galleries loaded from cache")
            return cached_metadata

        logger.info(f"Fetching metadata for {len(ids_to_fetch)} new galleries...")
        if cache_key:
            logger.info(f"({len(cached_metadata)} Cached galleries, fetching {len(ids_to_fetch)} new)")
        log_clarification()

        metadata = dict(cached_metadata)
        failed_ids = []

        for gallery_id in tqdm(ids_to_fetch, desc="Fetching gallery metadata", unit="gallery"):
            try:
                meta = Fetch.gallery_metadata(gallery_id)
                if meta and isinstance(meta, dict):
                    meta_entry = Cache.Save.cache(meta, gallery_id)
                    if meta_entry:
                        metadata[gallery_id] = meta_entry
                else:
                    failed_ids.append(gallery_id)
            except Exception as e:
                logger.debug(f"Failed to fetch metadata for Gallery {gallery_id}: {e}")
                failed_ids.append(gallery_id)

        if failed_ids:
            logger.warning(f"Failed to fetch metadata for {len(failed_ids)} galleries (they will be skipped)")

        if metadata and cache_key:
            Cache.Save.cache(cache_key, gallery_ids)
        elif metadata:
            general_metadata = Cache.Load.cached_metadata(clean=True)
            general_metadata.update(metadata)
            Cache.Save.cached_metadata(general_metadata, clean=True)

        return metadata