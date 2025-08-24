#!/usr/bin/env python3
import sys, time, json
from datetime import datetime
from nhentai_scraper_core import load_progress, download_galleries_parallel

STATUS_FILE = "/opt/nhentai-scraper/status.json"
ROOT_FOLDER = "/opt/suwayomi/local/"

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
    start_id = load_progress().get("last_id",0)+1
    end_id = 400010
    try:
        download_galleries_parallel(start_id, end_id, ROOT_FOLDER)
        update_status(success=True)
        print("[*] Done!")
    except Exception as e:
        update_status(error=str(e))
        raise