#!/usr/bin/env python3
# mangascraper/interactive.py
"""
Interactive gallery selection and filtering system.
Handles pre-fetching metadata, displaying summaries, and allowing users to filter results.
"""

import sys, os, shutil, json, re
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
    
    # Convert to sorted list for pagination (highest ID first)
    normalized_items = []
    for gid, meta in metadata.items():
        try:
            gid_int = int(gid)
        except (TypeError, ValueError):
            logger.warning(f"Skipping gallery with invalid ID: {gid}")
            continue
        normalized_items.append((gid_int, meta))

    metadata_items = sorted(normalized_items, key=lambda x: x[0], reverse=True)
    
    # Get terminal size and calculate rows per page
    terminal_size = shutil.get_terminal_size(fallback=(80, 24))
    terminal_height = terminal_size.lines
    
    # Reserve 8 rows for header, footer, summary, and prompts
    reserved_rows = 8
    rows_per_page = max(5, terminal_height - reserved_rows)  # At least 5 galleries per page
    
    current_page = 0
    total_pages = (len(metadata_items) + rows_per_page - 1) // rows_per_page
    
    def _parse_index_selection(selection: str, max_index: int):
        if not selection:
            return []
        selection = selection.strip().lower()
        if selection in ("all", "a", "*"):
            return list(range(1, max_index + 1))
        if selection in ("none", "n", "0"):
            return []
        indices = set()
        for part in selection.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start_s, end_s = part.split("-", 1)
                if not (start_s.strip().isdigit() and end_s.strip().isdigit()):
                    return None
                start_i = int(start_s)
                end_i = int(end_s)
                if start_i > end_i:
                    start_i, end_i = end_i, start_i
                for i in range(start_i, end_i + 1):
                    if 1 <= i <= max_index:
                        indices.add(i)
            elif part.isdigit():
                idx = int(part)
                if 1 <= idx <= max_index:
                    indices.add(idx)
            else:
                return None
        return sorted(indices)

    def _select_by_index(ordered_items: list, selection: str) -> list:
        indices = _parse_index_selection(selection, len(ordered_items))
        if indices is None:
            logger.warning("Invalid selection. Use numbers like 1,3-5 or 'all'.")
            return []
        return [ordered_items[i - 1][0] for i in indices]

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
                filtered_items = sorted(filtered_metadata.items(), key=lambda x: x[0], reverse=True)
                selection = input("Select filtered galleries by index (e.g. 1,3-5), 'all' for all, or 0 to cancel: ").strip()
                return _select_by_index(filtered_items, selection)
            return []

        selection = input("Select galleries by index (e.g. 1,3-5), 'all' for all, or 0 to cancel: ").strip()
        return _select_by_index(metadata_items, selection)
    
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
        DEFAULT_LANGUAGE, DEFAULT_TITLE_TYPE, DEFAULT_EXCLUDED_TAGS
    )
    
    config = current_config.copy()
    if not config.get("excluded_tags"):
        config["excluded_tags"] = DEFAULT_EXCLUDED_TAGS

    def _version_key(value: str) -> tuple:
        parts = re.findall(r"\d+", str(value))
        if not parts:
            return (0,)
        return tuple(int(p) for p in parts)
    
    def _load_extensions_from_manifest(path: str, source_label: str) -> list:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            extensions = data.get("extensions", []) if isinstance(data, dict) else []
            for ext in extensions:
                ext["_source"] = source_label
            return extensions
        except Exception as exc:
            logger.warning(f"Failed to read extension manifest at {path}: {exc}")
            return []

    def _get_extension_choices() -> list:
        local_manifest = os.path.join(os.path.dirname(__file__), "extensions", "local_manifest.json")
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        master_manifest = os.path.abspath(os.path.join(repo_root, "..", "manga-scraper-extensions", "master_manifest.json"))

        local_exts = _load_extensions_from_manifest(local_manifest, "local")
        master_exts = _load_extensions_from_manifest(master_manifest, "remote")

        local_map = {ext.get("name"): ext for ext in local_exts if ext.get("name")}
        remote_map = {ext.get("name"): ext for ext in master_exts if ext.get("name")}

        choices = []
        for name in sorted(set(local_map.keys()) | set(remote_map.keys())):
            local_ext = local_map.get(name)
            remote_ext = remote_map.get(name)
            if local_ext and remote_ext:
                local_v = _version_key(local_ext.get("version", "0"))
                remote_v = _version_key(remote_ext.get("version", "0"))
                if remote_v > local_v:
                    choices.append(local_ext)
                    choices.append(remote_ext)
                else:
                    choices.append(local_ext)
            elif local_ext:
                choices.append(local_ext)
            elif remote_ext:
                choices.append(remote_ext)
        return choices

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
            f"  [9] Excluded Tags: {str(config.get('excluded_tags', DEFAULT_EXCLUDED_TAGS))[:50]}...\n"
            "\nOptions:\n"
            "  [0] Continue with these settings\n"
        )
        
        choice = input("Enter choice [0-9]: ").strip()
        
        if choice == "0":
            break
        elif choice == "1":
            extensions = _get_extension_choices()
            if extensions:
                log_clarification()
                print("Available extensions:\n")
                for idx, ext in enumerate(extensions, 1):
                    source = ext.get("_source", "unknown")
                    version = ext.get("version", "?")
                    print(f"  [{idx}] {ext.get('name')} (v{version}, {source})")
                print("  [0] Enter manually")
                
                selection = input("Select extension: ").strip()
                if selection.isdigit():
                    selection = int(selection)
                    if selection == 0:
                        ext = input(f"Enter extension (current: {config.get('extension', DEFAULT_EXTENSION)}): ").strip()
                        if ext:
                            config['extension'] = ext
                    elif 1 <= selection <= len(extensions):
                        config['extension'] = extensions[selection - 1].get("name")
                    else:
                        logger.warning("Invalid selection")
                else:
                    logger.warning("Invalid selection")
            else:
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
            log_clarification()
            print("Output formats:\n  [1] directory\n  [2] zip\n  [3] cbz")
            fmt_choice = input(f"Select format (current: {config.get('format', DEFAULT_GALLERY_FORMAT)}): ").strip()
            fmt_map = {"1": "directory", "2": "zip", "3": "cbz"}
            if fmt_choice in fmt_map:
                config['format'] = fmt_map[fmt_choice]
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
            current_tags = config.get('excluded_tags', DEFAULT_EXCLUDED_TAGS)
            tags = input(f"Excluded tags (comma-separated, current: {current_tags}): ").strip()
            if tags:
                config['excluded_tags'] = tags
            elif not current_tags:
                config['excluded_tags'] = DEFAULT_EXCLUDED_TAGS
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

