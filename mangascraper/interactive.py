#!/usr/bin/env python3
# mangascraper/interactive.py
"""
Interactive gallery selection and filtering system.
Handles pre-fetching metadata, displaying summaries, and allowing users to filter results.
"""

import sys, os, shutil, json, re, subprocess, tempfile
from collections import deque
from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import logger, log_clarification, log, update_env, refresh_globals, RUNTIME_LOG_FILE
from mangascraper.core.api import (
    fetch_all_metadata_for_galleries,
    get_metadata_summary,
    fetch_gallery_ids,
    get_session,
    fetch_gallery_metadata,
    fetch_image_urls,
)
from mangascraper.core.cache import (
    get_cache_key,
    load_cache,
    clear_cache,
    load_search_history,
    save_search_history,
    save_selected_galleries,
    load_selected_galleries,
    load_cached_metadata_for_ids,
    get_cache_dir,
)
from mangascraper.extensions.extension_manager import get_extension_download_path

READER_SETTINGS = {
    "quality": "ultra",
    "colors": "full",
    "symbols": "block",
    "dither": "none",
    "oversample": False,
    "clamp_to_terminal": True,
    "preserve_aspect": True,
}

####################################################################################################
# DISPLAY UTILITIES
####################################################################################################

def clear_screen():
    """Clear the terminal screen using ANSI escape codes."""
    # \033[2J clears the entire screen, \033[H moves cursor to home position
    print("\033[2J\033[H", end="", flush=True)

def get_latest_gallery_id(timeout: int = 5) -> int | None:
    """Fetch the latest gallery ID directly from nhentai API without affecting application state."""
    try:
        log_clarification("debug")
        log(f"Fetching latest gallery ID from nhentai homepage...", "debug")
        
        # Request homepage directly from API without using fetch_gallery_ids to avoid state pollution
        session = get_session(referrer="Latest ID Fetch", status="return")
        url = f"{orchestrator.nhentai_api_base}/galleries/all?page=1"
        
        resp = session.get(url, timeout=(10, 10))
        resp.raise_for_status()
        data = resp.json()
        
        results = data.get("result", [])
        if results:
            # Get the first (newest) gallery's ID
            latest_id = int(results[0]["id"])
            log_clarification("debug")
            log(f"Latest gallery ID fetched: {latest_id}", "debug")
            return latest_id
    except Exception as e:
        log_clarification("debug")
        logger.warning(f"Could not fetch latest gallery ID: {e}")
    return None

####################################################################################################
# METADATA UTILITIES
####################################################################################################

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

