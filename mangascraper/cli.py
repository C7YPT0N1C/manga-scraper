#!/usr/bin/env python3
# mangascraper/cli.py

import os, time, sys, argparse, re, subprocess, urllib.parse

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.core.downloader import start_downloader
from mangascraper.core.api import get_session, fetch_gallery_ids
from mangascraper.extensions.extension_manager import install_selected_extension, uninstall_selected_extension

INSTALLER_PATH = "/opt/manga-scraper/mangascraper-install.sh"

EPILOG = """Examples:
    manga-scraper --homepage 1 3
    manga-scraper --latest 1 5
    manga-scraper --artist "some artist" popular 1 2
    manga-scraper --search "\"big breasts\" -yaoi" popular
    manga-scraper --output-folder /mnt/storage --ids "123456,654321" --output-format cbz
"""


class _HelpFormatter(argparse.RawTextHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
    pass

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
    parser = argparse.ArgumentParser(
        description="Manga scraper CLI",
        formatter_class=_HelpFormatter,
        epilog=EPILOG,
    )

    installer_group = parser.add_argument_group("Installer / updater")
    extension_group = parser.add_argument_group("Extensions")
    source_group = parser.add_argument_group("Gallery selection")
    filters_group = parser.add_argument_group("Filters")
    output_group = parser.add_argument_group("Output")
    perf_group = parser.add_argument_group("Performance")
    runtime_group = parser.add_argument_group("Runtime")
    logging_group = parser.add_argument_group("Logging")

    # Installer / Updater flags
    installer_group.add_argument("--install", action="store_true", help="Install manga-scraper and dependencies")
    installer_group.add_argument("--update", action="store_true", help="Update manga-scraper")
    installer_group.add_argument("--update-env", action="store_true", help="Update the .env file")
    installer_group.add_argument("--uninstall", "--remove", action="store_true", help="Uninstall manga-scraper")

    # Extension selection / management
    extension_group.add_argument("--install-extension", type=str, help="Install an extension by name")
    extension_group.add_argument("--uninstall-extension", type=str, help="Uninstall an extension by name")
    extension_group.add_argument(
        "--ext",
        "--extension",
        dest="extension",
        type=str,
        default=DEFAULT_EXTENSION,
        help="Extension to use",
    )
    
    # NHentai mirror URLs
    source_group.add_argument(
        "--mirror-urls",
        "--mirrors",
        dest="mirrors",
        type=str,
        default=DEFAULT_NHENTAI_MIRRORS,
        help="Comma-separated list of NHentai mirror URLs",
    )
    
    # Gallery selection
    source_group.add_argument(
        "--input",
        "--file",
        dest="file",
        type=str,
        nargs="?",                  # Makes the argument optional
        const=DEFAULT_DOUJIN_TXT_PATH,  # Use default if --file is passed without a value
        help="Path to a file containing gallery URLs or IDs (one per line)",
    )
    
    source_group.add_argument(
        "--id-range",
        "--range",
        dest="range",
        nargs=2,
        type=int,
        metavar=("START", "END"),
        help="Gallery ID range to download",
    )
    source_group.add_argument(
        "--ids",
        "--galleries",
        dest="galleries",
        type=str,
        help="Comma-separated gallery IDs to download",
    )
    
    source_group.add_argument(
        "--homepage",
        nargs="+",  # All args after this flag are collected
        metavar="ARGS",
        help=(
            "Homepage selection: [SORT] [START] [END]. "
            "SORT: date|recent|popular_today|popular_week|popular|all_time."
        )
    )

    source_group.add_argument(
        "--latest",
        nargs="*",
        metavar=("START", "END"),
        default=None,
        help="Homepage latest (recent). Optional START END or END only.",
    )
    source_group.add_argument(
        "--popular",
        nargs="*",
        metavar=("START", "END"),
        default=None,
        help="Homepage popular (all time). Optional START END or END only.",
    )
    source_group.add_argument(
        "--popular-today",
        nargs="*",
        metavar=("START", "END"),
        default=None,
        help="Homepage popular today. Optional START END or END only.",
    )
    source_group.add_argument(
        "--popular-week",
        nargs="*",
        metavar=("START", "END"),
        default=None,
        help="Homepage popular this week. Optional START END or END only.",
    )

    # Allow multiple --artist, --group, etc. each with their own arguments
    source_group.add_argument(
        "--artist",
        action="append",
        nargs="+",  # All args after this flag are collected
        metavar="ARGS",
        help="Download by artist. Usage: --artist NAME [SORT] [START] [END] [ARCHIVE]. Repeatable.",
    )
    source_group.add_argument(
        "--group",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by group. Usage: --group NAME [SORT] [START] [END] [ARCHIVE]. Repeatable.",
    )
    source_group.add_argument(
        "--tag",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by tag. Usage: --tag NAME [SORT] [START] [END] [ARCHIVE]. Repeatable.",
    )
    source_group.add_argument(
        "--character",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by character. Usage: --character NAME [SORT] [START] [END] [ARCHIVE]. Repeatable.",
    )
    source_group.add_argument(
        "--parody",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by parody. Usage: --parody NAME [SORT] [START] [END] [ARCHIVE]. Repeatable.",
    )
    source_group.add_argument(
        "--search",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by search query. Usage: --search QUERY [SORT] [START] [END] [ARCHIVE]. Repeatable.",
    )
    
    # NHentai Archival
    source_group.add_argument(
        "--archive",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Like --search, but downloads every gallery in the results.",
    )
    source_group.add_argument(
        "--archive-all",
        action="store_true",
        help="Archive everything from NHentai (all homepage pages).",
    )

    # Filters
    filters_group.add_argument(
        "--excluded-tags",
        type=str,
        default=None,
        help="Comma-separated list of tags to exclude galleries",
    )
    filters_group.add_argument(
        "--language",
        type=str,
        default=DEFAULT_LANGUAGE,
        help="Comma-separated list of languages to include",
    )
    filters_group.add_argument(
        "--title-type",
        choices=["english", "japanese", "pretty"],
        default=DEFAULT_TITLE_TYPE,
        help="Title type to use",
    )
    
    # Output format
    output_group.add_argument(
        "--output-folder",
        dest="output_folder",
        type=str,
        default=None,
        help="Override the download folder for this run",
    )
    output_group.add_argument(
        "--output-format",
        "--format",
        dest="format",
        type=str,
        default=DEFAULT_GALLERY_FORMAT,
        choices=["directory", "zip", "cbz"],
        help="Output format for downloaded galleries",
    )

    # Threads / concurrency
    perf_group.add_argument(
        "--threads-galleries",
        type=int,
        default=DEFAULT_THREADS_GALLERIES,
        help="Number of concurrent gallery downloads",
    )
    
    perf_group.add_argument(
        "--threads-images",
        type=int,
        default=DEFAULT_THREADS_IMAGES,
        help="Number of concurrent image downloads per gallery",
    )
    
    perf_group.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Maximum retry attempts for failed downloads",
    )
    perf_group.add_argument(
        "--min-sleep",
        type=int,
        default=DEFAULT_MIN_RETRY_SLEEP,
        help="Minimum sleep before starting a new download",
    )
    perf_group.add_argument(
        "--max-sleep",
        type=int,
        default=DEFAULT_MAX_RETRY_SLEEP,
        help="Maximum sleep before starting a new download",
    )
    
    # Download / runtime options
    runtime_group.add_argument(
        "--use-tor",
        action="store_true",
        default=DEFAULT_USE_TOR,
        help="Use TOR network for downloads",
    )
    runtime_group.add_argument(
        "--skip-post-batch",
        action="store_true",
        default=DEFAULT_SKIP_POST_BATCH,
        help="Skip periodic post-batch actions",
    )
    runtime_group.add_argument(
        "--skip-post-run",
        action="store_true",
        default=DEFAULT_SKIP_POST_RUN,
        help="Skip post-run actions",
    )
    runtime_group.add_argument(
        "--dry-run",
        action="store_true",
        default=DEFAULT_DRY_RUN,
        help="Simulate downloads without saving files",
    )
    
    # Make calm/debug mutually exclusive
    log_group = logging_group.add_mutually_exclusive_group()
    log_group.add_argument("--calm", action="store_true", default=DEFAULT_CALM, help="Enable calm logging")
    log_group.add_argument("--debug", action="store_true", default=DEFAULT_DEBUG, help="Enable debug logging")

    return parser.parse_args()