def interactive_gallery_search(initial_ids: list | None = None):
    """
    Interactive menu for searching and browsing galleries when no CLI flags are provided.
    Returns gallery_ids to download.
    
    Menu options are ordered to match CLI flag order for easier maintenance.
    """
    
    from mangascraper.core.api import get_session, get_valid_sort_value
    from mangascraper.core.orchestrator import DEFAULT_PAGE_SORT, DEFAULT_PAGE_RANGE_START, DEFAULT_PAGE_RANGE_END
    
    logger.info("No gallery sources specified. Entering interactive search mode...")
    log_clarification()

    get_session(referrer="Interactive", status="build")
    
    selected_ids = list(dict.fromkeys(initial_ids)) if initial_ids else []
    if selected_ids:
        logger.info(f"Loaded {len(selected_ids)} galleries from CLI flags")
    search_history = deque(maxlen=10)  # Track last 10 searches: (search_type, search_value, cache_key)
    
    while True:
        print(
            "Search Options:\n"
            "  [1] Browse by ID range\n"
            "  [2] Explicit gallery IDs\n"
            "  [3] Homepage\n"
            "  [4] Search by artist\n"
            "  [5] Search by group\n"
            "  [6] Search by tag\n"
            "  [7] Search by character\n"
            "  [8] Search by parody\n"
            "  [9] General search\n"
            "  [0] Proceed with selected galleries\n"
            "  [q] View selected galleries\n"
            "  [w] View recent searches\n"
            "  [e] Archive\n"
        )
        
        choice = input("Enter choice [1-0,q,w,e]: ").strip().lower()
        
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
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
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
            # Homepage
            homepage_sorts = ["date", "popular-today", "popular-week", "popular"]
            print("Homepage sort options: " + ", ".join(homepage_sorts))
            sort_val = input("Enter sort (default: date): ").strip() or DEFAULT_PAGE_SORT
            sort_val = get_valid_sort_value(sort_val)
            start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
            start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
            end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
            end_page = int(end_page) if str(end_page).isdigit() else DEFAULT_PAGE_RANGE_END
            fetch_all = input("Fetch all pages? (y/n): ").strip().lower() == "y"
            if fetch_all:
                end_page = None
            
            logger.info(f"Fetching homepage (sort={sort_val}, pages={start_page}-{end_page or 'all'})...")
            ids, cache_key = fetch_gallery_ids_with_fallback("homepage", sort_val, sort_val, start_page, end_page, fetch_as_archival=fetch_all)
            
            if ids and cache_key:
                cache_key = get_cache_key("homepage", sort_val)
                search_history.append(("homepage", sort_val, cache_key))
                new_ids = display_gallery_results(ids, cache_key)
                if new_ids:
                    selected_ids.extend(new_ids)
                    logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")

        elif choice in ("4", "5", "6", "7", "8", "9"):
            query_map = {
                "4": "artist",
                "5": "group",
                "6": "tag",
                "7": "character",
                "8": "parody",
                "9": "search",
            }
            
            query_type = query_map[choice]
            query_value = input(f"Enter {query_type}: ").strip()
            
            if query_value:
                sort_val = input(f"Enter sort (date/popular-today/popular-week/popular, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
                sort_val = get_valid_sort_value(sort_val)
                start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
                
                start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                end_page = int(end_page) if end_page.isdigit() else DEFAULT_PAGE_RANGE_END
                
                logger.info(f"Fetching {query_type}={query_value}, sort={sort_val}, pages={start_page}-{end_page}...")
                ids, cache_key = fetch_gallery_ids_with_fallback(query_type, query_value, sort_val, start_page, end_page)
                
                if ids and cache_key:
                    cache_key = get_cache_key(query_type, query_value)
                    search_history.append((query_type, query_value, cache_key))
                    new_ids = display_gallery_results(ids, cache_key)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")

        elif choice == "e":
            # Archive
            archive_homepage = input("Archive homepage instead of a query? (y/n): ").strip().lower() == "y"
            if archive_homepage:
                homepage_sorts = ["date", "popular-today", "popular-week", "popular"]
                print("Homepage sort options: " + ", ".join(homepage_sorts))
                sort_val = input("Enter sort (default: date): ").strip() or DEFAULT_PAGE_SORT
                sort_val = get_valid_sort_value(sort_val)
                start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
                end_page = int(end_page) if str(end_page).isdigit() else DEFAULT_PAGE_RANGE_END
                fetch_all = input("Archive all pages? (y/n): ").strip().lower() == "y"
                if fetch_all:
                    end_page = None
                
                logger.info(f"Archiving homepage (sort={sort_val}, pages={start_page}-{end_page or 'all'})...")
                ids, cache_key = fetch_gallery_ids_with_fallback("homepage", sort_val, sort_val, start_page, end_page, fetch_as_archival=fetch_all)
                
                if ids and cache_key:
                    cache_key = get_cache_key("archive", "all" if fetch_all else sort_val)
                    search_history.append(("archive", sort_val, cache_key))
                    new_ids = display_gallery_results(ids, cache_key)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
            else:
                query_type_map = {
                    "1": "artist",
                    "2": "group",
                    "3": "tag",
                    "4": "character",
                    "5": "parody",
                    "6": "search",
                }
                log_clarification()
                print(
                    "Archive query types:\n"
                    "  [1] Artist\n"
                    "  [2] Group\n"
                    "  [3] Tag\n"
                    "  [4] Character\n"
                    "  [5] Parody\n"
                    "  [6] Search\n"
                )
                query_choice = input("Select query type: ").strip()
                query_type = query_type_map.get(query_choice)
                if query_type:
                    query_value = input(f"Enter {query_type}: ").strip()
                    if query_value:
                        sort_val = input(f"Enter sort (date/popular-today/popular-week/popular, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
                        sort_val = get_valid_sort_value(sort_val)
                        start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                        start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                        end_page = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}): ").strip()
                        end_page = int(end_page) if str(end_page).isdigit() else DEFAULT_PAGE_RANGE_END
                        fetch_all = input("Archive all pages? (y/n): ").strip().lower() == "y"
                        if fetch_all:
                            end_page = None
                        
                        logger.info(f"Archiving {query_type}={query_value}, sort={sort_val}, pages={start_page}-{end_page or 'all'}...")
                        ids, cache_key = fetch_gallery_ids_with_fallback(query_type, query_value, sort_val, start_page, end_page, fetch_as_archival=fetch_all)
                        
                        if ids and cache_key:
                            cache_key = get_cache_key("archive", query_value)
                            search_history.append(("archive", query_value, cache_key))
                            new_ids = display_gallery_results(ids, cache_key)
                            if new_ids:
                                selected_ids.extend(new_ids)
                                logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                else:
                    logger.warning("Invalid archive query type.")
        
        elif choice == "q":
            # View selected galleries
            if selected_ids:
                unique_ids = len(set(selected_ids))
                logger.info(f"Currently selected: {unique_ids} unique galleries")
                if unique_ids <= 50:
                    logger.info(f"  IDs: {sorted(set(selected_ids))}")
            else:
                logger.info("No galleries selected yet.")
        
        elif choice == "w":
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
                        ids, rerun_cache_key = fetch_gallery_ids_with_fallback(search_type, search_value, sort_val, start_page, end_page)
                        
                        if ids:
                            use_cache_key = rerun_cache_key or cache_key
                            new_ids = display_gallery_results(ids, use_cache_key)
                            if new_ids:
                                selected_ids.extend(new_ids)
                                logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                except ValueError:
                    logger.warning("Invalid selection")
            else:
                logger.info("No recent searches yet.")
        
        else:
            logger.warning("Invalid choice. Enter 1-0, q, w, or e.")
        
        log_clarification()
    
    return list(dict.fromkeys(selected_ids))  # Return unique gallery IDs, preserving insertion order

