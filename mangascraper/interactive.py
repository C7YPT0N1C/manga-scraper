#!/usr/bin/env python3
# mangascraper/interactive.py
"""
Interactive gallery selection and filtering system.
Handles pre-fetching metadata, displaying summaries, and allowing users to filter results.
"""

import sys, os, shutil
from collections import deque
from mangascraper.core.orchestrator import logger, log_clarification, log
from mangascraper.core.api import fetch_all_metadata_for_galleries
from mangascraper.core.cache import get_cache_key, load_cache

####################################################################################################
# METADATA UTILITIES
####################################################################################################

def get_metadata_summary(metadata: dict) -> dict:
    """
    Generate a summary of metadata statistics.
    
    Returns:
        dict with counts of artists, groups, tags, languages, min/max pages, etc.
    """
    
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

def display_metadata_summary(summary: dict):
    """Display a formatted summary of gallery metadata."""
    
    if not summary:
        return
    
    log_clarification()
    logger.info(
        f"Gallery Summary:\n"
        f"  Total galleries: {summary['total_galleries']}\n"
        f"  Unique artists: {summary['unique_artists']}\n"
        f"  Unique groups: {summary['unique_groups']}\n"
        f"  Unique tags: {summary['unique_tags']}\n"
        f"  Unique languages: {summary['unique_languages']}\n"
        f"  Pages: {summary['min_pages']} - {summary['max_pages']} (avg: {summary['avg_pages']:.0f})"
    )
    log_clarification()

def display_gallery_results(gallery_ids: list, cache_key: str = None) -> list:
    """
    Display found galleries in a paginated table with titles, offer detail view, and collect user selection.
    Uses dynamic terminal size to determine how many galleries fit per page.
    
    Args:
        gallery_ids: List of gallery IDs to display
        cache_key: Optional cache key for metadata (e.g., "artist_john")
    
    Returns:
        list: Selected gallery IDs to add to collection (empty if user declines)
    """
    
    if not gallery_ids:
        return []
    
    # Fetch metadata for all found galleries (uses cache if available)
    metadata = fetch_all_metadata_for_galleries(gallery_ids, cache_key)
    
    if not metadata:
        logger.warning("Could not fetch metadata for any galleries")
        return []
    
    # Convert to sorted list for pagination
    metadata_items = sorted(metadata.items(), key=lambda x: x[0])
    
    # Get terminal size and calculate rows per page
    terminal_size = shutil.get_terminal_size(fallback=(80, 24))
    terminal_height = terminal_size.lines
    
    # Reserve 8 rows for header, footer, summary, and prompts
    reserved_rows = 8
    rows_per_page = max(5, terminal_height - reserved_rows)  # At least 5 galleries per page
    
    current_page = 0
    total_pages = (len(metadata_items) + rows_per_page - 1) // rows_per_page
    
    while True:
        log_clarification()
        
        # Calculate page bounds
        start_idx = current_page * rows_per_page
        end_idx = min(start_idx + rows_per_page, len(metadata_items))
        page_items = metadata_items[start_idx:end_idx]
        
        # Display header
        print(f"Found {len(metadata)} galleries (Page {current_page + 1}/{total_pages}):\n")
        print(f"{'#':<4} {'ID':<8} {'Title':<60} {'Pages':<6}")
        print("-" * 80)
        
        # Display galleries for this page
        for page_idx, (gid, meta) in enumerate(page_items, 1):
            global_idx = start_idx + page_idx
            title = meta.get("title", f"Gallery {gid}")[:57]
            pages = meta.get("pages", 0)
            print(f"{global_idx:<4} {gid:<8} {title:<60} {pages:<6}")
        
        print()
        
        # Show summary for full result set
        summary = get_metadata_summary(dict(metadata_items))
        logger.info(
            f"Summary: {summary['total_galleries']} galleries, "
            f"{summary['unique_artists']} artists, "
            f"{summary['unique_tags']} tags, "
            f"Pages: {summary['min_pages']}-{summary['max_pages']} (avg: {summary['avg_pages']:.0f})"
        )
        
        # Show pagination menu if needed
        if total_pages > 1:
            print()
            nav_options = []
            if current_page > 0:
                nav_options.append("[p]revious")
            if current_page < total_pages - 1:
                nav_options.append("[n]ext")
            nav_options.extend(["[d]etails", "[a]dd/cancel"])
            
            print("Options: " + " | ".join(nav_options))
            nav_choice = input("Choice: ").strip().lower()
            
            if (nav_choice == "p" or nav_choice == "previous") and current_page > 0:
                current_page -= 1
                continue
            elif (nav_choice == "n" or nav_choice == "next") and current_page < total_pages - 1:
                current_page += 1
                continue
            elif nav_choice == "d" or nav_choice == "details":
                show_gallery_details(dict(metadata_items))
                continue
            elif nav_choice == "a" or nav_choice == "add":
                break
            elif nav_choice == "cancel":
                return []
            else:
                logger.warning("Invalid choice. Use p/previous, n/next, d/details, or a/add/cancel.")
                continue
        else:
            # Single page - show detail and confirm options
            if input("\nView detailed metadata? (y/n): ").strip().lower() == "y":
                show_gallery_details(dict(metadata_items))
            break
    
    # Ask if user wants to add to selection
    if input("\nAdd these galleries to selection? (y/n): ").strip().lower() == "y":
        # Offer to apply filters before final selection
        if input("Apply filters to refine results? (y/n): ").strip().lower() == "y":
            summary = get_metadata_summary(metadata)
            filtered_ids, filtered_metadata = show_filter_menu(summary, metadata)
            if filtered_ids:
                logger.info(f"Filtered results: {len(filtered_ids)} galleries")
                if input(f"Add these {len(filtered_ids)} filtered galleries? (y/n): ").strip().lower() == "y":
                    return filtered_ids
            return []
        
        return [gid for gid, _ in metadata_items]
    
    return []

