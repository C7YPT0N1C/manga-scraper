#!/usr/bin/env python3
# nhscraper/cli.py
import os, sys, time, random, argparse, re, subprocess, urllib.parse # 'Default' imports

import threading, asyncio # Module-specific imports

from nhscraper.core import orchestrator
from nhscraper.core.orchestrator import *
from nhscraper.core.downloader import start_downloader
from nhscraper.core.api import get_session, fetch_gallery_ids
from nhscraper.extensions.extension_loader import install_selected_extension, uninstall_selected_extension

"""
Command-line interface for controlling the downloader.
Handles argument parsing, configuration loading, and
initiating orchestrator tasks.
"""

"""
CLI is synchronous. All async functions must be executed through the executor:
- executor.run_blocking(func, *args) → blocks until finished, returns result
- Do not use 'await' or executor.spawn_task() in this module
"""

_module_referrer=f"CLI" # Used in executor.* calls

INSTALLER_PATH = "/opt/nhentai-scraper/nhscraper-install.sh"

# ------------------------------------------------------------
# Delegate to installer
# ------------------------------------------------------------
INSTALLER_FLAGS = ["--install", "--update", "--update-env", "--uninstall", "--remove"]

def run_installer(flag: str):
    """
    Call the Bash installer with the given flag, using sudo if needed.
    """
    
    if not os.path.exists(INSTALLER_PATH):
        print(f"[ERROR] Installer not found at {INSTALLER_PATH}")
        sys.exit(1)

    # Build command: run via bash explicitly
    cmd = ["/bin/bash", INSTALLER_PATH, flag]

    # If not root, prepend sudo
    if os.geteuid() != 0:
        cmd.insert(0, "sudo")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Installer failed with exit code {e.returncode}")
        sys.exit(e.returncode)
    except PermissionError:
        print(f"[ERROR] Permission denied: {INSTALLER_PATH}. Did you chmod +x it?")
        sys.exit(1)

    sys.exit(0)  # Exit after running installer

