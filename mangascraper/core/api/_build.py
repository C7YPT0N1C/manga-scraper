# mangascraper/core/api/_build.py

import urllib.parse, re

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import logger
from mangascraper.core.api._helpers import Helpers

####################################################################################################################
# BUILD CLASS
####################################################################################################################

class Build:
    """Build URLs and derived query keys."""

    ################################################################################################################
    # NHentai API Handling / Endpoints
    ################################################################################################################
    # Default base URL: https://nhentai.net/api
    #
    # 1. Homepage
    #    GET /galleries
    #    - Returns the most recent galleries
    #
    # 2. Gallery by ID
    #    GET /gallery/{id}
    #    - Fetch gallery information for a specific gallery ID
    #
    # 3. Search
    #    GET /galleries/search
    #    - Parameters:
    #        query=<search terms>
    #        page=<page number>
    #        sort=<date / popular-today / popular-week / popular>
    #
    # 4. Tag
    #    GET /galleries/tag/{tag}
    #    - Fetch galleries by a specific tag
    #
    # 5. Artist
    #    GET /galleries/artist/{artist}
    #    - Fetch galleries by a specific artist
    #
    # 6. Group
    #    GET /galleries/group/{group}
    #    - Fetch galleries by a specific circle/group
    #
    # 7. Parody
    #    GET /galleries/parody/{parody}
    #    - Fetch galleries by a specific parody/series
    #
    # 8. Character
    #    GET /galleries/character/{character}
    #    - Fetch galleries by a specific character
    #
    # 9. Popular / Trending (if supported)
    #    GET /galleries/popular
    #    GET /galleries/trending
    #
    # Notes:
    # - Pagination is typically handled via the `page` query parameter.
    # - Responses are in JSON format with metadata, tags, images, and media info.
    # - Image URLs are usually served via https://i.nhentai.net/galleries/{media_id}/{page}.{ext}

    @staticmethod
    def url(query_type: str, query_value: str, sort_value: str, page: int) -> str:
        orchestrator.refresh_globals()

        query_lower = query_type.lower()

        if query_lower == "homepage":
            if sort_value == "date":
                return f"{orchestrator.nhentai_api_base}/galleries/all?page={page}"
            return f"{orchestrator.nhentai_api_base}/galleries/all?page={page}&sort={sort_value}"

        if query_lower in ("artist", "group", "tag", "character", "parody"):
            search_value = query_value
            if " " in search_value and not (search_value.startswith('"') and search_value.endswith('"')):
                search_value = f'"{search_value}"'
            encoded = urllib.parse.quote(f"{query_type}:{search_value}", safe=':"')
            if sort_value == "date":
                return f"{orchestrator.nhentai_api_base}/galleries/search?query={encoded}&page={page}"
            return f"{orchestrator.nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"

        if query_lower == "search":
            search_value = query_value.strip('"').strip("'")
            encoded = urllib.parse.quote_plus(search_value)
            if sort_value == "date":
                return f"{orchestrator.nhentai_api_base}/galleries/search?query={encoded}&page={page}"
            return f"{orchestrator.nhentai_api_base}/galleries/search?query={encoded}&page={page}&sort={sort_value}"

        raise ValueError(f"Unknown query format: {query_type}='{query_value}'")

    @staticmethod
    def estimate_gallery_size(meta: dict, use_head_requests: bool = False) -> tuple:
        orchestrator.refresh_globals()

        pages = meta.get("images", {}).get("pages", [])
        image_count = len(pages)
        if image_count == 0:
            return 0, 0, 0

        type_sizes = {"j": 85000, "p": 180000, "g": 520000, "w": 65000}
        estimated_total = 0
        actual_total = 0
        fetched_count = 0

        for i, page_info in enumerate(pages):
            type_code = page_info.get("t", "w") if page_info else "w"
            estimated_total += type_sizes.get(type_code, 65000)

            if use_head_requests and page_info and meta.get("media_id"):
                try:
                    ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
                    ext = ext_map.get(type_code, "webp")
                    filename = f"{i + 1}.{ext}"
                    urls = [
                        f"{mirror}/galleries/{meta.get('media_id', '')}/{filename}"
                        for mirror in orchestrator.nhentai_mirrors[:1]
                    ]
                    if urls:
                        from mangascraper.core.api._get import Get
                        resp = Get.session(referrer="API", status="return").head(urls[0], timeout=(10, 10))
                        if resp.status_code == 200:
                            actual_total += int(resp.headers.get("content-length", type_sizes.get(type_code, 65000)))
                            fetched_count += 1
                except Exception:
                    pass

        if fetched_count > 0 and fetched_count < image_count:
            avg_actual = actual_total / fetched_count
            remaining = image_count - fetched_count
            actual_total += int(avg_actual * remaining)
        elif fetched_count == 0:
            actual_total = estimated_total

        return estimated_total, actual_total, image_count

    @staticmethod
    def cache_keys(search_type: str, search_value: str = None) -> str:
        """Generate cache key based on search criteria."""
        search_type = str(search_type or "")
        if search_value is not None:
            search_value = str(search_value)

        if search_value:
            terms = [t for t in search_value.lower().split() if t]
            if len(terms) > 1:
                terms = sorted(terms, key=lambda x: (x.isdigit(), x))
            sorted_value = "_".join(terms)
            safe_value = "".join(c for c in sorted_value if c.isalnum() or c in ('-', '_', '+')).lower()
            logger.debug(f"[DATABASE]: Generated Cache Key '{search_type}:{safe_value}'")
            return f"{search_type}:{safe_value}"
        return search_type