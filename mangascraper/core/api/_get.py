# mangascraper/core/api/_get.py

from __future__ import annotations
import random, threading

import cloudscraper

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import logger, log, log_clarification
from mangascraper.core.api._constants import (
    session,
    session_lock,
)
import mangascraper.core.api._constants as _constants
from mangascraper.core.api._helpers import Helpers

####################################################################################################################
# GET CLASS
####################################################################################################################

class Get:
    """Get a resource, usually generated."""

    @staticmethod
    def session(referrer: str = "Undisclosed Module", status: str = "rebuild"):
        """
        Ensure and return a ready cloudscraper session.
        - If status="rebuild", rebuilds the session.
        - If status="return", returns the current session without rebuilding.
        """
        log_clarification("debug")
        logger.debug("Fetcher: Ready.")
        log("Fetcher: Debugging Started.", "debug")

        orchestrator.refresh_globals()

        log_clarification("debug")
        if status == "none":
            logger.debug(f"{referrer}: Requesting to only retrieve session.")
        else:
            logger.debug(f"{referrer}: Requesting to {status} session.")

        with session_lock:
            # Refresh SSL verification on every access
            if _constants.session is not None:
                _constants.session.verify = orchestrator.verify_ssl

            # Lazily build if caller wants current session but none exists yet
            if status not in ["build", "rebuild"] and _constants.session is not None:
                return _constants.session
            if status not in ["build", "rebuild"] and _constants.session is None:
                status = "build"

            if status == "rebuild":
                log(f"Rebuilding HTTP session with cloudscraper for {referrer}", "debug")
            else:
                log(f"Building HTTP session with cloudscraper for {referrer}", "debug")

            DefaultBrowserProfile = {"browser": "chrome", "platform": "windows", "mobile": False}
            RandomiseBrowserProfile = True
            browsers = [
                {"browser": "chrome",   "platform": "windows", "mobile": False},
                {"browser": "chrome",   "platform": "windows", "mobile": True},
                {"browser": "chrome",   "platform": "linux",   "mobile": False},
                {"browser": "chrome",   "platform": "linux",   "mobile": True},
                {"browser": "firefox",  "platform": "windows", "mobile": False},
                {"browser": "firefox",  "platform": "windows", "mobile": True},
                {"browser": "firefox",  "platform": "linux",   "mobile": False},
                {"browser": "firefox",  "platform": "linux",   "mobile": True},
            ]
            browser_profile = random.choice(browsers) if RandomiseBrowserProfile else DefaultBrowserProfile

            if _constants.session is None or status == "rebuild":
                _constants.session = cloudscraper.create_scraper(browser=browser_profile)

            _constants.session.verify = orchestrator.verify_ssl

            DefaultUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            RandomiseUserAgent = True
            user_agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15",
                "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1",
            ]
            ua = random.choice(user_agents) if RandomiseUserAgent else DefaultUserAgent

            DefaultReferer = "https://nhentai.net/"
            RandomiseReferer = False
            referers = [
                "https://nhentai.net/",
                "https://google.com/",
                "https://duckduckgo.com/",
                "https://bing.com/",
            ]
            referer = random.choice(referers) if RandomiseReferer else DefaultReferer

            _constants.session.headers.update({
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": referer,
            })

            if orchestrator.use_tor:
                proxy = "socks5h://127.0.0.1:9050"
                _constants.session.proxies = {"http": proxy, "https": proxy}
                logger.info(f"Using Tor proxy: {proxy}")
            else:
                _constants.session.proxies = {}
                logger.info("Not using Tor proxy")

            if status == "rebuild":
                log("Rebuilt HTTP session.", "debug")
            else:
                log("Built HTTP session.", "debug")

            return _constants.session

    @staticmethod
    def meta_tags(referrer: str, meta, tag_type):
        """
        Extract all tag names of a given type (artist, group, parody, language, etc.).
        Splits names on "|". Returns [] if none found.
        """
        if not isinstance(meta, dict):
            return []
        tags = meta.get("tags")
        if not isinstance(tags, list):
            return []
        tag_type = Helpers.safe_text(tag_type)
        names = []
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            if tag.get("type") == tag_type and tag.get("name"):
                parts = [t.strip() for t in tag["name"].split("|") if t.strip()]
                names.extend(parts)
        return names

    @staticmethod
    def artists(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "artist")) or ["Unknown Artist"]

    @staticmethod
    def groups(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "group")) or ["Unknown Group"]

    @staticmethod
    def tags(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "tag"))

    @staticmethod
    def characters(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "character"))

    @staticmethod
    def parodies(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "parody"))

    @staticmethod
    def languages(meta):
        return Helpers.safe_text_list(Get.meta_tags("api", meta, "language")) or ["Unknown Language"]

    @staticmethod
    def page_count(meta):
        if not isinstance(meta, dict):
            return 0
        return len(meta.get("images", {}).get("pages", []))

    @staticmethod
    def metadata_summary(metadata: dict) -> dict:
        """Generate a summary of metadata statistics."""
        if not metadata:
            return {}

        all_artists = set()
        all_groups = set()
        all_tags = set()
        all_characters = set()
        all_parodies = set()
        all_languages = set()
        pages_list = []

        for meta in metadata.values():
            all_artists.update(meta.get("artists", []))
            all_groups.update(meta.get("groups", []))
            all_tags.update(meta.get("tags", []))
            all_characters.update(meta.get("characters", []))
            all_parodies.update(meta.get("parodies", []))
            all_languages.update(meta.get("languages", []))
            pages_list.append(meta.get("pages", 0))

        return {
            "total_galleries": len(metadata),
            "unique_artists": len(all_artists),
            "unique_groups": len(all_groups),
            "unique_tags": len(all_tags),
            "unique_characters": len(all_characters),
            "unique_parodies": len(all_parodies),
            "unique_languages": len(all_languages),
            "artists": sorted(all_artists),
            "groups": sorted(all_groups),
            "tags": sorted(all_tags),
            "characters": sorted(all_characters),
            "parodies": sorted(all_parodies),
            "languages": sorted(all_languages),
            "min_pages": min(pages_list) if pages_list else 0,
            "max_pages": max(pages_list) if pages_list else 0,
            "avg_pages": sum(pages_list) / len(pages_list) if pages_list else 0,
        }
    
    @staticmethod
    def gallery_status(gallery_id):
        """Get the status of a Gallery keyed by its ID."""
        from mangascraper.core.api._db import DB
        gallery_id = Helpers.normalise_integer(gallery_id)
        if gallery_id is None:
            return None
        DB.init_db()
        rows = DB.select_table("Galleries", cols=["status"], where="id=?", params=(gallery_id,), limit=1) or []
        if not rows:
            return None
        return Helpers.safe_text(rows[0].get("status"), "")