def parse_args():
    parser = argparse.ArgumentParser(description="NHentai scraper CLI")

    # Installer / Updater flags
    parser.add_argument("--install", action="store_true", help="Install nhentai-scraper and dependencies")
    parser.add_argument("--update", action="store_true", help="Update nhentai-scraper")
    parser.add_argument("--update-env", action="store_true", help="Update the .env file")
    parser.add_argument("--uninstall", "--remove", action="store_true", help="Uninstall nhentai-scraper")

    # Extension selection / management
    parser.add_argument("--install-extension", type=str, help="Install an extension by name")
    parser.add_argument("--uninstall-extension", type=str, help="Uninstall an extension by name")
    parser.add_argument("--extension", type=str, default=DEFAULT_EXTENSION, help=f"Extension to use (default: {DEFAULT_EXTENSION})")

    # Gallery selection
    parser.add_argument(
        "--file",
        type=str,
        nargs="?",                  # Makes the argument optional
        const=DEFAULT_DOUJIN_TXT_PATH,  # Use default if --file is passed without a value
        help=(
            "Path to a file containing gallery URLs or IDs (one per line)."
            "If no path is given, uses the default file."
        )
    )
    
    # NHentai mirror URLs
    parser.add_argument(
        "--mirrors",
        type=str,
        default=DEFAULT_NHENTAI_MIRRORS,
        help=(
            f"Comma-separated list of NHentai mirror URLs (default: {DEFAULT_NHENTAI_MIRRORS}). "
            "Use this if the main site is down or to rotate mirrors."
        )
    )
    
    parser.add_argument("--range", nargs=2, type=int, metavar=("START","END"), help=f"Gallery ID range to download (default: {DEFAULT_RANGE_START}-{DEFAULT_RANGE_END})")
    parser.add_argument("--galleries", type=str, help="Comma-separated gallery IDs to download. Must be incased in quotes if multiple. (e.g. '123456, 654321')")
    
    parser.add_argument(
        "--homepage",
        nargs="+",  # All args after this flag are collected
        metavar="ARGS",
        help=(
            f"Page range or sort type of galleries to download from NHentai Homepage (default: {DEFAULT_PAGE_RANGE_START} - {DEFAULT_PAGE_RANGE_END})"
        )
    )

    # Allow multiple --artist, --group, etc. each with their own arguments
    parser.add_argument(
        "--artist",
        action="append",
        nargs="+",  # All args after this flag are collected
        metavar="ARGS",
        help="Download galleries by artist. Usage: --artist ARTIST [SORT_TYPE] [START_PAGE] [END_PAGE]. Can be repeated."
    )
    parser.add_argument(
        "--group",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by group. Usage: --group GROUP [SORT_TYPE] [START_PAGE] [END_PAGE]. Can be repeated."
    )
    parser.add_argument(
        "--tag",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by tag. Usage: --tag TAG [SORT_TYPE] [START_PAGE] [END_PAGE]. Can be repeated."
    )
    parser.add_argument(
        "--character",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by character. Usage: --character CHARACTER [SORT_TYPE] [START_PAGE] [END_PAGE]. Can be repeated."
    )
    parser.add_argument(
        "--parody",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by parody. Usage: --parody PARODY [SORT_TYPE] [START_PAGE] [END_PAGE]. Can be repeated."
    )
    parser.add_argument(
        "--search",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by search. Usage: --search SEARCH [SORT_TYPE] [START_PAGE] [END_PAGE]. Can be repeated."
    )

    # Filters
    parser.add_argument("--excluded-tags", type=str, default=None, help=f"Comma-separated list of tags to exclude galleries (default: '{DEFAULT_EXCLUDED_TAGS}')")
    parser.add_argument("--language", type=str, default=DEFAULT_LANGUAGE, help=f"Comma-separated list of languages to include (default: '{DEFAULT_LANGUAGE}')")
    parser.add_argument(
        "--title-type",
        choices=["english","japanese","pretty"],
        default=DEFAULT_TITLE_TYPE,
        help=(
            f"What title type to use (default: {DEFAULT_TITLE_TYPE}). "
            "Not using 'pretty' may lead to unsupported symbols in gallery names being replaced to be filesystem compatible, although titles are cleaned to try and avoid this."
        )
    )

    # Threads / concurrency
    parser.add_argument(
        "--threads-galleries",
        type=int,
        default=DEFAULT_THREADS_GALLERIES,
        help=(
            f"Number of threads downloading galleries at once (default: {DEFAULT_THREADS_GALLERIES}). "
            f"Be careful setting this any higher than {DEFAULT_THREADS_GALLERIES}"
        )
    )
    
    parser.add_argument(
        "--threads-images",
        type=int,
        default=DEFAULT_THREADS_IMAGES,
        help=(
            f"Number of threads per gallery downloading images at once (default: {DEFAULT_THREADS_IMAGES}). "
            f"Be careful setting this any higher than {DEFAULT_THREADS_IMAGES}"
        )
    )
    
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help=f"Maximum number of retry attempts for failed downloads (default: {DEFAULT_MAX_RETRIES})")
    parser.add_argument(
        "--min-sleep",
        type=int,
        default=DEFAULT_MIN_SLEEP,
        help=(
            f"Minimum amount of time each thread should sleep before starting a new download (default: {DEFAULT_MIN_SLEEP}). "
            f"Set this to a higher number if you are hitting API limits."
        )
    )
    parser.add_argument(
        "--max-sleep",
        type=int,
        default=DEFAULT_MAX_SLEEP,
        help=(
            f"Maximum amount of time each thread can sleep before starting a new download (default: {DEFAULT_MAX_SLEEP}). "
            f"Setting this to a number lower than {DEFAULT_MAX_SLEEP}, may result in hitting API limits."
        )
    )
    
    # Download / runtime options
    parser.add_argument("--use-tor", action="store_true", default=DEFAULT_USE_TOR, help=f"Use TOR network for downloads (default: {DEFAULT_USE_TOR})")
    parser.add_argument(
        "--skip-post-run",
        action="store_true",
        default=DEFAULT_SKIP_POST_RUN,
        help=(
            f"Skips the extra post download actions (default: {DEFAULT_SKIP_POST_RUN}). "
            "For example, if you're using the Suwayomi extension, the download directory is still cleaned, but things like updating Suwayomi are skipped."
        )
    )
    parser.add_argument("--dry-run", action="store_true", default=DEFAULT_DRY_RUN, help=f"Simulate downloads without saving files (default: {DEFAULT_DRY_RUN})")
    
    # Make calm/debug mutually exclusive
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--calm", action="store_true", default=DEFAULT_CALM, help=f"Reduces logging to warnings and higher. (default: {DEFAULT_CALM})")
    group.add_argument(
        "--debug",
        action="store_true",
        default=DEFAULT_DEBUG,
        help=(
            f"Increases logging to critical errors and lower (default: {DEFAULT_DEBUG}). "
            "Turning this on WILL make your logs massive."
        )
    )

    return parser.parse_args()

