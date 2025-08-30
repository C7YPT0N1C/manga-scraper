#!/usr/bin/env python3
# nhscraper/cli.py

import argparse

from nhscraper.core.logger import *
from nhscraper.core.config import *
from nhscraper.core.downloader import *
from nhscraper.core.fetchers import fetch_galleries_by_artist, fetch_galleries_by_group, fetch_galleries_by_tag, fetch_galleries_by_parody
from nhscraper.extensions.extension_loader import *

# ------------------------------
# LOG CLARIFICATION
# Prints Blank Line To Make Logs Look Cleaner)
# ------------------------------
def log_clarification():  
    print()
    logger.debug("")

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

    # Artist/Group/Tag/Parody arguments
    parser.add_argument(
        "--artist",
        nargs='+',
        metavar=("ARTIST", "START_PAGE", "END_PAGE"),
        help="Download galleries by artist. Usage: --artist ARTIST [START_PAGE] [END_PAGE]. "
            "If START_PAGE is omitted, defaults to 1. If END_PAGE is omitted, all pages from START_PAGE onwards will be fetched."
    )

    parser.add_argument(
        "--group",
        nargs='+',
        metavar=("GROUP", "START_PAGE", "END_PAGE"),
        help="Download galleries by group. Usage: --group GROUP [START_PAGE] [END_PAGE]. "
            "START_PAGE defaults to 1, END_PAGE fetches all pages if omitted."
    )

    parser.add_argument(
        "--tag",
        nargs='+',
        metavar=("TAG", "START_PAGE", "END_PAGE"),
        help="Download galleries by tag. Usage: --tag TAG [START_PAGE] [END_PAGE]. "
            "START_PAGE defaults to 1, END_PAGE fetches all pages if omitted."
    )

    parser.add_argument(
        "--parody",
        nargs='+',
        metavar=("PARODY", "START_PAGE", "END_PAGE"),
        help="Download galleries by parody. Usage: --parody PARODY [START_PAGE] [END_PAGE]. "
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
    # Artist
    # ------------------------------
    if args.artist:
        artist_name = args.artist[0]
        start_page = int(args.artist[1]) if len(args.artist) > 1 else 1
        end_page = int(args.artist[2]) if len(args.artist) > 2 else None
        gallery_ids.update(fetch_galleries_by_artist(artist_name, start_page, end_page))

    # ------------------------------
    # Group
    # ------------------------------
    if args.group:
        group_name = args.group[0]
        start_page = int(args.group[1]) if len(args.group) > 1 else 1
        end_page = int(args.group[2]) if len(args.group) > 2 else None
        gallery_ids.update(fetch_galleries_by_group(group_name, start_page, end_page))

    # ------------------------------
    # Tag
    # ------------------------------
    if args.tag:
        tag_name = args.tag[0]
        start_page = int(args.tag[1]) if len(args.tag) > 1 else 1
        end_page = int(args.tag[2]) if len(args.tag) > 2 else None
        gallery_ids.update(fetch_galleries_by_tag(tag_name, start_page, end_page))

    # ------------------------------
    # Parody
    # ------------------------------
    if args.parody:
        parody_name = args.parody[0]
        start_page = int(args.parody[1]) if len(args.parody) > 1 else 1
        end_page = int(args.parody[2]) if len(args.parody) > 2 else None
        gallery_ids.update(fetch_galleries_by_parody(parody_name, start_page, end_page))

    # ------------------------------
    # Final sorted list
    # ------------------------------
    gallery_list = sorted(gallery_ids)
    log_clarification()
    logger.debug(f"Gallery List: {gallery_list}")
    return gallery_list

def main():
    args = parse_args()

    # ------------------------------
    # Update config
    # ------------------------------
    config["GALLERIES"] = build_gallery_list(args) # TEST
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
    # Select extension (skeleton fallback)
    # ------------------------------
    selected_extension = get_selected_extension(args.extension.lower())
    log_clarification()
    logger.debug(f"Using extension: {getattr(selected_extension, '__name__', 'skeleton')}")

    # ------------------------------
    # Build gallery list
    # ------------------------------
    #gallery_list = build_gallery_list(args)
    #if not gallery_list:
    #    log_clarification()
    #    logger.warning("No galleries to download. Exiting.")
    #    return

    # ------------------------------
    # Pre-download hook
    # ------------------------------
    if selected_extension and hasattr(selected_extension, "pre_download_hook"):
        gallery_list = selected_extension.pre_download_hook(config, gallery_list)

    # ------------------------------
    # Download galleries
    # ------------------------------
    download_galleries(gallery_list)
    start_downloader()

    # ------------------------------
    # Post-download hook
    # ------------------------------
    if selected_extension and hasattr(selected_extension, "post_download_hook"):
        selected_extension.post_download_hook(config, gallery_list)

if __name__ == "__main__":
    main()