_DEPRECATED_FLAGS = {
    "--file": "--input",
    "--range": "--id-range",
    "--galleries": "--ids",
    "--format": "--output-format",
    "--mirrors": "--mirror-urls",
    "--extension": "--ext",
}


def _warn_deprecated_flags(argv: list[str]):
    for old_flag, new_flag in _DEPRECATED_FLAGS.items():
        if old_flag in argv:
            print(f"[WARN] {old_flag} is deprecated; use {new_flag}.", file=sys.stderr)


def _parse_positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be an integer.")
    if parsed <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return parsed


def _parse_optional_page_range(values: list[str] | None, label: str) -> list[int] | None:
    if values is None:
        return None
    if len(values) == 0:
        start_page = DEFAULT_PAGE_RANGE_START
        end_page = DEFAULT_PAGE_RANGE_END
    elif len(values) == 1:
        start_page = DEFAULT_PAGE_RANGE_START
        end_page = _parse_positive_int(values[0], f"{label} END")
    elif len(values) == 2:
        start_page = _parse_positive_int(values[0], f"{label} START")
        end_page = _parse_positive_int(values[1], f"{label} END")
    else:
        raise ValueError(f"{label} accepts at most 2 values (START END).")

    if start_page > end_page:
        raise ValueError(f"{label} START must be <= END.")
    return [start_page, end_page]