def _handle_gallery_args(arg_list: list | None, query_type: str) -> set[int]:
    """
    Parse CLI args or file URLs and call fetch_gallery_ids for any query type.
    Supports optional sort type in flags: --artist ARTIST [SORT_TYPE] [START_PAGE] [END_PAGE]
    Defaults: sort='date', start_page=1, end_page=DEFAULT_PAGE_RANGE_END

    File input supports:
      - Plain gallery IDs
      - Full gallery URLs /g/ID/
      - Artist / group / tag / character / parody / search URLs
    """
    
    if not arg_list:
        return set()

    gallery_ids = set()
    query_lower = query_type.lower()
    
    valid_sorts = ("date", "recent", "popular_today", "today", "popular_week", "week", "popular", "all_time")

    # --- File input ---
    if query_lower == "file":
        file_path = arg_list[0] if isinstance(arg_list, list) else arg_list
        if not os.path.isfile(file_path):
            logger.warning(f"Gallery file not found: {file_path}")
            return set()
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # Skip comments without warning 
                if line.startswith("#"):
                    continue

                # Plain numeric ID
                if line.isdigit():
                    gallery_ids.add(int(line))
                    continue

                # Full gallery URL "/g/ID/"
                m_gallery = re.search(r"nhentai\.net/g/(\d+)", line)
                if m_gallery:
                    gallery_ids.add(int(m_gallery.group(1)))
                    continue

                # Homepage URLs (e.g. https://nhentai.net/?page=6)
                m_homepage = re.search(r"nhentai\.net/\?page=(\d+)", line)
                if m_homepage:
                    end_page = int(m_homepage.group(1))
                    sort_val = DEFAULT_PAGE_SORT
                    sort_val = get_valid_sort_value(sort_val)
                    start_page = DEFAULT_PAGE_RANGE_START
                    
                    result = executor.run_blocking(fetch_gallery_ids, "homepage", None, sort_val, start_page, end_page)
                    if result:
                        gallery_ids.update(result)
                    
                    continue

                # Creator / group / tag / character / parody / search URLs
                m_query = re.search(
                    r"nhentai\.net/(artist|group|tag|character|parody)/([^/?]+)(?:/(popular-today|popular-week|popular))?(?:\?page=(\d+))?",
                    line
                )
                m_search = re.search(r"nhentai\.net/search/\?q=([^&]+)(?:&page=(\d+))?", line)
                
                if m_query:
                    qtype, qvalue, sort_path, page_q = m_query.groups()
                    qvalue = urllib.parse.unquote(qvalue)
                    sort_val = get_valid_sort_value(sort_path if sort_path else DEFAULT_PAGE_SORT)
                    start_page = 1
                    end_page = int(page_q) if page_q else DEFAULT_PAGE_RANGE_END
                    
                    result = executor.run_blocking(fetch_gallery_ids, qtype, qvalue, sort_val, start_page, end_page)
                    if result:
                        gallery_ids.update(result)
                        
                    continue

                elif m_search:
                    search_query, page_q = m_search.groups()
                    search_query = urllib.parse.unquote(search_query)
                    sort_val = get_valid_sort_value(DEFAULT_PAGE_SORT)
                    start_page = 1
                    end_page = int(page_q) if page_q else DEFAULT_PAGE_RANGE_END
                    
                    result = executor.run_blocking(fetch_gallery_ids, "search", search_query, sort_val, start_page, end_page)
                    if result:
                        gallery_ids.update(result)
                        
                    continue

                else:
                    logger.warning(f"Unrecognised line in file, skipping: {line}")

        return gallery_ids

    # --- Homepage ---
    if query_lower == "homepage":
        sort_val = DEFAULT_PAGE_SORT
        sort_val = get_valid_sort_value(sort_val)
        start_page = DEFAULT_PAGE_RANGE_START
        end_page = DEFAULT_PAGE_RANGE_END

        if arg_list:
            first = str(arg_list[0]).lower()
            if first in valid_sorts:
                sort_val = first
                if len(arg_list) > 1:
                    start_page = int(arg_list[1])
                if len(arg_list) > 2:
                    end_page = int(arg_list[2])
            else:
                start_page = int(arg_list[0])
                if len(arg_list) > 1:
                    end_page = int(arg_list[1])

        result = executor.run_blocking(fetch_gallery_ids, "homepage", None, sort_val, start_page, end_page)
        if result:
            gallery_ids.update(result)
        
        return gallery_ids

    # --- Other queries (CLI flags) ---
    for entry in arg_list:
        if isinstance(entry, str):
            entry = [entry]

        name = str(entry[0]).strip()
        sort_val = DEFAULT_PAGE_SORT
        sort_val = get_valid_sort_value(sort_val)
        start_page = DEFAULT_PAGE_RANGE_START
        end_page = DEFAULT_PAGE_RANGE_END

        if len(entry) > 1 and str(entry[1]).lower() in valid_sorts:
            sort_val = str(entry[1]).lower()
            if len(entry) > 2:
                start_page = int(entry[2])
            if len(entry) > 3:
                end_page = int(entry[3])
        else:
            if len(entry) > 1:
                start_page = int(entry[1])
            if len(entry) > 2:
                end_page = int(entry[2])

        result = executor.run_blocking(fetch_gallery_ids, query_lower, name, sort_val, start_page, end_page)
        if result:
            gallery_ids.update(result)

    return gallery_ids

