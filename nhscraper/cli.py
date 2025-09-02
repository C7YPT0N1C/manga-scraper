#!/usr/bin/env python3
# nhscraper/cli.py

import os, sys, argparse

from nhscraper.core.config import *
from nhscraper.core.downloader import start_downloader
from nhscraper.core.fetchers import build_session, fetch_gallery_ids
from nhscraper.extensions.extension_loader import *

INSTALLER_PATH = "/opt/nhentai-scraper/nhscraper-install.sh"

# ------------------------------
# Delegate to installer
# ------------------------------
INSTALLER_FLAGS = ["--install", "--update", "--update-env", "--uninstall", "--remove"]

def run_installer(flag: str):
    """Call the Bash installer with the given flag, using sudo if needed."""
    if not os.path.exists(INSTALLER_PATH):
        print(f"Installer not found at {INSTALLER_PATH}")
        sys.exit(1)
    
    cmd = [INSTALLER_PATH, flag]
    
    # If not root, prepend sudo
    if os.geteuid() != 0:
        cmd.insert(0, "sudo")
    
    subprocess.run(cmd, check=True)
    sys.exit(0) # Exit after running installer

def parse_args():
    parser = argparse.ArgumentParser(description="NHentai scraper CLI")

    # Installer / Updater flags
    parser.add_argument("--install", action="store_true", help="Install nhentai-scraper and dependencies")
    parser.add_argument("--update", action="store_true", help="Update nhentai-scraper")
    parser.add_argument("--update-env", action="store_true", help="Update the .env file")
    parser.add_argument("--uninstall", "--remove", action="store_true", help="Uninstall nhentai-scraper")

    # Extension selection / management
    parser.add_argument("--extension", type=str, default=DEFAULT_EXTENSION, help=f"Extension to use (default: {DEFAULT_EXTENSION})")
    parser.add_argument("--uninstall-extension", type=str, help="Uninstall an extension by name")

    # Gallery selection
    parser.add_argument("--homepage", nargs=2, type=int, metavar=("START","END"), help=f"Page range of galleries to download from NHentai Homepage (default: {DEFAULT_HOMEPAGE_RANGE_START}-{DEFAULT_HOMEPAGE_RANGE_END})")
    parser.add_argument("--range", nargs=2, type=int, metavar=("START","END"), help=f"Gallery ID range to download (default: {DEFAULT_RANGE_START}-{DEFAULT_RANGE_END})")
    parser.add_argument("--galleries", type=str, help="Comma-separated gallery IDs to download")
    parser.add_argument("--artist", nargs="+", metavar="ARGS", help="Download galleries by artist. Usage: --artist ARTIST [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")
    parser.add_argument("--group", nargs="+", metavar="ARGS", help="Download galleries by group. Usage: --group GROUP [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")
    parser.add_argument("--tag", nargs="+", metavar="ARGS", help="Download galleries by tag. Usage: --tag TAG [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")
    parser.add_argument("--parody", nargs="+", metavar="ARGS", help="Download galleries by parody. Usage: --parody PARODY [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")
    parser.add_argument("--search", nargs="+", metavar="ARGS", help="Download galleries by search. Usage: --search SEARCH [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")

    # Filters
    parser.add_argument("--excluded-tags", type=str, default=DEFAULT_EXCLUDED_TAGS, help=f"Comma-separated list of tags to exclude galleries (default: '{DEFAULT_EXCLUDED_TAGS}')")
    parser.add_argument("--language", type=str, default=DEFAULT_LANGUAGE, help=f"Comma-separated list of languages to include (default: '{DEFAULT_LANGUAGE}')")

    # Titles
    parser.add_argument("--title-type", choices=["english","japanese","pretty"], default=DEFAULT_TITLE_TYPE, help=f"What title type to use (default: {DEFAULT_TITLE_TYPE})")
    parser.add_argument("--title-sanitise", action="store_true", default=DEFAULT_TITLE_SANITISE, help=f"Sanitise titles for filesystem safety (pretty only by default, default: {DEFAULT_TITLE_SANITISE})")

    # Threads / concurrency
    parser.add_argument("--threads-galleries", type=int, default=DEFAULT_THREADS_GALLERIES, help=f"Number of threads to use for gallery downloads (default: {DEFAULT_THREADS_GALLERIES})")
    parser.add_argument("--threads-images", type=int, default=DEFAULT_THREADS_IMAGES, help=f"Number of threads to use for image downloads (default: {DEFAULT_THREADS_IMAGES})")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help=f"Maximum number of retry attempts for failed downloads (default: {DEFAULT_MAX_RETRIES})")

    # Download / runtime options
    parser.add_argument("--use-tor", action="store_true", default=DEFAULT_USE_TOR, help=f"Use TOR network for downloads (default: {DEFAULT_USE_TOR})")
    parser.add_argument("--dry-run", action="store_true", default=DEFAULT_DRY_RUN, help=f"Simulate downloads without saving files (default: {DEFAULT_DRY_RUN})")
    parser.add_argument("--verbose", action="store_true", default=DEFAULT_VERBOSE, help=f"Enable verbose logging (default: {DEFAULT_VERBOSE})")

    return parser.parse_args()

def _handle_gallery_arg(arg_list: list[str] | None, query_type: str) -> set[int]:
    """Parse CLI args and call fetch_gallery_ids."""
    if not arg_list:
        return set()

    name = arg_list[0].strip()
    start_page = int(arg_list[1]) if len(arg_list) > 1 else 1
    end_page = int(arg_list[2]) if len(arg_list) > 2 else None

    return fetch_gallery_ids(query_type, name, start_page, end_page)

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
    if args.homepage:
        gallery_ids.update(_handle_gallery_arg(args.homepage, "homepage"))
    
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
    # Final sorted list (Processes highest gallery ID (latest gallery) first.)
    # ------------------------------
    gallery_list = list(reversed(sorted(gallery_ids)))
    log_clarification()
    logger.debug(f"Gallery List: {gallery_list}")
    return gallery_list

def update_config(args): # Update config   
    config["EXTENSION"] = args.extension 
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


# ------------------------------
# Main
# ------------------------------
def main():
    args = parse_args()

    # Overwrite placeholder logger with real one
    logger = setup_logger(verbose=args.verbose)
    
    log_clarification()
    logger.info("CLI: Ready.")
    logger.debug("CLI: Debugging Started.")
    
    # Installer / Updater
    if args.install:
        run_installer("--install")
    elif args.update:
        run_installer("--update")
    elif args.update_env:
        run_installer("--update-env")
    elif args.uninstall:
        run_installer("--uninstall")
    
    log_clarification()
    logger.debug("Updating Config...")
    
    # Update Config
    # Allows session to use correct config values on creation
    update_config(args)
    
    # Build scraper session.
    build_session()
    
    # Build Gallery List (make sure not empty.)
    gallery_list = build_gallery_list(args)
    if not gallery_list:
        logger.warning("No galleries provided. Exiting.")
        sys.exit(0)  # Or just return
    
    # Update Config with Built Gallery List
    update_env("GALLERIES", gallery_list)
    
    log_clarification()
    logger.debug(f"Updated Config:\n{config}")

    # ------------------------------
    # Handle extension uninstallation (--extension automatically installs extension)
    # ------------------------------
    if args.uninstall_extension:
        uninstall_selected_extension(args.uninstall_extension)
        return
    
    # ------------------------------
    # Download galleries
    # ------------------------------
    start_downloader()

if __name__ == "__main__":
    main()