def display_gallery_results(gallery_ids: list, cache_key: str = None) -> tuple[list, dict]:
    """
    Display found galleries in a paginated table with titles, offer detail view, and collect user selection.
    Uses dynamic terminal size to determine how many galleries fit per page.
    
    Args:
        gallery_ids: List of gallery IDs to display
        cache_key: Optional cache key for metadata (e.g., "artist_john")
    
    Returns:
        tuple: (selected_gallery_ids, metadata_dict) where metadata_dict maps gid to metadata
    """
    
    if not gallery_ids:
        return [], {}
    
    # Fetch metadata for all found galleries (uses cache if available)
    metadata = fetch_all_metadata_for_galleries(gallery_ids, cache_key)
    
    if not metadata:
        logger.warning("Could not fetch metadata for any galleries")
        return [], {}
    
    # Deduplicate by ID and convert to sorted list for pagination (highest ID first)
    unique_metadata = {}
    for gid, meta in metadata.items():
        try:
            gid_int = int(gid)
        except (TypeError, ValueError):
            log(f"Skipping gallery with invalid ID: {gid}", "warning")
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
        print(f"{'#':<4} {'ID':<7} {'Title':<55} {'Pages':<5}")
        print("-" * 75)
        
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
            title = title[:52]
            pages = meta.get("pages", 0)
            print(f"{global_idx:<4} {gid:<7} {title:<55} {pages:<5}")
        
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
            logger.debug(f"Results menu choice: {nav_choice}")
            
            if (nav_choice == "p" or nav_choice == "previous") and current_page > 0:
                current_page -= 1
                logger.debug(f"Results menu page -> {current_page + 1}")
                continue
            elif (nav_choice == "n" or nav_choice == "next") and current_page < total_pages - 1:
                current_page += 1
                logger.debug(f"Results menu page -> {current_page + 1}")
                continue
            elif nav_choice == "d" or nav_choice == "details":
                show_gallery_details(dict(metadata_items))
                continue
            elif nav_choice == "s" or nav_choice == "select":
                break
            elif nav_choice == "q" or nav_choice == "quit":
                return []
            else:
                log("Invalid choice. Use p/previous, n/next, d/details, s/select, or q/quit.", "warning")
                continue
        else:
            # Single page - show navigation options
            print()
            print("Options: [d]etails | [s]elect galleries | [q]uit")
            nav_choice = input("Choice: ").strip().lower()
            logger.debug(f"Results menu choice: {nav_choice}")
            
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
    logger.debug(f"Results selection choice: {select_choice}")
    
    if select_choice in ("n", "none"):
        return [], {}
    elif select_choice in ("a", "all"):
        # Select all galleries
        return [gid for gid, _ in metadata_items], metadata
    elif select_choice in ("s", "specific"):
        # Offer to apply filters before final selection
        if input("\nApply filters to refine results? (y/n): ").strip().lower() == "y":
            logger.debug("Results filter prompt: yes")
            summary = get_metadata_summary(metadata)
            filtered_ids, filtered_metadata = show_filter_menu(summary, metadata)
            if filtered_ids:
                logger.info(f"Filtered results: {len(filtered_ids)} galleries")
                filtered_items = sorted(filtered_metadata.items(), key=lambda x: x[0], reverse=True)
                selection = input("Select galleries by list index (e.g. 1,3-5 for galleries #1, #3-#5 shown above), 'all' for all, or 0 to cancel: ").strip()
                selected = _select_by_index(filtered_items, selection)
                return selected, {gid: filtered_metadata[gid] for gid in selected}
            return [], {}

        selection = input("Select galleries by list index (e.g. 1,3-5 for galleries #1, #3-#5 shown above), 'all' for all, or 0 to cancel: ").strip()
        logger.debug(f"Results selection input: {selection}")
        selected = _select_by_index(metadata_items, selection)
        selected_metadata = {}
        for gid in selected:
            if gid in metadata:
                selected_metadata[gid] = metadata[gid]
            else:
                gid_str = str(gid)
                if gid_str in metadata:
                    selected_metadata[gid] = metadata[gid_str]
        return selected, selected_metadata
    else:
        logger.warning("Invalid choice. Returning without selection.")
        return [], {}


