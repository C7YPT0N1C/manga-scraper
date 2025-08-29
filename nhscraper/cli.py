#!/usr/bin/env python3
# nhscraper/cli.py
import argparse
from nhscraper.core.config import config
from nhscraper.core.downloader import download_galleries
from nhscraper.extensions.extension_loader import (
    INSTALLED_EXTENSIONS,
    install_extension,
    uninstall_extension,
    update_local_manifest_from_remote,
    get_selected_extension
)
from nhscraper.logger import logger


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

    # Artist/Group/Tag/Parody
    parser.add_argument("--artist", nargs=3, metavar=("ARTIST","START_PAGE","END_PAGE"))
    parser.add_argument("--group", nargs=3, metavar=("GROUP","START_PAGE","END_PAGE"))
    parser.add_argument("--tag", nargs=3, metavar=("TAG","START_PAGE","END_PAGE"))
    parser.add_argument("--parody", nargs=3, metavar=("PARODY","START_PAGE","END_PAGE"))

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

    # Tor / dry-run / verbose
    parser.add_argument("--use-tor", action="store_true", default=False)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--verbose", action="store_true", default=False)

    return parser.parse_args()


def build_gallery_list(args):
    gallery_ids = set()

    # Range
    if args.range:
        start, end = args.range
        gallery_ids.update(range(start, end+1))

    # Explicit galleries
    if args.galleries:
        ids = [int(x.strip()) for x in args.galleries.split(",") if x.strip().isdigit()]
        gallery_ids.update(ids)

    # TODO: implement artist/group/tag/parody fetching using nhentai API
    gallery_list = sorted(gallery_ids)
    return gallery_list


def main():
    args = parse_args()

    # ------------------------------
    # Update config
    # ------------------------------
    config["THREADS_GALLERIES"] = args.threads_galleries
    config["THREADS_IMAGES"] = args.threads_images
    config["LANGUAGE"] = [lang.strip().lower() for lang in args.language.split(",")]
    config["EXCLUDED_TAGS"] = [t.strip().lower() for t in args.excluded_tags.split(",")]
    config["TITLE_TYPE"] = args.title_type
    config["TITLE_SANITISE"] = args.title_sanitise
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
    logger.debug(f"\nUsing extension: {getattr(selected_extension, '__name__', 'skeleton')}")

    # ------------------------------
    # Build gallery list
    # ------------------------------
    gallery_list = build_gallery_list(args)
    if not gallery_list:
        logger.warning("\nNo galleries to download. Exiting.")
        return

    # ------------------------------
    # Pre-download hook
    # ------------------------------
    if selected_extension and hasattr(selected_extension, "pre_download_hook"):
        gallery_list = selected_extension.pre_download_hook(config, gallery_list)

    # ------------------------------
    # Download galleries
    # ------------------------------
    download_galleries(gallery_list)

    # ------------------------------
    # Post-download hook
    # ------------------------------
    if selected_extension and hasattr(selected_extension, "post_download_hook"):
        selected_extension.post_download_hook(config, gallery_list)


if __name__ == "__main__":
    main()