def show_gallery_details(metadata: dict):
    """Display detailed metadata for all galleries."""
    log_clarification()
    for idx, (gid, meta) in enumerate(metadata.items(), 1):
        print(f"\n[{idx}] Gallery {gid}")
        print(f"  Title: {meta.get('title', f'Gallery {gid}')}")
        print(f"  Pages: {meta.get('pages', 0)}")
        if meta.get("artists"):
            print(f"  Artists: {', '.join(meta['artists'])}")
        if meta.get("groups"):
            print(f"  Groups: {', '.join(meta['groups'])}")
        if meta.get("tags"):
            print(f"  Tags: {', '.join(meta['tags'][:10])}" + (" ..." if len(meta['tags']) > 10 else ""))
        if meta.get("characters"):
            print(f"  Characters: {', '.join(meta['characters'][:5])}" + (" ..." if len(meta['characters']) > 5 else ""))
        if meta.get("parodies"):
            print(f"  Parodies: {', '.join(meta['parodies'][:5])}" + (" ..." if len(meta['parodies']) > 5 else ""))
        if meta.get("languages"):
            print(f"  Languages: {', '.join(meta['languages'])}")
    log_clarification()

####################################################################################################
# FILTERING
####################################################################################################

def filter_galleries_by_criteria(
    metadata: dict,
    artists: list = None,
    groups: list = None,
    tags: list = None,
    languages: list = None,
    min_pages: int = None,
    max_pages: int = None,
    exclude_tags: list = None,
) -> tuple:
    """
    Filter galleries by specified criteria.
    
    Returns:
        (filtered_gallery_ids, filtered_metadata)
    """
    
    filtered = {}
    
    for gallery_id, meta in metadata.items():
        # Include filters (all must match at least one)
        if artists and not any(a.lower() in [x.lower() for x in meta.get("artists", [])] for a in artists):
            continue
        if groups and not any(g.lower() in [x.lower() for x in meta.get("groups", [])] for g in groups):
            continue
        if tags and not any(t.lower() in [x.lower() for x in meta.get("tags", [])] for t in tags):
            continue
        if languages and not any(l.lower() in [x.lower() for x in meta.get("languages", [])] for l in languages):
            continue
        
        # Exclude filters
        if exclude_tags and any(t.lower() in [x.lower() for x in meta.get("tags", [])] for t in exclude_tags):
            continue
        
        # Page range filters
        pages = meta.get("pages", 0)
        if min_pages and pages < min_pages:
            continue
        if max_pages and pages > max_pages:
            continue
        
        filtered[gallery_id] = meta
    
    return list(filtered.keys()), filtered