def read_gallery_in_terminal(gallery_id: int):
    def prompt_reader_settings():
        choice = input("Reader quality (ultra/high/medium/low, default ultra): ").strip().lower()
        if choice in ("medium", "m"):
            READER_SETTINGS.update({"quality": "medium", "colors": 16})
        elif choice in ("low", "l"):
            READER_SETTINGS.update({"quality": "low", "colors": 8})
        elif choice in ("high", "h"):
            READER_SETTINGS.update({"quality": "high", "colors": 256})
        elif choice in ("ultra", "u", ""):
            pass
        else:
            logger.warning("Unknown quality option. Keeping default (ultra).")

        oversample = input("Enable oversample render scale (may be slow)? (y/n): ").strip().lower()
        READER_SETTINGS["oversample"] = oversample in ("y", "yes")

        clamp = input("Clamp to terminal size (recommended)? (y/n): ").strip().lower()
        READER_SETTINGS["clamp_to_terminal"] = clamp in ("y", "yes", "")

        preserve = input("Preserve aspect ratio (recommended)? (y/n): ").strip().lower()
        READER_SETTINGS["preserve_aspect"] = preserve in ("y", "yes", "")

    if not sys.platform.startswith("linux"):
        logger.warning("Read mode is Linux-only. Skipping.")
        return
    if not sys.stdout.isatty():
        logger.warning("Read mode requires a TTY. Skipping.")
        return
    if shutil.which("chafa") is None:
        install = input("chafa is required for reader. Install now? (y/n): ").strip().lower()
        if install in ("y", "yes"):
            subprocess.run(["sudo", "apt-get", "install", "-y", "chafa"], check=False)
        if shutil.which("chafa") is None:
            logger.warning("Read mode requires 'chafa' on PATH. Skipping.")
            return

    meta = fetch_gallery_metadata(gallery_id)
    if not meta or not isinstance(meta, dict):
        logger.warning(f"Failed to fetch metadata for Gallery {gallery_id}")
        return

    pages = meta.get("images", {}).get("pages", [])
    total_pages = len(pages)
    if total_pages == 0:
        logger.warning(f"Gallery {gallery_id} has no pages to display")
        return

    prompt_reader_settings()

    session = get_session(referrer="Interactive Reader", status="return")
    base_tmp_dir = "/tmp/manga-scraper"
    os.makedirs(base_tmp_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix=f"mangascraper-read-{gallery_id}-", dir=base_tmp_dir)
    cached_paths = {}

    def _get_page_path(page: int) -> str | None:
        if page in cached_paths:
            return cached_paths[page]
        urls = fetch_image_urls(meta, page)
        if not urls:
            return None
        url = urls[0]
        ext = os.path.splitext(url.split("?")[0])[1]
        if not ext:
            ext = ".jpg"
        target = os.path.join(temp_dir, f"{page}{ext}")
        try:
            resp = session.get(url, timeout=(60, 60), stream=True)
            resp.raise_for_status()
            with open(target, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        except Exception as e:
            logger.warning(f"Failed to fetch page {page} for Gallery {gallery_id}: {e}")
            return None
        cached_paths[page] = target
        return target

    current_page = 1
    try:
        while True:
            clear_screen()
            print(f"Gallery {gallery_id} - Page {current_page}/{total_pages}\n")
            page_path = _get_page_path(current_page)
            if page_path:
                term_size = shutil.get_terminal_size(fallback=(80, 24))
                if READER_SETTINGS["clamp_to_terminal"]:
                    render_cols = max(20, term_size.columns)
                    render_rows = max(10, term_size.lines - 4)
                else:
                    render_cols = max(1, term_size.columns)
                    render_rows = max(1, term_size.lines)

                chafa_args = [
                    "chafa",
                    f"--size={render_cols}x{render_rows}",
                    f"--symbols={READER_SETTINGS['symbols']}",
                    f"--colors={READER_SETTINGS['colors']}",
                    f"--dither={READER_SETTINGS['dither']}",
                    page_path,
                ]
                if not READER_SETTINGS["preserve_aspect"]:
                    chafa_args.insert(-1, "--stretch")
                if READER_SETTINGS["oversample"]:
                    chafa_args.insert(-1, "--scale=2")
                result = subprocess.run(chafa_args, check=False)
                if result.returncode != 0 and READER_SETTINGS["oversample"]:
                    fallback_args = [arg for arg in chafa_args if arg != "--scale=2"]
                    subprocess.run(fallback_args, check=False)
            else:
                logger.warning("Unable to display this page.")

            print("\nOptions: [n]ext | [p]revious | [g]oto | [s]ettings | [q]uit")
            choice = input("Choice: ").strip().lower()
            if choice in ("q", "quit"):
                break
            if choice in ("n", "next"):
                if current_page < total_pages:
                    current_page += 1
                continue
            if choice in ("p", "prev", "previous"):
                if current_page > 1:
                    current_page -= 1
                continue
            if choice in ("g", "goto"):
                page_input = input(f"Go to page (1-{total_pages}): ").strip()
                if page_input.isdigit():
                    page_num = int(page_input)
                    if 1 <= page_num <= total_pages:
                        current_page = page_num
                continue
            if choice in ("s", "settings"):
                prompt_reader_settings()
                continue
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def view_selected_galleries(selected_ids: list, cached_metadata: dict | None = None) -> list:
    """
    Display selected galleries in paginated format with removal capability.
    
    Args:
        selected_ids: List of selected gallery IDs
        cached_metadata: Optional pre-fetched metadata to avoid redundant API calls
    
    Returns:
        list: Updated list of selected IDs (after any removals)
    """
    cached_ids = load_selected_galleries()
    selected_ids = cached_ids

    if not selected_ids:
        logger.info("No galleries selected yet.")
        return []
    
    unique_ids = list(dict.fromkeys(selected_ids))
    logger.info(f"Currently selected: {len(unique_ids)} unique galleries")
    
    # Use cached metadata if provided, otherwise fall back to cached selections
    metadata = {}
    if cached_metadata:
        metadata.update({gid: cached_metadata[gid] for gid in unique_ids if gid in cached_metadata})

    cached_by_ids = load_cached_metadata_for_ids(unique_ids)
    if cached_by_ids:
        for gid in unique_ids:
            if gid not in metadata and gid in cached_by_ids:
                metadata[gid] = cached_by_ids[gid]

    missing_ids = [gid for gid in unique_ids if gid not in metadata]
    if missing_ids:
        logger.info(f"Fetching metadata for {len(missing_ids)} galleries not in cache...")
        missing_metadata = fetch_all_metadata_for_galleries(missing_ids)
        metadata.update(missing_metadata)
    
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
        print(f"{'#':<4} {'ID':<7} {'Title':<55} {'Pages':<5}")
        print("-" * 75)
        
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
            title = title[:52]
            pages = meta.get("pages", 0)
            print(f"{global_idx:<4} {gid:<7} {title:<55} {pages:<5}")
        
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
        logger.debug(f"Selected galleries menu choice: {nav_choice}")
        
        if (nav_choice == "p" or nav_choice == "previous") and current_page > 0:
            current_page -= 1
            logger.debug(f"Selected galleries page -> {current_page + 1}")
            continue
        elif (nav_choice == "n" or nav_choice == "next") and current_page < total_pages - 1:
            current_page += 1
            logger.debug(f"Selected galleries page -> {current_page + 1}")
            continue
        elif nav_choice == "d" or nav_choice == "details":
            show_gallery_details(dict(active_items))
            continue
        elif nav_choice == "r" or nav_choice == "remove":
            # Ask which galleries to remove
            selection = input("Remove galleries by index (comma-separated list, e.g. 1,3-5, 'all' to remove all, or 0 to cancel): ").strip()
            logger.debug(f"Selected galleries remove input: {selection}")
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
        logger.debug(f"Filter menu choice: {choice}")
        
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
        DEFAULT_VERIFY_SSL, DEFAULT_USE_DAEMON_THREADS, DEFAULT_CALM
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
            f"  [r] Allow Background Processing: {config.get('use_daemon_threads', DEFAULT_USE_DAEMON_THREADS)}\n"
            f"  [t] Max Download Retries: {config.get('max_retries', DEFAULT_MAX_RETRIES)}\n"
            "\nOptions:\n"
            f"  [y] Reduce Logs: {config.get('calm', DEFAULT_CALM)}\n"
            "  [a] Clear cache\n"
            "  [0] Continue to search with these settings\n"
        )
        
        choice = input("Enter choice [1-9,q,w,e,r,t,y,a,0]: ").strip().lower()
        
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
        elif choice == "r":
            val = input(f"Allow Background Processing? (y/n, current: {config.get('use_daemon_threads', DEFAULT_USE_DAEMON_THREADS)}): ").strip().lower()
            if val in ('y', 'n'):
                config['use_daemon_threads'] = val == 'y'
            else:
                logger.warning("Invalid input")
        elif choice == "t":
            try:
                val = int(input(f"Max retries (current: {config.get('max_retries', DEFAULT_MAX_RETRIES)}): ").strip())
                if val >= 0:
                    config['max_retries'] = val
                else:
                    logger.warning("Must be 0 or greater")
            except ValueError:
                logger.warning("Invalid number")
        elif choice == "y":
            val = input(f"Reduce Logs? (y/n, current: {config.get('calm', DEFAULT_CALM)}): ").strip().lower()
            if val in ('y', 'n'):
                config['calm'] = val == 'y'
            else:
                logger.warning("Invalid input")
        elif choice == "a":
            confirm = input("Are you sure you want to clear the cache? (y/n): ").strip().lower()
            if confirm == 'y':
                clear_cache()
                logger.info("Cache cleared successfully.")
            else:
                logger.info("Cache clear cancelled.")
        else:
            logger.warning("Invalid choice. Enter 0-9, q, w, e, r, t, y, or a.")
        
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
            ids = fetch_gallery_ids(
                search_type,
                search_value,
                sort_val,
                start_page,
                end_page,
                fetch_as_archival=fetch_as_archival,
            )
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

