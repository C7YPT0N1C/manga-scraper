#!/usr/bin/env python3
import argparse, sys, json
from datetime import datetime
from nhentai_scraper_core import load_progress, download_galleries_parallel

STATUS_FILE = "/opt/nhentai-scraper/status.json"
ROOT_FOLDER_DEFAULT = "/opt/suwayomi/local/"

def update_status(downloaded=0, skipped=0, error=None, success=False):
    status = {
        "last_run": datetime.now().isoformat(),
        "downloaded": downloaded,
        "skipped": skipped,
        "error": error,
        "success": success
    }
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)

if __name__=="__main__":
    parser = argparse.ArgumentParser(description="nhentai-scraper")
    parser.add_argument("--start", type=int, required=True, help="Start gallery ID")
    parser.add_argument("--end", type=int, required=False, help="End gallery ID")
    parser.add_argument("--root", type=str, default=ROOT_FOLDER_DEFAULT, help="Root folder for downloads")
    parser.add_argument("--threads-galleries", type=int, default=3)
    parser.add_argument("--threads-images", type=int, default=5)
    parser.add_argument("--exclude-tags", type=str, default="")
    parser.add_argument("--include-tags", type=str, default="")
    parser.add_argument("--language", type=str, default="english")
    parser.add_argument("--use-tor", action="store_true")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    start_id = args.start or (load_progress().get("last_id",0)+1)
    end_id = args.end or start_id
    excluded_tags = [t.strip().lower() for t in args.exclude_tags.split(",") if t.strip()]
    include_tags = [t.strip().lower() for t in args.include_tags.split(",") if t.strip()]

    try:
        download_galleries_parallel(
            start_id=start_id,
            end_id=end_id,
            root=args.root,
            language_filter=args.language.lower(),
            excluded_tags=excluded_tags,
            include_tags=include_tags,
            max_threads_galleries=args.threads_galleries,
            max_threads_images=args.threads_images,
            use_tor=args.use_tor,
            verbose=args.verbose
        )
        update_status(success=True)
        print("[*] Done!")
    except Exception as e:
        update_status(error=str(e))
        raise