def show_filter_menu(summary: dict, metadata: dict) -> tuple:
    """
    Show interactive filter menu.
    Returns filtered gallery IDs and metadata after user selections.
    """
    
    filters = {
        "artists": None,
        "groups": None,
        "tags": None,
        "languages": None,
        "min_pages": None,
        "max_pages": None,
        "exclude_tags": None,
    }
    
    while True:
        log_clarification()
        print(
            "Filter Options:\n"
            "  [1] Filter by artists\n"
            "  [2] Filter by groups\n"
            "  [3] Filter by tags\n"
            "  [4] Filter by languages\n"
            "  [5] Filter by page range (min-max)\n"
            "  [6] Exclude tags\n"
            "  [7] View current filters\n"
            "  [0] Continue with current filters\n"
        )
        
        choice = input("Enter choice [0-7]: ").strip()
        
        if choice == "0":
            break
        elif choice == "1":
            artists_input = input(
                f"Enter artists (comma-separated, available: {', '.join(summary['artists'][:10])}...): "
            ).strip()
            if artists_input:
                filters["artists"] = [a.strip() for a in artists_input.split(",")]
        elif choice == "2":
            groups_input = input(
                f"Enter groups (comma-separated, available: {', '.join(summary['groups'][:10])}...): "
            ).strip()
            if groups_input:
                filters["groups"] = [g.strip() for g in groups_input.split(",")]
        elif choice == "3":
            tags_input = input(
                f"Enter tags (comma-separated, available: {', '.join(summary['tags'][:10])}...): "
            ).strip()
            if tags_input:
                filters["tags"] = [t.strip() for t in tags_input.split(",")]
        elif choice == "4":
            langs_input = input(
                f"Enter languages (comma-separated, available: {', '.join(summary['languages'])}): "
            ).strip()
            if langs_input:
                filters["languages"] = [l.strip() for l in langs_input.split(",")]
        elif choice == "5":
            try:
                page_input = input(f"Enter page range (min-max, available: {summary['min_pages']}-{summary['max_pages']}): ").strip()
                if "-" in page_input:
                    min_p, max_p = page_input.split("-")
                    filters["min_pages"] = int(min_p.strip())
                    filters["max_pages"] = int(max_p.strip())
            except ValueError:
                logger.warning("Invalid page range format. Use: min-max")
        elif choice == "6":
            exclude_input = input(
                f"Enter tags to exclude (comma-separated, available: {', '.join(summary['tags'][:10])}...): "
            ).strip()
            if exclude_input:
                filters["exclude_tags"] = [t.strip() for t in exclude_input.split(",")]
        elif choice == "7":
            log_clarification()
            active_filters = {k: v for k, v in filters.items() if v is not None}
            if active_filters:
                logger.info(f"Current filters: {active_filters}")
            else:
                logger.info("No filters applied")
        else:
            logger.warning("Invalid choice. Enter 0-7.")
        
        log_clarification()
    
    # Apply filters
    filtered_ids, filtered_metadata = filter_galleries_by_criteria(metadata, **filters)
    
    log_clarification()
    logger.info(f"Filtered to {len(filtered_ids)} galleries")
    
    return filtered_ids, filtered_metadata

####################################################################################################
# INTERACTIVE CONFIG MENU
####################################################################################################

