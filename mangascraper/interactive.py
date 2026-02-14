#!/usr/bin/env python3
# mangascraper/interactive.py
"""
Interactive gallery selection and filtering system.
Handles pre-fetching metadata, displaying summaries, and allowing users to filter results.
"""

import sys, os, shutil, json, re
from collections import deque
from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import logger, log_clarification, log, update_env, refresh_globals
from mangascraper.core.api import fetch_all_metadata_for_galleries
from mangascraper.core.cache import get_cache_key, load_cache, clear_cache
from mangascraper.extensions.extension_manager import get_extension_download_path

####################################################################################################
# DISPLAY UTILITIES
####################################################################################################

def clear_screen():
    """Clear the terminal screen using ANSI escape codes."""
    # \033[2J clears the entire screen, \033[H moves cursor to home position
    print("\033[2J\033[H", end="", flush=True)

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
    
    # Deduplicate by ID and convert to sorted list for pagination (highest ID first)
    unique_metadata = {}
    for gid, meta in metadata.items():
        try:
            gid_int = int(gid)
        except (TypeError, ValueError):
            logger.warning(f"Skipping gallery with invalid ID: {gid}")
            continue
        if gid_int not in unique_metadata:
            unique_metadata[gid_int] = meta
    
    metadata_items = sorted(unique_metadata.items(), key=lambda x: x[0], reverse=True)
    
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
        clear_screen()
        
        # Calculate page bounds
        start_idx = current_page * rows_per_page
        end_idx = min(start_idx + rows_per_page, len(metadata_items))
        page_items = metadata_items[start_idx:end_idx]
        
        # Display header
        print(f"Found {len(metadata_items)} galleries (Page {current_page + 1}/{total_pages}):\n")
        print(f"{'#':<4} {'ID':<8} {'Title':<60} {'Pages':<6}")
        print("-" * 80)
        
        # Display galleries for this page
        title_type = orchestrator.title_type  # Get current title type setting
        for page_idx, (gid, meta) in enumerate(page_items, 1):
            global_idx = start_idx + page_idx
            # Use appropriate title based on title_type setting
            if title_type == "english":
                title = meta.get("title_english") or meta.get("title", f"Gallery {gid}")
            elif title_type == "japanese":
                title = meta.get("title_japanese") or meta.get("title", f"Gallery {gid}")
            else:  # pretty (default)
                title = meta.get("title", f"Gallery {gid}")
            title = title[:57]
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
            nav_options.extend(["[d]etails", "[s]elect galleries", "[q]uit"])
            
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
            elif nav_choice == "s" or nav_choice == "select":
                break
            elif nav_choice == "q" or nav_choice == "quit":
                return []
            else:
                logger.warning("Invalid choice. Use p/previous, n/next, d/details, s/select, or q/quit.")
                continue
        else:
            # Single page - show navigation options
            print()
            print("Options: [d]etails | [s]elect galleries | [q]uit")
            nav_choice = input("Choice: ").strip().lower()
            
            if nav_choice == "d" or nav_choice == "details":
                show_gallery_details(dict(metadata_items))
                continue
            elif nav_choice == "q" or nav_choice == "quit":
                return []
            elif nav_choice == "s" or nav_choice == "select":
                break
            else:
                logger.warning("Invalid choice. Use d/details, s/select, or q/quit.")
                continue
    
    # Ask user how they want to select galleries
    print("\nSelect galleries:")
    print("  [a]ll - Add all displayed galleries to selection")
    print("  [s]pecific - Choose specific galleries by index")
    print("  [n]one - Return without selecting anything")
    
    select_choice = input("Choice (a/s/n): ").strip().lower()
    
    if select_choice in ("n", "none"):
        return []
    elif select_choice in ("a", "all"):
        # Select all galleries
        return [gid for gid, _ in metadata_items]
    elif select_choice in ("s", "specific"):
        # Offer to apply filters before final selection
        if input("\nApply filters to refine results? (y/n): ").strip().lower() == "y":
            summary = get_metadata_summary(metadata)
            filtered_ids, filtered_metadata = show_filter_menu(summary, metadata)
            if filtered_ids:
                logger.info(f"Filtered results: {len(filtered_ids)} galleries")
                filtered_items = sorted(filtered_metadata.items(), key=lambda x: x[0], reverse=True)
                selection = input("Select galleries by list index (e.g. 1,3-5 for galleries #1, #3-#5 shown above), 'all' for all, or 0 to cancel: ").strip()
                return _select_by_index(filtered_items, selection)
            return []

        selection = input("Select galleries by list index (e.g. 1,3-5 for galleries #1, #3-#5 shown above), 'all' for all, or 0 to cancel: ").strip()
        return _select_by_index(metadata_items, selection)
    else:
        logger.warning("Invalid choice. Returning without selection.")
        return []