def _apply_homepage_shortcuts(args):
    shortcuts = {
        "--latest": ("recent", args.latest),
        "--popular": ("popular", args.popular),
        "--popular-today": ("popular_today", args.popular_today),
        "--popular-week": ("popular_week", args.popular_week),
    }

    used = [(flag, sort, values) for flag, (sort, values) in shortcuts.items() if values is not None]
    if args.homepage and used:
        raise ValueError("Use only one homepage selector: --homepage or a shortcut flag.")
    if len(used) > 1:
        flags = ", ".join(flag for flag, _, _ in used)
        raise ValueError(f"Use only one homepage shortcut flag. Provided: {flags}.")

    if used:
        flag, sort, values = used[0]
        page_range = _parse_optional_page_range(values, flag)
        args.homepage = [sort] + page_range


def _validate_args(args):
    if args.range:
        start, end = args.range
        if start <= 0 or end <= 0:
            raise ValueError("--id-range values must be greater than zero.")
        if start > end:
            raise ValueError("--id-range START must be <= END.")

    if args.threads_galleries <= 0:
        raise ValueError("--threads-galleries must be greater than zero.")
    if args.threads_images <= 0:
        raise ValueError("--threads-images must be greater than zero.")
    if args.max_retries < 0:
        raise ValueError("--max-retries must be zero or greater.")
    if args.min_sleep < 0 or args.max_sleep < 0:
        raise ValueError("--min-sleep and --max-sleep must be zero or greater.")
    if args.min_sleep > args.max_sleep:
        raise ValueError("--min-sleep must be <= --max-sleep.")
    if args.output_folder is not None and not str(args.output_folder).strip():
        raise ValueError("--output-folder must be a non-empty path.")


def _parse_galleries_arg(galleries_value: str) -> list[int]:
    ids = []
    invalid = []
    for part in galleries_value.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            ids.append(int(part))
        else:
            invalid.append(part)

    if invalid:
        print(f"[WARN] Ignoring invalid gallery IDs: {', '.join(invalid)}", file=sys.stderr)
    return ids

