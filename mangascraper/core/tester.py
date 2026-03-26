#!/usr/bin/env python3
# mangascraper/core/tester.py

import os
import time
import uuid
from contextlib import contextmanager

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import DEFAULT_PAGE_SORT, logger
from mangascraper.core.api import api as scraperapi
from mangascraper.core import downloader as scraper_downloader

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
TEST_RUNTIME_ROOT = f"{orchestrator.TEMP_DIR}/"

TEST_GALLERY_IDS = {
    TEST_GALLERY_ID,
    TEST_GALLERY_ID + 1,
    TEST_GALLERY_ID + 2,
    TEST_GALLERY_ID + 3,
    TEST_GALLERY_ID + 4,   # gallery status: started
    TEST_GALLERY_ID + 5,   # gallery status: skipped
    TEST_GALLERY_ID + 6,   # gallery status: failed
    TEST_GALLERY_ID + 7,   # clear_cached_items gallery_id test
    TEST_GALLERY_ID + 10,
    TEST_GALLERY_ID + 11,
}
TEST_CACHE_KEYS = {
    TEST_CACHE_KEY,
    "test:cache_mixed_ids",
    "test:cache_expired",
    "test:clear_ck_test",
    "search:ass_big",
}
TEST_DOWNLOAD_ROOTS = {
    f"{TEST_RUNTIME_ROOT}root-a",
    f"{TEST_RUNTIME_ROOT}root-a/downloads",
}
TEST_DB_STALE_SECONDS = 24 * 60 * 60


def _cleanup_stale_test_databases(test_data_dir: str):
    """
    Remove stale self-test DB files left behind by previous crashed runs.
    """
    now = time.time()
    try:
        if not os.path.isdir(test_data_dir):
            return
        for entry in os.scandir(test_data_dir):
            if not entry.is_file():
                continue
            name = entry.name
            if not (name.startswith("mangascraper-selftest-") and name.endswith(".db")):
                continue
            try:
                age_seconds = now - entry.stat().st_mtime
                if age_seconds >= TEST_DB_STALE_SECONDS:
                    os.remove(entry.path)
            except Exception:
                continue
    except Exception:
        pass