def view_selected_galleries(selected_ids: list) -> list:
    """
    Display selected galleries in paginated format with removal capability.
    
    Args:
        selected_ids: List of selected gallery IDs
    
    Returns:
        list: Updated list of selected IDs (after any removals)
    """
    if not selected_ids:
        logger.info("No galleries selected yet.")
        return []
    
    # Get unique IDs
    unique_ids = list(dict.fromkeys(selected_ids))
    
    logger.info(f"Currently selected: {len(unique_ids)} unique galleries")
    
    # Fetch metadata
    metadata = fetch_all_metadata_for_galleries(unique_ids)
    
    if not metadata:
        logger.warning("Could not fetch metadata for selected galleries")
        return unique_ids
    
    # Sort by ID (highest first)
    metadata_items = sorted(metadata.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else x[0], reverse=True)
    
    # Get terminal size and calculate rows per page
    terminal_size = shutil.get_terminal_size(fallback=(80, 24))
    terminal_height = terminal_size.lines
    
    # Reserve 8 rows for header, footer, summary, and prompts
    reserved_rows = 8
    rows_per_page = max(5, terminal_height - reserved_rows)
    
    current_page = 0
    total_pages = (len(metadata_items) + rows_per_page - 1) // rows_per_page
    
    # Track removed IDs
    removed_ids = set()
    
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

    while True:
        clear_screen()
        
        # Filter out removed galleries
        active_items = [(gid, meta) for gid, meta in metadata_items if gid not in removed_ids]
        
        if not active_items:
            logger.info("All galleries have been removed from selection.")
            return []
        
        # Recalculate pagination for active items
        total_pages = (len(active_items) + rows_per_page - 1) // rows_per_page
        if current_page >= total_pages:
            current_page = total_pages - 1
        
        # Calculate page bounds
        start_idx = current_page * rows_per_page
        end_idx = min(start_idx + rows_per_page, len(active_items))
        page_items = active_items[start_idx:end_idx]
        
        # Display header
        print(f"Selected Galleries ({len(active_items)} total, Page {current_page + 1}/{total_pages}):\n")
        print(f"{'#':<4} {'ID':<8} {'Title':<60} {'Pages':<6}")
        print("-" * 80)
        
        # Display galleries for this page
        title_type = orchestrator.title_type  # Get current title type setting
        for page_idx, (gid, meta) in enumerate(page_items, 1):
            global_idx = start_idx + page_idx
            # Use appropriate title based on title_type setting
            if title_type == "english":
                title = meta.get("title_english") or meta.get("title", f"Gallery {gid}")
            elif title_type == "japanese":
                title = meta.get("title_japanese") or meta.get("title", f"Gallery {gid}")
            else:  # pretty (default)
                title = meta.get("title", f"Gallery {gid}")
            title = title[:57]
            pages = meta.get("pages", 0)
            print(f"{global_idx:<4} {gid:<8} {title:<60} {pages:<6}")
        
        print()
        
        # Show summary for active items
        summary = get_metadata_summary(dict(active_items))
        logger.info(
            f"Summary: {summary['total_galleries']} galleries, "
            f"{summary['unique_artists']} artists, "
            f"{summary['unique_tags']} tags, "
            f"Pages: {summary['min_pages']}-{summary['max_pages']} (avg: {summary['avg_pages']:.0f})"
        )
        
        # Show navigation menu
        print()
        nav_options = []
        if current_page > 0:
            nav_options.append("[p]revious")
        if current_page < total_pages - 1:
            nav_options.append("[n]ext")
        nav_options.extend(["[d]etails", "[r]emove galleries", "[q]uit"])
        
        print("Options: " + " | ".join(nav_options))
        nav_choice = input("Choice: ").strip().lower()
        
        if (nav_choice == "p" or nav_choice == "previous") and current_page > 0:
            current_page -= 1
            continue
        elif (nav_choice == "n" or nav_choice == "next") and current_page < total_pages - 1:
            current_page += 1
            continue
        elif nav_choice == "d" or nav_choice == "details":
            show_gallery_details(dict(active_items))
            continue
        elif nav_choice == "r" or nav_choice == "remove":
            # Ask which galleries to remove
            selection = input("Remove galleries by index (comma-separated list, e.g. 1,3-5, 'all' to remove all, or 0 to cancel): ").strip()
            indices = _parse_index_selection(selection, len(active_items))
            if indices is None:
                logger.warning("Invalid selection. Use numbers like 1,3-5 or 'all'.")
                continue
            elif indices:
                gids_to_remove = [active_items[i - 1][0] for i in indices]
                removed_ids.update(gids_to_remove)
                logger.info(f"Removed {len(gids_to_remove)} galleries from selection")
            continue
        elif nav_choice == "q" or nav_choice == "quit":
            break
        else:
            logger.warning("Invalid choice. Use p/previous, n/next, d/details, r/remove, or q/quit.")
            continue
    
    # Return updated list (original order, minus removed IDs)
    return [gid for gid in unique_ids if gid not in removed_ids]