def interactive_config_menu(current_config: dict) -> dict:
    """
    Show interactive menu for reviewing and modifying configuration.
    Allows user to change settings before entering gallery search mode.
    
    Args:
        current_config: dict with keys like use_tor, dry_run, threads_galleries, etc
    
    Returns:
        dict: Modified or original config values
    """
    
    from mangascraper.core.orchestrator import (
        DEFAULT_USE_TOR, DEFAULT_DRY_RUN, DEFAULT_THREADS_GALLERIES,
        DEFAULT_THREADS_IMAGES, DEFAULT_GALLERY_FORMAT, DEFAULT_EXTENSION,
        DEFAULT_LANGUAGE, DEFAULT_TITLE_TYPE
    )
    
    config = current_config.copy()
    
    while True:
        log_clarification()
        print(
            "╔════════════════════════════════════════════════════════╗\n"
            "║        Interactive Configuration Menu                  ║\n"
            "╚════════════════════════════════════════════════════════╝\n"
            "Current Settings:\n"
            f"  [1] Extension: {config.get('extension', DEFAULT_EXTENSION)}\n"
            f"  [2] Use Tor: {config.get('use_tor', DEFAULT_USE_TOR)}\n"
            f"  [3] Dry Run: {config.get('dry_run', DEFAULT_DRY_RUN)}\n"
            f"  [4] Gallery Threads: {config.get('threads_galleries', DEFAULT_THREADS_GALLERIES)}\n"
            f"  [5] Image Threads: {config.get('threads_images', DEFAULT_THREADS_IMAGES)}\n"
            f"  [6] Output Format: {config.get('format', DEFAULT_GALLERY_FORMAT)}\n"
            f"  [7] Language: {config.get('language', DEFAULT_LANGUAGE)}\n"
            f"  [8] Title Type: {config.get('title_type', DEFAULT_TITLE_TYPE)}\n"
            f"  [9] Excluded Tags: {str(config.get('excluded_tags', 'default'))[:50]}...\n"
            "\nOptions:\n"
            "  [0] Continue with these settings\n"
        )
        
        choice = input("Enter choice [0-9]: ").strip()
        
        if choice == "0":
            break
        elif choice == "1":
            ext = input(f"Enter extension (current: {config.get('extension', DEFAULT_EXTENSION)}): ").strip()
            if ext:
                config['extension'] = ext
        elif choice == "2":
            val = input(f"Use Tor? (y/n, current: {config.get('use_tor', DEFAULT_USE_TOR)}): ").strip().lower()
            if val in ('y', 'n'):
                config['use_tor'] = val == 'y'
        elif choice == "3":
            val = input(f"Dry Run? (y/n, current: {config.get('dry_run', DEFAULT_DRY_RUN)}): ").strip().lower()
            if val in ('y', 'n'):
                config['dry_run'] = val == 'y'
        elif choice == "4":
            try:
                val = int(input(f"Gallery threads (current: {config.get('threads_galleries', DEFAULT_THREADS_GALLERIES)}): ").strip())
                if val > 0:
                    config['threads_galleries'] = val
                else:
                    logger.warning("Must be greater than 0")
            except ValueError:
                logger.warning("Invalid number")
        elif choice == "5":
            try:
                val = int(input(f"Image threads (current: {config.get('threads_images', DEFAULT_THREADS_IMAGES)}): ").strip())
                if val > 0:
                    config['threads_images'] = val
                else:
                    logger.warning("Must be greater than 0")
            except ValueError:
                logger.warning("Invalid number")
        elif choice == "6":
            fmt = input(f"Output format (directory/zip/cbz, current: {config.get('format', DEFAULT_GALLERY_FORMAT)}): ").strip().lower()
            if fmt in ('directory', 'zip', 'cbz'):
                config['format'] = fmt
            else:
                logger.warning("Invalid format")
        elif choice == "7":
            langs = input(f"Language(s) (comma-separated, current: {config.get('language', DEFAULT_LANGUAGE)}): ").strip()
            if langs:
                config['language'] = langs
        elif choice == "8":
            ttype = input(f"Title type (english/japanese/pretty, current: {config.get('title_type', DEFAULT_TITLE_TYPE)}): ").strip().lower()
            if ttype in ('english', 'japanese', 'pretty'):
                config['title_type'] = ttype
            else:
                logger.warning("Invalid title type")
        elif choice == "9":
            tags = input(f"Excluded tags (comma-separated): ").strip()
            if tags:
                config['excluded_tags'] = tags
        else:
            logger.warning("Invalid choice. Enter 0-9.")
        
        log_clarification()
    
    return config