@contextmanager
def _temporary_test_runtime_paths():
    f"""
    Temporarily redirect runtime download paths for cache tests.
    If any download-related path is touched during tests, it stays under {TEST_RUNTIME_ROOT}/.
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


@contextmanager
def _temporary_test_database():
    """
    Temporarily redirect API DB globals to an isolated test database file.
    """

    prev_data_dir = scraperapi.DATA_DIR
    prev_db_path = scraperapi.DB_PATH
    test_data_dir = os.path.join(orchestrator.TEMP_DIR, "selftest-db")
    test_db_path = os.path.join(test_data_dir, f"mangascraper-selftest-{uuid.uuid4().hex}.db")

    try:
        os.makedirs(test_data_dir, exist_ok=True)
        _cleanup_stale_test_databases(test_data_dir)
        # Close any existing connection bound to the normal DB before switching.
        scraperapi.DB.close_connection()
        scraperapi.DATA_DIR = test_data_dir
        scraperapi.DB_PATH = test_db_path
        yield
    finally:
        # Ensure the temporary connection is closed before restoring globals.
        scraperapi.DB.close_connection()
        scraperapi.DATA_DIR = prev_data_dir
        scraperapi.DB_PATH = prev_db_path
        # Best-effort cleanup; if a crash occurs this file remains isolated in TEMP.
        try:
            if os.path.exists(test_db_path):
                os.remove(test_db_path)
        except Exception:
            pass

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
        line = f"[SELF-TEST] {status}: {name}"
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

    def _cleanup_test_data():
        scraperapi.DB.init_db()
        with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
            cursor = conn.cursor()

            gallery_ids = sorted(TEST_GALLERY_IDS)
            cache_keys = sorted(TEST_CACHE_KEYS)

            if cache_keys:
                placeholders = ",".join("?" for _ in cache_keys)
                cursor.execute(
                    f"DELETE FROM CacheReferences WHERE cache_key IN ({placeholders})",
                    cache_keys,
                )

            if TEST_DOWNLOAD_ROOTS:
                dl_roots = sorted(TEST_DOWNLOAD_ROOTS)
                placeholders = ",".join("?" for _ in dl_roots)
                cursor.execute(
                    f"DELETE FROM DownloadLocations WHERE root_path IN ({placeholders})",
                    dl_roots,
                )

            if gallery_ids:
                placeholders = ",".join("?" for _ in gallery_ids)
                cursor.execute(
                    f"DELETE FROM CachedMetadata WHERE gallery_id IN ({placeholders})",
                    gallery_ids,
                )
                # Delete galleries directly; per-gallery tag/language/parody lists are stored on Galleries
                cursor.execute(
                    f"DELETE FROM Galleries WHERE id IN ({placeholders})",
                    gallery_ids,
                )
                cursor.execute(
                    f"DELETE FROM GalleriesQueue WHERE id IN ({placeholders})",
                    gallery_ids,
                )

            # Remove orphaned aggregate rows left behind by gallery cleanup.
            cursor.execute(
                """
                DELETE FROM Creators
                WHERE id NOT IN (
                    SELECT DISTINCT CAST(json_each.value AS INTEGER)
                    FROM Galleries, json_each(Galleries.creator_ids)
                    WHERE Galleries.creator_ids IS NOT NULL
                )
                """
            )
            cursor.execute(
                """
                DELETE FROM Tags
                WHERE id NOT IN (
                    SELECT DISTINCT CAST(json_each.value AS INTEGER)
                    FROM Galleries, json_each(Galleries.tag_ids)
                    WHERE Galleries.tag_ids IS NOT NULL
                )
                """
            )
            cursor.execute(
                """
                DELETE FROM Languages
                WHERE id NOT IN (
                    SELECT DISTINCT CAST(json_each.value AS INTEGER)
                    FROM Galleries, json_each(Galleries.language_ids)
                    WHERE Galleries.language_ids IS NOT NULL
                )
                """
            )

            conn.commit()

    with _temporary_test_database(), _temporary_test_runtime_paths():
        scraperapi.DB.init_db()
        try:
            # 0) Runtime path safety assertion
            try:
                _report(
                    f"test runtime paths set to {orchestrator.TEMP_DIR}/",
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
                _report(f"test runtime paths set to {orchestrator.TEMP_DIR}/", False, f"exception={e}")

            # 0.1) Pre-download path generation safety (computed path must stay under test runtime root)
            try:
                path_gid = TEST_GALLERY_ID + 10
                scraperapi.DB.Gallery.start(path_gid, TEST_RUNTIME_ROOT, "skeleton")
                scraperapi.Cache.upsert_cached_metadata(
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
                scraperapi.DB.Gallery.complete(path_gid)

                with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT download_path FROM Galleries WHERE id = ?", (path_gid,))
                    row = cursor.fetchone()

                final_path = str(row[0]) if row and row[0] is not None else ""
                ok = final_path.startswith(TEST_RUNTIME_ROOT)
                _report(f"computed gallery output path stays under {orchestrator.TEMP_DIR}/", ok, f"download_path={final_path}")
            except Exception as e:
                _report(f"computed gallery output path stays under {orchestrator.TEMP_DIR}/", False, f"exception={e}")

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
                scraperapi.Cache.upsert_cache_reference(
                    TEST_CACHE_KEY,
                    {
                        "cache_key": TEST_CACHE_KEY,
                        "cache_type": "test",
                        "cache_target": "cache_sanity",
                        "ids": TEST_IDS,
                        "expires_at": now + 300,
                    },
                )
                result = scraperapi.read_cached_metadata_entry(cache_key=TEST_CACHE_KEY)
                ref = result.get("references", {}).get(TEST_CACHE_KEY) if isinstance(result, dict) else None
                ok = (
                    isinstance(ref, dict)
                    and isinstance(ref.get("ids"), list)
                    and isinstance(ref.get("expires_at"), float)
                    and all(isinstance(gid, int) for gid in ref.get("ids", []))
                )
                _report("upsert_cache_reference + read_cached_metadata_entry(cache_key)", ok, f"ref={ref}")
            except Exception as e:
                _report("upsert_cache_reference + read_cached_metadata_entry(cache_key)", False, f"exception={e}")

            # 3) Ensure invalid IDs are dropped from cache reference IDs
            try:
                mixed_key = "test:cache_mixed_ids"
                now = time.time()
                scraperapi.Cache.upsert_cache_reference(
                    mixed_key,
                    {
                        "cache_key": mixed_key,
                        "cache_type": "test",
                        "cache_target": "mixed_ids",
                        "ids": ["1", "bad", None, 2, 2],
                        "expires_at": now + 300,
                    },
                )
                mixed = scraperapi.Cache.Load.cache(cache_key=mixed_key)
                ok = isinstance(mixed, list) and all(isinstance(gid, int) for gid in mixed)
                _report("Cache.Load.cache normalises mixed IDs", ok, f"ids={mixed}")
            except Exception as e:
                _report("Cache.Load.cache normalises mixed IDs", False, f"exception={e}")

            # 4) Upsert/read CachedMetadata by ID
            try:
                raw_meta = {"id": TEST_GALLERY_ID, "title": {"english": "Cache Test Gallery"}}
                clean_meta = {"id": TEST_GALLERY_ID, "title": "Cache Test Gallery", "pages": 1}
                scraperapi.Cache.upsert_cached_metadata(
                    TEST_GALLERY_ID,
                    time.time(),
                    clean_metadata=clean_meta,
                    raw_metadata=raw_meta,
                )
                result = scraperapi.read_cached_metadata_entry(gallery_id=TEST_GALLERY_ID)
                entry = result.get("metadata", {}).get(TEST_GALLERY_ID) if isinstance(result, dict) else None
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
                result = scraperapi.read_cached_metadata_entry(ids=[TEST_GALLERY_ID])
                batch = result.get("metadata", {}) if isinstance(result, dict) else {}
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
                scraperapi.Cache.upsert_cached_metadata(old_gid, old_ts, clean_metadata={"id": old_gid}, raw_metadata={"id": old_gid})
                scraperapi.Cache.upsert_cached_metadata(new_gid, new_ts, clean_metadata={"id": new_gid}, raw_metadata={"id": new_gid})
                cutoff = time.time() - 60
                result = scraperapi.read_cached_metadata_entry(cutoff=cutoff)
                recent = result.get("metadata", {}) if isinstance(result, dict) else {}
                ok = isinstance(recent, dict) and new_gid in recent and old_gid not in recent
                _report("read_cached_metadata_entry(cutoff=...)", ok, f"recent_keys={list(recent.keys())[:5] if isinstance(recent, dict) else None}")
            except Exception as e:
                _report("read_cached_metadata_entry(cutoff=...)", False, f"exception={e}")

            # 7) Cache.Load/Save explicit cache refs
            try:
                scraperapi.Cache.Save.cache(cache_key=TEST_CACHE_KEY, gallery_ids=TEST_IDS)
                loaded_ids = scraperapi.Cache.Load.cache(cache_key=TEST_CACHE_KEY)
                ok = isinstance(loaded_ids, list) and all(isinstance(gid, int) for gid in loaded_ids)
                _report("Cache.Save.cache(cache_key, gallery_ids) + Cache.Load.cache(cache_key)", ok, f"loaded_ids={loaded_ids}")
            except Exception as e:
                _report("Cache.Save.cache(cache_key, gallery_ids) + Cache.Load.cache(cache_key)", False, f"exception={e}")

            # 8) Backward-compatible cache(meta, gallery_id) call shape
            try:
                compat_meta = {"id": TEST_GALLERY_ID + 3, "title": {"english": "Compat Title"}, "tags": [], "images": {"pages": []}}
                entry = scraperapi.Cache.Save.cache(compat_meta, TEST_GALLERY_ID + 3)
                loaded = scraperapi.Cache.Load.cache(gallery_id=TEST_GALLERY_ID + 3)
                ok = isinstance(entry, dict) and isinstance(loaded, dict)
                _report("Cache.Save.cache(meta, gallery_id) compatibility", ok, f"loaded_keys={list(loaded.keys()) if isinstance(loaded, dict) else None}")
            except Exception as e:
                _report("Cache.Save.cache(meta, gallery_id) compatibility", False, f"exception={e}")

            # 9) Cache.Load.id_metadata
            try:
                md = scraperapi.Cache.Load.id_metadata([TEST_GALLERY_ID])
                ok = isinstance(md, dict)
                _report("Cache.Load.id_metadata([...])", ok, f"keys={list(md.keys()) if isinstance(md, dict) else None}")
            except Exception as e:
                _report("Cache.Load.id_metadata([...])", False, f"exception={e}")

            # 10) Cache.Load.cached_metadata (raw + clean)
            try:
                raw_block = scraperapi.Cache.Load.cached_metadata(clean=False)
                clean_block = scraperapi.Cache.Load.cached_metadata(clean=True)
                ok = isinstance(raw_block, dict) and isinstance(clean_block, dict)
                _report("Cache.Load.cached_metadata(clean=False/True)", ok, f"raw={len(raw_block)} clean={len(clean_block)}")
            except Exception as e:
                _report("Cache.Load.cached_metadata(clean=False/True)", False, f"exception={e}")

            # 11) prune_all_caches removes expired references
            try:
                expired_key = "test:cache_expired"
                scraperapi.Cache.upsert_cache_reference(
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
                result = scraperapi.read_cached_metadata_entry(cache_key=expired_key)
                expired = result.get("references", {}).get(expired_key) if isinstance(result, dict) else None
                ok = expired is None
                _report("prune_all_caches removes expired refs", ok, f"expired_entry={expired}")
            except Exception as e:
                _report("prune_all_caches removes expired refs", False, f"exception={e}")

            # 11.1) prune_all_caches removes expired metadata
            try:
                expired_gid = TEST_GALLERY_ID + 11
                now = time.time()
                scraperapi.Cache.upsert_cached_metadata(
                    expired_gid,
                    now,
                    clean_metadata={"id": expired_gid, "title": "Expired Metadata"},
                    raw_metadata={"id": expired_gid},
                )
                with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE CachedMetadata SET expires_at = ? WHERE gallery_id = ?",
                        (time.time() - 1, expired_gid),
                    )
                    conn.commit()

                scraperapi.prune_all_caches()
                result = scraperapi.read_cached_metadata_entry(gallery_id=expired_gid)
                expired_md = result.get("metadata", {}).get(expired_gid) if isinstance(result, dict) else None
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

            # ======================================================================
            # A) DB helper primitives
            # ======================================================================

            # A.1) _normalise_integer: valid int
            try:
                ok = scraperapi.Helpers.normalise_integer(42) == 42
                _report("_normalise_integer(42)", ok)
            except Exception as e:
                _report("_normalise_integer(42)", False, f"exception={e}")

            # A.2) _normalise_integer: coerce string
            try:
                ok = scraperapi.Helpers.normalise_integer("123") == 123
                _report("_normalise_integer('123')", ok)
            except Exception as e:
                _report("_normalise_integer('123')", False, f"exception={e}")

            # A.3) _normalise_integer: invalid → None
            try:
                ok = scraperapi.Helpers.normalise_integer("bad") is None
                _report("_normalise_integer('bad') → None", ok)
            except Exception as e:
                _report("_normalise_integer('bad') → None", False, f"exception={e}")

            # A.4) _normalise_integer: None → None
            try:
                ok = scraperapi.Helpers.normalise_integer(None) is None
                _report("_normalise_integer(None) → None", ok)
            except Exception as e:
                _report("_normalise_integer(None) → None", False, f"exception={e}")

            # A.5) _normalise_integer_list: mixed valid/invalid/float inputs
            try:
                result = scraperapi.Helpers.normalise_integer_list([1, "2", "bad", None, 3.7])
                ok = result == [1, 2, 3]  # int(3.7) == 3
                _report("_normalise_integer_list mixed inputs", ok, f"result={result}")
            except Exception as e:
                _report("_normalise_integer_list mixed inputs", False, f"exception={e}")

            # A.6) _normalise_integer_list: None → []
            try:
                ok = scraperapi.Helpers.normalise_integer_list(None) == []
                _report("_normalise_integer_list(None) → []", ok)
            except Exception as e:
                _report("_normalise_integer_list(None) → []", False, f"exception={e}")

            # A.7) _safe_json_dict: dict passthrough
            try:
                ok = scraperapi.Helpers.safe_json_dict({"x": 1}) == {"x": 1}
                _report("_safe_json_dict(dict) passthrough", ok)
            except Exception as e:
                _report("_safe_json_dict(dict) passthrough", False, f"exception={e}")

            # A.8) _safe_json_dict: JSON string → dict
            try:
                ok = scraperapi.Helpers.safe_json_dict('{"a": 1}') == {"a": 1}
                _report("_safe_json_dict(JSON string) → dict", ok)
            except Exception as e:
                _report("_safe_json_dict(JSON string) → dict", False, f"exception={e}")

            # A.9) _safe_json_dict: invalid string → {}
            try:
                ok = scraperapi.Helpers.safe_json_dict("not json") == {}
                _report("_safe_json_dict(invalid) → {}", ok)
            except Exception as e:
                _report("_safe_json_dict(invalid) → {}", False, f"exception={e}")

            # A.10) _safe_float: valid float string
            try:
                ok = abs(scraperapi.Helpers.safe_float("3.14") - 3.14) < 1e-9
                _report("_safe_float('3.14')", ok)
            except Exception as e:
                _report("_safe_float('3.14')", False, f"exception={e}")

            # A.11) _safe_float: invalid + custom default
            try:
                ok = scraperapi.Helpers.safe_float("bad", 99.0) == 99.0
                _report("_safe_float('bad', 99.0) → 99.0", ok)
            except Exception as e:
                _report("_safe_float('bad', 99.0) → 99.0", False, f"exception={e}")

            # A.12) _safe_text: None → ""
            try:
                ok = scraperapi.Helpers.safe_text(None) == ""
                _report("_safe_text(None) → ''", ok)
            except Exception as e:
                _report("_safe_text(None) → ''", False, f"exception={e}")

            # A.13) _safe_text: non-str → str
            try:
                ok = scraperapi.Helpers.safe_text(42) == "42"
                _report("_safe_text(42) → '42'", ok)
            except Exception as e:
                _report("_safe_text(42) → '42'", False, f"exception={e}")

            # A.14) _safe_text_list: mixed inputs (blanks stripped, None skipped)
            try:
                result = scraperapi.Helpers.safe_text_list([" hello ", None, "", "  world  "])
                ok = result == ["hello", "world"]
                _report("_safe_text_list mixed inputs", ok, f"result={result}")
            except Exception as e:
                _report("_safe_text_list mixed inputs", False, f"exception={e}")

            # ======================================================================
            # B) split_cache_key
            # ======================================================================

            # B.1) simple key with colon
            try:
                ct, tgt = scraperapi.Cache.split_key("artist:abc")
                ok = ct == "artist" and tgt == "abc"
                _report("split_cache_key('artist:abc')", ok, f"type={ct} target={tgt}")
            except Exception as e:
                _report("split_cache_key('artist:abc')", False, f"exception={e}")

            # B.2) key with multiple colons → splits on first colon only
            try:
                ct, tgt = scraperapi.Cache.split_key("artist:abc:extra")
                ok = ct == "artist" and tgt == "abc:extra"
                _report("split_cache_key multi-colon → first split only", ok, f"type={ct} target={tgt}")
            except Exception as e:
                _report("split_cache_key multi-colon → first split only", False, f"exception={e}")

            # B.3) key with no colon → type only, empty target
            try:
                ct, tgt = scraperapi.Cache.split_key("homepage")
                ok = ct == "homepage" and tgt == ""
                _report("split_cache_key('homepage') → no target", ok, f"type={ct} target={repr(tgt)}")
            except Exception as e:
                _report("split_cache_key('homepage') → no target", False, f"exception={e}")

            # ======================================================================
            # C) build_url
            # ======================================================================

            # C.1) homepage + date sort → no sort param
            try:
                url = scraperapi.Build.url("homepage", None, "date", 1)
                ok = "/galleries/all?page=1" in url and "sort=" not in url
                _report("build_url homepage+date → no sort param", ok, f"url={url}")
            except Exception as e:
                _report("build_url homepage+date → no sort param", False, f"exception={e}")

            # C.2) homepage + non-date sort → includes &sort=
            try:
                url = scraperapi.Build.url("homepage", None, "popular", 2)
                ok = "page=2" in url and "sort=popular" in url
                _report("build_url homepage+popular → has sort param", ok, f"url={url}")
            except Exception as e:
                _report("build_url homepage+popular → has sort param", False, f"exception={e}")

            # C.3) artist query → encoded artist and name present in URL
            try:
                url = scraperapi.Build.url("artist", "john", "date", 1)
                ok = "artist" in url and "john" in url
                _report("build_url artist → encoded in URL", ok, f"url={url}")
            except Exception as e:
                _report("build_url artist → encoded in URL", False, f"exception={e}")

            # C.4) search query → spaces encoded as '+' via quote_plus
            try:
                url = scraperapi.Build.url("search", "big ass", "date", 1)
                ok = "big+ass" in url
                _report("build_url search → spaces encoded as '+'", ok, f"url={url}")
            except Exception as e:
                _report("build_url search → spaces encoded as '+'", False, f"exception={e}")

            # C.5) invalid query_type → ValueError
            try:
                raised = False
                try:
                    scraperapi.Build.url("invalid_type", "x", "date", 1)
                except ValueError:
                    raised = True
                _report("build_url invalid type → ValueError", raised)
            except Exception as e:
                _report("build_url invalid type → ValueError", False, f"exception={e}")

            # ======================================================================
            # D) Get.cache_keys
            # ======================================================================

            # D.1) multi-word value → tokens sorted alphabetically
            try:
                key = scraperapi.Cache.cache_key("artist", "John Doe")
                ok = key == "artist:doe_john"
                _report("Get.cache_keys multi-word → sorted tokens", ok, f"key={key}")
            except Exception as e:
                _report("Get.cache_keys multi-word → sorted tokens", False, f"exception={e}")

            # D.2) single-word value → type:value
            try:
                key = scraperapi.Cache.cache_key("tag", "schoolgirl")
                ok = key == "tag:schoolgirl"
                _report("Get.cache_keys single-word", ok, f"key={key}")
            except Exception as e:
                _report("Get.cache_keys single-word", False, f"exception={e}")

            # D.3) no value → type only
            try:
                key = scraperapi.Cache.cache_key("homepage")
                ok = key == "homepage"
                _report("Get.cache_keys no value → type only", ok, f"key={key}")
            except Exception as e:
                _report("Get.cache_keys no value → type only", False, f"exception={e}")

            # D.4) multi-word with modifiers → main tokens sorted and joined with underscores, modifiers appended
            try:
                key = scraperapi.Cache.cache_key("artist", "anon 2-okunen +date 1-20")
                ok = key == "artist:2-okunen_anon+date_1-20"
                _report("Get.cache_keys multi-word with modifiers", ok, f"key={key}")
            except Exception as e:
                _report("Get.cache_keys multi-word with modifiers", False, f"exception={e}")

            # D.5) modifiers with extra spaces normalise correctly
            try:
                key = scraperapi.Cache.cache_key("artist", "Anon 2-okunen + date 1-20")
                ok = key == "artist:2-okunen_anon+date_1-20"
                _report("Get.cache_keys modifiers with spaces normalise", ok, f"key={key}")
            except Exception as e:
                _report("Get.cache_keys modifiers with spaces normalise", False, f"exception={e}")

            # ======================================================================
            # E) Get.meta_tags
            # ======================================================================

            _meta_with_tags = {
                "tags": [
                    {"type": "artist", "name": "Test Artist"},
                    {"type": "tag", "name": "schoolgirl"},
                    {"type": "artist", "name": "Second Artist"},
                ]
            }

            # E.1) valid meta with artist tags → names extracted in order
            try:
                artists = scraperapi.Get.meta_tags("tester", _meta_with_tags, "artist")
                ok = artists == ["Test Artist", "Second Artist"]
                _report("Get.meta_tags artist → correct names", ok, f"artists={artists}")
            except Exception as e:
                _report("Get.meta_tags artist → correct names", False, f"exception={e}")

            # E.2) valid meta with no matching tag type → []
            try:
                chars = scraperapi.Get.meta_tags("tester", _meta_with_tags, "character")
                ok = chars == []
                _report("Get.meta_tags missing type → []", ok, f"result={chars}")
            except Exception as e:
                _report("Get.meta_tags missing type → []", False, f"exception={e}")

            # E.3) non-dict meta → []
            try:
                ok = scraperapi.Get.meta_tags("tester", "not a dict", "artist") == []
                _report("Get.meta_tags non-dict → []", ok)
            except Exception as e:
                _report("Get.meta_tags non-dict → []", False, f"exception={e}")

            # ======================================================================
            # F) Get.page_count
            # ======================================================================

            # F.1) meta with 3 pages → 3
            try:
                meta_3_pages = {"images": {"pages": [{"t": "j"}, {"t": "j"}, {"t": "p"}]}}
                ok = scraperapi.Get.page_count(meta_3_pages) == 3
                _report("Get.page_count meta with 3 pages", ok)
            except Exception as e:
                _report("Get.page_count meta with 3 pages", False, f"exception={e}")

            # F.2) empty meta → 0
            try:
                ok = scraperapi.Get.page_count({}) == 0
                _report("Get.page_count empty meta → 0", ok)
            except Exception as e:
                _report("Get.page_count empty meta → 0", False, f"exception={e}")

            # ======================================================================
            # G) Get.metadata_summary
            # ======================================================================

            _fake_meta = {
                1: {"artists": ["Artist A"], "groups": [], "tags": ["tag1", "tag2"],
                    "characters": [], "parodies": [], "languages": ["english"], "pages": 10},
                2: {"artists": ["Artist B"], "groups": ["Group X"], "tags": ["tag2", "tag3"],
                    "characters": ["Char 1"], "parodies": [], "languages": ["english"], "pages": 20},
            }

            # G.1) populated metadata → correct summary keys and counts
            try:
                summ = scraperapi.Get.metadata_summary(_fake_meta)
                ok = (
                    isinstance(summ, dict)
                    and summ.get("total_galleries") == 2
                    and summ.get("unique_artists") == 2
                    and summ.get("unique_tags") == 3
                    and summ.get("unique_languages") == 1
                )
                _report("Get.metadata_summary populated dict", ok, f"total={summ.get('total_galleries')} artists={summ.get('unique_artists')} tags={summ.get('unique_tags')}")
            except Exception as e:
                _report("Get.metadata_summary populated dict", False, f"exception={e}")

            # G.2) empty metadata → {}
            try:
                ok = scraperapi.Get.metadata_summary({}) == {}
                _report("Get.metadata_summary empty → {}", ok)
            except Exception as e:
                _report("Get.metadata_summary empty → {}", False, f"exception={e}")

            # ======================================================================
            # H) sanitise_string
            # ======================================================================

            # H.1) plain ASCII title preserved
            try:
                result = scraperapi.Helpers.sanitise("Test Title")
                ok = isinstance(result, str) and "Test" in result
                _report("sanitise_string plain ASCII", ok, f"result={repr(result)}")
            except Exception as e:
                _report("sanitise_string plain ASCII", False, f"exception={e}")

            # H.2) bracket content stripped
            try:
                result = scraperapi.Helpers.sanitise("[Group] Title [Subtitle]")
                ok = isinstance(result, str) and "[" not in result and "]" not in result
                _report("sanitise_string bracket content stripped", ok, f"result={repr(result)}")
            except Exception as e:
                _report("sanitise_string bracket content stripped", False, f"exception={e}")

            # H.3) dict with title.english → extracts and strips brackets
            try:
                result = scraperapi.Helpers.sanitise({"id": 999, "title": {"english": "[Author] My Story"}})
                ok = isinstance(result, str) and "My Story" in result and "[" not in result
                _report("sanitise_string dict → extracts english title and strips brackets", ok, f"result={repr(result)}")
            except Exception as e:
                _report("sanitise_string dict → extracts english title and strips brackets", False, f"exception={e}")

            # H.4) empty string → "UNTITLED"
            try:
                result = scraperapi.Helpers.sanitise("")
                ok = result == "UNTITLED"
                _report("sanitise_string empty string → 'UNTITLED'", ok, f"result={repr(result)}")
            except Exception as e:
                _report("sanitise_string empty string → 'UNTITLED'", False, f"exception={e}")

            # H.5) slashes converted to dashes
            try:
                result = scraperapi.Helpers.sanitise("Part A / Part B")
                ok = "/" not in result and "-" in result
                _report("sanitise_string slashes → dashes", ok, f"result={repr(result)}")
            except Exception as e:
                _report("sanitise_string slashes → dashes", False, f"exception={e}")

            # ======================================================================
            # I) Gallery status lifecycle
            # ======================================================================

            _STATUS_GID_STARTED = TEST_GALLERY_ID + 4
            _STATUS_GID_SKIPPED = TEST_GALLERY_ID + 5
            _STATUS_GID_FAILED  = TEST_GALLERY_ID + 6

            # I.1) mark_gallery_started → status == "started"
            try:
                scraperapi.DB.Gallery.start(_STATUS_GID_STARTED, TEST_RUNTIME_ROOT, "skeleton")
                status = scraperapi.Get.gallery_status(_STATUS_GID_STARTED)
                ok = status == "started"
                _report("mark_gallery_started → status='started'", ok, f"status={status}")
            except Exception as e:
                _report("mark_gallery_started → status='started'", False, f"exception={e}")

            # I.2) mark_gallery_skipped → status == "skipped"
            try:
                scraperapi.DB.Gallery.start(_STATUS_GID_SKIPPED, TEST_RUNTIME_ROOT, "skeleton")
                scraperapi.DB.Gallery.skip(_STATUS_GID_SKIPPED)
                status = scraperapi.Get.gallery_status(_STATUS_GID_SKIPPED)
                ok = status == "skipped"
                _report("mark_gallery_skipped → status='skipped'", ok, f"status={status}")
            except Exception as e:
                _report("mark_gallery_skipped → status='skipped'", False, f"exception={e}")

            # I.3) mark_gallery_failed → status == "failed"
            try:
                scraperapi.DB.Gallery.start(_STATUS_GID_FAILED, TEST_RUNTIME_ROOT, "skeleton")
                scraperapi.DB.Gallery.fail(_STATUS_GID_FAILED)
                status = scraperapi.Get.gallery_status(_STATUS_GID_FAILED)
                ok = status == "failed"
                _report("mark_gallery_failed → status='failed'", ok, f"status={status}")
            except Exception as e:
                _report("mark_gallery_failed → status='failed'", False, f"exception={e}")

            # I.4) list_galleries() includes newly inserted row
            try:
                all_ids = {row[0] for row in scraperapi.DB.Gallery.list()}
                ok = _STATUS_GID_STARTED in all_ids
                _report("list_galleries() includes newly started gallery", ok)
            except Exception as e:
                _report("list_galleries() includes newly started gallery", False, f"exception={e}")

            # I.5) list_galleries(status) filters by status correctly
            try:
                started_ids = {row[0] for row in scraperapi.DB.Gallery.list_by_status("started")}
                skipped_ids = {row[0] for row in scraperapi.DB.Gallery.list_by_status("skipped")}
                ok = (
                    _STATUS_GID_STARTED in started_ids
                    and _STATUS_GID_SKIPPED not in started_ids
                    and _STATUS_GID_SKIPPED in skipped_ids
                )
                _report("list_galleries(status) filters correctly", ok)
            except Exception as e:
                _report("list_galleries(status) filters correctly", False, f"exception={e}")

            # ======================================================================
            # J) set_queued_galleries + Cache.Load.queued_galleries
            # ======================================================================

            # J.1) set queue → load back as a sorted list
            try:
                _queue_ids = sorted([TEST_GALLERY_ID + 4, TEST_GALLERY_ID + 5, TEST_GALLERY_ID + 6])
                scraperapi.Cache.Save.queued_galleries(_queue_ids)
                loaded = scraperapi.Cache.Load.queued_galleries()
                ok = isinstance(loaded, list) and all(gid in loaded for gid in _queue_ids)
                _report("set_queued_galleries + queued_galleries() roundtrip", ok, f"loaded={loaded}")
            except Exception as e:
                _report("set_queued_galleries + queued_galleries() roundtrip", False, f"exception={e}")

            # ======================================================================
            # K) clear_cached_items
            # ======================================================================

            _CLEAR_CACHE_KEY = "test:clear_ck_test"
            _CLEAR_META_GID  = TEST_GALLERY_ID + 7

            # K.1) clear by cache_key removes that reference
            try:
                scraperapi.Cache.upsert_cache_reference(_CLEAR_CACHE_KEY, {
                    "cache_key": _CLEAR_CACHE_KEY,
                    "cache_type": "test",
                    "cache_target": "clear_test",
                    "ids": [1],
                    "expires_at": time.time() + 300,
                })
                scraperapi.clear_cached_items(cache_key=_CLEAR_CACHE_KEY)
                result = scraperapi.read_cached_metadata_entry(cache_key=_CLEAR_CACHE_KEY)
                ok = result.get("references", {}).get(_CLEAR_CACHE_KEY) is None
                _report("clear_cached_items(cache_key=...) removes reference", ok)
            except Exception as e:
                _report("clear_cached_items(cache_key=...) removes reference", False, f"exception={e}")

            # K.2) clear by gallery_id removes metadata
            try:
                scraperapi.Cache.upsert_cached_metadata(_CLEAR_META_GID, time.time(),
                    clean_metadata={"id": _CLEAR_META_GID},
                    raw_metadata={"id": _CLEAR_META_GID},
                )
                scraperapi.clear_cached_items(gallery_id=_CLEAR_META_GID)
                result = scraperapi.read_cached_metadata_entry(gallery_id=_CLEAR_META_GID)
                ok = result.get("metadata", {}).get(_CLEAR_META_GID) is None
                _report("clear_cached_items(gallery_id=...) removes metadata", ok)
            except Exception as e:
                _report("clear_cached_items(gallery_id=...) removes metadata", False, f"exception={e}")

            # K.3) providing both cache_key and gallery_id → ValueError
            try:
                raised = False
                try:
                    scraperapi.clear_cached_items(cache_key="x", gallery_id=1)
                except ValueError:
                    raised = True
                _report("clear_cached_items(both args) → ValueError", raised)
            except Exception as e:
                _report("clear_cached_items(both args) → ValueError", False, f"exception={e}")

            # ======================================================================
            # L) estimate_gallery_size
            # ======================================================================

            # L.1) meta with typed pages → positive size estimate
            try:
                _meta_pages = {
                    "media_id": "999999",
                    "images": {"pages": [{"t": "j"}, {"t": "p"}, {"t": "w"}, {"t": "g"}]},
                }
                est, actual, count = scraperapi.Build.gallery_size_estimate(_meta_pages, use_head_requests=False)
                ok = isinstance(est, int) and est > 0 and count == 4
                _report("estimate_gallery_size with typed pages", ok, f"est={est} count={count}")
            except Exception as e:
                _report("estimate_gallery_size with typed pages", False, f"exception={e}")

            # L.2) empty meta → (0, 0, 0)
            try:
                est, actual, count = scraperapi.Build.gallery_size_estimate({}, use_head_requests=False)
                ok = est == 0 and actual == 0 and count == 0
                _report("estimate_gallery_size empty meta → (0, 0, 0)", ok, f"result=({est},{actual},{count})")
            except Exception as e:
                _report("estimate_gallery_size empty meta → (0, 0, 0)", False, f"exception={e}")

            # ======================================================================
            # M) downloader._format_bytes
            # ======================================================================

            # M.1) 0 bytes → "0.00 B"
            try:
                result = scraper_downloader._format_bytes(0)
                ok = result == "0.00 B"
                _report("_format_bytes(0) → '0.00 B'", ok, f"result={repr(result)}")
            except Exception as e:
                _report("_format_bytes(0) → '0.00 B'", False, f"exception={e}")

            # M.2) 1024 bytes → "1.00 KB"
            try:
                result = scraper_downloader._format_bytes(1024)
                ok = result == "1.00 KB"
                _report("_format_bytes(1024) → '1.00 KB'", ok, f"result={repr(result)}")
            except Exception as e:
                _report("_format_bytes(1024) → '1.00 KB'", False, f"exception={e}")

            # M.3) 1 MiB → "1.00 MB"
            try:
                result = scraper_downloader._format_bytes(1024 * 1024)
                ok = result == "1.00 MB"
                _report("_format_bytes(1 MiB) → '1.00 MB'", ok, f"result={repr(result)}")
            except Exception as e:
                _report("_format_bytes(1 MiB) → '1.00 MB'", False, f"exception={e}")

            # ======================================================================
            # N) infer_location_root regression + root priority
            # ======================================================================

            # N.1) infer_location_root must not call DB.init_db (prevents recursion during init)
            try:
                original_init_db = scraperapi.DB.init_db

                def _sentinel_init_db():
                    raise RuntimeError("sentinel: DB.init_db should not be called by infer_location_root")

                scraperapi.DB.init_db = _sentinel_init_db
                root = scraperapi.Helpers.infer_location_root(f"{TEST_RUNTIME_ROOT}alpha/beta/gamma")
                ok = isinstance(root, str) and root != ""
                _report("infer_location_root avoids DB.init_db recursion path", ok, f"root={root}")
            except Exception as e:
                _report("infer_location_root avoids DB.init_db recursion path", False, f"exception={e}")
            finally:
                scraperapi.DB.init_db = original_init_db

            # N.2) infer_location_root picks the most specific known root
            try:
                root_a = f"{TEST_RUNTIME_ROOT}root-a"
                root_b = f"{TEST_RUNTIME_ROOT}root-a/downloads"
                with scraperapi.db_lock, scraperapi.DB.dbconnect() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT OR IGNORE INTO DownloadLocations (root_path, extension_used) VALUES (?, ?)",
                        (root_a, "other"),
                    )
                    cursor.execute(
                        "INSERT OR IGNORE INTO DownloadLocations (root_path, extension_used) VALUES (?, ?)",
                        (root_b, "skeleton"),
                    )
                    conn.commit()

                probe = f"{root_b}/creator/gallery"
                inferred = scraperapi.Helpers.infer_location_root(probe)
                ok = inferred == root_b
                _report("infer_location_root prefers most specific DownloadLocations root", ok, f"inferred={inferred}")
            except Exception as e:
                _report("infer_location_root prefers most specific DownloadLocations root", False, f"exception={e}")

        finally:
            try:
                _cleanup_test_data()
                _report(
                    "cleanup removes test data from database",
                    True,
                    f"gallery_ids={sorted(TEST_GALLERY_IDS)} cache_keys={sorted(TEST_CACHE_KEYS)}",
                )
            except Exception as e:
                _report("cleanup removes test data from database", False, f"exception={e}")

    summary = f"[SELF-TEST] SELF-TEST COMPLETE: passed={passed}, failed={failed}, skipped={skipped}"
    print(summary)
    logger.info(summary)
    logger.debug(summary)

    return failed == 0