def _handle_gallery_args(arg_list: list | None, query_type: str) -> set[int]:
    """
    Parse CLI args or file URLs and call fetch_gallery_ids for any query type.
    Supports optional sort type in flags: --artist ARTIST [SORT_TYPE] [START_PAGE] [END_PAGE] [ARCHIVAL_BOOL]
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
    force_archive = (query_lower == "archive")
    
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
                    sort_val = DEFAULT_PAGE_SORT
                    sort_val = get_valid_sort_value(sort_val)
                    start_page = DEFAULT_PAGE_RANGE_START
                    end_page = int(m_homepage.group(1))
                    gallery_ids.update(fetch_gallery_ids("homepage", None, sort_val, start_page, end_page, file_used=True))
                    continue

                # Creator / group / tag / character / parody / search URLs
                m_query = re.search(
                    r"nhentai\.net/(artist|group|tag|character|parody)/([\w%-]+)(?:/(popular-today|popular-week|popular))?(?:/\?page=(\d+)|\?page=(\d+))?",
                    line.strip()
                )
                m_search = re.search(r"nhentai\.net/search/\?q=([^&]+)(?:&page=(\d+))?", line)
                
                if m_query:
                    qtype, qvalue, sort_path, page1, page2 = m_query.groups()
                    page_q = page1 or page2
                    qvalue = urllib.parse.unquote(qvalue)
                    sort_val = get_valid_sort_value(sort_path if sort_path else DEFAULT_PAGE_SORT)
                    start_page = 1
                    end_page = int(page_q) if page_q else DEFAULT_PAGE_RANGE_END
                    gallery_ids.update(fetch_gallery_ids(qtype, qvalue, sort_val, start_page, end_page, file_used=True))
                    continue

                elif m_search:
                    search_query, page_q = m_search.groups()
                    search_query = urllib.parse.unquote(search_query)
                    sort_val = get_valid_sort_value(DEFAULT_PAGE_SORT)
                    start_page = 1
                    end_page = int(page_q) if page_q else DEFAULT_PAGE_RANGE_END
                    gallery_ids.update(fetch_gallery_ids("search", search_query, sort_val, start_page, end_page, file_used=True))
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

        gallery_ids.update(fetch_gallery_ids("homepage", None, sort_val, start_page, end_page))
        return gallery_ids

    # --- Other queries (CLI flags) ---
    for entry in arg_list:
        # Normalise to list
        if isinstance(entry, str):
            # Allow comma-separated or space-separated inputs
            parts = [p.strip() for p in re.split(r'[,\s]+', entry) if p.strip()]
            entry = parts

        name = str(entry[0]).strip()
        sort_val = DEFAULT_PAGE_SORT
        sort_val = get_valid_sort_value(sort_val)
        start_page = DEFAULT_PAGE_RANGE_START
        end_page = DEFAULT_PAGE_RANGE_END
        
        archive_mode = DEFAULT_ARCHIVING
        if str(entry[-1]).lower() in ("true", "archive"):
            archive_mode = True
            entry = entry[:-1] # Remove the flag before parsing numbers
        
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

        archival_flag = archive_mode or force_archive
        gallery_ids.update(fetch_gallery_ids(query_lower, name, sort_val, start_page, end_page, fetch_as_archival=archival_flag))

    return gallery_ids

def build_gallery_list(args):
    
    log_clarification()
    log(f"Parsing galleries from NHentai. This may take a while...")
    
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
        ids = _parse_galleries_arg(args.galleries)
        if not ids:
            logger.warning("No valid gallery IDs provided in --ids.")
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
    # Archive Queries
    # ------------------------------------------------------------
    if args.archive:
        # Same as search crawl but infinite
        gallery_ids.update(_handle_gallery_args(args.archive, "archive"))
    
    if args.archive_all:
        # Same as homepage crawl but infinite
        gallery_ids.update(fetch_gallery_ids("homepage", None, DEFAULT_PAGE_SORT, start_page=1, end_page=None, fetch_as_archival=True))

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

def update_config(args, archive_all: bool = False):
    log_clarification("debug")
    log("Updating Config...", "debug")
    
    if args.extension is not None:
        update_env("EXTENSION", args.extension)
        
    if getattr(args, "mirrors", None):
        update_env("NHENTAI_MIRRORS", args.mirrors)

    if args.output_folder:
        update_env("DOWNLOAD_PATH", args.output_folder)
        update_env("EXTENSION_DOWNLOAD_PATH", args.output_folder)
    
    if args.excluded_tags is not None: # Use new excluded tags.
        update_env("EXCLUDED_TAGS", [t.strip().lower() for t in args.excluded_tags.split(",")])
    else:
        # Use whatever excluded tags were already in config (env or default)
        if isinstance(excluded_tags, str):
            update_env("EXCLUDED_TAGS", [t.strip().lower() for t in orchestrator.excluded_tags.split(",")])
    
    update_env("LANGUAGE", [lang.strip().lower() for lang in args.language.split(",")])
    update_env("TITLE_TYPE", args.title_type)
    update_env("GALLERY_FORMAT", args.format)
    update_env("THREADS_GALLERIES", args.threads_galleries)
    update_env("THREADS_IMAGES", args.threads_images)
    update_env("MAX_RETRIES", args.max_retries)
    update_env("DRY_RUN", args.dry_run)
    update_env("USE_TOR", args.use_tor)
    update_env("SKIP_POST_BATCH", args.skip_post_batch)
    update_env("SKIP_POST_RUN", args.skip_post_run)
    update_env("CALM", args.calm)
    update_env("DEBUG", args.debug)
    
    orchestrator.refresh_globals()
    
    log_clarification("debug") # NOTE: DEBUGGING
    log(f"GALLERY THREADS = {orchestrator.threads_galleries}", "debug")
    log(f"IMAGE THREADS = {orchestrator.threads_images}", "debug")

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    """
    This is one this module's entrypoints.
    """
    
    args = parse_args()
    _warn_deprecated_flags(sys.argv[1:])
    try:
        _apply_homepage_shortcuts(args)
        _validate_args(args)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)

    # Overwrite placeholder logger with real one
    logger = setup_logger(calm=args.calm, debug=args.debug)
    
    normalise_config() # Populate config immediately.
    
    log_clarification()
    log("====================================================")
    log("                  manga-scraper                   ")
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
    
    # --- Handle --archive-all conflicts by overriding other gallery-selection flags ---
    if args.archive_all:
        conflict_flags = {
            "--file": "file",
            "--range": "range",
            "--galleries": "galleries",
            "--homepage": "homepage",
            "--latest": "latest",
            "--popular": "popular",
            "--popular-today": "popular_today",
            "--popular-week": "popular_week",
            "--artist": "artist",
            "--group": "group",
            "--tag": "tag",
            "--character": "character",
            "--parody": "parody",
            "--search": "search",
            "--archive": "archive",
        }

        # Detect and clear conflicting flags
        used_conflicts = [flag for flag, attr in conflict_flags.items() if getattr(args, attr)]
        if used_conflicts:
            print(f"[INFO] --archive-all detected. Ignoring conflicting gallery-selection flags:")
            print(f"       {', '.join(used_conflicts)}")
            for attr in conflict_flags.values():
                setattr(args, attr, None)
    else:
        # If no gallery input is provided, default to homepage
        gallery_args = [
            args.file,
            args.homepage,
            args.latest,
            args.popular,
            args.popular_today,
            args.popular_week,
            args.range,
            args.galleries,
            args.artist,
            args.group,
            args.tag,
            args.character,
            args.parody,
            args.search,
            args.archive,
            args.archive_all,
        ]
        if not any(gallery_args):
            args.homepage = [DEFAULT_PAGE_RANGE_START, DEFAULT_PAGE_RANGE_END] # Use defaults.
        
    # Update Config With CLI Args
    # Allows session to use correct config values on creation
    update_config(args)
    
    # Build initial session.
    get_session(referrer="CLI", status="build")
    
    # Build Gallery List (make sure not empty.)
    gallery_list = build_gallery_list(args)
    if not gallery_list:
        logger.warning("No galleries provided. Exiting.")
        sys.exit(0)  # Or just return
    
    # Update Config with Built Gallery List
    update_env("GALLERIES", gallery_list)
    
    log_clarification("debug")
    log(f"Final Config:\n{config}", "debug")
    
    # ------------------------------------------------------------
    # Download galleries
    # ------------------------------------------------------------
    start_downloader(gallery_list) # Start download

if __name__ == "__main__":
    main()