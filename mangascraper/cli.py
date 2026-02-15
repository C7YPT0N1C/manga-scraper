#!/usr/bin/env python3
# mangascraper/cli.py

import os, time, sys, argparse, re, subprocess, urllib.parse

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import *
from mangascraper.core.downloader import start_downloader
from mangascraper.core.api import get_session, fetch_gallery_ids, fetch_all_metadata_for_galleries
from mangascraper.core.cache import (
    get_cache_key,
    load_cache,
    ensure_cache_files_exist,
)
from mangascraper.core import database
from mangascraper.extensions.extension_manager import (
    ensure_extension_cli,
    get_selected_extension,
    uninstall_selected_extension,
)

INSTALLER_PATH = "/opt/manga-scraper/mangascraper-install.sh"

EPILOG = """Examples:
    manga-scraper --file archive=true
    manga-scraper --homepage 1 3
    manga-scraper --homepage recent 1 5
    manga-scraper --artist "some artist" popular 1 2
    manga-scraper --artist "some artist" archive=true
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
    
    # Mutually exclusive groups
    interactive_mode_group = parser.add_mutually_exclusive_group()
    summary_mode_group = runtime_group.add_mutually_exclusive_group()

    # Installer / Updater flags
    installer_group.add_argument("--install", action="store_true", help="Install manga-scraper and dependencies")
    installer_group.add_argument("--update", action="store_true", help="Update manga-scraper")
    installer_group.add_argument("--update-env", action="store_true", help="Update the .env file")
    installer_group.add_argument("--uninstall", "--remove", action="store_true", help="Uninstall manga-scraper")

    # Extension selection / management
    extension_group.add_argument("--install-extension", type=str, help="Install an extension by name")
    extension_group.add_argument("--uninstall-extension", type=str, help="Uninstall an extension by name")
    extension_group.add_argument(
        "--extension",
        dest="extension",
        type=str,
        default=None,
        help="Extension to use",
    )
    
    # NHentai mirror URLs
    source_group.add_argument(
        "--mirrors",
        dest="mirrors",
        type=str,
        default=None,
        help="Comma-separated list of NHentai mirror URLs",
    )
    source_group.add_argument(
        "--disable-ssl-verify",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable SSL certificate verification for downloads (use only if mirrors have expired certs)",
    )
    
    # Gallery selection
    interactive_mode_group.add_argument(
        "--interactive",
        action="store_true",
        default=False,
        help="Enter interactive mode to search/browse galleries and configure settings (mutually exclusive with --unattended)",
    )
    
    source_group.add_argument(
        "--file",
        dest="file",
        type=str,
        nargs="*",                  # Makes the argument optional
        const=[DEFAULT_DOUJIN_TXT_PATH],  # Use default if --file is passed without a value
        help="Usage: --file [PATH] [ARCHIVE]. PATH defaults to the .env list. ARCHIVE is archive=true or archive=false.",
    )
    
    source_group.add_argument(
        "--id-range",
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

    # Allow multiple --artist, --group, etc. each with their own arguments
    source_group.add_argument(
        "--artist",
        action="append",
        nargs="+",  # All args after this flag are collected
        metavar="ARGS",
        help="Download by artist. Usage: --artist NAME [SORT] [START] [END] [ARCHIVE]. ARCHIVE is archive=true or archive=false. Repeatable.",
    )
    source_group.add_argument(
        "--group",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by group. Usage: --group NAME [SORT] [START] [END] [ARCHIVE]. ARCHIVE is archive=true or archive=false. Repeatable.",
    )
    source_group.add_argument(
        "--tag",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by tag. Usage: --tag NAME [SORT] [START] [END] [ARCHIVE]. ARCHIVE is archive=true or archive=false. Repeatable.",
    )
    source_group.add_argument(
        "--character",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by character. Usage: --character NAME [SORT] [START] [END] [ARCHIVE]. ARCHIVE is archive=true or archive=false. Repeatable.",
    )
    source_group.add_argument(
        "--parody",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by parody. Usage: --parody NAME [SORT] [START] [END] [ARCHIVE]. ARCHIVE is archive=true or archive=false. Repeatable.",
    )
    source_group.add_argument(
        "--search",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Download by search query. Usage: --search QUERY [SORT] [START] [END] [ARCHIVE]. ARCHIVE is archive=true or archive=false. Repeatable.",
    )
    
    # NHentai Archival
    source_group.add_argument(
        "--archive",
        action="append",
        nargs="+",
        metavar="ARGS",
        help="Archive results. Use: --archive QUERY [SORT] [START] [END] or --archive all.",
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
        default=argparse.SUPPRESS,
        help="Comma-separated list of languages to include",
    )
    filters_group.add_argument(
        "--title-type",
        choices=["english", "japanese", "pretty"],
        default=argparse.SUPPRESS,
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
        dest="format",
        type=str,
        default=argparse.SUPPRESS,
        choices=["directory", "zip", "cbz"],
        help="Output format for downloaded galleries",
    )

    # Threads / concurrency
    perf_group.add_argument(
        "--threads-galleries",
        type=int,
        default=argparse.SUPPRESS,
        help="Number of concurrent gallery downloads",
    )
    
    perf_group.add_argument(
        "--threads-images",
        type=int,
        default=argparse.SUPPRESS,
        help="Number of concurrent image downloads per gallery",
    )
    
    perf_group.add_argument(
        "--max-retries",
        type=int,
        default=argparse.SUPPRESS,
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
    
    summary_mode_group.add_argument(
        "--show-summary",
        action="store_true",
        default=False,
        help="Fetch metadata and show gallery summary with size estimate before downloading",
    )
    
    # Download / runtime options
    runtime_group.add_argument(
        "--use-tor",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Use TOR network for downloads",
    )
    runtime_group.add_argument(
        "--skip-post-batch",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Skip periodic post-batch actions",
    )
    runtime_group.add_argument(
        "--skip-post-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Skip post-run actions",
    )
    runtime_group.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Simulate downloads without saving files",
    )
    summary_mode_group.add_argument(
        "--unattended",
        action="store_true",
        default=False,
        help="Skip all confirmation prompts and warnings (use with caution, mutually exclusive with --interactive)",
    )
    
    # Make calm/debug mutually exclusive
    log_group = logging_group.add_mutually_exclusive_group()
    log_group.add_argument("--calm", action="store_true", default=argparse.SUPPRESS, help="Enable calm logging")
    log_group.add_argument("--debug", action="store_true", default=argparse.SUPPRESS, help="Enable debug logging")

    return parser.parse_args()


_DEPRECATED_FLAGS = {}


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


def _parse_archive_flag(value: str) -> bool | None:
    """
    Parse archive flags like: archive=true or archive=false.
    Returns True/False if matched, otherwise None.
    """
    lowered = str(value).strip().lower()
    if lowered.startswith("archive="):
        flag_val = lowered.split("=", 1)[1]
        if flag_val in ("true", "yes", "1"):
            return True
        if flag_val in ("false", "no", "0"):
            return False
    return None


def _validate_args(args):
    if args.interactive and args.unattended:
        raise ValueError("--interactive and --unattended cannot be used together.")
    if args.range:
        start, end = args.range
        if start <= 0 or end <= 0:
            raise ValueError("--id-range values must be greater than zero.")
        if start > end:
            raise ValueError("--id-range START must be <= END.")

    # Only validate attributes that were explicitly provided (exist on args)
    if hasattr(args, 'threads_galleries') and args.threads_galleries <= 0:
        raise ValueError("--threads-galleries must be greater than zero.")
    if hasattr(args, 'threads_images') and args.threads_images <= 0:
        raise ValueError("--threads-images must be greater than zero.")
    if hasattr(args, 'max_retries') and args.max_retries < 0:
        raise ValueError("--max-retries must be zero or greater.")
    if args.min_sleep < 0 or args.max_sleep < 0:
        raise ValueError("--min-sleep and --max-sleep must be zero or greater.")
    if args.min_sleep > args.max_sleep:
        raise ValueError("--min-sleep must be <= --max-sleep.")
    if args.output_folder is not None and not str(args.output_folder).strip():
        raise ValueError("--output-folder must be a non-empty path.")
    if args.galleries:
        ids = _parse_galleries_arg(args.galleries, warn_invalid=False)
        if len(ids) > 25:
            raise ValueError("--ids supports at most 25 IDs. Use --file for larger lists.")


def _parse_galleries_arg(galleries_value: str, warn_invalid: bool = True) -> list[int]:
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

    if invalid and warn_invalid:
        print(f"[WARN] Ignoring invalid gallery IDs: {', '.join(invalid)}", file=sys.stderr)
    return ids

####################################################################################################
# CLI Helper Functions
####################################################################################################

def fetch_gallery_ids_with_fallback(query_type: str, query_value: str, sort_val: str, start_page: int, end_page: int = None, fetch_as_archival: bool = False) -> list[int]:
    """
    Fetch gallery IDs with error handling and fallback to cached results.
    """
    cache_key = get_cache_key(query_type, query_value) if query_value else None
    max_retries = 2
    attempt = 0
    
    while attempt < max_retries:
        try:
            ids = fetch_gallery_ids(
                query_type,
                query_value,
                sort_val,
                start_page,
                end_page,
                fetch_as_archival=fetch_as_archival,
            )
            return ids or []
        except Exception as e:
            attempt += 1
            logger.error(f"Error fetching galleries (attempt {attempt}/{max_retries}): {e}")
            
            if attempt < max_retries:
                logger.info("Retrying...")
                time.sleep(2)
                continue
            
            # Try to fallback to cached results
            if cache_key:
                try:
                    logger.info("Attempting to use cached results...")
                    cached_metadata = load_cache(cache_key)
                    if cached_metadata:
                        cached_ids = list(cached_metadata.keys())
                        logger.warning(f"Using {len(cached_ids)} galleries from cache")
                        return cached_ids
                except:
                    pass
            
            logger.warning(f"Failed to fetch galleries for {query_type}={query_value}. Skipping.")
            return []

def estimate_download_size(metadata: dict) -> tuple[int, str]:
    """
    Estimate total download size in bytes from metadata.
    Returns: (total_bytes, human_readable_string)
    """
    if not metadata:
        return 0, "0 B"
    
    total_bytes = 0
    avg_bytes_per_page = 150000  # ~150KB per page average estimate
    
    for gid, meta in metadata.items():
        pages = meta.get("pages", 0)
        total_bytes += pages * avg_bytes_per_page
    
    # Convert to human readable
    for unit in ["B", "KB", "MB", "GB"]:
        if total_bytes < 1024:
            return total_bytes, f"{total_bytes:.0f} {unit}"
        total_bytes /= 1024
    
    return total_bytes, f"{total_bytes:.2f} TB"

def display_download_summary(gallery_ids: list, show_summary: bool = False, cache_key: str | None = None) -> bool:
    """
    Display gallery summary with size estimate and get confirmation.
    Returns True if user wants to proceed, False otherwise.
    """
    if not show_summary:
        return True
    
    if not gallery_ids:
        logger.warning("No galleries to display summary for.")
        return False
    
    log_clarification()
    logger.info(f"Fetching metadata for {len(gallery_ids)} galleries (this may take a moment)...")
    
    # Fetch metadata
    metadata = fetch_all_metadata_for_galleries(gallery_ids, cache_key=cache_key)
    
    if not metadata:
        logger.warning("Could not fetch metadata. Proceed without summary? (y/n): ", end="")
        return input().strip().lower() == "y"
    
    # Calculate summary statistics
    all_artists = set()
    all_groups = set()
    all_tags = set()
    all_languages = set()
    pages_list = []
    
    for gid, meta in metadata.items():
        all_artists.update(meta.get("artists", []))
        all_groups.update(meta.get("groups", []))
        all_tags.update(meta.get("tags", []))
        all_languages.update(meta.get("languages", []))
        pages_list.append(meta.get("pages", 0))
    
    total_size_bytes, size_str = estimate_download_size(metadata)
    min_pages = min(pages_list) if pages_list else 0
    max_pages = max(pages_list) if pages_list else 0
    avg_pages = sum(pages_list) / len(pages_list) if pages_list else 0
    
    # Display summary
    log_clarification()
    print(
        f"Gallery Summary:\n"
        f"  Total galleries: {len(metadata)}\n"
        f"  Unique artists: {len(all_artists)}\n"
        f"  Unique groups: {len(all_groups)}\n"
        f"  Unique tags: {len(all_tags)}\n"
        f"  Languages: {len(all_languages)}\n"
        f"  Pages: {min_pages}-{max_pages} (avg: {avg_pages:.0f})\n"
        f"  Estimated size: {size_str}\n"
    )
    
    # Get confirmation
    log_clarification()
    response = input("Proceed with download? (y/n): ").strip().lower()
    return response == "y"

def _handle_gallery_args(arg_list: list | None, query_type: str) -> set[int]:
    """
    Parse CLI args or file URLs and call fetch_gallery_ids for any query type.
    Supports optional sort type in flags: --artist ARTIST [SORT_TYPE] [START_PAGE] [END_PAGE] [ARCHIVE]
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
        archive_mode = DEFAULT_ARCHIVING
        file_path = None

        if isinstance(arg_list, list):
            for token in arg_list:
                if token is None:
                    continue
                parsed_flag = _parse_archive_flag(token)
                if parsed_flag is None:
                    if file_path is None:
                        file_path = str(token)
                    else:
                        logger.warning(f"Ignoring extra --file argument: {token}")
                else:
                    archive_mode = parsed_flag
        else:
            parsed_flag = _parse_archive_flag(arg_list)
            if parsed_flag is None:
                file_path = str(arg_list)
            else:
                archive_mode = parsed_flag

        if not file_path:
            file_path = DEFAULT_DOUJIN_TXT_PATH

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
                    gallery_ids.update(fetch_gallery_ids_with_fallback("homepage", "", sort_val, start_page, end_page, fetch_as_archival=archive_mode))
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
                    gallery_ids.update(fetch_gallery_ids_with_fallback(qtype, qvalue, sort_val, start_page, end_page, fetch_as_archival=archive_mode))
                    continue

                elif m_search:
                    search_query, page_q = m_search.groups()
                    search_query = urllib.parse.unquote(search_query)
                    sort_val = get_valid_sort_value(DEFAULT_PAGE_SORT)
                    start_page = 1
                    end_page = int(page_q) if page_q else DEFAULT_PAGE_RANGE_END
                    gallery_ids.update(fetch_gallery_ids_with_fallback("search", search_query, sort_val, start_page, end_page, fetch_as_archival=archive_mode))
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

        gallery_ids.update(fetch_gallery_ids_with_fallback("homepage", "", sort_val, start_page, end_page))
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
        if query_lower == "archive":
            archive_override = _parse_archive_flag(entry[-1]) if entry else None
            if archive_override is not None:
                entry = entry[:-1] # Ignore redundant archive flag for --archive
        else:
            archive_override = _parse_archive_flag(entry[-1]) if entry else None
            if archive_override is not None:
                archive_mode = archive_override
                entry = entry[:-1] # Remove the flag before parsing numbers

        if not entry:
            logger.warning(f"No query value provided for --{query_lower}; skipping.")
            continue
        
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
        gallery_ids.update(fetch_gallery_ids_with_fallback(query_lower, name, sort_val, start_page, end_page, fetch_as_archival=archival_flag))

    return gallery_ids

