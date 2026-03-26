# mangascraper/core/api/_db.py

from __future__ import annotations
import os, time, threading, sqlite3, json, re
from datetime import datetime, timezone

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import logger
from mangascraper.core.api._constants import (
    db_lock,
    _thread_local,
    DATA_DIR,
    DB_PATH,
    CACHE_REFERENCES_TTL_SECONDS,
    CACHED_METADATA_TTL_SECONDS,
    DOWNLOAD_ROOT_MARKER_FILE,
)
from mangascraper.core.api._helpers import (
    Helpers,
    prune_all_caches,
    read_cached_metadata_entry,
    clear_cached_items,
)
from mangascraper.core.api._smart_rules import evaluate_smart_rpn, smart_expression_to_rpn

####################################################################################################################
# DB CLASS
####################################################################################################################

class DB:
    """Database access namespace."""

    @staticmethod
    def connect():
        return DB.dbconnect()

    @staticmethod
    def dbconnect():
        conn = getattr(_thread_local, "connection", None)
        if conn is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            conn = sqlite3.connect(DB_PATH, timeout=120.0)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 120000")
            conn.execute("PRAGMA synchronous = FULL")
            _thread_local.connection = conn
        return conn

    @staticmethod
    def init():
        return DB.init_db()

    @staticmethod
    def init_db():
        orchestrator.refresh_globals()
        os.makedirs(DATA_DIR, exist_ok=True)
        with db_lock, DB.dbconnect() as conn:
            c = conn.cursor()
            c.executescript(f"""
            CREATE TABLE IF NOT EXISTS DownloadQueue (
                download_no INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'queued',
                ids_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS Creators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                display_name TEXT,
                creator_type TEXT,
                first_seen TEXT,
                last_updated TEXT,
                total_galleries INTEGER,
                most_popular_tags TEXT,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS Galleries (
                id INTEGER PRIMARY KEY,
                raw_title TEXT,
                clean_title TEXT,
                num_pages INTEGER,
                creator_ids TEXT,
                language_ids TEXT,
                tag_ids TEXT,
                parody_ids TEXT,
                status TEXT,
                started_at TEXT,
                completed_at TEXT,
                extension_used TEXT,
                download_path TEXT,
                cover_path TEXT,
                favourite INTEGER DEFAULT 0,
                rating REAL
            );
            CREATE TABLE IF NOT EXISTS DownloadLocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_path TEXT NOT NULL UNIQUE,
                extension_used TEXT
            );
            CREATE TABLE IF NOT EXISTS GalleryTags (
                gallery_id INTEGER PRIMARY KEY,
                tag_ids TEXT,
                FOREIGN KEY (gallery_id) REFERENCES Galleries(id)
            );
            CREATE TABLE IF NOT EXISTS GalleryParodies (
                gallery_id INTEGER PRIMARY KEY,
                parody_ids TEXT,
                FOREIGN KEY (gallery_id) REFERENCES Galleries(id)
            );
            CREATE TABLE IF NOT EXISTS GalleryLanguages (
                gallery_id INTEGER PRIMARY KEY,
                language_ids TEXT,
                FOREIGN KEY (gallery_id) REFERENCES Galleries(id)
            );
            CREATE TABLE IF NOT EXISTS Tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                count INTEGER
            );
            CREATE TABLE IF NOT EXISTS Languages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                count INTEGER
            );
            CREATE TABLE IF NOT EXISTS Parodies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                count INTEGER
            );
            CREATE TABLE IF NOT EXISTS BrokenSymbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE,
                date_detected TEXT,
                fixed INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS CachedMetadata (
                gallery_id INTEGER PRIMARY KEY,
                timestamp REAL,
                raw_metadata TEXT,
                clean_metadata TEXT,
                expires_at REAL
            );
            CREATE TABLE IF NOT EXISTS CacheReferences (
                cache_key TEXT PRIMARY KEY,
                cache_type TEXT,
                cache_target TEXT,
                ids TEXT,
                expires_at REAL
            );
            CREATE TABLE IF NOT EXISTS Collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                collection_type TEXT NOT NULL DEFAULT 'normal',
                sort_mode TEXT NOT NULL DEFAULT 'id_desc',
                expressions TEXT,
                smart_expression TEXT,
                items TEXT,
                created_at TEXT,
                updated_at TEXT,
                last_refreshed_at TEXT
            );
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_downloadqueue_status_no ON DownloadQueue(status, download_no)")

            # Migrate legacy GalleriesQueue
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='GalleriesQueue'")
            if c.fetchone() is not None:
                c.execute("SELECT id FROM GalleriesQueue")
                legacy_ids = sorted({int(row[0]) for row in c.fetchall() if row and row[0] is not None})
                if legacy_ids:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    c.execute("DELETE FROM DownloadQueue WHERE LOWER(COALESCE(status, ''))='selected'")
                    c.execute(
                        "INSERT INTO DownloadQueue (status, ids_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        ("selected", json.dumps(legacy_ids), now_iso, now_iso),
                    )
                c.execute("DROP TABLE GalleriesQueue")

            # Schema migrations
            c.execute("PRAGMA table_info(CacheReferences)")
            if "expires_at" not in [row[1] for row in c.fetchall()]:
                c.execute("ALTER TABLE CacheReferences ADD COLUMN expires_at REAL")

            c.execute("PRAGMA table_info(CachedMetadata)")
            if "expires_at" not in [row[1] for row in c.fetchall()]:
                c.execute("ALTER TABLE CachedMetadata ADD COLUMN expires_at REAL")

            c.execute("PRAGMA table_info(Collections)")
            collection_columns = [row[1] for row in c.fetchall()]
            if "expressions" not in collection_columns:
                c.execute("ALTER TABLE Collections ADD COLUMN expressions TEXT")
            if "items" not in collection_columns:
                c.execute("ALTER TABLE Collections ADD COLUMN items TEXT")

            c.execute("PRAGMA table_info(Collections)")
            collection_columns = [row[1] for row in c.fetchall()]
            desired_collection_columns = [
                "id", "name", "description", "collection_type", "sort_mode",
                "expressions", "smart_expression", "items", "created_at",
                "updated_at", "last_refreshed_at",
            ]

            if collection_columns != desired_collection_columns:
                c.execute("DROP TABLE IF EXISTS Collections__reordered")
                c.execute(
                    """
                    CREATE TABLE Collections__reordered (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        description TEXT,
                        collection_type TEXT NOT NULL DEFAULT 'normal',
                        sort_mode TEXT NOT NULL DEFAULT 'id_desc',
                        expressions TEXT,
                        smart_expression TEXT,
                        items TEXT,
                        created_at TEXT,
                        updated_at TEXT,
                        last_refreshed_at TEXT
                    )
                    """
                )
                select_parts = []
                for col in desired_collection_columns:
                    if col in collection_columns:
                        if col in {"expressions", "items"}:
                            select_parts.append(f"COALESCE({col}, '[]') AS {col}")
                        elif col == "smart_expression":
                            select_parts.append(f"COALESCE({col}, '') AS {col}")
                        else:
                            select_parts.append(col)
                    else:
                        if col in {"expressions", "items"}:
                            select_parts.append(f"'[]' AS {col}")
                        elif col == "smart_expression":
                            select_parts.append(f"'' AS {col}")
                        else:
                            select_parts.append(f"NULL AS {col}")
                c.execute(
                    "INSERT INTO Collections__reordered (" + ", ".join(desired_collection_columns) + ") "
                    "SELECT " + ", ".join(select_parts) + " FROM Collections"
                )
                c.execute("DROP TABLE Collections")
                c.execute("ALTER TABLE Collections__reordered RENAME TO Collections")

            # Migrate CollectionFilters / CollectionItems
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='CollectionFilters'")
            has_collection_filters = c.fetchone() is not None
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='CollectionItems'")
            has_collection_items = c.fetchone() is not None

            if has_collection_filters or has_collection_items:
                c.execute("SELECT id FROM Collections")
                collection_ids = [int(row[0]) for row in c.fetchall() if row and row[0] is not None]

                for collection_id in collection_ids:
                    expressions_payload = []
                    if has_collection_filters:
                        c.execute(
                            "SELECT filter_key, filter_type, filter_value FROM CollectionFilters WHERE collection_id=? ORDER BY id",
                            (int(collection_id),),
                        )
                        for fkey, ftype, fvalue in c.fetchall():
                            key_text = Helpers.safe_text(fkey, "").strip().upper()
                            type_text = Helpers.safe_text(ftype, "").strip().lower()
                            value_text = Helpers.safe_text(fvalue, "").strip()
                            if not key_text or not type_text or not value_text:
                                continue
                            expressions_payload.append({
                                "filter_key": key_text,
                                "filter_type": type_text,
                                "filter_value": value_text,
                            })

                    items_payload = []
                    if has_collection_items:
                        c.execute(
                            "SELECT gallery_id FROM CollectionItems WHERE collection_id=? ORDER BY manual_order ASC, gallery_id DESC",
                            (int(collection_id),),
                        )
                        seen_gallery_ids = set()
                        for (gallery_id,) in c.fetchall():
                            gid = Helpers.normalise_integer(gallery_id)
                            if gid is None or gid in seen_gallery_ids:
                                continue
                            seen_gallery_ids.add(int(gid))
                            items_payload.append(int(gid))

                    c.execute(
                        "UPDATE Collections SET expressions=?, items=? WHERE id=?",
                        (
                            json.dumps(expressions_payload, ensure_ascii=True),
                            json.dumps(items_payload, ensure_ascii=True),
                            int(collection_id),
                        ),
                    )

                if has_collection_filters:
                    c.execute("DROP TABLE CollectionFilters")
                if has_collection_items:
                    c.execute("DROP TABLE CollectionItems")

            c.execute("PRAGMA table_info(Creators)")
            if "favourite" not in [row[1] for row in c.fetchall()]:
                c.execute("ALTER TABLE Creators ADD COLUMN favourite INTEGER DEFAULT 0")

            c.execute("UPDATE Collections SET expressions='[]' WHERE expressions IS NULL")
            c.execute("UPDATE Collections SET items='[]' WHERE items IS NULL")

            now = time.time()
            c.execute(
                "UPDATE CacheReferences SET expires_at = ? WHERE expires_at IS NULL",
                (now + CACHE_REFERENCES_TTL_SECONDS,),
            )
            c.execute(
                """
                UPDATE CachedMetadata
                SET expires_at = CASE
                    WHEN timestamp IS NOT NULL THEN timestamp + ?
                    ELSE ?
                END
                WHERE expires_at IS NULL
                """,
                (CACHED_METADATA_TTL_SECONDS, now + CACHED_METADATA_TTL_SECONDS),
            )

            # GalleryLocations schema migration
            c.execute("PRAGMA table_info(GalleryLocations)")
            gl_cols = {row[1] for row in c.fetchall()}

            if not gl_cols:
                c.execute("""
                    CREATE TABLE GalleryLocations (
                        gallery_id INTEGER NOT NULL,
                        location_id INTEGER NOT NULL,
                        download_path TEXT NOT NULL,
                        cover_path TEXT,
                        first_seen TEXT,
                        last_seen TEXT,
                        PRIMARY KEY (gallery_id, location_id),
                        FOREIGN KEY (gallery_id) REFERENCES Galleries(id),
                        FOREIGN KEY (location_id) REFERENCES DownloadLocations(id)
                    )
                """)
                c.execute("CREATE INDEX IF NOT EXISTS idx_gallerylocations_gallery_id ON GalleryLocations(gallery_id)")

            elif "root_path" in gl_cols and "location_id" not in gl_cols:
                c.execute("SELECT DISTINCT root_path, extension_used FROM GalleryLocations WHERE root_path IS NOT NULL AND TRIM(root_path) != ''")
                for root_path, ext_used in c.fetchall():
                    c.execute(
                        "INSERT OR IGNORE INTO DownloadLocations (root_path, extension_used) VALUES (?, ?)",
                        (root_path, ext_used or ""),
                    )
                c.execute("""
                    CREATE TABLE GalleryLocations_new (
                        gallery_id INTEGER NOT NULL,
                        location_id INTEGER NOT NULL,
                        download_path TEXT NOT NULL,
                        cover_path TEXT,
                        first_seen TEXT,
                        last_seen TEXT,
                        PRIMARY KEY (gallery_id, location_id),
                        FOREIGN KEY (gallery_id) REFERENCES Galleries(id),
                        FOREIGN KEY (location_id) REFERENCES DownloadLocations(id)
                    )
                """)
                c.execute("SELECT gallery_id, root_path, download_path, cover_path, first_seen, last_seen FROM GalleryLocations WHERE root_path IS NOT NULL AND TRIM(root_path) != ''")
                for gallery_id, root_path, dl_path, cov_path, f_seen, l_seen in c.fetchall():
                    c.execute("SELECT id FROM DownloadLocations WHERE root_path=?", (root_path,))
                    loc = c.fetchone()
                    if loc:
                        c.execute(
                            "INSERT OR IGNORE INTO GalleryLocations_new (gallery_id, location_id, download_path, cover_path, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                            (gallery_id, loc[0], dl_path or "", cov_path or "", f_seen or "", l_seen or ""),
                        )
                c.execute("DROP TABLE GalleryLocations")
                c.execute("ALTER TABLE GalleryLocations_new RENAME TO GalleryLocations")
                c.execute("CREATE INDEX IF NOT EXISTS idx_gallerylocations_gallery_id ON GalleryLocations(gallery_id)")

            # Backfill GalleryLocations from Galleries.download_path
            c.execute(
                "SELECT id, extension_used, download_path, cover_path, started_at, completed_at FROM Galleries WHERE download_path IS NOT NULL AND TRIM(download_path) != ''"
            )
            for gallery_id, extension_used, download_path, cover_path, started_at, completed_at in c.fetchall():
                root_path = Helpers.infer_location_root(download_path)
                if not root_path:
                    continue
                first_seen = started_at or completed_at or datetime.now(timezone.utc).isoformat()
                last_seen = completed_at or started_at or datetime.now(timezone.utc).isoformat()
                c.execute(
                    "INSERT OR IGNORE INTO DownloadLocations (root_path, extension_used) VALUES (?, ?)",
                    (root_path, extension_used or ""),
                )
                c.execute("SELECT id FROM DownloadLocations WHERE root_path=?", (root_path,))
                loc = c.fetchone()
                if loc:
                    c.execute(
                        """
                        INSERT INTO GalleryLocations (gallery_id, location_id, download_path, cover_path, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(gallery_id, location_id) DO UPDATE SET
                            download_path=excluded.download_path,
                            cover_path=excluded.cover_path,
                            last_seen=excluded.last_seen
                        """,
                        (gallery_id, loc[0], download_path, cover_path or "", first_seen, last_seen),
                    )

            # Normalise creator display_names
            c.execute("SELECT id, name FROM Creators")
            for creator_id, raw_name in c.fetchall():
                display_name = Helpers.sanitise(Helpers.safe_text(raw_name, ""))
                c.execute("UPDATE Creators SET display_name=? WHERE id=?", (display_name, creator_id))

            conn.commit()

    @staticmethod
    def close():
        return DB.close_connection()

    @staticmethod
    def close_connection():
        conn = getattr(_thread_local, "connection", None)
        if conn is not None:
            conn.close()
            _thread_local.connection = None

    @staticmethod
    def migrate():
        return DB.init_db()

    @staticmethod
    def schema():
        return DB.init_db()

    @staticmethod
    def migrations():
        return DB.init_db()

    @staticmethod
    def list_table_names() -> list[str]:
        DB.init_db()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            return [row[0] for row in cursor.fetchall()]

    @staticmethod
    def query_table(
        table_name: str,
        search: str = None,
        limit: int = 500,
        offset: int = 0,
        sort_by: str | None = None,
        sort_dir: str = "asc",
    ) -> dict:
        DB.init_db()
        allowed = DB.list_table_names()
        if table_name not in allowed:
            return {"columns": [], "rows": [], "error": f"Unknown table '{table_name}'"}

        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = [row[1] for row in cursor.fetchall()]

            safe_limit = max(1, int(limit or 500))
            safe_offset = max(0, int(offset or 0))
            safe_sort_dir = "desc" if str(sort_dir or "").strip().lower() == "desc" else "asc"
            safe_sort_by = str(sort_by or "").strip()
            has_sort = safe_sort_by in columns
            order_clause = ""
            if has_sort:
                quoted_col = '"' + safe_sort_by.replace('"', '""') + '"'
                order_clause = f" ORDER BY CAST({quoted_col} AS TEXT) COLLATE NOCASE {safe_sort_dir.upper()}"
            total_rows = 0

            if search and search.strip():
                like = f"%{search.strip()}%"
                conditions = " OR ".join(f"CAST({col} AS TEXT) LIKE ?" for col in columns)
                count_params = [like] * len(columns)
                cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {conditions}", count_params)
                total_rows = int((cursor.fetchone() or [0])[0] or 0)
                params = [like] * len(columns) + [safe_limit, safe_offset]
                cursor.execute(
                    f"SELECT * FROM {table_name} WHERE {conditions}{order_clause} LIMIT ? OFFSET ?",
                    params,
                )
            else:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                total_rows = int((cursor.fetchone() or [0])[0] or 0)
                cursor.execute(f"SELECT * FROM {table_name}{order_clause} LIMIT ? OFFSET ?", (safe_limit, safe_offset))

            rows = [list(row) for row in cursor.fetchall()]

        return {
            "columns": columns,
            "rows": rows,
            "total_rows": total_rows,
            "limit": safe_limit,
            "offset": safe_offset,
            "sort_by": safe_sort_by if has_sort else "",
            "sort_dir": safe_sort_dir,
        }

    @staticmethod
    def set_queued_galleries(ids):
        DB.init_db()
        normalised_ids = sorted(set(Helpers.normalise_integer_list(ids)))
        now = datetime.now(timezone.utc).isoformat()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM DownloadQueue WHERE LOWER(COALESCE(status, ''))='selected'")
            if normalised_ids:
                cursor.execute(
                    "INSERT INTO DownloadQueue (status, ids_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    ("selected", json.dumps(normalised_ids), now, now),
                )
            conn.commit()

    @staticmethod
    def _normalise_collection_type(value: str) -> str:
        text = Helpers.safe_text(value, "normal").strip().lower()
        return "smart" if text == "smart" else "normal"

    @staticmethod
    def _normalise_sort_mode(value: str, collection_type: str) -> str:
        if collection_type == "smart":
            return "id_desc"
        text = Helpers.safe_text(value, "id_desc").strip().lower()
        allowed = {"id_desc", "id_asc", "title_asc", "title_desc", "manual"}
        return text if text in allowed else "id_desc"

    @staticmethod
    def _normalise_collection_filter_type(value: str) -> str:
        text = Helpers.safe_text(value, "").strip().lower()
        allowed = {
            "id", "language", "tag", "creator", "favourite", "status",
            "rating_min", "rating_max", "page_min", "page_max",
        }
        return text if text in allowed else ""

    @staticmethod
    def _parse_bool_text(value: str) -> bool | None:
        text = Helpers.safe_text(value, "").strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return None

    @staticmethod
    def _parse_json_int_list(value) -> list[int]:
        if value is None:
            return []
        if isinstance(value, list):
            return [int(v) for v in Helpers.normalise_integer_list(value)]
        text = Helpers.safe_text(value, "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return []
        return [int(v) for v in Helpers.normalise_integer_list(parsed)]

    @staticmethod
    def _parse_collection_expressions(value) -> list[dict]:
        if value is None:
            return []
        parsed = value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except Exception:
                return []
        if not isinstance(parsed, list):
            return []

        expressions = []
        seen_keys = set()
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            key_text = Helpers.safe_text(entry.get("filter_key", ""), "").strip().upper()
            type_text = DB._normalise_collection_filter_type(entry.get("filter_type", ""))
            value_text = Helpers.safe_text(entry.get("filter_value", ""), "").strip()
            if not key_text or not re.fullmatch(r"F\d+", key_text):
                continue
            if key_text in seen_keys:
                continue
            if not type_text or not value_text:
                continue
            seen_keys.add(key_text)
            expressions.append({
                "filter_key": key_text,
                "filter_type": type_text,
                "filter_value": value_text,
            })
        return expressions

    @staticmethod
    def _read_collection_row(cursor, collection_id: int):
        cursor.execute(
            """
            SELECT id, name, description, collection_type, sort_mode, expressions,
                   smart_expression, items, created_at, updated_at, last_refreshed_at
            FROM Collections WHERE id=?
            """,
            (int(collection_id),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "name": Helpers.safe_text(row[1], ""),
            "description": Helpers.safe_text(row[2], ""),
            "collection_type": Helpers.safe_text(row[3], "normal"),
            "sort_mode": Helpers.safe_text(row[4], "id_desc"),
            "expressions": DB._parse_collection_expressions(row[5]),
            "smart_expression": Helpers.safe_text(row[6], ""),
            "items": DB._parse_json_int_list(row[7]),
            "created_at": Helpers.safe_text(row[8], ""),
            "updated_at": Helpers.safe_text(row[9], ""),
            "last_refreshed_at": Helpers.safe_text(row[10], ""),
        }

    @staticmethod
    def _collection_gallery_dataset(cursor) -> tuple[list[dict], dict[int, str], dict[int, str]]:
        cursor.execute("SELECT id, name FROM Tags")
        tag_map = {int(row[0]): Helpers.safe_text(row[1], "") for row in cursor.fetchall() if row[0] is not None}

        cursor.execute("SELECT id, name FROM Languages")
        language_map = {int(row[0]): Helpers.safe_text(row[1], "") for row in cursor.fetchall() if row[0] is not None}

        cursor.execute("SELECT id, name, display_name FROM Creators")
        creator_name_map: dict[int, set[str]] = {}
        for row in cursor.fetchall():
            creator_id = Helpers.normalise_integer(row[0])
            if creator_id is None:
                continue
            values = set()
            for text in (row[1], row[2]):
                value = Helpers.safe_text(text, "").strip().lower()
                if value:
                    values.add(value)
            creator_name_map[int(creator_id)] = values

        cursor.execute(
            """
            SELECT id, clean_title, raw_title, status, favourite, rating, num_pages,
                   creator_ids, tag_ids, language_ids
            FROM Galleries
            WHERE LOWER(COALESCE(status,'')) = 'completed'
            """
        )
        galleries = []
        for row in cursor.fetchall():
            gallery_id = Helpers.normalise_integer(row[0])
            if gallery_id is None:
                continue
            creator_ids = DB._parse_json_int_list(row[7])
            tag_ids = DB._parse_json_int_list(row[8])
            language_ids = DB._parse_json_int_list(row[9])
            creator_names = set()
            for creator_id in creator_ids:
                creator_names.update(creator_name_map.get(int(creator_id), set()))
            galleries.append({
                "id": int(gallery_id),
                "title": Helpers.safe_text(row[1] or row[2], ""),
                "status": Helpers.safe_text(row[3], "").strip().lower(),
                "favourite": bool(row[4]),
                "rating": Helpers.safe_float(row[5], 0.0),
                "num_pages": Helpers.normalise_integer(row[6]) or 0,
                "creator_names": creator_names,
                "tag_names": {Helpers.safe_text(tag_map.get(tag_id), "").strip().lower() for tag_id in tag_ids if tag_id in tag_map},
                "language_names": {Helpers.safe_text(language_map.get(language_id), "").strip().lower() for language_id in language_ids if language_id in language_map},
            })
        return galleries, tag_map, language_map

    @staticmethod
    def _filter_matches_gallery(filter_type: str, filter_value: str, gallery_row: dict) -> bool:
        text_value = Helpers.safe_text(filter_value, "").strip()
        if not text_value:
            return False
        lower_value = text_value.lower()

        if filter_type == "id":
            gid = Helpers.normalise_integer(text_value)
            return gid is not None and int(gallery_row.get("id") or 0) == int(gid)
        if filter_type == "language":
            return lower_value in (gallery_row.get("language_names") or set())
        if filter_type == "tag":
            return lower_value in (gallery_row.get("tag_names") or set())
        if filter_type == "creator":
            return lower_value in (gallery_row.get("creator_names") or set())
        if filter_type == "favourite":
            expected = DB._parse_bool_text(text_value)
            return expected is not None and bool(gallery_row.get("favourite")) == expected
        if filter_type == "status":
            return Helpers.safe_text(gallery_row.get("status"), "").strip().lower() == lower_value
        if filter_type == "rating_min":
            try:
                return Helpers.safe_float(gallery_row.get("rating"), 0.0) >= float(text_value)
            except Exception:
                return False
        if filter_type == "rating_max":
            try:
                return Helpers.safe_float(gallery_row.get("rating"), 0.0) <= float(text_value)
            except Exception:
                return False
        if filter_type == "page_min":
            try:
                return int(gallery_row.get("num_pages") or 0) >= int(float(text_value))
            except Exception:
                return False
        if filter_type == "page_max":
            try:
                return int(gallery_row.get("num_pages") or 0) <= int(float(text_value))
            except Exception:
                return False
        return False

    @staticmethod
    def _recompute_creator_rollups(cursor) -> None:
        cursor.execute("SELECT id FROM Creators")
        creator_ids = [int(cid) for cid in [Helpers.normalise_integer(row[0]) for row in cursor.fetchall()] if cid is not None]

        for creator_id in creator_ids:
            cursor.execute("SELECT id FROM Galleries WHERE creator_ids IS NOT NULL AND creator_ids != ''")
            related_gallery_ids = []
            for (gid,) in cursor.fetchall():
                gallery_id = Helpers.normalise_integer(gid)
                if gallery_id is None:
                    continue
                cursor.execute("SELECT creator_ids FROM Galleries WHERE id=?", (int(gallery_id),))
                row = cursor.fetchone()
                if not row:
                    continue
                if int(creator_id) in DB._parse_json_int_list(row[0]):
                    related_gallery_ids.append(int(gallery_id))

            tag_counter = {}
            for gallery_id in related_gallery_ids:
                cursor.execute("SELECT tag_ids FROM GalleryTags WHERE gallery_id=?", (int(gallery_id),))
                row = cursor.fetchone()
                if not row or not row[0]:
                    continue
                for tag_id in DB._parse_json_int_list(row[0]):
                    tag_counter[int(tag_id)] = tag_counter.get(int(tag_id), 0) + 1

            most_popular = [tid for tid, _ in sorted(tag_counter.items(), key=lambda item: item[1], reverse=True)[:15]]
            cursor.execute(
                "UPDATE Creators SET total_galleries=?, most_popular_tags=?, last_updated=? WHERE id=?",
                (len(related_gallery_ids), json.dumps(most_popular), datetime.now(timezone.utc).isoformat(), int(creator_id)),
            )

    @staticmethod
    def list_galleries(status=None):
        DB.init_db()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries WHERE status=?", (status,))
            else:
                cursor.execute("SELECT id, status, started_at, completed_at FROM Galleries")
            result = []
            for row in cursor.fetchall():
                gid = Helpers.normalise_integer(row[0])
                if gid is None:
                    continue
                result.append((gid, Helpers.safe_text(row[1], ""), Helpers.safe_text(row[2], ""), Helpers.safe_text(row[3], "")))
            return result

        @staticmethod
        def select_table(table_name: str, cols: list | None = None, where: str | None = None, params: tuple | None = None, limit: int | None = None) -> list[dict]:
            """Select rows from a table and return a list of dicts keyed by column name.

            - `cols` defaults to `None` meaning `*` (all columns).
            - `where` may include placeholders (`?`) and `params` will be bound.
            - `limit` can restrict returned rows.

            This helper takes the DB lock and creates its own connection so callers
            don't need to manage cursors or concern themselves with column ordering.
            """

            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cols_sql = ", ".join(cols) if cols else "*"
                sql = f"SELECT {cols_sql} FROM {table_name}"
                if where:
                    sql += " WHERE " + where
                if limit and isinstance(limit, int) and limit > 0:
                    sql += " LIMIT " + str(int(limit))
                cursor.execute(sql, params or ())
                return DB.rows_to_dicts(cursor)

    @staticmethod
    def remove_gallery_from_database(gallery_id: int) -> dict:
        DB.init_db()
        gid = Helpers.normalise_integer(gallery_id)
        if gid is None:
            return {"removed": False, "gallery_id": None}

        removed = False
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, items FROM Collections")
            now_iso = datetime.now(timezone.utc).isoformat()
            for row in cursor.fetchall():
                collection_id = Helpers.normalise_integer(row[0])
                if collection_id is None:
                    continue
                existing_ids = DB._parse_json_int_list(row[1])
                next_ids = [int(item_id) for item_id in existing_ids if int(item_id) != int(gid)]
                if len(next_ids) == len(existing_ids):
                    continue
                cursor.execute(
                    "UPDATE Collections SET items=?, updated_at=? WHERE id=?",
                    (json.dumps(next_ids, ensure_ascii=True), now_iso, int(collection_id)),
                )
            cursor.execute("DELETE FROM GalleryLocations WHERE gallery_id=?", (int(gid),))
            cursor.execute("DELETE FROM GalleryTags WHERE gallery_id=?", (int(gid),))
            cursor.execute("DELETE FROM GalleryLanguages WHERE gallery_id=?", (int(gid),))
            cursor.execute("DELETE FROM GalleryParodies WHERE gallery_id=?", (int(gid),))
            cursor.execute("DELETE FROM CachedMetadata WHERE gallery_id=?", (int(gid),))
            cursor.execute("DELETE FROM Galleries WHERE id=?", (int(gid),))
            removed = bool(cursor.rowcount)
            DB._recompute_creator_rollups(cursor)
            conn.commit()

        return {"removed": removed, "gallery_id": int(gid)}

    @staticmethod
    def upsert_gallery_location(gallery_id, extension_used=None, download_path=None, cover_path=None, first_seen=None, last_seen=None, root_path=None):
        DB.init_db()
        gallery_id = Helpers.normalise_integer(gallery_id)
        download_path = Helpers.safe_text(download_path, "")
        if gallery_id is None or not download_path:
            return
        extension_used = Helpers.safe_text(extension_used, "")
        cover_path = Helpers.safe_text(cover_path, "")
        explicit_root = Helpers.safe_text(root_path, "")
        if explicit_root:
            explicit_root = os.path.normpath(explicit_root)
        root_path = explicit_root if explicit_root else Helpers.infer_location_root(download_path)
        if not root_path:
            return
        first_seen = Helpers.safe_text(first_seen, "") or datetime.now(timezone.utc).isoformat()
        last_seen = Helpers.safe_text(last_seen, "") or first_seen
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO DownloadLocations (root_path, extension_used) VALUES (?, ?)",
                (root_path, extension_used),
            )
            cursor.execute("SELECT id FROM DownloadLocations WHERE root_path=?", (root_path,))
            loc = cursor.fetchone()
            if not loc:
                return
            cursor.execute(
                """
                INSERT INTO GalleryLocations (gallery_id, location_id, download_path, cover_path, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(gallery_id, location_id) DO UPDATE SET
                    download_path=excluded.download_path,
                    cover_path=excluded.cover_path,
                    last_seen=excluded.last_seen
                """,
                (gallery_id, loc[0], download_path, cover_path, first_seen, last_seen),
            )
            conn.commit()

    @staticmethod
    def list_download_locations() -> list[dict]:
        DB.init_db()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT dl.id, dl.root_path, dl.extension_used, COUNT(gl.gallery_id) AS gallery_count
                FROM DownloadLocations dl
                LEFT JOIN GalleryLocations gl ON gl.location_id = dl.id
                GROUP BY dl.id
                ORDER BY dl.extension_used, dl.root_path
            """)
            return [
                {
                    "id": Helpers.normalise_integer(row[0]),
                    "root_path": Helpers.safe_text(row[1], ""),
                    "extension_used": Helpers.safe_text(row[2], ""),
                    "count": Helpers.normalise_integer(row[3]) or 0,
                }
                for row in cursor.fetchall()
            ]

    @staticmethod
    def upsert_download_location(root_path: str, extension_used: str = ""):
        DB.init_db()
        root_path = Helpers.safe_text(root_path, "").strip()
        if not root_path:
            return
        root_path = os.path.normpath(root_path)
        extension_used = Helpers.safe_text(extension_used, "").strip()
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO DownloadLocations (root_path, extension_used)
                VALUES (?, ?)
                ON CONFLICT(root_path) DO UPDATE SET
                    extension_used = CASE
                        WHEN excluded.extension_used IS NOT NULL AND TRIM(excluded.extension_used) != ''
                        THEN excluded.extension_used
                        ELSE DownloadLocations.extension_used
                    END
                """,
                (root_path, extension_used),
            )
            conn.commit()

    @staticmethod
    def list_gallery_locations(gallery_id=None, root_path=None) -> list[dict]:
        DB.init_db()
        query = """
            SELECT gl.gallery_id, dl.extension_used, dl.root_path, gl.download_path,
                   gl.cover_path, gl.first_seen, gl.last_seen, dl.id
            FROM GalleryLocations gl
            JOIN DownloadLocations dl ON dl.id = gl.location_id
        """
        clauses = []
        params = []
        gid = Helpers.normalise_integer(gallery_id)
        root_path_filter = Helpers.safe_text(root_path, "")
        if gid is not None:
            clauses.append("gl.gallery_id=?")
            params.append(gid)
        if root_path_filter:
            clauses.append("dl.root_path=?")
            params.append(root_path_filter)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY dl.root_path, gl.download_path"
        with db_lock, DB.dbconnect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return [
                {
                    "gallery_id": Helpers.normalise_integer(row[0]),
                    "extension_used": Helpers.safe_text(row[1], ""),
                    "root_path": Helpers.safe_text(row[2], ""),
                    "download_path": Helpers.safe_text(row[3], ""),
                    "cover_path": Helpers.safe_text(row[4], ""),
                    "first_seen": Helpers.safe_text(row[5], ""),
                    "last_seen": Helpers.safe_text(row[6], ""),
                    "location_id": Helpers.normalise_integer(row[7]),
                }
                for row in cursor.fetchall()
            ]

    @staticmethod
    def database_cleanup() -> dict:
        DB.init_db()

        stats = {
            "cache_entries_pruned": 0,
            "pruned_roots": 0,
            "pruned_gallery_locations": 0,
            "removed_galleries": 0,
            "page_checks": 0,
            "pages_downloaded": 0,
            "smart_collections_refreshed": 0,
            "errors": [],
        }

        try:
            logger.debug("[DATABASE_CLEANUP] Pruning expired cache entries...")
            stats["cache_entries_pruned"] = prune_all_caches()

            logger.debug("[DATABASE_CLEANUP] Pruning unmanaged download locations...")
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, root_path FROM DownloadLocations")
                dl_rows = cursor.fetchall()

                for loc_id, root_path in dl_rows:
                    loc_id = Helpers.normalise_integer(loc_id)
                    root_text = Helpers.safe_text(root_path, "").strip()
                    if loc_id is None or not root_text:
                        continue
                    marker_path = os.path.join(root_text, DOWNLOAD_ROOT_MARKER_FILE)
                    if os.path.isfile(marker_path):
                        continue
                    cursor.execute("DELETE FROM GalleryLocations WHERE location_id=?", (loc_id,))
                    gl_deleted = cursor.rowcount if cursor.rowcount is not None else 0
                    cursor.execute("DELETE FROM DownloadLocations WHERE id=?", (loc_id,))
                    dl_deleted = cursor.rowcount if cursor.rowcount is not None else 0
                    if gl_deleted > 0:
                        stats["pruned_gallery_locations"] += int(gl_deleted)
                    if dl_deleted > 0:
                        stats["pruned_roots"] += int(dl_deleted)
                conn.commit()

            logger.debug("[DATABASE_CLEANUP] Scanning for orphaned galleries...")
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT gl.gallery_id, gl.download_path, gl.location_id
                    FROM GalleryLocations gl
                    JOIN DownloadLocations dl ON dl.id = gl.location_id
                """)
                location_rows = cursor.fetchall()

            orphaned_galleries = []
            for gallery_id, download_path, location_id in location_rows:
                gid = Helpers.normalise_integer(gallery_id)
                dpath = Helpers.safe_text(download_path, "").strip()
                if gid is None or not dpath:
                    continue
                if not os.path.exists(dpath):
                    orphaned_galleries.append((gid, location_id, dpath))

            if orphaned_galleries:
                with db_lock, DB.dbconnect() as conn:
                    cursor = conn.cursor()
                    for gid, location_id, dpath in orphaned_galleries:
                        try:
                            cursor.execute("DELETE FROM GalleryLocations WHERE gallery_id=? AND location_id=?", (gid, location_id))
                            cursor.execute("SELECT COUNT(*) FROM GalleryLocations WHERE gallery_id=?", (gid,))
                            remaining_row = cursor.fetchone()
                            remaining = int(remaining_row[0]) if remaining_row and remaining_row[0] is not None else 0
                            if remaining == 0:
                                cursor.execute("DELETE FROM Galleries WHERE id=?", (gid,))
                                stats["removed_galleries"] += 1
                                logger.info(f"[DATABASE_CLEANUP] Removed orphaned gallery {gid}")
                        except Exception as e:
                            stats["errors"].append(f"Error removing gallery {gid}: {e}")
                            logger.warning(f"[DATABASE_CLEANUP] Error removing gallery {gid}: {e}")
                    conn.commit()

            logger.debug("[DATABASE_CLEANUP] Checking gallery page counts...")
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, num_pages, download_path
                    FROM Galleries
                    WHERE status = 'completed' AND num_pages > 0
                """)
                gallery_rows = cursor.fetchall()

            missing_pages = []
            for gallery_id, num_pages, download_path in gallery_rows:
                gid = Helpers.normalise_integer(gallery_id)
                num_p = Helpers.normalise_integer(num_pages) or 0
                dpath = Helpers.safe_text(download_path, "").strip()
                if gid is None or num_p <= 0 or not dpath or not os.path.exists(dpath):
                    continue
                stats["page_checks"] += 1
                try:
                    if os.path.isdir(dpath):
                        image_files = [f for f in os.listdir(dpath) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))]
                        actual_count = len(image_files)
                    elif dpath.endswith(('.cbz', '.zip')):
                        try:
                            import zipfile
                            with zipfile.ZipFile(dpath, 'r') as z:
                                actual_count = len([f for f in z.namelist() if not f.endswith('/') and f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))])
                        except Exception:
                            actual_count = -1
                    else:
                        continue
                    if actual_count >= 0 and actual_count < num_p:
                        missing_pages.append((gid, num_p, actual_count, num_p - actual_count, dpath))
                except Exception as e:
                    logger.debug(f"[DATABASE_CLEANUP] Error checking pages for gallery {gid}: {e}")

            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                DB._recompute_creator_rollups(cursor)
                conn.commit()

            try:
                refresh_result = DB.Collection.refresh_all_smart()
                stats["smart_collections_refreshed"] = int(refresh_result.get("refreshed") or 0)
                for err in refresh_result.get("errors") or []:
                    stats["errors"].append(str(err))
            except Exception as e:
                stats["errors"].append(f"Smart collection refresh failed: {e}")
                logger.warning(f"[DATABASE_CLEANUP] Smart collection refresh failed: {e}")

            stats["page_checks"] = len(missing_pages)
            logger.info(f"[DATABASE_CLEANUP] Cleanup complete: {stats}")

        except Exception as e:
            logger.error(f"[DATABASE_CLEANUP] Fatal error during cleanup: {e}")
            stats["errors"].append(f"Fatal error: {e}")

        return stats

    ####################################################################################################################
    # INNER CLASSES
    ####################################################################################################################

    class DownloadQueue:
        @staticmethod
        def _row_to_dict(row) -> dict | None:
            if not row:
                return None
            try:
                ids = json.loads(row[2]) if row[2] else []
            except Exception:
                ids = []
            return {
                "download_no": Helpers.normalise_integer(row[0]),
                "status": Helpers.safe_text(row[1], "queued"),
                "ids": Helpers.normalise_integer_list(ids),
                "created_at": Helpers.safe_text(row[3], ""),
                "updated_at": Helpers.safe_text(row[4], ""),
            }

        @staticmethod
        def enqueue(ids: list[int]) -> dict | None:
            DB.init_db()
            normalised_ids = sorted(set(Helpers.normalise_integer_list(ids)))
            if not normalised_ids:
                return None
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO DownloadQueue (status, ids_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    ("queued", json.dumps(normalised_ids), now, now),
                )
                download_no = Helpers.normalise_integer(cursor.lastrowid)
                conn.commit()
                return {
                    "download_no": download_no,
                    "status": "queued",
                    "ids": normalised_ids,
                    "created_at": now,
                    "updated_at": now,
                }

        @staticmethod
        def list(statuses: list[str] | None = None) -> list[dict]:
            DB.init_db()
            if statuses is None:
                statuses = ["queued", "running"]
            normalised_statuses = [s for s in [Helpers.safe_text(s, "").strip().lower() for s in (statuses or [])] if s]
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                if normalised_statuses:
                    placeholders = ",".join("?" for _ in normalised_statuses)
                    cursor.execute(
                        f"SELECT download_no, status, ids_json, created_at, updated_at FROM DownloadQueue WHERE LOWER(COALESCE(status, '')) IN ({placeholders}) ORDER BY download_no ASC",
                        tuple(normalised_statuses),
                    )
                else:
                    cursor.execute("SELECT download_no, status, ids_json, created_at, updated_at FROM DownloadQueue ORDER BY download_no ASC")
                rows = []
                for row in cursor.fetchall():
                    parsed = DB.DownloadQueue._row_to_dict(row)
                    if parsed and parsed.get("download_no") is not None:
                        rows.append(parsed)
                return rows

        @staticmethod
        def next_queued() -> dict | None:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT download_no, status, ids_json, created_at, updated_at FROM DownloadQueue WHERE LOWER(COALESCE(status,''))='queued' ORDER BY download_no ASC LIMIT 1"
                )
                return DB.DownloadQueue._row_to_dict(cursor.fetchone())

        @staticmethod
        def set_status(download_no: int, status: str) -> bool:
            DB.init_db()
            job_no = Helpers.normalise_integer(download_no)
            safe_status = Helpers.safe_text(status, "").strip().lower()
            if job_no is None or safe_status not in {"queued", "running"}:
                return False
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE DownloadQueue SET status=?, updated_at=? WHERE download_no=?",
                    (safe_status, now, int(job_no)),
                )
                conn.commit()
                return bool(cursor.rowcount)

        @staticmethod
        def remove(download_no: int) -> bool:
            DB.init_db()
            job_no = Helpers.normalise_integer(download_no)
            if job_no is None:
                return False
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM DownloadQueue WHERE download_no=?", (int(job_no),))
                conn.commit()
                return bool(cursor.rowcount)

        @staticmethod
        def clear() -> int:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM DownloadQueue")
                deleted = cursor.rowcount if cursor.rowcount is not None else 0
                conn.commit()
                return int(deleted)

        @staticmethod
        def remove_by_status(status: str) -> int:
            DB.init_db()
            safe_status = Helpers.safe_text(status, "").strip().lower()
            if not safe_status:
                return 0
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM DownloadQueue WHERE LOWER(COALESCE(status, ''))=?", (safe_status,))
                deleted = cursor.rowcount if cursor.rowcount is not None else 0
                conn.commit()
                return int(deleted)

    class Collection:
        @staticmethod
        def list() -> list[dict]:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, name, description, collection_type, sort_mode,
                           expressions, smart_expression, items, created_at, updated_at, last_refreshed_at
                    FROM Collections ORDER BY LOWER(name)
                    """
                )
                rows = []
                for row in cursor.fetchall():
                    expressions = DB._parse_collection_expressions(row[5])
                    item_ids = DB._parse_json_int_list(row[7])
                    ctype = DB._normalise_collection_type(row[3])
                    total_count = len(item_ids)
                    rows.append({
                        "id": int(row[0]),
                        "name": Helpers.safe_text(row[1], ""),
                        "description": Helpers.safe_text(row[2], ""),
                        "collection_type": ctype,
                        "sort_mode": Helpers.safe_text(row[4], "id_desc"),
                        "expressions": expressions,
                        "smart_expression": Helpers.safe_text(row[6], ""),
                        "items": item_ids,
                        "created_at": Helpers.safe_text(row[8], ""),
                        "updated_at": Helpers.safe_text(row[9], ""),
                        "last_refreshed_at": Helpers.safe_text(row[10], ""),
                        "manual_count": int(total_count) if ctype != "smart" else 0,
                        "rule_count": int(total_count) if ctype == "smart" else 0,
                        "total_count": int(total_count),
                    })
                return rows

        @staticmethod
        def get(collection_id: int) -> dict | None:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                return DB._read_collection_row(cursor, int(collection_id))

        @staticmethod
        def create(name: str, description: str = "", collection_type: str = "normal", sort_mode: str = "id_desc") -> dict:
            DB.init_db()
            now = datetime.now(timezone.utc).isoformat()
            ctype = DB._normalise_collection_type(collection_type)
            smode = DB._normalise_sort_mode(sort_mode, ctype)
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO Collections (name, description, collection_type, sort_mode, expressions, smart_expression, items, created_at, updated_at, last_refreshed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (Helpers.safe_text(name, "").strip(), Helpers.safe_text(description, ""), ctype, smode, "[]", "", "[]", now, now, ""),
                )
                conn.commit()
                collection_id = int(cursor.lastrowid)
                return DB.Collection.get(collection_id) or {"id": collection_id}

        @staticmethod
        def update(collection_id: int, name: str | None = None, description: str | None = None, sort_mode: str | None = None) -> dict | None:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                collection = DB._read_collection_row(cursor, int(collection_id))
                if not collection:
                    return None
                ctype = DB._normalise_collection_type(collection.get("collection_type", "normal"))
                new_name = Helpers.safe_text(name, collection["name"]).strip()
                new_description = Helpers.safe_text(description, collection.get("description", ""))
                new_sort = DB._normalise_sort_mode(sort_mode or collection.get("sort_mode", "id_desc"), ctype)
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    "UPDATE Collections SET name=?, description=?, sort_mode=?, updated_at=? WHERE id=?",
                    (new_name, new_description, new_sort, now, int(collection_id)),
                )
                conn.commit()
                return DB.Collection.get(int(collection_id))

        @staticmethod
        def delete(collection_id: int) -> bool:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM Collections WHERE id=?", (int(collection_id),))
                conn.commit()
                return bool(cursor.rowcount)

        @staticmethod
        def set_filters(collection_id: int, filters: list[dict]) -> dict | None:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                collection = DB._read_collection_row(cursor, int(collection_id))
                if not collection:
                    return None
                cleaned = []
                seen = set()
                for entry in (filters or []):
                    if not isinstance(entry, dict):
                        continue
                    key = Helpers.safe_text(entry.get("filter_key", ""), "").strip().upper()
                    ftype = DB._normalise_collection_filter_type(entry.get("filter_type", ""))
                    fvalue = Helpers.safe_text(entry.get("filter_value", ""), "").strip()
                    if not key or not re.fullmatch(r"F\d+", key):
                        continue
                    if key in seen:
                        raise ValueError(f"Duplicate filter key '{key}'.")
                    if not ftype or not fvalue:
                        raise ValueError(f"Filter '{key}' requires a valid type and value.")
                    seen.add(key)
                    cleaned.append({"filter_key": key, "filter_type": ftype, "filter_value": fvalue})
                cursor.execute(
                    "UPDATE Collections SET expressions=?, updated_at=? WHERE id=?",
                    (json.dumps(cleaned, ensure_ascii=True), datetime.now(timezone.utc).isoformat(), int(collection_id)),
                )
                conn.commit()
            return DB.Collection.get(int(collection_id))

        @staticmethod
        def set_smart_rule(collection_id: int, expression: str, clear_existing_items: bool = True) -> dict | None:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                collection = DB._read_collection_row(cursor, int(collection_id))
                if not collection:
                    return None
                if DB._normalise_collection_type(collection.get("collection_type", "normal")) != "smart":
                    raise ValueError("Smart rule can only be set for smart collections.")
                rpn = smart_expression_to_rpn(expression)
                if not rpn:
                    raise ValueError("Smart expression cannot be empty.")
                expressions = DB._parse_collection_expressions(collection.get("expressions"))
                known_filters = {Helpers.safe_text(entry.get("filter_key"), "").strip().upper() for entry in expressions}
                used_filters = {token for token in rpn if re.fullmatch(r"F\d+", token)}
                missing = sorted(filter_key for filter_key in used_filters if filter_key not in known_filters)
                if missing:
                    raise ValueError(f"Expression references unknown filters: {', '.join(missing)}")
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    "UPDATE Collections SET smart_expression=?, updated_at=? WHERE id=?",
                    (Helpers.safe_text(expression, "").strip(), now, int(collection_id)),
                )
                if clear_existing_items:
                    cursor.execute("UPDATE Collections SET items='[]' WHERE id=?", (int(collection_id),))
                conn.commit()
            DB.Collection.refresh_smart(int(collection_id))
            return DB.Collection.get(int(collection_id))

        @staticmethod
        def add_galleries(collection_id: int, gallery_ids: list[int], manual: bool = True) -> dict:
            DB.init_db()
            ids = sorted({int(gid) for gid in Helpers.normalise_integer_list(gallery_ids)})
            if not ids:
                return {"added": 0, "ids": []}
            now = datetime.now(timezone.utc).isoformat()
            added = 0
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                collection = DB._read_collection_row(cursor, int(collection_id))
                if not collection:
                    return {"added": 0, "ids": []}
                existing_ids = DB._parse_json_int_list(collection.get("items"))
                existing_set = set(existing_ids)
                for gid in ids:
                    cursor.execute("SELECT id FROM Galleries WHERE id=?", (int(gid),))
                    if not cursor.fetchone():
                        continue
                    if int(gid) in existing_set:
                        continue
                    existing_ids.append(int(gid))
                    existing_set.add(int(gid))
                    added += 1
                cursor.execute(
                    "UPDATE Collections SET items=?, updated_at=? WHERE id=?",
                    (json.dumps(existing_ids, ensure_ascii=True), now, int(collection_id)),
                )
                conn.commit()
            return {"added": added, "ids": ids}

        @staticmethod
        def add_creator_snapshot(collection_id: int, creator_name: str) -> dict:
            DB.init_db()
            creator_text = Helpers.safe_text(creator_name, "").strip().lower()
            if not creator_text:
                return {"added": 0, "ids": []}
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                galleries, _, _ = DB._collection_gallery_dataset(cursor)
                ids = [int(row["id"]) for row in galleries if creator_text in (row.get("creator_names") or set())]
            return DB.Collection.add_galleries(int(collection_id), ids, manual=True)

        @staticmethod
        def remove_gallery(collection_id: int, gallery_id: int) -> bool:
            DB.init_db()
            gid = Helpers.normalise_integer(gallery_id)
            if gid is None:
                return False
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                collection = DB._read_collection_row(cursor, int(collection_id))
                if not collection:
                    return False
                existing_ids = DB._parse_json_int_list(collection.get("items"))
                next_ids = [int(item_id) for item_id in existing_ids if int(item_id) != int(gid)]
                changed = len(next_ids) != len(existing_ids)
                if changed:
                    cursor.execute(
                        "UPDATE Collections SET items=?, updated_at=? WHERE id=?",
                        (json.dumps(next_ids, ensure_ascii=True), datetime.now(timezone.utc).isoformat(), int(collection_id)),
                    )
                conn.commit()
                return changed

        @staticmethod
        def clear_items(collection_id: int) -> bool:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                collection = DB._read_collection_row(cursor, int(collection_id))
                if not collection:
                    return False
                changed = bool(DB._parse_json_int_list(collection.get("items")))
                if changed:
                    cursor.execute(
                        "UPDATE Collections SET items='[]', updated_at=? WHERE id=?",
                        (datetime.now(timezone.utc).isoformat(), int(collection_id)),
                    )
                conn.commit()
                return changed

        @staticmethod
        def refresh_smart(collection_id: int) -> dict:
            DB.init_db()
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                collection = DB._read_collection_row(cursor, int(collection_id))
                if not collection:
                    return {"updated": False, "matched": 0}
                if DB._normalise_collection_type(collection.get("collection_type", "normal")) != "smart":
                    return {"updated": False, "matched": 0}
                expression = Helpers.safe_text(collection.get("smart_expression"), "").strip()
                if not expression:
                    cursor.execute(
                        "UPDATE Collections SET items='[]', last_refreshed_at=?, updated_at=? WHERE id=?",
                        (now, now, int(collection_id)),
                    )
                    conn.commit()
                    return {"updated": True, "matched": 0}
                rpn = smart_expression_to_rpn(expression)
                expressions = DB._parse_collection_expressions(collection.get("expressions"))
                filter_map = {
                    Helpers.safe_text(expr.get("filter_key"), "").strip().upper(): {
                        "type": Helpers.safe_text(expr.get("filter_type"), "").strip().lower(),
                        "value": Helpers.safe_text(expr.get("filter_value"), "").strip(),
                    }
                    for expr in expressions
                    if Helpers.safe_text(expr.get("filter_key"), "").strip()
                }
                referenced_keys = {token for token in rpn if re.fullmatch(r"F\d+", token)}
                missing = sorted([key for key in referenced_keys if key not in filter_map])
                if missing:
                    raise ValueError(f"Smart expression references unknown filters: {', '.join(missing)}")
                galleries, _, _ = DB._collection_gallery_dataset(cursor)
                matched_gallery_ids = []
                for gallery_row in galleries:
                    result_map = {}
                    for key in referenced_keys:
                        rule = filter_map.get(key)
                        result_map[key] = DB._filter_matches_gallery(rule["type"], rule["value"], gallery_row) if rule else False
                    if evaluate_smart_rpn(rpn, result_map):
                        matched_gallery_ids.append(int(gallery_row["id"]))
                matched_ids = sorted(set(int(gid) for gid in matched_gallery_ids))
                cursor.execute(
                    "UPDATE Collections SET items=?, last_refreshed_at=?, updated_at=? WHERE id=?",
                    (json.dumps(matched_ids, ensure_ascii=True), now, now, int(collection_id)),
                )
                conn.commit()
                return {"updated": True, "matched": len(matched_ids)}

        @staticmethod
        def refresh_all_smart() -> dict:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM Collections WHERE LOWER(collection_type)='smart'")
                ids = [int(row[0]) for row in cursor.fetchall() if row and row[0] is not None]
            refreshed = 0
            total_matches = 0
            errors = []
            for collection_id in ids:
                try:
                    result = DB.Collection.refresh_smart(int(collection_id))
                    if result.get("updated"):
                        refreshed += 1
                    total_matches += int(result.get("matched") or 0)
                except Exception as exc:
                    errors.append(f"Collection {collection_id}: {exc}")
            return {"refreshed": refreshed, "matched": total_matches, "errors": errors}

        @staticmethod
        def items(collection_id: int) -> list[dict]:
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                collection = DB._read_collection_row(cursor, int(collection_id))
                if not collection:
                    return []
                item_ids = DB._parse_json_int_list(collection.get("items"))
                if not item_ids:
                    return []
                sort_mode = DB._normalise_sort_mode(
                    collection.get("sort_mode", "id_desc"),
                    DB._normalise_collection_type(collection.get("collection_type", "normal")),
                )
                placeholders = ",".join("?" for _ in item_ids)
                cursor.execute(
                    "SELECT g.id, g.clean_title, g.raw_title, g.num_pages, g.status, g.favourite, g.rating "
                    "FROM Galleries g WHERE g.id IN (" + placeholders + ")",
                    tuple(int(gid) for gid in item_ids),
                )
                by_id = {}
                for row in cursor.fetchall():
                    gid = Helpers.normalise_integer(row[0])
                    if gid is None:
                        continue
                    by_id[int(gid)] = {
                        "gallery_id": int(gid),
                        "title": Helpers.safe_text(row[1] or row[2], ""),
                        "page_count": Helpers.normalise_integer(row[3]) or 0,
                        "status": Helpers.safe_text(row[4], ""),
                        "favourite": bool(row[5]),
                        "rating": Helpers.safe_float(row[6], 0.0) if row[6] is not None else None,
                    }
                row_items = [by_id[int(gid)] for gid in item_ids if int(gid) in by_id]
                if sort_mode == "id_desc":
                    row_items.sort(key=lambda item: int(item.get("gallery_id") or 0), reverse=True)
                elif sort_mode == "id_asc":
                    row_items.sort(key=lambda item: int(item.get("gallery_id") or 0))
                elif sort_mode == "title_asc":
                    row_items.sort(key=lambda item: Helpers.safe_text(item.get("title"), "").lower())
                elif sort_mode == "title_desc":
                    row_items.sort(key=lambda item: Helpers.safe_text(item.get("title"), "").lower(), reverse=True)
                ctype = DB._normalise_collection_type(collection.get("collection_type", "normal"))
                for index, item in enumerate(row_items, start=1):
                    item["is_manual"] = ctype != "smart"
                    item["is_rule"] = ctype == "smart"
                    item["manual_order"] = index
                return row_items

    class Gallery:
        @staticmethod
        def status(gallery_id):
            return DB.Gallery._get_status(gallery_id)

        @staticmethod
        def _get_status(gallery_id):
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return None
            DB.init_db()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM Galleries WHERE id=?", (gallery_id,))
                row = cursor.fetchone()
                return Helpers.safe_text(row[0], "") if row else None

        @staticmethod
        def start(gallery_id, download_path=None, extension_used=None):
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return
            download_path = Helpers.safe_text(download_path, "")
            extension_used = Helpers.safe_text(extension_used, "")
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT extension_used FROM Galleries WHERE id=?", (gallery_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    cursor.execute(
                        """
                        INSERT INTO Galleries (id, status, started_at, download_path)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            status=excluded.status,
                            started_at=excluded.started_at,
                            download_path=excluded.download_path
                        """,
                        (gallery_id, "started", now, download_path),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO Galleries (id, status, started_at, download_path, extension_used)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            status=excluded.status,
                            started_at=excluded.started_at,
                            download_path=excluded.download_path,
                            extension_used=excluded.extension_used
                        """,
                        (gallery_id, "started", now, download_path, extension_used),
                    )
                conn.commit()
            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as started.")

        @staticmethod
        def skip(gallery_id):
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE Galleries SET status=?, completed_at=? WHERE id=?", ("skipped", now, gallery_id))
                conn.commit()
            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as skipped.")

        @staticmethod
        def fail(gallery_id):
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return
            now = datetime.now(timezone.utc).isoformat()
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE Galleries SET status=?, completed_at=? WHERE id=?", ("failed", now, gallery_id))
                conn.commit()
            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as failed.")

        @staticmethod
        def complete(gallery_id):
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return
            now = datetime.now(timezone.utc).isoformat()
            cache = read_cached_metadata_entry(ids=[gallery_id])
            meta = None
            for gid, entry in cache["metadata"].items():
                meta = entry.get("clean_metadata") or {}
                break

            download_path = None
            cover_path = None
            extension_used = None
            started_at = None
            ext_download_path = ""
            cleaned_creator = "Unknown"
            ext = "cbz"
            is_archive = True
            gallery_title = ""

            if meta and meta.get("clean_title"):
                with db_lock, DB.dbconnect() as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE Galleries SET clean_title=? WHERE id=?", (meta["clean_title"], gallery_id))
                    conn.commit()

            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT clean_title FROM Galleries WHERE id=?", (gallery_id,))
                row = cursor.fetchone()
                if row and isinstance(row[0], str) and row[0].strip():
                    gallery_title = row[0].strip()

            if meta:
                ext_download_path = meta.get("extension_download_path") or meta.get("download_path") or None
                base_ext_path = None
                try:
                    from mangascraper.extensions.extension_manager import calculate_extension_download_path
                    ext_name = meta.get("extension_used") or meta.get("extension") or getattr(orchestrator, "extension", "skeleton")
                    base_ext_path = calculate_extension_download_path(ext_name)
                except Exception:
                    base_ext_path = getattr(orchestrator, "extension_download_path", "/opt/manga-scraper/downloads/")
                if not ext_download_path:
                    ext_download_path = base_ext_path
                elif not os.path.isabs(ext_download_path):
                    ext_download_path = os.path.join(base_ext_path, ext_download_path)

                primary_creator = None
                if "artists" in meta and isinstance(meta["artists"], list) and meta["artists"]:
                    primary_creator = meta["artists"][0]
                elif "groups" in meta and isinstance(meta["groups"], list) and meta["groups"]:
                    primary_creator = meta["groups"][0]
                else:
                    primary_creator = "Unknown"
                cleaned_creator = Helpers.choose_creator_folder_name(
                    raw_name=primary_creator,
                    base_path=ext_download_path,
                    fallback_name=Helpers.sanitise(primary_creator),
                )
                ext = meta.get("archive_ext") or meta.get("ext") or "cbz"
                is_archive = meta.get("is_archive", True)
                started_at = meta.get("started_at")

            if gallery_title:
                # Always store download_path in the canonical format: "(<id>) <clean_title>"
                gallery_base = f"({int(gallery_id)}) {gallery_title}"
                if is_archive:
                    download_path = os.path.join(ext_download_path, cleaned_creator, f"{gallery_base}.{ext}")
                else:
                    download_path = os.path.join(ext_download_path, cleaned_creator, gallery_base)
                cover_path = os.path.join(ext_download_path, cleaned_creator, ".covers", gallery_base)
            else:
                download_path = ""
                cover_path = ""

            if not started_at:
                with db_lock, DB.dbconnect() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT started_at FROM Galleries WHERE id=?", (gallery_id,))
                    row = cursor.fetchone()
                    if row and row[0]:
                        started_at = row[0]

            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT extension_used FROM Galleries WHERE id=?", (gallery_id,))
                row = cursor.fetchone()
                extension_used = row[0] if row and row[0] else (meta.get("extension_used") or meta.get("extension") or None)
                cursor.execute(
                    "UPDATE Galleries SET status=?, completed_at=?, download_path=?, cover_path=?, extension_used=?, started_at=? WHERE id=?",
                    ("completed", now, download_path, cover_path, extension_used, started_at, gallery_id),
                )
                conn.commit()

            DB.upsert_gallery_location(
                gallery_id,
                extension_used=extension_used,
                download_path=download_path,
                cover_path=cover_path,
                first_seen=started_at or now,
                last_seen=now,
                root_path=ext_download_path,
            )

            cache = read_cached_metadata_entry(ids=[gallery_id])
            creators = {}
            tags = {}
            languages = {}
            parodies = {}
            galleries = {}
            gallery_tags = {}
            gallery_languages = {}
            gallery_parodies = {}

            for gid, entry in cache["metadata"].items():
                meta = entry.get("clean_metadata") or {}
                raw_title = Helpers.safe_text(meta.get("raw_title") or meta.get("title") or f"Gallery_{gid}")
                clean_title = Helpers.safe_text(meta.get("clean_title") or meta.get("title") or f"Gallery_{gid}")
                num_pages = Helpers.normalise_integer(meta.get("num_pages") or meta.get("pages") or 0) or 0

                creator_names = []
                creator_types = {}
                if "artists" in meta and isinstance(meta["artists"], list):
                    creator_names.extend(meta["artists"])
                    for artist in meta["artists"]:
                        creator_types[artist] = "artist"
                if "groups" in meta and isinstance(meta["groups"], list):
                    creator_names.extend(meta["groups"])
                    for group in meta["groups"]:
                        creator_types[group] = "group"

                tag_names = meta.get("tags") or []
                if isinstance(tag_names, str):
                    tag_names = [tag_names]
                language_names = meta.get("languages") or meta.get("language") or []
                if isinstance(language_names, str):
                    language_names = [language_names]
                parody_names = meta.get("parodies") or meta.get("parody") or []
                if isinstance(parody_names, str):
                    parody_names = [parody_names]
                # Normalise parody names and default empty values to 'original'
                normalised_parody_names = []
                for p in parody_names:
                    pname = str(p or "").strip()
                    if not pname:
                        pname = "original"
                    normalised_parody_names.append(pname)
                parody_names = normalised_parody_names

                for cname in creator_names:
                    ctype = creator_types.get(cname, None)
                    creators.setdefault(cname, {"display_name": cname, "creator_type": ctype, "first_seen": None, "last_updated": None, "total_galleries": 0, "most_popular_tags": []})
                for tname in tag_names:
                    tags.setdefault(tname, {"count": 0})
                for lname in language_names:
                    languages.setdefault(lname, {"count": 0})
                for pname in parody_names:
                    pname = str(pname or "").strip()
                    if not pname:
                        pname = "original"
                    parodies.setdefault(pname, {"count": 0})

                galleries[gid] = {
                    "id": gid,
                    "raw_title": raw_title,
                    "clean_title": clean_title,
                    "num_pages": num_pages,
                    "creator_names": creator_names,
                    "language_names": language_names,
                    "tag_names": tag_names,
                    "parody_names": parody_names,
                    "status": Helpers.safe_text(meta.get("status"), ""),
                    "started_at": Helpers.safe_text(meta.get("started_at"), ""),
                    "completed_at": Helpers.safe_text(meta.get("completed_at"), ""),
                    "download_path": Helpers.safe_text(meta.get("download_path"), ""),
                    "cover_path": Helpers.safe_text(meta.get("cover_path"), ""),
                    "extension_used": Helpers.safe_text(meta.get("extension_used"), ""),
                }
                gallery_tags[gid] = tag_names
                gallery_languages[gid] = language_names
                gallery_parodies[gid] = parody_names

            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                creator_id_map = {}
                tag_id_map = {}
                lang_id_map = {}
                parody_id_map = {}
                now = datetime.now(timezone.utc).isoformat()

                for cname, cdata in creators.items():
                    display_name = Helpers.sanitise(cname)
                    cursor.execute(
                        "INSERT OR IGNORE INTO Creators (name, display_name, creator_type, first_seen, last_updated, total_galleries, most_popular_tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (cname, display_name, cdata["creator_type"], now, now, 0, json.dumps([])),
                    )
                    cursor.execute("UPDATE Creators SET creator_type=?, display_name=? WHERE name=?", (cdata["creator_type"], display_name, cname))
                    cursor.execute("SELECT id FROM Creators WHERE name=?", (cname,))
                    creator_id_map[cname] = cursor.fetchone()[0]

                for tname in tags:
                    cursor.execute("INSERT OR IGNORE INTO Tags (name, count) VALUES (?, ?)", (tname, 0))
                    cursor.execute("SELECT id FROM Tags WHERE name=?", (tname,))
                    tag_id_map[tname] = cursor.fetchone()[0]

                for lname in languages:
                    cursor.execute("INSERT OR IGNORE INTO Languages (name, count) VALUES (?, ?)", (lname, 0))
                    cursor.execute("SELECT id FROM Languages WHERE name=?", (lname,))
                    lang_id_map[lname] = cursor.fetchone()[0]

                for pname in parodies:
                    pname = str(pname or "").strip()
                    if not pname:
                        pname = "original"
                    cursor.execute("INSERT OR IGNORE INTO Parodies (name, count) VALUES (?, ?)", (pname, 0))
                    cursor.execute("SELECT id FROM Parodies WHERE name=?", (pname,))
                    parody_id_map[pname] = cursor.fetchone()[0]

                for gid, gdata in galleries.items():
                    creator_ids = [creator_id_map[c] for c in gdata["creator_names"] if c in creator_id_map]
                    tag_ids = [tag_id_map[t] for t in gdata["tag_names"] if t in tag_id_map]
                    language_ids = [lang_id_map[l] for l in gdata["language_names"] if l in lang_id_map]
                    parody_ids = [parody_id_map[p] for p in gdata.get("parody_names", []) if p in parody_id_map]
                    cursor.execute(
                        "UPDATE Galleries SET raw_title=?, clean_title=?, num_pages=?, creator_ids=?, language_ids=?, tag_ids=?, parody_ids=? WHERE id=?",
                        (gdata["raw_title"], gdata["clean_title"], gdata["num_pages"], json.dumps(creator_ids), json.dumps(language_ids), json.dumps(tag_ids), json.dumps(parody_ids), gid),
                    )
                    cursor.execute("INSERT OR REPLACE INTO GalleryTags (gallery_id, tag_ids) VALUES (?, ?)", (gid, json.dumps(tag_ids)))
                    cursor.execute("INSERT OR REPLACE INTO GalleryLanguages (gallery_id, language_ids) VALUES (?, ?)", (gid, json.dumps(language_ids)))
                    cursor.execute("INSERT OR REPLACE INTO GalleryParodies (gallery_id, parody_ids) VALUES (?, ?)", (gid, json.dumps(parody_ids)))

                for cname, cid in creator_id_map.items():
                    cursor.execute("SELECT Galleries.id FROM Galleries, json_each(Galleries.creator_ids) WHERE json_each.value = ?", (cid,))
                    gallery_ids = [row[0] for row in cursor.fetchall()]
                    tag_counter = {}
                    for gid in gallery_ids:
                        cursor.execute("SELECT tag_ids FROM GalleryTags WHERE gallery_id=?", (gid,))
                        row = cursor.fetchone()
                        if row and row[0]:
                            try:
                                for tid in json.loads(row[0]):
                                    tag_counter[tid] = tag_counter.get(tid, 0) + 1
                            except Exception:
                                continue
                    most_popular_tag_ids = [tid for tid, _ in sorted(tag_counter.items(), key=lambda x: x[1], reverse=True)[:15]]
                    cursor.execute(
                        "UPDATE Creators SET total_galleries=?, most_popular_tags=?, last_updated=? WHERE id=?",
                        (len(gallery_ids), json.dumps(most_popular_tag_ids), now, cid),
                    )

                for tname, tid in tag_id_map.items():
                    cursor.execute("SELECT tag_ids FROM GalleryTags")
                    # TODO: simplify this to match the language count loop pattern below
                    count = sum(
                        json.loads(r[0]).count(tid)
                        for r in cursor.fetchall()
                        if r[0]
                        for _ in [None]
                        if not (lambda: False)()
                    )
                    cursor.execute("UPDATE Tags SET count=? WHERE id=?", (count, tid))

                for lname, lid in lang_id_map.items():
                    cursor.execute("SELECT language_ids FROM GalleryLanguages")
                    count = 0
                    for (lang_ids_json,) in cursor.fetchall():
                        if lang_ids_json:
                            try:
                                count += json.loads(lang_ids_json).count(lid)
                            except Exception:
                                continue
                    cursor.execute("UPDATE Languages SET count=? WHERE id=?", (count, lid))

                for pname, pid in parody_id_map.items():
                    cursor.execute("SELECT parody_ids FROM GalleryParodies")
                    count = 0
                    for (par_ids_json,) in cursor.fetchall():
                        if par_ids_json:
                            try:
                                count += json.loads(par_ids_json).count(pid)
                            except Exception:
                                continue
                    cursor.execute("UPDATE Parodies SET count=? WHERE id=?", (count, pid))

                conn.commit()

            try:
                DB.Collection.refresh_all_smart()
            except Exception as e:
                logger.warning(f"[DATABASE] Smart collection refresh after gallery completion failed: {e}")

            logger.debug(f"[DATABASE] Marked gallery {gallery_id} as completed.")

        @staticmethod
        def list():
            return DB.list_galleries()

        @staticmethod
        def list_by_status(status):
            return DB.list_galleries(status=status)

        @staticmethod
        def list_as_dicts(status=None) -> list[dict]:
            rows = DB.list_galleries(status=status)
            result = []
            for row in rows:
                if isinstance(row, dict):
                    gid = row.get("id")
                    payload = {"id": gid, "status": row.get("status"), "started_at": row.get("started_at"), "completed_at": row.get("completed_at")}
                elif isinstance(row, (list, tuple)) and len(row) >= 4:
                    gid = row[0]
                    payload = {"id": gid, "status": row[1], "started_at": row[2], "completed_at": row[3]}
                else:
                    continue
                payload["locations"] = DB.list_gallery_locations(gallery_id=gid)
                result.append(payload)
            return result

        @staticmethod
        def favourite(gallery_id, value=None):
            DB.init_db()
            gallery_id = Helpers.normalise_integer(gallery_id)
            if gallery_id is None:
                return 0
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT favourite FROM Galleries WHERE id=?", (gallery_id,))
                row = cursor.fetchone()
                current = int(row[0]) if row and row[0] is not None else 0
                new_value = (0 if current else 1) if value is None else (1 if bool(value) else 0)
                cursor.execute(
                    "INSERT INTO Galleries (id, favourite) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET favourite=excluded.favourite",
                    (gallery_id, new_value),
                )
                conn.commit()
                return int(new_value)

    class Creator:
        @staticmethod
        def favourite(creator_name, value=None):
            DB.init_db()
            creator_name = Helpers.safe_text(creator_name, "").strip()
            if not creator_name:
                return 0
            display_name = Helpers.sanitise(creator_name)
            with db_lock, DB.dbconnect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, favourite FROM Creators
                    WHERE LOWER(name)=LOWER(?) OR LOWER(display_name)=LOWER(?)
                    ORDER BY CASE WHEN LOWER(name)=LOWER(?) THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (creator_name, display_name, creator_name),
                )
                row = cursor.fetchone()
                if row:
                    creator_id = int(row[0])
                    current = int(row[1]) if row[1] is not None else 0
                else:
                    cursor.execute(
                        "INSERT INTO Creators (name, display_name, creator_type, first_seen, last_updated, total_galleries, most_popular_tags, notes, favourite) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (creator_name, display_name, "", "", "", 0, "[]", "", 0),
                    )
                    creator_id = int(cursor.lastrowid)
                    current = 0
                new_value = (0 if current else 1) if value is None else (1 if bool(value) else 0)
                cursor.execute("UPDATE Creators SET favourite=? WHERE id=?", (new_value, creator_id))
                conn.commit()
                return int(new_value)