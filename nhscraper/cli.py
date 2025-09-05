#!/usr/bin/env python3
# nhscraper/cli.py

import os, sys, argparse, re

from nhscraper.core.config import *
from nhscraper.core.downloader import start_downloader
from nhscraper.core.api import build_session, fetch_gallery_ids
from nhscraper.extensions.extension_loader import *

INSTALLER_PATH = "/opt/nhentai-scraper/nhscraper-install.sh"

# ------------------------------
# Delegate to installer
# ------------------------------
INSTALLER_FLAGS = ["--install", "--update", "--update-env", "--uninstall", "--remove"]

def run_installer(flag: str):
    """Call the Bash installer with the given flag, using sudo if needed."""
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
    parser.add_argument("--extension", type=str, default=DEFAULT_EXTENSION, help=f"Extension to use (default: {DEFAULT_EXTENSION})")
    parser.add_argument("--uninstall-extension", type=str, help="Uninstall an extension by name")

    # Gallery selection
    parser.add_argument(
        "--file",
        nargs="?",
        const=config.get("DOUJIN_TXT_PATH"),
        type=str,
        help="Path to a file containing gallery URLs (one per line). These will be converted to gallery IDs and downloaded."
    )
    parser.add_argument("--homepage", nargs=2, type=int, metavar=("START","END"), help=f"Page range of galleries to download from NHentai Homepage (default: {DEFAULT_HOMEPAGE_RANGE_START}-{DEFAULT_HOMEPAGE_RANGE_END}). Passing no gallery flags (--gallery, --artist, etc) defaults here.")
    parser.add_argument("--range", nargs=2, type=int, metavar=("START","END"), help=f"Gallery ID range to download (default: {DEFAULT_RANGE_START}-{DEFAULT_RANGE_END})")
    parser.add_argument("--galleries", type=str, help="Comma-separated gallery IDs to download")
    parser.add_argument("--artist", nargs="+", metavar="ARGS", help="Download galleries by artist. Usage: --artist ARTIST [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")
    parser.add_argument("--group", nargs="+", metavar="ARGS", help="Download galleries by group. Usage: --group GROUP [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")
    parser.add_argument("--tag", nargs="+", metavar="ARGS", help="Download galleries by tag. Usage: --tag TAG [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")
    parser.add_argument("--parody", nargs="+", metavar="ARGS", help="Download galleries by parody. Usage: --parody PARODY [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")
    parser.add_argument("--search", nargs="+", metavar="ARGS", help="Download galleries by search. Usage: --search SEARCH [START_PAGE] [END_PAGE]. START_PAGE defaults to 1, END_PAGE fetches all pages if omitted.")

    # Filters
    parser.add_argument("--excluded-tags", type=str, default=None, help=f"Comma-separated list of tags to exclude galleries (default: '{DEFAULT_EXCLUDED_TAGS}')")
    parser.add_argument("--language", type=str, default=DEFAULT_LANGUAGE, help=f"Comma-separated list of languages to include (default: '{DEFAULT_LANGUAGE}')")
    parser.add_argument("--title-type", choices=["english","japanese","pretty"], default=DEFAULT_TITLE_TYPE, help=f"What title type to use (default: {DEFAULT_TITLE_TYPE})")

    # Threads / concurrency
    parser.add_argument("--threads-galleries", type=int, default=DEFAULT_THREADS_GALLERIES, help=f"Number of threads to use for gallery downloads (default: {DEFAULT_THREADS_GALLERIES})")
    parser.add_argument("--threads-images", type=int, default=DEFAULT_THREADS_IMAGES, help=f"Number of threads to use for image downloads (default: {DEFAULT_THREADS_IMAGES})")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help=f"Maximum number of retry attempts for failed downloads (default: {DEFAULT_MAX_RETRIES})")

    # Download / runtime options
    parser.add_argument("--use-tor", action="store_true", default=DEFAULT_USE_TOR, help=f"Use TOR network for downloads (default: {DEFAULT_USE_TOR})")
    parser.add_argument("--dry-run", action="store_true", default=DEFAULT_DRY_RUN, help=f"Simulate downloads without saving files (default: {DEFAULT_DRY_RUN})")
    
    # Make verbose/debug mutually exclusive
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--verbose", action="store_true", default=False, help="Enable verbose logging (print to stdout)")
    group.add_argument("--debug", action="store_true", default=False, help="Enable debug logging (logger.debug)")

    return parser.parse_args()