def _get_summary_cache_key(args) -> str | None:
    """Return a cache key when a single search source is used, else None."""
    if args.file or args.range or args.galleries:
        return None

    source_flags = [
        bool(args.homepage),
        bool(args.artist),
        bool(args.group),
        bool(args.tag),
        bool(args.character),
        bool(args.parody),
        bool(args.search),
        bool(args.archive),
    ]
    if sum(source_flags) != 1:
        return None

    valid_sorts = ("date", "recent", "popular_today", "today", "popular_week", "week", "popular", "all_time")

    if args.homepage:
        sort_val = DEFAULT_PAGE_SORT
        if args.homepage:
            first = str(args.homepage[0]).lower()
            if first in valid_sorts:
                sort_val = first
        return get_cache_key("homepage", sort_val)

    if args.artist and len(args.artist) == 1:
        return get_cache_key("artist", str(args.artist[0][0]))

    if args.group and len(args.group) == 1:
        return get_cache_key("group", str(args.group[0][0]))

    if args.tag and len(args.tag) == 1:
        return get_cache_key("tag", str(args.tag[0][0]))

    if args.character and len(args.character) == 1:
        return get_cache_key("character", str(args.character[0][0]))

    if args.parody and len(args.parody) == 1:
        return get_cache_key("parody", str(args.parody[0][0]))

    if args.search and len(args.search) == 1:
        return get_cache_key("search", str(args.search[0][0]))

    if args.archive and len(args.archive) == 1:
        entry = args.archive[0]
        if isinstance(entry, str):
            entry = [entry]
        if len(entry) == 1 and str(entry[0]).lower() == "all":
            return get_cache_key("archive", "all")
        return get_cache_key("archive", str(entry[0]))

    return None

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
        archive_entries = []
        archive_all = False
        for entry in args.archive:
            if isinstance(entry, str):
                entry = [entry]
            if len(entry) == 1 and str(entry[0]).lower() == "all":
                archive_all = True
                continue
            archive_entries.append(entry)
        if archive_entries:
            gallery_ids.update(_handle_gallery_args(archive_entries, "archive"))
        if archive_all:
            # Same as homepage crawl but infinite
            gallery_ids.update(fetch_gallery_ids_with_fallback("homepage", "", DEFAULT_PAGE_SORT, start_page=1, end_page=None, fetch_as_archival=True))

    # --- Final sorted list (Processes highest gallery ID (latest gallery) first.) ---
    # Deduplicate while preserving highest-ID-first order
    sorted_ids = sorted(map(int, gallery_ids), reverse=True)
    gallery_list = list(dict.fromkeys(sorted_ids))  # Removes duplicates while preserving order
    
    return gallery_list

