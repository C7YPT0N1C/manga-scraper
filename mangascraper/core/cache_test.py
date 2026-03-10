#!/usr/bin/env python3
# mangascraper/core/cache_test.py

import time
from contextlib import contextmanager

from mangascraper.core import api as scraperapi
from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import DEFAULT_PAGE_SORT, logger

####################################################################################################################
# GLOBAL VARIABLES
####################################################################################################################

# Test inputs
TEST_CACHE_KEY = "test:cache_sanity"
TEST_GALLERY_ID = 598500
TEST_IDS = [598499, 598498, 598497]
TEST_SEARCH_TYPE = "search"
TEST_SEARCH_VALUE = "big ass"
TEST_SEARCH_SORT = DEFAULT_PAGE_SORT
TEST_SEARCH_START = 1
TEST_SEARCH_END = 1
TEST_RUNTIME_ROOT = "/tmp/manga-scraper/"


@contextmanager
def _temporary_test_runtime_paths():
    """
    Temporarily redirect runtime download paths for cache tests.
    If any download-related path is touched during tests, it stays under /tmp/manga-scraper/.
    """

    prev_download = orchestrator.config.get("DOWNLOAD_PATH")
    prev_ext_download = orchestrator.config.get("EXTENSION_DOWNLOAD_PATH")
    prev_runtime_download = getattr(orchestrator, "download_path", "")
    prev_runtime_ext_download = getattr(orchestrator, "extension_download_path", "")
    try:
        orchestrator.config["DOWNLOAD_PATH"] = TEST_RUNTIME_ROOT
        orchestrator.config["EXTENSION_DOWNLOAD_PATH"] = TEST_RUNTIME_ROOT
        orchestrator.refresh_globals()
        yield
    finally:
        orchestrator.config["DOWNLOAD_PATH"] = prev_download
        orchestrator.config["EXTENSION_DOWNLOAD_PATH"] = prev_ext_download
        orchestrator.refresh_globals()
        # Restore runtime values directly as a final guard.
        orchestrator.download_path = prev_runtime_download
        orchestrator.extension_download_path = prev_runtime_ext_download

####################################################################################################################
# MAIN
####################################################################################################################

