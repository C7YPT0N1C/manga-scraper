#!/usr/bin/env python3
import argparse, sys, json
from datetime import datetime
from scraper_core import load_progress, download_galleries_parallel, load_config, save_config

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
    parser.add_argument("--user_agent", type=str, default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.5790.171 Safari/537.36", help="Custom User-Agent header")
    parser.add_argument("--cookie", type=str, default="", help="Session cookie from nhentai.net")    
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

    # load config
    cfg = load_config()

    # update with CLI overrides
    cfg.update({
        "language": args.language.lower(),
        "exclude_tags": [t.strip().lower() for t in args.exclude_tags.split(",") if t.strip()],
        "include_tags": [t.strip().lower() for t in args.include_tags.split(",") if t.strip()],
        "threads_galleries": args.threads_galleries,
        "threads_images": args.threads_images,
        "use_tor": args.use_tor,
        "root": args.root or cfg.get("root", "./downloads"),
        "user_agent": args.user_agent or cfg.get("user_agent", ""),
        "cookie": args.cookie or cfg.get("cookie", "")
    })

    # persist changes
    save_config(cfg)

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