def _handle_search_error(search_type: str, search_value: str = ""):
    """
    Handle search errors consistently and return user to config menu.
    
    Args:
        search_type: Type of search that failed
        search_value: Optional search value for more specific error messages
    """
    log_clarification()
    logger.error(
        f"An error occurred during {search_type} search"
        f"{f' for {search_value}' if search_value else ''}.\n"
        f"Please check the log file for details: {RUNTIME_LOG_FILE}\n"
        f"The search menu will now return to the configuration menu to prevent further issues."
    )
    log_clarification()
    return True  # Signal to return to config menu

def _check_no_results_and_prompt_filters():
    """
    Helper function to prompt user when a search returns no results.
    Asks if they want to adjust their language/tag filters.
    Returns True if user wants to change filters, False otherwise.
    """
    log_clarification()
    logger.warning("No galleries found with current filters.")
    response = input("\nWould you like to change your filters? (y/n): ").strip().lower()
    if response == "y":
        logger.info("Returning to configuration menu to adjust filters...")
        log_clarification()
        return True
    return False

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

    def prompt_yes_no(prompt: str) -> bool:
        while True:
            value = input(prompt).strip().lower()
            if value in ("y", "yes"):
                return True
            if value in ("n", "no"):
                return False
            logger.warning("Invalid input. Enter y or n.")

    def prompt_archive_mode() -> bool:
        archive = prompt_yes_no("Archive this query (add every result to download)? (y/n): ")
        if not archive:
            return False
        logger.warning("WARNING: Archiving adds every result to the download queue.")
        confirm = prompt_yes_no("Are you sure you want to archive this query? (y/n): ")
        if not confirm:
            logger.info("Archive cancelled. Continuing with normal browsing.")
            return False
        return True
    
    logger.info("No gallery sources specified. Entering interactive search mode...")
    log_clarification()

    get_session(referrer="Interactive", status="build")

    # Only clear selected galleries if this is the first invocation (no initial_ids and not unattended)
    if (not initial_ids) and (not unattended):
        save_selected_galleries([])

    selected_ids = []
    cache_path = get_cache_dir() / "(selected_galleries).json"
    if cache_path.exists():
        selected_ids = load_selected_galleries()
    if initial_ids:
        selected_ids.extend(initial_ids)
        selected_ids = list(dict.fromkeys(selected_ids))
        if selected_ids:
            logger.info(f"Loaded {len(selected_ids)} galleries from CLI flags")
    search_history_max = 10
    search_history = deque(maxlen=search_history_max)  # Each entry is a dict with search details
    for entry in load_search_history(search_history_max):
        search_history.append(entry)
    selected_metadata = {}  # Track metadata for all selected galleries to avoid redundant fetches

    def persist_selected_ids():
        nonlocal selected_ids
        cache_exists = cache_path.exists()
        cached_ids = load_selected_galleries() if cache_exists else []
        merged = list(dict.fromkeys(cached_ids + selected_ids))
        save_selected_galleries(sorted(set(merged)) if merged else [])
        selected_ids = merged

    if selected_ids:
        persist_selected_ids()

    def add_search_history(
        search_type: str,
        search_value: str,
        cache_key: str | None,
        sort_val: str | None = None,
        start_page: int | None = None,
        end_page: int | None = None,
        archive_mode: bool = False,
    ):
        entry = {
            "type": search_type,
            "value": search_value,
            "cache_key": cache_key,
            "sort": sort_val,
            "start_page": start_page,
            "end_page": end_page,
            "archive_mode": archive_mode,
        }
        search_history.append(entry)
        save_search_history(list(search_history), search_history_max)
    
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
            "  [q] Read a gallery (don't expect high quality lmfaoooo)\n"
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
        logger.debug(f"Search menu choice: {choice}")
        
        if choice == "0":
            if cache_path.exists():
                selected_ids = load_selected_galleries()
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
                DEFAULT_CALM, DEFAULT_VERIFY_SSL, DEFAULT_USE_DAEMON_THREADS,
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
                'calm': config.get('CALM', DEFAULT_CALM),
                'verify_ssl': config.get('VERIFY_SSL', DEFAULT_VERIFY_SSL),
                'use_daemon_threads': config.get('USE_DAEMON_THREADS', DEFAULT_USE_DAEMON_THREADS),
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
            update_env('CALM', modified_config.get('calm'))
            update_env('VERIFY_SSL', modified_config.get('verify_ssl'))
            update_env('USE_DAEMON_THREADS', modified_config.get('use_daemon_threads'))
            refresh_globals()
            
            logger.info("Configuration updated.")
            continue
        
        elif choice == "1":
            # Homepage
            archive_mode = prompt_archive_mode()
            if archive_mode:
                sort_val = DEFAULT_PAGE_SORT
                start_page = DEFAULT_PAGE_RANGE_START
                end_page = None
                fetch_all = True
            else:
                sort_val = input(f"Enter sort (1=date, 2=popular-today, 3=popular-week, 4=popular-all-time, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
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
            
            if not archive_mode:
                fetch_all = False
                view_results = prompt_yes_no("View results? (y/n): ")
                if not view_results:
                    add_all = prompt_yes_no("Add all galleries to download? (y/n): ")
                    if not add_all:
                        logger.info("No galleries added. Returning to menu.")
                        continue
                    fetch_all = True
                    if not unattended:
                        log_clarification()
                        logger.warning(
                            "WARNING: Adding all galleries to download queue will:\n"
                            "  • Parse potentially thousands of pages\n"
                            "  • Take significant time (hours)\n"
                            "  • Risk rate limiting (403 errors)\n"
                            "  • Use this for archival purposes only"
                        )
                        confirm = input("Continue with add to download queue? (yes/no): ").strip().lower()
                        if confirm != "yes":
                            logger.info("Add to download queue cancelled.")
                            continue
                    end_page = None
            
            logger.info(f"Fetching homepage (sort={sort_val}, pages={start_page}-{end_page or 'all'})...")
            fetch_all_pages = archive_mode or fetch_all or end_page is None
            ids, cache_key = fetch_gallery_ids_with_fallback(
                "homepage",
                sort_val,
                sort_val,
                start_page,
                end_page,
                fetch_as_archival=fetch_all_pages,
            )
            
            # Check for search errors
            if cache_key is None:
                logger.error(
                    f"Failed to fetch homepage.\n"
                    f"Check the log file for details: {RUNTIME_LOG_FILE}"
                )
                logger.info("Returning to menu. Please try a different search or check your connection.")
                log_clarification()
                continue
            
            if ids and cache_key:
                cache_key = get_cache_key("homepage", sort_val)
                add_search_history("homepage", sort_val, cache_key, sort_val, start_page, end_page, archive_mode=archive_mode)
                if archive_mode:
                    selected_ids.extend(ids)
                    logger.info(f"Added {len(ids)} galleries to download list.")
                    logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                    persist_selected_ids()
                    continue
                new_ids, new_metadata = display_gallery_results(ids, cache_key)
                if new_ids:
                    selected_ids.extend(new_ids)
                    selected_metadata.update(new_metadata)
                    logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                    persist_selected_ids()
            else:
                if not ids:
                    if _check_no_results_and_prompt_filters():
                        break  # Break out to config menu
                else:
                    logger.info("No galleries found on homepage")

        elif choice == "2":
            # Browse by ID range
            try:
                while True:
                    start_input = input(f"Enter start ID (numbers only): ").strip()
                    if not start_input:
                        logger.warning("Start ID cannot be empty.")
                        continue
                    if not start_input.isdigit():
                        logger.warning(f"Invalid input '{start_input}'. Please enter only numbers (0-9).")
                        continue
                    
                    start_id = int(start_input)
                    break
                
                # Fetch latest ID if user doesn't specify end ID
                logger.info("Fetching latest gallery ID from nhentai...")
                latest_id = get_latest_gallery_id()
                
                while True:
                    if latest_id is None:
                        default_text = "(manual entry required)"
                        end_input = input(f"Enter end ID {default_text}: ").strip()
                        if not end_input:
                            logger.warning("End ID cannot be empty.")
                            continue
                        if not end_input.isdigit():
                            logger.warning(f"Invalid input '{end_input}'. Please enter only numbers (0-9).")
                            continue
                        end_id = int(end_input)
                    else:
                        end_input = input(f"Enter end ID (default: {latest_id}): ").strip()
                        if not end_input:
                            end_id = latest_id
                        elif not end_input.isdigit():
                            logger.warning(f"Invalid input '{end_input}'. Using default {latest_id}.")
                            end_id = latest_id
                        else:
                            end_id = int(end_input)
                    break
                
                if start_id > end_id:
                    logger.warning(f"Start ID ({start_id}) is greater than end ID ({end_id}). Swapping...")
                    start_id, end_id = end_id, start_id
                
                ids = list(range(start_id, end_id + 1))
                logger.info(f"Fetching {len(ids)} galleries (IDs {start_id} to {end_id})...")
                new_ids, new_metadata = display_gallery_results(ids)
                if new_ids:
                    selected_ids.extend(new_ids)
                    selected_metadata.update(new_metadata)
                    logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                    persist_selected_ids()
            except ValueError as e:
                logger.warning(f"Error processing ID range: {e}")
        
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
                if len(ids) > 25:
                    logger.warning("You can enter at most 25 gallery IDs at a time. Use --file in the CLI for larger lists.")
                    continue
                if ids:
                    selected_ids.extend(ids)
                    persist_selected_ids()
                    new_ids, new_metadata = display_gallery_results(ids)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        selected_metadata.update(new_metadata)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                        persist_selected_ids()
        
        elif choice == "4":
            # General search
            search_query = input("Enter search query (or press Enter to go back): ").strip()
            if search_query:
                archive_mode = prompt_archive_mode()
                if archive_mode:
                    sort_val = DEFAULT_PAGE_SORT
                    start_page = DEFAULT_PAGE_RANGE_START
                    end_page = None
                else:
                    sort_val = input(f"Enter sort (1=date, 2=popular-today, 3=popular-week, 4=popular-all-time, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
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
                
                logger.info(f"Fetching search={search_query}, sort={sort_val}, pages={start_page}-{end_page or 'all'}...")
                fetch_all_pages = archive_mode or end_page is None
                ids, cache_key = fetch_gallery_ids_with_fallback(
                    "search",
                    search_query,
                    sort_val,
                    start_page,
                    end_page,
                    fetch_as_archival=fetch_all_pages,
                )
                
                # Check for search errors
                if cache_key is None:
                    logger.error(
                        f"Failed to fetch search results for '{search_query}'.\n"
                        f"Check the log file for details: {RUNTIME_LOG_FILE}"
                    )
                    logger.info("Returning to menu. Please try a different search or check your connection.")
                    log_clarification()
                    continue
                
                if ids and cache_key:
                    cache_key = get_cache_key("search", search_query)
                    add_search_history("search", search_query, cache_key, sort_val, start_page, end_page, archive_mode=archive_mode)
                    if archive_mode:
                        selected_ids.extend(ids)
                        logger.info(f"Added {len(ids)} galleries to download list.")
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                        persist_selected_ids()
                        continue
                    new_ids, new_metadata = display_gallery_results(ids, cache_key)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        selected_metadata.update(new_metadata)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                        persist_selected_ids()
                else:
                    if not ids:
                        if _check_no_results_and_prompt_filters():
                            break  # Break out to config menu
                    else:
                        logger.info(f"No galleries found for search: {search_query}")
        
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
                archive_mode = prompt_archive_mode()
                if archive_mode:
                    sort_val = DEFAULT_PAGE_SORT
                    start_page = DEFAULT_PAGE_RANGE_START
                    end_page = None
                else:
                    sort_val = input(f"Enter sort (1=date, 2=popular-today, 3=popular-week, 4=popular-all-time, default: {DEFAULT_PAGE_SORT}): ").strip() or DEFAULT_PAGE_SORT
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
                
                logger.info(f"Fetching {query_type}={query_value}, sort={sort_val}, pages={start_page}-{end_page or 'all'}...")
                fetch_all_pages = archive_mode or end_page is None
                ids, cache_key = fetch_gallery_ids_with_fallback(
                    query_type,
                    query_value,
                    sort_val,
                    start_page,
                    end_page,
                    fetch_as_archival=fetch_all_pages,
                )
                
                # Check for search errors
                if cache_key is None:
                    logger.error(
                        f"Failed to fetch {query_type}={query_value}.\n"
                        f"Check the log file for details: {RUNTIME_LOG_FILE}"
                    )
                    logger.info("Returning to menu. Please try a different search or check your connection.")
                    log_clarification()
                    continue
                
                if ids and cache_key:
                    cache_key = get_cache_key(query_type, query_value)
                    add_search_history(query_type, query_value, cache_key, sort_val, start_page, end_page, archive_mode=archive_mode)
                    if archive_mode:
                        selected_ids.extend(ids)
                        logger.info(f"Added {len(ids)} galleries to download list.")
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                        persist_selected_ids()
                        continue
                    new_ids, new_metadata = display_gallery_results(ids, cache_key)
                    if new_ids:
                        selected_ids.extend(new_ids)
                        selected_metadata.update(new_metadata)
                        logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                        persist_selected_ids()
                else:
                    if not ids:
                        if _check_no_results_and_prompt_filters():
                            break  # Break out to config menu
                    else:
                        logger.info(f"No galleries found for {query_type}={query_value}")

        elif choice == "w":
            # View recent searches
            if search_history:
                log_clarification()
                print("Recent searches (most recent first):\n")
                for idx, entry in enumerate(reversed(list(search_history)), 1):
                    label = f"{entry['type']}: {entry['value']}"
                    if entry.get("archive_mode"):
                        label = f"archive {entry['type']}: {entry['value']}"
                    print(f"  [{idx}] {label}")
                
                try:
                    selection = int(input("\nSelect search to re-run (1-{}) or 0 to cancel: ".format(len(search_history))).strip())
                    if 1 <= selection <= len(search_history):
                        selected_search = list(reversed(list(search_history)))[selection - 1]
                        search_type = selected_search["type"]
                        search_value = selected_search["value"]
                        cache_key = selected_search.get("cache_key")
                        archive_mode = bool(selected_search.get("archive_mode", False))
                        default_sort = selected_search.get("sort") or DEFAULT_PAGE_SORT
                        default_start = selected_search.get("start_page") or DEFAULT_PAGE_RANGE_START
                        default_end = selected_search.get("end_page")
                        default_end_display = "all" if default_end is None else default_end
                        
                        sort_val = input(
                            f"Enter sort (1=date, 2=popular-today, 3=popular-week, 4=popular-all-time, default: {default_sort}): "
                        ).strip() or default_sort
                        sort_val = get_valid_sort_value(sort_val)
                        start_page_input = input(f"Enter start page (default: {default_start}): ").strip()
                        end_page_input = input(
                            f"Enter end page (default: {default_end_display}, or 'all' for all pages): "
                        ).strip()
                        
                        start_page = int(start_page_input) if start_page_input.isdigit() else default_start
                        if end_page_input == "":
                            end_page = default_end
                            if end_page is None:
                                end_page = None
                            elif not isinstance(end_page, int):
                                end_page = DEFAULT_PAGE_RANGE_END
                        else:
                            fallback_end = default_end if isinstance(default_end, int) else DEFAULT_PAGE_RANGE_END
                            end_page = parse_end_page(end_page_input, fallback_end)
                        
                        logger.info(f"Re-running search: {search_type}={search_value}...")
                        fetch_all_pages = archive_mode or end_page is None
                        ids, rerun_cache_key = fetch_gallery_ids_with_fallback(
                            search_type,
                            search_value,
                            sort_val,
                            start_page,
                            end_page,
                            fetch_as_archival=fetch_all_pages,
                        )
                        
                        # Check for search errors
                        if rerun_cache_key is None:
                            logger.error(
                                f"Failed to fetch {search_type}={search_value}.\n"
                                f"Check the log file for details: {RUNTIME_LOG_FILE}"
                            )
                            logger.info("Returning to menu. Please try a different search or check your connection.")
                            log_clarification()
                            continue
                        
                        if ids:
                            use_cache_key = rerun_cache_key or cache_key
                            new_ids, new_metadata = display_gallery_results(ids, use_cache_key)
                            if new_ids:
                                selected_ids.extend(new_ids)
                                selected_metadata.update(new_metadata)
                                logger.info(f"Total selected: {len(dict.fromkeys(selected_ids))} unique galleries")
                                persist_selected_ids()
                        else:
                            if _check_no_results_and_prompt_filters():
                                break  # Break out to config menu
                            else:
                                logger.info(f"No galleries found for {search_type}={search_value}")
                except ValueError:
                    logger.warning("Invalid selection")
            else:
                logger.info("No recent searches yet.")
        
        elif choice == "e":
            # View selected galleries
            if cache_path.exists():
                selected_ids = load_selected_galleries()
            selected_ids = view_selected_galleries(selected_ids, selected_metadata)
            persist_selected_ids()
            continue

        elif choice == "q":
            gallery_input = input("Enter a single gallery ID to read (press Enter to cancel): ").strip()
            if not gallery_input:
                continue
            if not gallery_input.isdigit():
                logger.warning("Invalid gallery ID. Enter numbers only.")
                continue
            read_gallery_in_terminal(int(gallery_input))
            continue
        
        else:
            logger.warning("Invalid choice. Enter 1-9, q, w, e, r, or 0.")
        
        log_clarification()
    
    return list(dict.fromkeys(selected_ids))  # Return unique gallery IDs, preserving insertion order