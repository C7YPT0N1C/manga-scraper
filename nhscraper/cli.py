#!/usr/bin/env python3
# nhscraper/cli.py

import argparse

from nhscraper.core.config import logger, config, setup_logger # TEST
from nhscraper.core.downloader import *
from nhscraper.core.fetchers import fetch_gallery_ids
from nhscraper.extensions.extension_loader import *

# ------------------------------
# LOG CLARIFICATION
# Prints Blank Line To Make Logs Look Cleaner)
# ------------------------------
def log_clarification():  
    print()
    logger.debug("")

log_clarification()
logger.info("CLI: Ready.")
logger.debug("CLI: Debugging Started.")

def parse_args():
    parser = argparse.ArgumentParser(
        description="NHentai scraper with Suwayomi integration"
    )

    # Extension installation/uninstallation
    parser.add_argument("--install-extension", type=str, help="Install an extension by name")
    parser.add_argument("--uninstall-extension", type=str, help="Uninstall an extension by name")

    # Extension selection
    parser.add_argument(
        "--extension",
        type=str,
        default="none",
        help="Extension to use (default: none)"
    )

    # Gallery ID selection
    parser.add_argument(
        "--range",
        nargs=2,
        type=int,
        metavar=("START", "END"),
        help="Gallery ID range to download"
    )
    parser.add_argument(
        "--galleries",
        type=str,
        help="Comma-separated gallery IDs to download"
    )

    # Artist/Group/Tag/Parody/Search arguments
    parser.add_argument(
        "--artist",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by artist. Usage: --artist ARTIST [START_PAGE] [END_PAGE]. "
            "START_PAGE defaults to 1, END_PAGE fetches all pages if omitted."
    )

    parser.add_argument(
        "--group",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by group. Usage: --group GROUP [START_PAGE] [END_PAGE]. "
            "START_PAGE defaults to 1, END_PAGE fetches all pages if omitted."
    )

    parser.add_argument(
        "--tag",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by tag. Usage: --tag TAG [START_PAGE] [END_PAGE]. "
            "START_PAGE defaults to 1, END_PAGE fetches all pages if omitted."
    )

    parser.add_argument(
        "--parody",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by parody. Usage: --parody PARODY [START_PAGE] [END_PAGE]. "
            "START_PAGE defaults to 1, END_PAGE fetches all pages if omitted."
    )

    parser.add_argument(
        "--search",
        nargs="+",
        metavar="ARGS",
        help="Download galleries by search. Usage: --search SEARCH [START_PAGE] [END_PAGE]. "
            "START_PAGE defaults to 1, END_PAGE fetches all pages if omitted."
    )

    # Filters
    parser.add_argument(
        "--excluded-tags", type=str, default="",
        help="Comma-separated list of tags to exclude galleries"
    )
    parser.add_argument(
        "--language", type=str, default="english",
        help="Comma-separated list of languages to include"
    )

    # Titles
    parser.add_argument(
        "--title-type", choices=["english","japanese","pretty"], default="english"
    )
    parser.add_argument(
        "--title-sanitise", action="store_true", default=True,
        help="Sanitise titles for filesystem safety (pretty only by default)"
    )

    # Threads
    parser.add_argument("--threads-galleries", type=int, default=4)
    parser.add_argument("--threads-images", type=int, default=1)
    
    parser.add_argument(
        "--max-retries",
        type=int,
        default=int(os.getenv("MAX_RETRIES", 3)),
        help="Maximum number of retry attempts for failed downloads (default: 3)"
    )   

    # Download Options
    parser.add_argument("--use-tor", action="store_true", default=False)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--verbose", action="store_true", default=False)

    return parser.parse_args()

def _handle_gallery_arg(arg_list: list[str] | None, query_type: str) -> set[int]:
    """Helper to parse CLI args and call fetch_gallery_ids."""
    if not arg_list:
        return set()

    name = arg_list[0]
    start_page = int(arg_list[1]) if len(arg_list) > 1 else 1
    end_page = int(arg_list[2]) if len(arg_list) > 2 else None

    query = f'{query_type}:"{name}"'
    return fetch_gallery_ids(query, start_page, end_page)

def build_gallery_list(args):
    gallery_ids = set()

    # ------------------------------
    # Range
    # ------------------------------
    if args.range:
        start, end = args.range
        gallery_ids.update(range(start, end + 1))

    # ------------------------------
    # Explicit galleries
    # ------------------------------
    if args.galleries:
        ids = [int(x.strip()) for x in args.galleries.split(",") if x.strip().isdigit()]
        gallery_ids.update(ids)
    
    # ------------------------------
    # Artist / Group / Tag / Parody / Search
    # ------------------------------
    if args.artist:
        gallery_ids.update(_handle_gallery_arg(args.artist, "artist"))

    if args.group:
        gallery_ids.update(_handle_gallery_arg(args.group, "group"))

    if args.tag:
        gallery_ids.update(_handle_gallery_arg(args.tag, "tag"))

    if args.parody:
        gallery_ids.update(_handle_gallery_arg(args.parody, "parody"))
    
    if args.search:
        gallery_ids.update(_handle_gallery_arg(args.search, "search"))

    # ------------------------------
    # Final sorted list
    # ------------------------------
    gallery_list = sorted(gallery_ids)
    log_clarification()
    logger.debug(f"Gallery List: {gallery_list}")
    return gallery_list

def update_config(args, gallery_list): # Update config
    config["GALLERIES"] = gallery_list # TEST
    config["EXCLUDED_TAGS"] = [t.strip().lower() for t in args.excluded_tags.split(",")]
    config["LANGUAGE"] = [lang.strip().lower() for lang in args.language.split(",")]
    config["TITLE_TYPE"] = args.title_type
    config["TITLE_SANITISE"] = args.title_sanitise
    config["THREADS_GALLERIES"] = args.threads_galleries
    config["THREADS_IMAGES"] = args.threads_images
    config["MAX_RETRIES"] = args.max_retries
    config["DRY_RUN"] = args.dry_run
    config["USE_TOR"] = args.use_tor
    config["VERBOSE"] = args.verbose

def main():
    args = parse_args()
    
    setup_logger(dry_run=args.dry_run, verbose=args.verbose) # Allow logger to set log level.

    # ------------------------------
    # Build gallery list
    # ------------------------------
    gallery_list = build_gallery_list(args)
    if not gallery_list:
        log_clarification()
        logger.warning("No galleries to download. Exiting.")
        return
    
    log_clarification()
    logger.debug("Updating Config...")
    update_config(args, gallery_list)    
    log_clarification()
    logger.debug(f"Updated Config: {config}")
    
    build_session() # Call fetcher to build cloudscraper session.

    # ------------------------------
    # Handle extension installation/uninstallation
    # ------------------------------
    if args.install_extension:
        update_local_manifest_from_remote()  # just call it without argument
        install_extension(args.install_extension)
        return

    if args.uninstall_extension:
        uninstall_extension(args.uninstall_extension)
        return
    
    # ------------------------------
    # Download galleries
    # ------------------------------
    #download_galleries(gallery_list)
    start_downloader() # TEST

if __name__ == "__main__":
    setup_logger(dry_run=False, verbose=False) # Set logger early to allow for Module Import Logging. TEST
    main()