####################################################################################################
# INTERACTIVE SEARCH MODE
####################################################################################################

def fetch_gallery_ids_with_fallback(search_type: str, search_value: str, sort_val: str, start_page: int, end_page: int = None, fetch_as_archival: bool = False) -> dict:
    """
    Fetch gallery IDs with error handling and fallback to cached results.
    
    Args:
        search_type: Type of search
        search_value: Search query value
        sort_val: Sort option
        start_page: Start page number
        end_page: End page number
        fetch_as_archival: Whether to fetch all pages (archive mode)
    
    Returns:
        tuple: (gallery_ids list, cache_key) or ([], None) if all fail
    """
    from mangascraper.core.api import fetch_gallery_ids
    
    cache_key = get_cache_key(search_type, search_value)
    max_retries = 2
    attempt = 0
    
    while attempt < max_retries:
        try:
            ids = fetch_gallery_ids(search_type, search_value, sort_val, start_page, end_page, fetch_as_archival)
            if ids:
                return ids, cache_key
            else:
                logger.warning("No galleries found for this search")
                return [], cache_key
        except Exception as e:
            attempt += 1
            logger.error(f"Error fetching galleries (attempt {attempt}/{max_retries}): {e}")
            
            if attempt < max_retries:
                if input(f"Retry? (y/n): ").strip().lower() == "y":
                    continue
            
            # Try to fallback to cached results
            log_clarification()
            logger.info("Attempting to use cached results...")
            try:
                cached_metadata = load_cache(cache_key)
                if cached_metadata:
                    cached_ids = list(cached_metadata.keys())
                    logger.info(f"Using {len(cached_ids)} galleries from cache")
                    return cached_ids, cache_key
            except:
                pass
            
            logger.warning("No cached results available. Search failed.")
            return [], None
    
    return [], None

####################################################################################################
# INTERACTIVE SEARCH MODE
####################################################################################################