def update_config(args):
    log_clarification("debug")
    log("Updating Config...", "debug")
    
    # Only update .env for values explicitly provided via CLI flags
    # If flag not provided, use value already loaded from .env (or default)
    
    if args.extension is not None:
        update_env("EXTENSION", args.extension)
    
    # Handle mirrors (from CLI or interactive menu)
    if args.mirrors is not None:
        update_env("NHENTAI_MIRRORS", args.mirrors)

    # Handle output folder (from CLI or interactive menu)
    if args.output_folder:
        update_env("DOWNLOAD_PATH", args.output_folder)
        update_env("EXTENSION_DOWNLOAD_PATH", args.output_folder)
    
    # Handle max retries (from CLI or interactive menu)
    if hasattr(args, 'max_retries'):
        update_env("MAX_RETRIES", args.max_retries)
    
    # Handle excluded tags
    if args.excluded_tags is not None:
        update_env("EXCLUDED_TAGS", [t.strip().lower() for t in args.excluded_tags.split(",")])
    
    # Only update if explicitly provided
    if hasattr(args, 'language'):
        update_env("LANGUAGE", [lang.strip().lower() for lang in args.language.split(",")])
    
    if hasattr(args, 'title_type'):
        update_env("TITLE_TYPE", args.title_type)
    
    if hasattr(args, 'format'):
        update_env("GALLERY_FORMAT", args.format)
    
    if hasattr(args, 'threads_galleries'):
        update_env("THREADS_GALLERIES", args.threads_galleries)
    
    if hasattr(args, 'threads_images'):
        update_env("THREADS_IMAGES", args.threads_images)
    
    if hasattr(args, 'dry_run'):
        update_env("DRY_RUN", args.dry_run)
    
    if hasattr(args, 'use_tor'):
        update_env("USE_TOR", args.use_tor)
    
    if hasattr(args, 'skip_post_batch'):
        update_env("SKIP_POST_BATCH", args.skip_post_batch)
    
    if hasattr(args, 'skip_post_run'):
        update_env("SKIP_POST_RUN", args.skip_post_run)
    
    if hasattr(args, 'calm'):
        update_env("CALM", args.calm)
    
    if hasattr(args, 'debug'):
        update_env("DEBUG", args.debug)
    
    # SSL verification: --disable-ssl-verify flag sets VERIFY_SSL to False
    if hasattr(args, 'disable_ssl_verify'):
        update_env("VERIFY_SSL", False)
    
    orchestrator.refresh_globals()
    
    log_clarification("debug") # NOTE: DEBUGGING
    log(f"GALLERY THREADS = {orchestrator.threads_galleries}", "debug")
    log(f"IMAGE THREADS = {orchestrator.threads_images}", "debug")

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    ensure_cache_files_exist()
    database.init_db()
    """
    This is one this module's entrypoints.
    """
    
    args = parse_args()
    _warn_deprecated_flags(sys.argv[1:])
    try:
        _validate_args(args)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)

    # Overwrite placeholder logger with real one
    # Use orchestrator values (from .env or defaults) if flags not provided
    calm = getattr(args, 'calm', orchestrator.calm)
    debug = getattr(args, 'debug', orchestrator.debug)
    logger = setup_logger(calm=calm, debug=debug)
    
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
        ensure_extension_cli(args.install_extension)
        return
    
    if args.uninstall_extension:
        uninstall_selected_extension(args.uninstall_extension)
        return
    
    logger.debug("CLI: Ready.")
    log("CLI: Debugging Started.", "debug")
    
    # --- Handle --interactive flag (allow with other gallery-selection flags) ---
    if not args.interactive:
        # If no gallery input is provided, default to homepage
        gallery_args = [
            args.file,
            args.homepage,
            args.range,
            args.galleries,
            args.artist,
            args.group,
            args.tag,
            args.character,
            args.parody,
            args.search,
            args.archive,
        ]
        if not any(gallery_args):
            args.homepage = [DEFAULT_PAGE_RANGE_START, DEFAULT_PAGE_RANGE_END] # Use defaults.
        
    # Update Config With CLI Args
    # Allows session to use correct config values on creation
    update_config(args)
    
    # --- Handle --interactive mode (before building gallery list) ---
    if args.interactive:
        from mangascraper.interactive import interactive_config_menu, interactive_gallery_search
        
        log_clarification()
        logger.info("Entering interactive mode...")
        log_clarification()
        
        # Show config menu - read from orchestrator to preserve .env values
        current_config = {
            'extension': orchestrator.extension,
            'use_tor': orchestrator.use_tor,
            'dry_run': orchestrator.dry_run,
            'threads_galleries': orchestrator.threads_galleries,
            'threads_images': orchestrator.threads_images,
            'format': orchestrator.gallery_format,
            'language': ','.join(orchestrator.language) if isinstance(orchestrator.language, list) else orchestrator.language,
            'title_type': orchestrator.title_type,
            'excluded_tags': ','.join(orchestrator.excluded_tags) if isinstance(orchestrator.excluded_tags, list) else orchestrator.excluded_tags,
            'mirrors': ','.join(orchestrator.nhentai_mirrors) if isinstance(orchestrator.nhentai_mirrors, list) else orchestrator.nhentai_mirrors,
            'output_folder': orchestrator.download_path,
            'max_retries': orchestrator.max_retries,
            'calm': orchestrator.calm,
            'verify_ssl': orchestrator.verify_ssl,
            'use_daemon_threads': orchestrator.use_daemon_threads,
        }
        
        modified_config = interactive_config_menu(current_config)
        
        # Only update .env for values that actually changed in the interactive menu
        # This prevents overwriting .env with unchanged values
        if modified_config.get('extension') != current_config.get('extension'):
            update_env('EXTENSION', modified_config['extension'])
        if modified_config.get('mirrors') != current_config.get('mirrors'):
            update_env('NHENTAI_MIRRORS', modified_config['mirrors'])
        if modified_config.get('verify_ssl') != current_config.get('verify_ssl'):
            update_env('VERIFY_SSL', modified_config['verify_ssl'])
        if modified_config.get('language') != current_config.get('language'):
            update_env('LANGUAGE', modified_config['language'])
        if modified_config.get('title_type') != current_config.get('title_type'):
            update_env('TITLE_TYPE', modified_config['title_type'])
        if modified_config.get('excluded_tags') != current_config.get('excluded_tags'):
            update_env('EXCLUDED_TAGS', modified_config['excluded_tags'])
        if modified_config.get('output_folder') != current_config.get('output_folder'):
            update_env('DOWNLOAD_PATH', modified_config['output_folder'])
            update_env('EXTENSION_DOWNLOAD_PATH', modified_config['output_folder'])
        if modified_config.get('format') != current_config.get('format'):
            update_env('GALLERY_FORMAT', modified_config['format'])
        if modified_config.get('use_tor') != current_config.get('use_tor'):
            update_env('USE_TOR', modified_config['use_tor'])
        if modified_config.get('dry_run') != current_config.get('dry_run'):
            update_env('DRY_RUN', modified_config['dry_run'])
        if modified_config.get('threads_galleries') != current_config.get('threads_galleries'):
            update_env('THREADS_GALLERIES', modified_config['threads_galleries'])
        if modified_config.get('threads_images') != current_config.get('threads_images'):
            update_env('THREADS_IMAGES', modified_config['threads_images'])
        if modified_config.get('use_daemon_threads') != current_config.get('use_daemon_threads'):
            update_env('USE_DAEMON_THREADS', modified_config['use_daemon_threads'])
        if modified_config.get('max_retries') != current_config.get('max_retries'):
            update_env('MAX_RETRIES', modified_config['max_retries'])
        if modified_config.get('calm') != current_config.get('calm'):
            update_env('CALM', modified_config['calm'])
        
        orchestrator.refresh_globals()
        
        # Seed interactive selections from CLI gallery flags if provided
        gallery_args = [
            args.file,
            args.homepage,
            args.range,
            args.galleries,
            args.artist,
            args.group,
            args.tag,
            args.character,
            args.parody,
            args.search,
            args.archive,
        ]
        initial_gallery_list = []
        if any(gallery_args):
            get_session(referrer="CLI", status="build")
            initial_gallery_list = build_gallery_list(args)

        # Enter gallery search mode
        log_clarification()
        gallery_list = interactive_gallery_search(initial_gallery_list, unattended=args.unattended)
        if not gallery_list:
            logger.warning("No galleries selected. Exiting.")
            sys.exit(0)
    else:
        # Build initial session.
        get_session(referrer="CLI", status="build")
        
        # Build Gallery List (make sure not empty.)
        gallery_list = build_gallery_list(args)
        if not gallery_list:
            logger.warning("No galleries provided. Exiting.")
            sys.exit(0)  # Or just return
    
    # --- Show summary before downloading (if requested) ---
    if args.show_summary:
        summary_cache_key = _get_summary_cache_key(args)
        if not display_download_summary(gallery_list, show_summary=True, cache_key=summary_cache_key):
            logger.info("Download cancelled.")
            sys.exit(0)
    
    # Update Config with Built Gallery List
    update_env("GALLERIES", gallery_list)
    
    log_clarification("debug")
    log(f"Final Config:\n{config}", "debug")
    
    # ------------------------------------------------------------
    # Warn about large download operations
    # ------------------------------------------------------------
    if len(gallery_list) >= 300 and not args.unattended:
        log_clarification()
        logger.warning(
            f"WARNING: Downloading {len(gallery_list)} galleries will:\n"
            f"  • Consume significant disk space (tens of GB)\n"
            f"  • Take hours or days to complete\n"
            f"  • Risk rate limiting (403 errors, temporary bans)\n"
            f"Recommended: Download in smaller batches (<300 galleries)."
        )
        confirm = input("Continue with download? (yes/no): ").strip().lower()
        if confirm not in ("yes", "y"):
            logger.info("Download cancelled.")
            sys.exit(0)
    
    # ------------------------------------------------------------
    # Download galleries
    # ------------------------------------------------------------
    start_downloader(gallery_list) # Start download

if __name__ == "__main__":
    main()