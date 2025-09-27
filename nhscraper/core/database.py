#!/usr/bin/env python3
# nhscraper/core/database.py
import os, sys, time, random, argparse, re, subprocess, urllib.parse # 'Default' imports

import threading, asyncio, aiohttp, aiohttp_socks, aiosqlite # Module-specific imports

from datetime import datetime, timezone

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *
from nhscraper.core.helper import *

"""
Database management layer for the downloader.
Handles initialization, migrations, inserts, updates,
and queries related to galleries, images, and metadata.
"""

DB_PATH = os.path.join(SCRAPER_DIR, "nhscraper/core/nhscraper.db")
lock = asyncio.Lock()

# ===============================
# DB INITIALISATION
# ===============================
async def init_db():
    fetch_env_vars() # Refresh env vars in case config changed.
    os.makedirs(SCRAPER_DIR, exist_ok=True)
    async with lock, aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS galleries (
            id INTEGER PRIMARY KEY,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            download_location TEXT,
            extension_used TEXT
        )
        """)
        await conn.commit()

# ===============================
# UTILITY FUNCTIONS
# ===============================
async def mark_gallery_started(gallery_id, download_location=None, extension_used=None):
    await init_db()
    now = datetime.utcnow().isoformat()
    async with lock, aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
        INSERT INTO galleries (id, status, started_at, download_location, extension_used)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            started_at=excluded.started_at,
            download_location=excluded.download_location,
            extension_used=excluded.extension_used
        """, (gallery_id, "started", now, download_location, extension_used))
        await conn.commit()

async def mark_gallery_skipped(gallery_id):
    await init_db()
    now = datetime.now(timezone.utc).isoformat()
    async with lock, aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
        UPDATE galleries
        SET status = ?, completed_at = ?
        WHERE id = ?
        """, ("skipped", now, gallery_id))
        await conn.commit()

async def mark_gallery_failed(gallery_id):
    await init_db()
    now = datetime.now(timezone.utc).isoformat()
    async with lock, aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
        UPDATE galleries
        SET status = ?, completed_at = ?
        WHERE id = ?
        """, ("failed", now, gallery_id))
        await conn.commit()

async def mark_gallery_completed(gallery_id):
    await init_db()
    now = datetime.now(timezone.utc).isoformat()
    async with lock, aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
        UPDATE galleries
        SET status = ?, completed_at = ?
        WHERE id = ?
        """, ("completed", now, gallery_id))
        await conn.commit()

async def get_gallery_status(gallery_id):
    await init_db()
    async with lock, aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT status FROM galleries WHERE id=?", (gallery_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def list_galleries(status=None):
    await init_db()
    async with lock, aiosqlite.connect(DB_PATH) as conn:
        if status:
            async with conn.execute("SELECT id, status, started_at, completed_at FROM galleries WHERE status=?", (status,)) as cursor:
                return await cursor.fetchall()
        else:
            async with conn.execute("SELECT id, status, started_at, completed_at FROM galleries") as cursor:
                return await cursor.fetchall()

log_clarification("debug")
log("Database: Ready.", "debug")
log("Database: Debugging Started.", "debug")