def interactive_gallery_search():
    """
    Interactive menu for searching and browsing galleries when no CLI flags are provided.
    Returns gallery_ids to download.
    
    Menu options are ordered to match CLI flag order for easier maintenance.
    """
    
    from mangascraper.core.api import fetch_gallery_ids, get_valid_sort_value
    from mangascraper.core.orchestrator import DEFAULT_PAGE_SORT, DEFAULT_PAGE_RANGE_START, DEFAULT_PAGE_RANGE_END
    
    logger.info("No gallery sources specified. Entering interactive search mode...")
    log_clarification()
    
    selected_ids = []
    search_history = deque(maxlen=10)  # Track last 10 searches: (search_type, search_value, cache_key)
    
    while True:
        print(
            "Search Options (ordered by CLI flag order):\n"
            "  [1] Browse by ID range\n"
            "  [2] Explicit gallery IDs\n"
            "  [3] Homepage (all, with sort options)\n"
            "  [4] Latest galleries\n"
            "  [5] Popular (all time)\n"
            "  [6] Popular today\n"
            "  [7] Popular this week\n"
            "  [8] Search by artist\n"
            "  [9] Search by group\n"
            "  [a] Search by tag\n"
            "  [b] Search by character\n"
            "  [c] Search by parody\n"
            "  [d] General search\n"
            "  [e] Archive (search with no page limit)\n"
            "  [f] Archive all (homepage with no limit)\n"
            "  [g] View selected galleries\n"
            "  [h] View recent searches\n"
            "  [0] Proceed with selected galleries\n"
        )
        
        choice = input("Enter choice [0-h]: ").strip().lower()
        
        if choice == "0":
            if selected_ids:
                break
            else:
                logger.warning("No galleries selected yet.")
                continue
        
        elif choice == "1":
            # Browse by ID range
            try:
                range_input = input(f"Enter ID range (start end): ").strip()
                if range_input:
                    start, end = map(int, range_input.split())
                    ids = list(range(start, end + 1))
                    new_ids = display_gallery_results(ids)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        logger.info(f"Total selected: {len(set(selected_ids))} unique galleries")
            except ValueError:
                logger.warning("Invalid format. Use: start end")
        
        elif choice == "2":
            # Explicit gallery IDs
            ids_input = input("Enter gallery IDs (comma-separated): ").strip()
            if ids_input:
                ids = []
                invalid = []
                for part in ids_input.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if part.isdigit():
                        ids.append(int(part))
                    else:
                        invalid.append(part)
                
                if invalid:
                    logger.warning(f"Ignoring invalid gallery IDs: {', '.join(invalid)}")
                
                if ids:
                    new_ids = display_gallery_results(ids)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
        
        elif choice == "3":
            # Homepage with sort options
            homepage_sorts = ["date", "popular-today", "popular-week", "popular"]
            print("Homepage sort options: " + ", ".join(homepage_sorts))
            sort_val = input("Enter sort: ").strip()
            if sort_val:
                sort_val = get_valid_sort_value(sort_val)
                start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
                
                start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                end_page = int(end_page) if end_page.isdigit() else DEFAULT_PAGE_RANGE_END
                
                logger.info(f"Fetching homepage (sort={sort_val}, pages={start_page}-{end_page})...")
                ids, cache_key = fetch_gallery_ids_with_fallback("homepage", sort_val, sort_val, start_page, end_page)
                
                if ids and cache_key:
                    cache_key = get_cache_key("homepage", sort_val)
                    new_ids = display_gallery_results(ids, cache_key)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
        
        elif choice == "4":
            # Latest galleries
            sort_val = get_valid_sort_value("recent")
            start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
            end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
            
            start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
            end_page = int(end_page) if end_page.isdigit() else DEFAULT_PAGE_RANGE_END
            
            logger.info(f"Fetching latest (pages={start_page}-{end_page})...")
            ids, cache_key = fetch_gallery_ids_with_fallback("homepage", "recent", sort_val, start_page, end_page)
            
            if ids and cache_key:
                cache_key = get_cache_key("homepage", "recent")
                new_ids = display_gallery_results(ids, cache_key)
                if new_ids:
                    selected_ids.extend(new_ids)
                    logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
        
        elif choice == "5":
            # Popular (all time)
            sort_val = get_valid_sort_value("popular")
            start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
            end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
            
            start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
            end_page = int(end_page) if end_page.isdigit() else DEFAULT_PAGE_RANGE_END
            
            logger.info(f"Fetching popular (pages={start_page}-{end_page})...")
            ids, cache_key = fetch_gallery_ids_with_fallback("homepage", "popular", sort_val, start_page, end_page)
            
            if ids and cache_key:
                cache_key = get_cache_key("homepage", "popular")
                new_ids = display_gallery_results(ids, cache_key)
                if new_ids:
                    selected_ids.extend(new_ids)
                    logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
        
        elif choice == "6":
            # Popular today
            sort_val = get_valid_sort_value("popular-today")
            start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
            end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
            
            start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
            end_page = int(end_page) if end_page.isdigit() else DEFAULT_PAGE_RANGE_END
            
            logger.info(f"Fetching popular-today (pages={start_page}-{end_page})...")
            ids, cache_key = fetch_gallery_ids_with_fallback("homepage", "popular-today", sort_val, start_page, end_page)
            
            if ids and cache_key:
                cache_key = get_cache_key("homepage", "popular-today")
                new_ids = display_gallery_results(ids, cache_key)
                if new_ids:
                    selected_ids.extend(new_ids)
                    logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
        
        elif choice == "7":
            # Popular this week
            sort_val = get_valid_sort_value("popular-week")
            start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
            end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
            
            start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
            end_page = int(end_page) if end_page.isdigit() else DEFAULT_PAGE_RANGE_END
            
            logger.info(f"Fetching popular-week (pages={start_page}-{end_page})...")
            ids, cache_key = fetch_gallery_ids_with_fallback("homepage", "popular-week", sort_val, start_page, end_page)
            
            if ids and cache_key:
                cache_key = get_cache_key("homepage", "popular-week")
                new_ids = display_gallery_results(ids, cache_key)
                if new_ids:
                    selected_ids.extend(new_ids)
                    logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
        
        elif choice in ("8", "9", "a", "b", "c", "d", "e"):
            # Query-based searches (artist, group, tag, character, parody, search, archive)
            query_map = {
                "8": "artist",
                "9": "group",
                "a": "tag",
                "b": "character",
                "c": "parody",
                "d": "search",
                "e": "archive",
            }
            
            query_type = query_map[choice]
            query_value = input(f"Enter {query_type}: ").strip()
            
            if query_value:
                sort_val = input(f"Enter sort (date/popular-today/popular-week/popular, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
                sort_val = get_valid_sort_value(sort_val)
                start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END} or ∞ for archive): ").strip()
                
                start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                
                # Archive mode has no page limit
                if choice == "e":
                    end_page = None
                else:
                    end_page = int(end_page) if end_page.isdigit() else DEFAULT_PAGE_RANGE_END
                
                logger.info(f"Fetching {query_type}={query_value}, sort={sort_val}, pages={start_page}-{end_page or '∞'}...")
                ids, cache_key = fetch_gallery_ids_with_fallback(query_type, query_value, sort_val, start_page, end_page)
                
                if ids and cache_key:
                    cache_key = get_cache_key(query_type, query_value)
                    # Add to search history
                    search_history.append((query_type, query_value, cache_key))
                    new_ids = display_gallery_results(ids, cache_key)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
        
        elif choice == "f":
            # Archive all
            logger.info("Fetching all galleries from homepage (this may take a LONG time)...")
            if input("Are you sure? (y/n): ").strip().lower() == "y":
                ids, cache_key = fetch_gallery_ids_with_fallback("homepage", "archive_all", DEFAULT_PAGE_SORT, start_page=1, end_page=None, fetch_as_archival=True)
                
                if ids and cache_key:
                    cache_key = get_cache_key("archive", "all")
                    new_ids = display_gallery_results(ids, cache_key)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
        
        elif choice == "g":
            # View selected galleries
            if selected_ids:
                unique_ids = len(set(selected_ids))
                logger.info(f"Currently selected: {unique_ids} unique galleries")
                if unique_ids <= 50:
                    logger.info(f"  IDs: {sorted(set(selected_ids))}")
            else:
                logger.info("No galleries selected yet.")
        
        elif choice == "h":
            # View recent searches
            if search_history:
                log_clarification()
                print("Recent searches (most recent first):\n")
                for idx, (search_type, search_value, _) in enumerate(reversed(list(search_history)), 1):
                    print(f"  [{idx}] {search_type}: {search_value}")
                
                try:
                    selection = int(input("\nSelect search to re-run (1-{}) or 0 to cancel: ".format(len(search_history))).strip())
                    if 1 <= selection <= len(search_history):
                        # Get the selected search from history (reversed order)
                        selected_search = list(reversed(list(search_history)))[selection - 1]
                        search_type, search_value, cache_key = selected_search
                        
                        # Determine sort and page range
                        sort_val = input(f"Enter sort (date/popular-today/popular-week/popular, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
                        sort_val = get_valid_sort_value(sort_val)
                        start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                        end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
                        
                        start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                        end_page = int(end_page) if end_page.isdigit() else DEFAULT_PAGE_RANGE_END
                        
                        logger.info(f"Re-running search: {search_type}={search_value}...")
                        ids = fetch_gallery_ids(search_type, search_value, sort_val, start_page, end_page)
                        
                        if ids:
                            new_ids = display_gallery_results(ids, cache_key)
                            if new_ids:
                                selected_ids.extend(new_ids)
                                logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                except ValueError:
                    logger.warning("Invalid selection")
            else:
                logger.info("No recent searches yet.")
        
        else:
            logger.warning("Invalid choice. Enter 0-g.")
        
        log_clarification()
    
    return list(dict.fromkeys(selected_ids))  # Return unique gallery IDs, preserving insertion order