def build_gallery_list(args):
    
    gallery_ids = set()

    # ------------------------------------------------------------
    # File input (overrides .env galleries)
    # ------------------------------------------------------------
    if args.file:
        gallery_ids.update(_handle_gallery_args(args.file, "file"))

    # ------------------------------------------------------------
    # Range
    # ------------------------------------------------------------
    if args.range:
        start, end = args.range
        gallery_ids.update(range(start, end + 1))

    # ------------------------------------------------------------
    # Explicit galleries
    # ------------------------------------------------------------
    if args.galleries:
        ids = [int(x.strip()) for x in args.galleries.split(",") if x.strip().isdigit()]
        gallery_ids.update(ids)
    
    # ------------------------------------------------------------
    # Artist / Group / Tag / Character / Parody / Search
    # ------------------------------------------------------------
    if args.homepage:
        gallery_ids.update(_handle_gallery_args(args.homepage, "homepage"))
    
    if args.artist:
        gallery_ids.update(_handle_gallery_args(args.artist, "artist"))

    if args.group:
        gallery_ids.update(_handle_gallery_args(args.group, "group"))

    if args.tag:
        gallery_ids.update(_handle_gallery_args(args.tag, "tag"))
        
    if args.character:
        gallery_ids.update(_handle_gallery_args(args.character, "character"))

    if args.parody:
        gallery_ids.update(_handle_gallery_args(args.parody, "parody"))
    
    if args.search:
        gallery_ids.update(_handle_gallery_args(args.search, "search"))

    # ------------------------------------------------------------
    # Final sorted list (Processes highest gallery ID (latest gallery) first.)
    # ------------------------------------------------------------
    gallery_list = list( # Convert to list
        reversed( # Highest ID first
            sorted( # Sort list so it can be reversed.
                map( # Make sure Gallery IDs processed as integers
                    int, gallery_ids
                    )
                )
            )
        )
    
    #log_clarification("debug")
    #log(f"Gallery List: {gallery_list}", "debug")
    
    return gallery_list

