#!/usr/bin/env python3
# nhscraper/cli.py
# DESCRIPTION: Command-line interface for nhentai-scraper.
# Called by: User from terminal
# Calls: config.py, downloader.py
# FUNCTION: Parse CLI arguments and launch the scraper or update dashboard password

import argparse
from nhscraper.config import config, update_config
from nhscraper.downloader import main as downloader_main
from nhscraper.tools.set_password import update_dashboard_password

def parse_args():
    parser = argparse.ArgumentParser(
        description="NHentai scraper with Suwayomi integration"
    )
    parser.add_argument("--galleries", nargs="+", type=int, help="Gallery IDs to scrape")
    parser.add_argument("--range", nargs=2, type=int, metavar=("START", "END"), help="Gallery ID range")
    parser.add_argument("--artist", type=str, help="Artist name or range")
    parser.add_argument("--group", type=str, help="Group name or range")
    parser.add_argument("--category", type=str, help="Category name or range")
    parser.add_argument("--excluded-tags", type=str, help="Comma-separated list of tags to exclude")
    parser.add_argument("--language", type=str, help="Comma-separated list of languages to include")
    parser.add_argument("--title-type", choices=["english", "japanese", "pretty"], default="pretty")
    parser.add_argument("--threads-galleries", type=int, default=1)
    parser.add_argument("--threads-images", type=int, default=4)
    parser.add_argument("--use-tor", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dashboard-password", type=str, help="Update dashboard password")
    return parser.parse_args()

def main():
    args = parse_args()

    if args.dashboard_password:
        update_dashboard_password(args.dashboard_password)
        print("[+] Dashboard password updated successfully.")
        return

    update_config(vars(args))  # merge CLI args into config
    downloader_main(config)

if __name__ == "__main__":
    main()