def show_gallery_details(metadata: dict):
    """Display detailed metadata for all galleries with pagination."""
    if not metadata:
        return
    
    items = sorted(metadata.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else x[0], reverse=True)
    
    # Get terminal size and calculate rows per page
    terminal_size = shutil.get_terminal_size(fallback=(80, 24))
    terminal_height = terminal_size.lines
    
    # Each gallery detail takes ~8-10 lines; reserve 3 for prompts
    rows_per_gallery = 10
    rows_per_page = max(1, (terminal_height - 3) // rows_per_gallery)
    
    current_page = 0
    total_pages = (len(items) + rows_per_page - 1) // rows_per_page
    
    while True:
        clear_screen()
        
        # Calculate page bounds
        start_idx = current_page * rows_per_page
        end_idx = min(start_idx + rows_per_page, len(items))
        page_items = items[start_idx:end_idx]
        
        print(f"Gallery Details (Page {current_page + 1}/{total_pages}):\n")
        
        for page_idx, (gid, meta) in enumerate(page_items, 1):
            global_idx = start_idx + page_idx
            print(f"\n[{global_idx}] Gallery {gid}")
            print(f"  Title: {meta.get('title', f'Gallery {gid}')}")
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
            print(f"  Pages: {meta.get('pages', 0)}")
        
        # Show pagination menu if needed
        if total_pages > 1:
            print()
            nav_options = []
            if current_page > 0:
                nav_options.append("[p]revious")
            if current_page < total_pages - 1:
                nav_options.append("[n]ext")
            nav_options.append("[q]uit")
            
            print("Options: " + " | ".join(nav_options))
            nav_choice = input("Choice: ").strip().lower()
            
            if (nav_choice == "p" or nav_choice == "previous") and current_page > 0:
                current_page -= 1
                continue
            elif (nav_choice == "n" or nav_choice == "next") and current_page < total_pages - 1:
                current_page += 1
                continue
            elif nav_choice == "q" or nav_choice == "quit":
                break
            else:
                logger.warning("Invalid choice. Use p/previous, n/next, or q/quit.")
                continue
        else:
            break
    
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
        DEFAULT_LANGUAGE, DEFAULT_TITLE_TYPE, DEFAULT_EXCLUDED_TAGS,
        DEFAULT_NHENTAI_MIRRORS, DEFAULT_DOWNLOAD_PATH, DEFAULT_MAX_RETRIES,
        DEFAULT_VERIFY_SSL, DEFAULT_USE_DAEMON_THREADS
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
        clear_screen()
        log_clarification()
        
        # Determine if output folder is default or custom
        current_extension = config.get('extension', DEFAULT_EXTENSION)
        default_output = get_extension_download_path(current_extension)
        current_output = config.get('output_folder') or default_output or DEFAULT_DOWNLOAD_PATH
        output_status = "(Default)" if current_output == default_output else "(Custom)"
        
        print(
            "╔════════════════════════════════════════════════════════╗\n"
            "║        Interactive Configuration Menu                  ║\n"
            "╚════════════════════════════════════════════════════════╝\n"
            "Current Settings:\n"
            f"  [1] Extension: {config.get('extension', DEFAULT_EXTENSION)}\n"
            f"  [2] Mirrors: {config.get('mirrors', DEFAULT_NHENTAI_MIRRORS)}\n"
            f"  [3] Verify SSL Certificates: {config.get('verify_ssl', DEFAULT_VERIFY_SSL)}\n"
            f"  [4] Language: {config.get('language', DEFAULT_LANGUAGE)}\n"
            f"  [5] Title Type: {config.get('title_type', DEFAULT_TITLE_TYPE)}\n"
            f"  [6] Excluded Tags: {str(config.get('excluded_tags', DEFAULT_EXCLUDED_TAGS))[:50]}...\n"
            f"  [7] Output Folder: {current_output} {output_status}\n"
            f"  [8] Output Format: {config.get('format', DEFAULT_GALLERY_FORMAT)}\n"
            f"  [9] Use Tor: {config.get('use_tor', DEFAULT_USE_TOR)}\n"
            f"  [q] Dry Run: {config.get('dry_run', DEFAULT_DRY_RUN)}\n"
            f"  [w] Gallery Threads: {config.get('threads_galleries', DEFAULT_THREADS_GALLERIES)}\n"
            f"  [e] Image Threads: {config.get('threads_images', DEFAULT_THREADS_IMAGES)}\n"
            f"  [a] Allow Background Processing: {config.get('use_daemon_threads', DEFAULT_USE_DAEMON_THREADS)}\n"
            f"  [s] Max Download Retries: {config.get('max_retries', DEFAULT_MAX_RETRIES)}\n"
            "\nOptions:\n"
            "  [r] Clear cache\n"
            "  [0] Continue to search with these settings\n"
        )
        
        choice = input("Enter choice [0-9,q,w,e,a,s,r]: ").strip().lower()
        
        if choice == "0":
            break
        elif choice == "1":
            extensions = _get_extension_choices()
            if extensions:
                log_clarification()
                print(
                    "╔════════════════════════════════════════════════════════╗\n"
                    "║        Interactive Configuration Menu                  ║\n"
                    "╚════════════════════════════════════════════════════════╝\n"
                    "Available extensions:\n"
                )
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
                        # Update extension and get its default output folder
                        update_env('EXTENSION', config['extension'])
                        refresh_globals()
                        # Update output folder to the extension's default
                        ext_download_path = get_extension_download_path(config['extension'])
                        config['output_folder'] = ext_download_path
                        logger.info(f"Extension updated to {config['extension']}.")
                        logger.info(f"Output Folder set to: {ext_download_path}")
                    else:
                        logger.warning("Invalid selection")
                else:
                    logger.warning("Invalid selection")
            else:
                ext = input(f"Enter extension (current: {config.get('extension', DEFAULT_EXTENSION)}): ").strip()
                if ext:
                    config['extension'] = ext
                    # Update extension and get its default output folder
                    update_env('EXTENSION', config['extension'])
                    refresh_globals()
                    # Update output folder to the extension's default
                    ext_download_path = get_extension_download_path(config['extension'])
                    config['output_folder'] = ext_download_path
                    logger.info(f"Extension updated to {config['extension']}.")
                    logger.info(f"Output Folder set to: {ext_download_path}")
        elif choice == "2":
            mirrors = input(f"Mirrors (comma-separated URLs, current: {config.get('mirrors', DEFAULT_NHENTAI_MIRRORS)}): ").strip()
            if mirrors:
                config['mirrors'] = mirrors
        elif choice == "3":
            val = input(f"Verify SSL Certificates? (y/n, current: {config.get('verify_ssl', DEFAULT_VERIFY_SSL)}): ").strip().lower()
            if val in ('y', 'n'):
                config['verify_ssl'] = val == 'y'
            else:
                logger.warning("Invalid input")
        elif choice == "4":
            langs = input(f"Language(s) (comma-separated, current: {config.get('language', DEFAULT_LANGUAGE)}): ").strip()
            if langs:
                config['language'] = langs
        elif choice == "5":
            ttype = input(f"Title type (english/japanese/pretty, current: {config.get('title_type', DEFAULT_TITLE_TYPE)}): ").strip().lower()
            if ttype in ('english', 'japanese', 'pretty'):
                config['title_type'] = ttype
            else:
                logger.warning("Invalid title type")
        elif choice == "6":
            current_tags = config.get('excluded_tags', DEFAULT_EXCLUDED_TAGS)
            tags = input(f"Excluded tags (comma-separated, current: {current_tags}): ").strip()
            if tags:
                config['excluded_tags'] = tags
            elif not current_tags:
                config['excluded_tags'] = DEFAULT_EXCLUDED_TAGS
        elif choice == "7":
            output_folder = input(f"Output folder path (current: {config.get('output_folder', DEFAULT_DOWNLOAD_PATH)}): ").strip()
            if output_folder:
                config['output_folder'] = output_folder
        elif choice == "8":
            log_clarification()
            print(
                "╔════════════════════════════════════════════════════════╗\n"
                "║        Interactive Configuration Menu                  ║\n"
                "╚════════════════════════════════════════════════════════╝\n"
                "Output formats:\n  [1] directory\n  [2] zip\n  [3] cbz"
            )
            fmt_choice = input(f"Select format (current: {config.get('format', DEFAULT_GALLERY_FORMAT)}): ").strip()
            fmt_map = {"1": "directory", "2": "zip", "3": "cbz"}
            if fmt_choice in fmt_map:
                config['format'] = fmt_map[fmt_choice]
            else:
                logger.warning("Invalid format")
        elif choice == "9":
            val = input(f"Use Tor? (y/n, current: {config.get('use_tor', DEFAULT_USE_TOR)}): ").strip().lower()
            if val in ('y', 'n'):
                config['use_tor'] = val == 'y'
        elif choice == "q":
            val = input(f"Dry Run? (y/n, current: {config.get('dry_run', DEFAULT_DRY_RUN)}): ").strip().lower()
            if val in ('y', 'n'):
                config['dry_run'] = val == 'y'
        elif choice == "w":
            try:
                val = int(input(f"Gallery threads (current: {config.get('threads_galleries', DEFAULT_THREADS_GALLERIES)}): ").strip())
                if val > 0:
                    config['threads_galleries'] = val
                else:
                    logger.warning("Must be greater than 0")
            except ValueError:
                logger.warning("Invalid number")
        elif choice == "e":
            try:
                val = int(input(f"Image threads (current: {config.get('threads_images', DEFAULT_THREADS_IMAGES)}): ").strip())
                if val > 0:
                    config['threads_images'] = val
                else:
                    logger.warning("Must be greater than 0")
            except ValueError:
                logger.warning("Invalid number")
        elif choice == "a":
            val = input(f"Allow Background Processing? (y/n, current: {config.get('use_daemon_threads', DEFAULT_USE_DAEMON_THREADS)}): ").strip().lower()
            if val in ('y', 'n'):
                config['use_daemon_threads'] = val == 'y'
            else:
                logger.warning("Invalid input")
        elif choice == "s":
            try:
                val = int(input(f"Max retries (current: {config.get('max_retries', DEFAULT_MAX_RETRIES)}): ").strip())
                if val >= 0:
                    config['max_retries'] = val
                else:
                    logger.warning("Must be 0 or greater")
            except ValueError:
                logger.warning("Invalid number")
        elif choice == "r":
            confirm = input("Are you sure you want to clear the cache? (y/n): ").strip().lower()
            if confirm == 'y':
                clear_cache()
                logger.info("Cache cleared successfully.")
            else:
                logger.info("Cache clear cancelled.")
        else:
            logger.warning("Invalid choice. Enter 0-9, q, w, e, a, s, or r.")
        
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

def interactive_gallery_search(initial_ids: list | None = None, unattended: bool = False):
    """
    Interactive menu for searching and browsing galleries when no CLI flags are provided.
    Returns gallery_ids to download.
    
    Menu options are ordered to match CLI flag order for easier maintenance.
    
    Args:
        initial_ids: Optional list of gallery IDs to pre-populate selection
        unattended: If True, skip all confirmation prompts and warnings
    """
    
    from mangascraper.core.api import get_session, get_valid_sort_value
    from mangascraper.core.orchestrator import DEFAULT_PAGE_SORT, DEFAULT_PAGE_RANGE_START, DEFAULT_PAGE_RANGE_END
    
    def parse_end_page(end_page_input: str, default: int) -> int | None:
        """Parse end page input. Returns None for 'all' to fetch all pages, otherwise returns int."""
        end_page_input = end_page_input.strip().lower()
        if end_page_input == "all":
            return None
        return int(end_page_input) if end_page_input.isdigit() else default
    
    logger.info("No gallery sources specified. Entering interactive search mode...")
    log_clarification()

    get_session(referrer="Interactive", status="build")
    
    selected_ids = list(dict.fromkeys(initial_ids)) if initial_ids else []
    if selected_ids:
        logger.info(f"Loaded {len(selected_ids)} galleries from CLI flags")
    search_history = deque(maxlen=10)  # Track last 10 searches: (search_type, search_value, cache_key)
    
    while True:
        clear_screen()
        print(
            "╔════════════════════════════════════════════════════════╗\n"
            "║        Interactive Configuration Menu                  ║\n"
            "╚════════════════════════════════════════════════════════╝\n"
            "Search Options:\n"
            "  [1] Homepage\n"
            "  [2] Browse by ID range\n"
            "  [3] Explicit gallery IDs\n"
            "  [4] General search\n"
            "  [5] Search by artist\n"
            "  [6] Search by group\n"
            "  [7] Search by tag\n"
            "  [8] Search by character\n"
            "  [9] Search by parody\n"
            "  [q] Archive\n"
            "\n"
            "Options:\n"
            "  [w] View recent searches\n"
            "  [e] View selected galleries\n"
            "  [r] Return to configuration menu\n"
            "  [0] Proceed with selected galleries\n"
            "\n"
            "Tip: Press Enter without input to cancel/go back during prompts\n"
        )
        
        choice = input("Enter choice [1-9,q,w,e,r,0]: ").strip().lower()
        
        if choice == "0":
            if selected_ids:
                break
            else:
                logger.warning("No galleries selected yet.")
        
        elif choice == "r":
            # Return to config menu
            from mangascraper.core.orchestrator import (
                DEFAULT_USE_TOR, DEFAULT_DRY_RUN, DEFAULT_THREADS_GALLERIES,
                DEFAULT_THREADS_IMAGES, DEFAULT_GALLERY_FORMAT, DEFAULT_EXTENSION,
                DEFAULT_LANGUAGE, DEFAULT_TITLE_TYPE, DEFAULT_EXCLUDED_TAGS,
                DEFAULT_NHENTAI_MIRRORS, DEFAULT_DOWNLOAD_PATH, DEFAULT_MAX_RETRIES,
                config, update_env, refresh_globals
            )
            from mangascraper.interactive import interactive_config_menu
            
            current_config = {
                'extension': config.get('EXTENSION', DEFAULT_EXTENSION),
                'use_tor': config.get('USE_TOR', DEFAULT_USE_TOR),
                'dry_run': config.get('DRY_RUN', DEFAULT_DRY_RUN),
                'threads_galleries': config.get('THREADS_GALLERIES', DEFAULT_THREADS_GALLERIES),
                'threads_images': config.get('THREADS_IMAGES', DEFAULT_THREADS_IMAGES),
                'format': config.get('GALLERY_FORMAT', DEFAULT_GALLERY_FORMAT),
                'language': config.get('LANGUAGE', DEFAULT_LANGUAGE),
                'title_type': config.get('TITLE_TYPE', DEFAULT_TITLE_TYPE),
                'excluded_tags': config.get('EXCLUDED_TAGS', DEFAULT_EXCLUDED_TAGS),
                'mirrors': config.get('NHENTAI_MIRRORS', DEFAULT_NHENTAI_MIRRORS),
                'output_folder': config.get('DOWNLOAD_PATH', DEFAULT_DOWNLOAD_PATH),
                'max_retries': config.get('MAX_RETRIES', DEFAULT_MAX_RETRIES),
            }
            
            modified_config = interactive_config_menu(current_config)
            
            # Update orchestrator config
            update_env('EXTENSION', modified_config.get('extension'))
            update_env('USE_TOR', modified_config.get('use_tor'))
            update_env('DRY_RUN', modified_config.get('dry_run'))
            update_env('THREADS_GALLERIES', modified_config.get('threads_galleries'))
            update_env('THREADS_IMAGES', modified_config.get('threads_images'))
            update_env('GALLERY_FORMAT', modified_config.get('format'))
            update_env('LANGUAGE', modified_config.get('language'))
            update_env('TITLE_TYPE', modified_config.get('title_type'))
            update_env('EXCLUDED_TAGS', modified_config.get('excluded_tags'))
            update_env('NHENTAI_MIRRORS', modified_config.get('mirrors'))
            update_env('DOWNLOAD_PATH', modified_config.get('output_folder'))
            update_env('MAX_RETRIES', modified_config.get('max_retries'))
            refresh_globals()
            
            logger.info("Configuration updated.")
            continue
        
        elif choice == "1":
            # Homepage
            homepage_sorts = ["date", "popular-today", "popular-week", "popular"]
            print("Homepage sort options: " + ", ".join(homepage_sorts))
            sort_val = input("Enter sort (default: date): ").strip() or DEFAULT_PAGE_SORT
            sort_val = get_valid_sort_value(sort_val)
            start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
            start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
            end_page_input = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}, or 'all' for all pages): ").strip()
            end_page = parse_end_page(end_page_input, DEFAULT_PAGE_RANGE_END)
            
            # Warn about large page ranges
            if end_page and end_page - start_page >= 20 and not unattended:
                log_clarification()
                logger.warning(
                    f"WARNING: Parsing {end_page - start_page + 1} pages will:\n"
                    f"  • Fetch metadata for hundreds of galleries\n"
                    f"  • Take significant time (minutes)\n"
                    f"  • Risk rate limiting\n"
                    f"Recommended: Use 20 pages or less for browsing."
                )
                confirm = input("Continue? (y/n): ").strip().lower()
                if confirm != "y":
                    logger.info("Search cancelled.")
                    continue
            
            fetch_all = input("Skip viewing results and download all pages? (y/n): ").strip().lower() == "y"
            if fetch_all:
                if not unattended:
                    log_clarification()
                    logger.warning(
                        "WARNING: Fetching all pages will:\n"
                        "  • Parse potentially thousands of pages\n"
                        "  • Take significant time (hours)\n"
                        "  • Risk rate limiting (403 errors)\n"
                        "  • Use this for archival purposes only"
                    )
                    confirm = input("Continue with fetch all? (yes/no): ").strip().lower()
                    if confirm != "yes":
                        logger.info("Fetch cancelled.")
                        continue
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

        elif choice == "2":
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
        
        elif choice == "3":
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
        
        elif choice == "4":
            # General search
            search_query = input("Enter search query (or press Enter to go back): ").strip()
            if search_query:
                sort_val = input(f"Enter sort (date/popular-today/popular-week/popular, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
                sort_val = get_valid_sort_value(sort_val)
                start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                end_page_input = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}, or 'all' for all pages): ").strip()
                
                start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                end_page = parse_end_page(end_page_input, DEFAULT_PAGE_RANGE_END)
                
                # Warn about large page ranges
                if end_page and end_page - start_page >= 20 and not unattended:
                    log_clarification()
                    logger.warning(
                        f"WARNING: Parsing {end_page - start_page + 1} pages will:\n"
                        f"  • Fetch metadata for hundreds of galleries\n"
                        f"  • Take significant time (minutes)\n"
                        f"  • Risk rate limiting\n"
                        f"Recommended: Use 20 pages or less for browsing."
                    )
                    confirm = input("Continue? (y/n): ").strip().lower()
                    if confirm != "y":
                        logger.info("Search cancelled.")
                        continue
                
                logger.info(f"Fetching search={search_query}, sort={sort_val}, pages={start_page}-{end_page}...")
                ids, cache_key = fetch_gallery_ids_with_fallback("search", search_query, sort_val, start_page, end_page)
                
                if ids and cache_key:
                    cache_key = get_cache_key("search", search_query)
                    search_history.append(("search", search_query, cache_key))
                    new_ids = display_gallery_results(ids, cache_key)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
        
        elif choice in ("5", "6", "7", "8", "9"):
            query_map = {
                "5": "artist",
                "6": "group",
                "7": "tag",
                "8": "character",
                "9": "parody",
            }
            
            query_type = query_map[choice]
            query_value = input(f"Enter {query_type} (or press Enter to go back): ").strip()
            
            if query_value:
                sort_val = input(f"Enter sort (date/popular-today/popular-week/popular, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
                sort_val = get_valid_sort_value(sort_val)
                start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                end_page_input = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}, or 'all' for all pages): ").strip()
                
                start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                end_page = parse_end_page(end_page_input, DEFAULT_PAGE_RANGE_END)
                
                # Warn about large page ranges
                if end_page and end_page - start_page >= 20 and not unattended:
                    log_clarification()
                    logger.warning(
                        f"WARNING: Parsing {end_page - start_page + 1} pages will:\n"
                        f"  • Fetch metadata for hundreds of galleries\n"
                        f"  • Take significant time (minutes)\n"
                        f"  • Risk rate limiting\n"
                        f"Recommended: Use 20 pages or less for browsing."
                    )
                    confirm = input("Continue? (y/n): ").strip().lower()
                    if confirm != "y":
                        logger.info("Search cancelled.")
                        continue
                
                logger.info(f"Fetching {query_type}={query_value}, sort={sort_val}, pages={start_page}-{end_page}...")
                ids, cache_key = fetch_gallery_ids_with_fallback(query_type, query_value, sort_val, start_page, end_page)
                
                if ids and cache_key:
                    cache_key = get_cache_key(query_type, query_value)
                    search_history.append((query_type, query_value, cache_key))
                    new_ids = display_gallery_results(ids, cache_key)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")

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
                        selected_search = list(reversed(list(search_history)))[selection - 1]
                        search_type, search_value, cache_key = selected_search
                        sort_val = input(f"Enter sort (date/popular-today/popular-week/popular, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
                        sort_val = get_valid_sort_value(sort_val)
                        start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                        end_page_input = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}, or 'all' for all pages): ").strip()
                        start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                        end_page = parse_end_page(end_page_input, DEFAULT_PAGE_RANGE_END)
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

        elif choice == "q":
            # Archive
            archive_homepage = input("Archive homepage instead of a query? (y/n): ").strip().lower() == "y"
            if archive_homepage:
                homepage_sorts = ["date", "popular-today", "popular-week", "popular"]
                print("Homepage sort options: " + ", ".join(homepage_sorts))
                sort_val = input("Enter sort (default: date): ").strip() or DEFAULT_PAGE_SORT
                sort_val = get_valid_sort_value(sort_val)
                start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                end_page_input = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}, or 'all' for all pages): ").strip()
                end_page = parse_end_page(end_page_input, DEFAULT_PAGE_RANGE_END)
                fetch_all = input("Skip viewing results and archive all pages? (y/n): ").strip().lower() == "y"
                if fetch_all:
                    if not unattended:
                        log_clarification()
                        logger.warning(
                            "WARNING: Archiving all pages will:\n"
                            "  • Download potentially thousands of galleries (100+ GB)\n"
                            "  • Take hours or days to complete\n"
                            "  • Risk rate limiting (403 errors, temporary bans)\n"
                            "  • Consume significant disk space (~150KB per page average)"
                        )
                        confirm = input("Continue with archive all? (yes/no): ").strip().lower()
                        if confirm != "yes":
                            logger.info("Archive cancelled.")
                            continue
                    end_page = None
                elif end_page - start_page > 10:
                    if not unattended:
                        log_clarification()
                        logger.warning(
                            f"WARNING: Archiving {end_page - start_page + 1} pages may:\n"
                            f"  • Download hundreds of galleries\n"
                            f"  • Take significant time (hours)\n"
                            f"  • Risk rate limiting\n"
                            f"Recommended: Start with 10 pages or less."
                        )
                        confirm = input("Continue? (y/n): ").strip().lower()
                        if confirm != "y":
                            logger.info("Archive cancelled.")
                            continue
                
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
                    query_value = input(f"Enter {query_type} (or press Enter to go back): ").strip()
                    if query_value:
                        sort_val = input(f"Enter sort (date/popular-today/popular-week/popular, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
                        sort_val = get_valid_sort_value(sort_val)
                        start_page = input(f"Enter start page (default: {DEFAULT_PAGE_RANGE_START}): ").strip()
                        start_page = int(start_page) if start_page.isdigit() else DEFAULT_PAGE_RANGE_START
                        end_page_input = input(f"Enter end page (default: {DEFAULT_PAGE_RANGE_END}, or 'all' for all pages): ").strip()
                        end_page = parse_end_page(end_page_input, DEFAULT_PAGE_RANGE_END)
                        fetch_all = input("Skip viewing results and download all pages? (y/n): ").strip().lower() == "y"
                        if fetch_all:
                            if not unattended:
                                log_clarification()
                                logger.warning(
                                    "WARNING: Archiving all pages will:\n"
                                    "  • Download potentially thousands of galleries (100+ GB)\n"
                                    "  • Take hours or days to complete\n"
                                    "  • Risk rate limiting (403 errors, temporary bans)\n"
                                    "  • Consume significant disk space (~150KB per page average)"
                                )
                                confirm = input("Continue with archive all? (yes/no): ").strip().lower()
                                if confirm != "yes":
                                    logger.info("Archive cancelled.")
                                    continue
                            end_page = None
                        elif end_page - start_page > 10:
                            if not unattended:
                                log_clarification()
                                logger.warning(
                                    f"WARNING: Archiving {end_page - start_page + 1} pages may:\n"
                                    f"  • Download hundreds of galleries\n"
                                    f"  • Take significant time (hours)\n"
                                    f"  • Risk rate limiting\n"
                                    f"Recommended: Start with 10 pages or less."
                                )
                                confirm = input("Continue? (y/n): ").strip().lower()
                                if confirm != "y":
                                    logger.info("Archive cancelled.")
                                    continue
                        
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
        
        elif choice == "e":
            # View selected galleries
            selected_ids = view_selected_galleries(selected_ids)
        
        else:
            logger.warning("Invalid choice. Enter 1-9, q, w, e, or 0.")
        
        log_clarification()
    
    return list(dict.fromkeys(selected_ids))  # Return unique gallery IDs, preserving insertion order