def update_config(args):
    log_clarification("debug")
    log("Updating Config...", "debug")
    
    if args.extension is not None:
        update_env("EXTENSION", args.extension)
        
    if getattr(args, "mirrors", None):
        update_env("NHENTAI_MIRRORS", args.mirrors)
    
    if args.excluded_tags is not None: # Use new excluded tags.
        update_env("EXCLUDED_TAGS", [t.strip().lower() for t in args.excluded_tags.split(",")])
    else:
        # Use whatever excluded tags were already in config (env or default)
        if isinstance(excluded_tags, str):
            update_env("EXCLUDED_TAGS", [t.strip().lower() for t in excluded_tags.split(",")])
    
    update_env("LANGUAGE", [lang.strip().lower() for lang in args.language.split(",")])
    update_env("TITLE_TYPE", args.title_type)
    update_env("THREADS_GALLERIES", args.threads_galleries)
    update_env("THREADS_IMAGES", args.threads_images)
    update_env("MAX_RETRIES", args.max_retries)
    update_env("MIN_SLEEP", args.min_sleep)
    update_env("MAX_SLEEP", args.max_sleep)
    update_env("DRY_RUN", args.dry_run)
    update_env("USE_TOR", args.use_tor)
    update_env("SKIP_POST_RUN", args.skip_post_run)
    update_env("CALM", args.calm)
    update_env("DEBUG", args.debug)
    
    fetch_env_vars() # Refresh env vars in case config changed.

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    """
    This is one of this module's entrypoints.
    """
    
    args = parse_args()

    # Overwrite placeholder logger with real one
    logger = setup_logger(calm=args.calm, debug=args.debug)
    
    normalise_config() # Populate config immediately.
    
    log_clarification()
    log("====================================================")
    log("                  nhentai-scraper                   ")
    log("====================================================")
    
    # ------------------------------------------------------------
    # Handle Installer / Updater
    # ------------------------------------------------------------
    if args.install:
        run_installer("--install")
    elif args.update:
        run_installer("--update")
    elif args.update_env:
        run_installer("--update-env")
    elif args.uninstall:
        run_installer("--uninstall")
        
    # ------------------------------------------------------------
    # Handle extension installation / uninstallation
    # ------------------------------------------------------------
    if args.install_extension:
        install_selected_extension(args.install_extension)
        return
    
    if args.uninstall_extension:
        uninstall_selected_extension(args.uninstall_extension)
        return
    
    logger.debug("CLI: Ready.")
    log("CLI: Debugging Started.", "debug")
    
    # If no gallery input is provided, default to homepage 1 1
    gallery_args = [args.file, args.homepage, args.range, args.galleries, args.artist,
                    args.group, args.tag, args.character, args.parody, args.search]
    if not any(gallery_args):
        args.homepage = [DEFAULT_PAGE_RANGE_START, DEFAULT_PAGE_RANGE_END] # Use defaults.
    
    # Update Config With CLI Args
    # Allows session to use correct config values on creation
    update_config(args)
    
    # Build initial session.
    session = executor.run_blocking(get_session, status="build")
    
    # Build Gallery List (make sure not empty.)
    log_clarification()
    log(f"Parsing galleries from NHentai. This may take a while...")
    gallery_list = build_gallery_list(args)
    if not gallery_list:
        logger.warning("No galleries provided. Exiting.")
        sys.exit(0)  # Or just return
    
    # Update Config with Built Gallery List
    init_scraper(gallery_list)
    
    log_clarification("debug")
    log(f"Final Config:\n{config}", "debug")
    
    # ------------------------------------------------------------
    # Download galleries
    # ------------------------------------------------------------
    executor.run_blocking(start_downloader, gallery_list) # Start download

if __name__ == "__main__":
    main()