def main() -> bool:
    """
    Run a cache self-test suite covering core cache read/write flows.
    Returns True when all tests pass, else False.
    """

    passed = 0
    failed = 0
    skipped = 0

    def _report(name: str, ok: bool | None, details: str = ""):
        nonlocal passed, failed, skipped
        if ok is True:
            status = "PASS"
        elif ok is False:
            status = "FAIL"
        else:
            status = "SKIP"
        line = f"[CACHE TEST] {status}: {name}"
        if details:
            line = f"{line} | {details}"

        # Terminal output
        print(line)
        if ok:
            logger.info(line)
        else:
            logger.warning(line)

        # Log file output (debug)
        logger.debug(line)

        if ok is True:
            passed += 1
        elif ok is False:
            failed += 1
        else:
            skipped += 1

    with _temporary_test_runtime_paths():
        scraperapi.init_db()

        # 0) Runtime path safety assertion
        try:
            _report(
                "test runtime paths set to /tmp/manga-scraper/",
                (
                    str(getattr(orchestrator, "download_path", "")).startswith(TEST_RUNTIME_ROOT)
                    and str(getattr(orchestrator, "extension_download_path", "")).startswith(TEST_RUNTIME_ROOT)
                ),
                (
                    f"download_path={getattr(orchestrator, 'download_path', None)} | "
                    f"extension_download_path={getattr(orchestrator, 'extension_download_path', None)}"
                ),
            )
        except Exception as e:
            _report("test runtime paths set to /tmp/manga-scraper/", False, f"exception={e}")

        # 0.1) Pre-download path generation safety (computed path must stay under test runtime root)
        try:
            path_gid = TEST_GALLERY_ID + 10
            scraperapi.mark_gallery_started(path_gid, TEST_RUNTIME_ROOT, "skeleton")
            scraperapi.upsert_cached_metadata(
                path_gid,
                time.time(),
                clean_metadata={
                    "id": path_gid,
                    "clean_title": "Cache Test Path Guard",
                    "artists": ["Path Tester"],
                    "groups": [],
                    "tags": [],
                    "languages": ["english"],
                    "pages": 1,
                    "is_archive": True,
                    "archive_ext": "cbz",
                    "extension_download_path": TEST_RUNTIME_ROOT,
                    "extension_used": "skeleton",
                },
                raw_metadata={"id": path_gid, "title": {"english": "Cache Test Path Guard"}},
            )
            scraperapi.mark_gallery_completed(path_gid)

            with scraperapi.lock, scraperapi.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT download_path FROM Galleries WHERE id = ?", (path_gid,))
                row = cursor.fetchone()

            final_path = str(row[0]) if row and row[0] is not None else ""
            ok = final_path.startswith(TEST_RUNTIME_ROOT)
            _report("computed gallery output path stays under /tmp/manga-scraper/", ok, f"download_path={final_path}")
        except Exception as e:
            _report("computed gallery output path stays under /tmp/manga-scraper/", False, f"exception={e}")

        # 1) Read all references shape
        try:
            refs = scraperapi.read_cached_metadata_entry()
            ok = isinstance(refs, dict)
            _report("read_cached_metadata_entry(all references)", ok, f"type={type(refs).__name__}")
        except Exception as e:
            _report("read_cached_metadata_entry(all references)", False, f"exception={e}")

        # 2) Upsert/read CacheReferences
        try:
            now = time.time()
            scraperapi.upsert_cache_reference(
                TEST_CACHE_KEY,
                {
                    "cache_key": TEST_CACHE_KEY,
                    "cache_type": "test",
                    "cache_target": "cache_sanity",
                    "ids": TEST_IDS,
                    "expires_at": now + 300,
                },
            )
            entry = scraperapi.read_cached_metadata_entry(cache_key=TEST_CACHE_KEY)
            ok = (
                isinstance(entry, dict)
                and isinstance(entry.get("ids"), list)
                and isinstance(entry.get("expires_at"), float)
                and all(isinstance(gid, int) for gid in entry.get("ids", []))
            )
            _report("upsert_cache_reference + read_cached_metadata_entry(cache_key)", ok, f"entry={entry}")
        except Exception as e:
            _report("upsert_cache_reference + read_cached_metadata_entry(cache_key)", False, f"exception={e}")

        # 3) Ensure invalid IDs are dropped from cache reference IDs
        try:
            mixed_key = "test:cache_mixed_ids"
            now = time.time()
            scraperapi.upsert_cache_reference(
                mixed_key,
                {
                    "cache_key": mixed_key,
                    "cache_type": "test",
                    "cache_target": "mixed_ids",
                    "ids": ["1", "bad", None, 2, 2],
                    "expires_at": now + 300,
                },
            )
            mixed = scraperapi.Caching.Load.cache(cache_key=mixed_key)
            ok = isinstance(mixed, list) and all(isinstance(gid, int) for gid in mixed)
            _report("Caching.Load.cache normalises mixed IDs", ok, f"ids={mixed}")
        except Exception as e:
            _report("Caching.Load.cache normalises mixed IDs", False, f"exception={e}")

        # 4) Upsert/read CachedMetadata by ID
        try:
            raw_meta = {"id": TEST_GALLERY_ID, "title": {"english": "Cache Test Gallery"}}
            clean_meta = {"id": TEST_GALLERY_ID, "title": "Cache Test Gallery", "pages": 1}
            scraperapi.upsert_cached_metadata(
                TEST_GALLERY_ID,
                time.time(),
                clean_metadata=clean_meta,
                raw_metadata=raw_meta,
            )
            entry = scraperapi.read_cached_metadata_entry(gallery_id=TEST_GALLERY_ID)
            ok = (
                isinstance(entry, dict)
                and isinstance(entry.get("timestamp"), float)
                and isinstance(entry.get("expires_at"), float)
                and isinstance(entry.get("clean_metadata"), dict)
                and isinstance(entry.get("raw_metadata"), dict)
            )
            _report(
                "upsert_cached_metadata + read_cached_metadata_entry(gallery_id)",
                ok,
                f"entry_keys={list(entry.keys()) if isinstance(entry, dict) else None}",
            )
        except Exception as e:
            _report("upsert_cached_metadata + read_cached_metadata_entry(gallery_id)", False, f"exception={e}")

        # 5) Read CachedMetadata by IDs list
        try:
            batch = scraperapi.read_cached_metadata_entry(ids=[TEST_GALLERY_ID])
            ok = isinstance(batch, dict) and TEST_GALLERY_ID in batch
            _report("read_cached_metadata_entry(ids=[...])", ok, f"keys={list(batch.keys()) if isinstance(batch, dict) else None}")
        except Exception as e:
            _report("read_cached_metadata_entry(ids=[...])", False, f"exception={e}")

        # 6) Read CachedMetadata by cutoff
        try:
            old_ts = time.time() - 10_000
            new_ts = time.time()
            old_gid = TEST_GALLERY_ID + 1
            new_gid = TEST_GALLERY_ID + 2
            scraperapi.upsert_cached_metadata(old_gid, old_ts, clean_metadata={"id": old_gid}, raw_metadata={"id": old_gid})
            scraperapi.upsert_cached_metadata(new_gid, new_ts, clean_metadata={"id": new_gid}, raw_metadata={"id": new_gid})
            cutoff = time.time() - 60
            recent = scraperapi.read_cached_metadata_entry(cutoff=cutoff)
            ok = isinstance(recent, dict) and new_gid in recent and old_gid not in recent
            _report("read_cached_metadata_entry(cutoff=...)", ok, f"recent_keys={list(recent.keys())[:5] if isinstance(recent, dict) else None}")
        except Exception as e:
            _report("read_cached_metadata_entry(cutoff=...)", False, f"exception={e}")

        # 7) Caching.Load/Save explicit cache refs
        try:
            scraperapi.Caching.Save.cache(cache_key=TEST_CACHE_KEY, gallery_ids=TEST_IDS)
            loaded_ids = scraperapi.Caching.Load.cache(cache_key=TEST_CACHE_KEY)
            ok = isinstance(loaded_ids, list) and all(isinstance(gid, int) for gid in loaded_ids)
            _report("Caching.Save.cache(cache_key, gallery_ids) + Caching.Load.cache(cache_key)", ok, f"loaded_ids={loaded_ids}")
        except Exception as e:
            _report("Caching.Save.cache(cache_key, gallery_ids) + Caching.Load.cache(cache_key)", False, f"exception={e}")

        # 8) Backward-compatible cache(meta, gallery_id) call shape
        try:
            compat_meta = {"id": TEST_GALLERY_ID + 3, "title": {"english": "Compat Title"}, "tags": [], "images": {"pages": []}}
            entry = scraperapi.Caching.Save.cache(compat_meta, TEST_GALLERY_ID + 3)
            loaded = scraperapi.Caching.Load.cache(gallery_id=TEST_GALLERY_ID + 3)
            ok = isinstance(entry, dict) and isinstance(loaded, dict)
            _report("Caching.Save.cache(meta, gallery_id) compatibility", ok, f"loaded_keys={list(loaded.keys()) if isinstance(loaded, dict) else None}")
        except Exception as e:
            _report("Caching.Save.cache(meta, gallery_id) compatibility", False, f"exception={e}")

        # 9) Caching.Load.id_metadata
        try:
            md = scraperapi.Caching.Load.id_metadata([TEST_GALLERY_ID])
            ok = isinstance(md, dict)
            _report("Caching.Load.id_metadata([...])", ok, f"keys={list(md.keys()) if isinstance(md, dict) else None}")
        except Exception as e:
            _report("Caching.Load.id_metadata([...])", False, f"exception={e}")

        # 10) Caching.Load.cached_metadata (raw + clean)
        try:
            raw_block = scraperapi.Caching.Load.cached_metadata(clean=False)
            clean_block = scraperapi.Caching.Load.cached_metadata(clean=True)
            ok = isinstance(raw_block, dict) and isinstance(clean_block, dict)
            _report("Caching.Load.cached_metadata(clean=False/True)", ok, f"raw={len(raw_block)} clean={len(clean_block)}")
        except Exception as e:
            _report("Caching.Load.cached_metadata(clean=False/True)", False, f"exception={e}")

        # 11) prune_all_caches removes expired references
        try:
            expired_key = "test:cache_expired"
            scraperapi.upsert_cache_reference(
                expired_key,
                {
                    "cache_key": expired_key,
                    "cache_type": "test",
                    "cache_target": "expired",
                    "ids": [123],
                    "expires_at": time.time() - 1,
                },
            )
            scraperapi.prune_all_caches()
            expired = scraperapi.read_cached_metadata_entry(cache_key=expired_key)
            ok = expired is None
            _report("prune_all_caches removes expired refs", ok, f"expired_entry={expired}")
        except Exception as e:
            _report("prune_all_caches removes expired refs", False, f"exception={e}")

        # 11.1) prune_all_caches removes expired metadata
        try:
            expired_gid = TEST_GALLERY_ID + 11
            now = time.time()
            scraperapi.upsert_cached_metadata(
                expired_gid,
                now,
                clean_metadata={"id": expired_gid, "title": "Expired Metadata"},
                raw_metadata={"id": expired_gid},
            )
            with scraperapi.lock, scraperapi.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE CachedMetadata SET expires_at = ? WHERE gallery_id = ?",
                    (time.time() - 1, expired_gid),
                )
                conn.commit()

            scraperapi.prune_all_caches()
            expired_md = scraperapi.read_cached_metadata_entry(gallery_id=expired_gid)
            ok = expired_md is None
            _report("prune_all_caches removes expired metadata", ok, f"expired_metadata={expired_md}")
        except Exception as e:
            _report("prune_all_caches removes expired metadata", False, f"exception={e}")

        # 12) Integration fetch test (small network sanity; no downloader invoked)
        try:
            fetched_cache_key, fetched_ids = scraperapi.Fetch.gallery_ids(
                TEST_SEARCH_TYPE,
                TEST_SEARCH_VALUE,
                TEST_SEARCH_SORT,
                TEST_SEARCH_START,
                TEST_SEARCH_END,
                fetch_as_archival=False,
            )
            entry = scraperapi.read_cached_metadata_entry(cache_key=fetched_cache_key) if fetched_cache_key else None
            ok = (
                fetched_cache_key is not None
                and isinstance(fetched_ids, list)
                and (entry is None or isinstance(entry, dict))
            )
            _report(
                "Fetch.gallery_ids integration -> CacheReferences",
                ok,
                f"cache_key={fetched_cache_key} ids={len(fetched_ids) if isinstance(fetched_ids, list) else 'n/a'}",
            )
        except Exception as e:
            _report("Fetch.gallery_ids integration -> CacheReferences", False, f"exception={e}")

    summary = f"[CACHE TEST] COMPLETE: passed={passed}, failed={failed}, skipped={skipped}"
    print(summary)
    logger.info(summary)
    logger.debug(summary)

    return failed == 0