def _handle_gallery_args(arg):
    """
    Normalise gallery input:
    - File path containing URLs or IDs
    - Explicit range (e.g. 500000-500010)
    - Single ID
    - nhentai URL
    Returns a list of gallery IDs (ints).
    """
    gallery_ids = []

    # --- Case 1: File input ---
    if arg and os.path.isfile(arg):
        with open(arg, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Match nhentai URL
                m = re.search(r"/g/(\d+)/", line)
                if m:
                    gallery_ids.append(int(m.group(1)))
                # Allow raw IDs in file too
                elif line.isdigit():
                    gallery_ids.append(int(line))
        return gallery_ids

    # --- Case 2: Range like 500000-500010 ---
    if "-" in str(arg):
        try:
            start, end = map(int, arg.split("-"))
            gallery_ids.extend(range(start, end + 1))
        except ValueError:
            raise ValueError(f"Invalid range format: {arg}")
        return gallery_ids

    # --- Case 3: Single ID ---
    if str(arg).isdigit():
        gallery_ids.append(int(arg))
        return gallery_ids

    # --- Case 4: Unhandled ---
    raise ValueError(f"Unsupported gallery argument: {arg}")

def build_gallery_list(args):
    gallery_ids = set()

    # ------------------------------
    # File input
    # ------------------------------
    if args.file:
        if os.path.exists(args.file):
            gallery_ids.update(_handle_gallery_args(args.file))
        else:
            logger.warning(f"Gallery file not found: {args.file}")

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
        gallery_ids.update(_handle_gallery_args(args.homepage, "homepage"))
    
    if args.artist:
        gallery_ids.update(_handle_gallery_args(args.artist, "artist"))

    if args.group:
        gallery_ids.update(_handle_gallery_args(args.group, "group"))

    if args.tag:
        gallery_ids.update(_handle_gallery_args(args.tag, "tag"))

    if args.parody:
        gallery_ids.update(_handle_gallery_args(args.parody, "parody"))
    
    if args.search:
        gallery_ids.update(_handle_gallery_args(args.search, "search"))

    # ------------------------------
    # Final sorted list (Processes highest gallery ID (latest gallery) first.)
    # ------------------------------
    gallery_list = list( # Convert to list
        reversed( # Highest ID first
            sorted( # Sort list so it can be reversed.
                map( # Make sure Gallery IDs processed as integers
                    int, gallery_ids
                    )
                )
            )
        )
    
    log_clarification()
    log(f"Gallery List: {gallery_list}")
    
    return gallery_list

def update_config(args): # Update config   
    config["EXTENSION"] = args.extension
    
    if args.excluded_tags is not None: # Use new excluded tags.
        config["EXCLUDED_TAGS"] = [t.strip().lower() for t in args.excluded_tags.split(",")]
    else:
        # Use whatever excluded tags were already in config (env or default)
        if isinstance(config["EXCLUDED_TAGS"], str):
            config["EXCLUDED_TAGS"] = [t.strip().lower() for t in config["EXCLUDED_TAGS"].split(",")]
    
    config["LANGUAGE"] = [lang.strip().lower() for lang in args.language.split(",")]
    config["TITLE_TYPE"] = args.title_type
    config["THREADS_GALLERIES"] = args.threads_galleries
    config["THREADS_IMAGES"] = args.threads_images
    config["MAX_RETRIES"] = args.max_retries
    config["DRY_RUN"] = args.dry_run
    config["USE_TOR"] = args.use_tor
    config["VERBOSE"] = args.verbose
    config["DEBUG"] = args.debug

# ------------------------------
# Main
# ------------------------------
def main():
    args = parse_args()
    
    # If no gallery input is provided, default to homepage 1 1
    gallery_args = [args.homepage, args.range, args.galleries, args.artist,
                    args.group, args.tag, args.parody, args.search]
    if not any(gallery_args):
        args.homepage = [DEFAULT_HOMEPAGE_RANGE_START, DEFAULT_HOMEPAGE_RANGE_END] # Use defaults.

    # Overwrite placeholder logger with real one
    logger = setup_logger(debug=args.debug)
    
    log_clarification()
    logger.info("====================================================")
    logger.info("                  nhentai-scraper                   ")
    logger.info("====================================================")
    
    logger.info("CLI: Ready.")
    log("CLI: Debugging Started.")
    
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
    log("Updating Config...")
    
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
    log(f"Updated Config:\n{config}")

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