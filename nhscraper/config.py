#!/usr/bin/env python3
# nhscraper/config.py
import os
import sys
import json
import logging
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv, set_key
from nhscraper.nhscraper_api import fetch_galleries_by_creator

# ===============================
# KEY
# ===============================
# [*] = Process / In Progress (Prefer logger.info)
# [+] = Success / Confirmation (Prefer logger.info)
# [!] = Warning/Error (Prefer logger.warning on soft errors, logger.error on critical errors)
# (Use logger.debug for debugging)

# ===============================
# PATHS & ENV
# ===============================
NHENTAI_DIR = "/opt/nhentai-scraper"
LOGS_DIR = os.path.join(NHENTAI_DIR, "logs")
SUWAYOMI_DIR = "/opt/suwayomi/local"
ENV_FILE = os.path.join(NHENTAI_DIR, "nhentai-scraper.env")

os.makedirs(LOGS_DIR, exist_ok=True)

# ===============================
# LOGGING
# ===============================
# Ensure logs directory exists
os.makedirs(LOGS_DIR, exist_ok=True)

# Timestamped log file for this run
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_LOG_FILE = os.path.join(LOGS_DIR, f"nhscraper_{timestamp}.log")

# Main log file that always appends
MAIN_LOG_FILE = os.path.join(LOGS_DIR, "master_nhscraper.log")

# Create logger
logger = logging.getLogger("nhentai-scraper")
logger.setLevel(logging.DEBUG)  # Change to INFO to reduce verbosity

formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# File handler for main log (always appends)
file_handler_main = logging.FileHandler(MAIN_LOG_FILE, mode="a")
file_handler_main.setLevel(logging.DEBUG)
file_handler_main.setFormatter(formatter)
logger.addHandler(file_handler_main)

# File handler for timestamped per-run log
file_handler_run = logging.FileHandler(RUN_LOG_FILE, mode="a")
file_handler_run.setLevel(logging.DEBUG)
file_handler_run.setFormatter(formatter)
logger.addHandler(file_handler_run)

# Clarification helper (print new line for readability)
def log_clarification():
    print()
    logger.debug("")

# CLEAN OLD LOGS
def clean_logs():
    if not os.path.exists(LOGS_DIR):
        return
    now = datetime.now()
    for f in os.listdir(LOGS_DIR):
        path = os.path.join(LOGS_DIR, f)
        if os.path.isfile(path):
            try:
                # Remove empty logs
                if os.path.getsize(path) == 0:
                    os.remove(path)
                    log_clarification()
                    file_handler_main.emit(logging.LogRecord(
                        name=logger.name,
                        level=logging.DEBUG,
                        pathname=__file__,
                        lineno=0,
                        msg=f"[+] Deleted empty log: {path}",
                        args=None,
                        exc_info=None
                    ))
                    log_clarification()
                    continue
                
                # Remove logs older than 7 days
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
                if now - mtime > timedelta(days=7):
                    os.remove(path)
                    log_clarification()
                    file_handler_main.emit(logging.LogRecord(
                        name=logger.name,
                        level=logging.DEBUG,
                        pathname=__file__,
                        lineno=0,
                        msg=f"[+] Deleted old log (>7 days): {path}",
                        args=None,
                        exc_info=None
                    ))
                    log_clarification()
            
            except Exception as e:
                log_clarification()
                file_handler_main.emit(logging.LogRecord(
                    name=logger.name,
                    level=logging.WARNING,
                    pathname=__file__,
                    lineno=0,
                    msg=f"[!] Failed to check/delete log {path}: {e}",
                    args=None,
                    exc_info=None
                ))
                log_clarification()

clean_logs()

# ===============================
# API / GRAPHQL
# ===============================
GRAPHQL_URL = "http://127.0.0.1:4567/api/graphql"
NHENTAI_API_BASE = "https://nhentai.net/api/"

# ===============================
# MIRRORS
# ===============================
def get_mirrors():
    env_mirrors = os.getenv("NHENTAI_MIRRORS")
    mirrors = []
    if env_mirrors:
        mirrors = [m.strip() for m in env_mirrors.split(",") if m.strip()]
    mirrors = ["https://i.nhentai.net"] + [m for m in mirrors if m != "https://i.nhentai.net"]
    return mirrors

MIRRORS = get_mirrors()

# ===============================
# LOAD ENV
# ===============================
load_dotenv(dotenv_path=ENV_FILE)

def update_env(key, value):
    set_key(ENV_FILE, key, str(value))

# ===============================
# CLI ARGUMENTS
# ===============================
parser = argparse.ArgumentParser(description="NHentai scraper with Suwayomi integration")

parser.add_argument(
    "--galleries",
    nargs="+",
    help="Specific gallery IDs (space-separated) or a file containing IDs: --galleries <ID> <ID>"
)
parser.add_argument(
    "--range",
    nargs=2,
    type=int,
    metavar=("START_ID", "END_ID"),
    help="Specify a range of gallery IDs: --range <START> <END>"
)
parser.add_argument("--artist", type=str, help="Download galleries by Artist name")
parser.add_argument("--group", type=str, help="Download galleries by Group name")
parser.add_argument("--excluded-tags", type=str, help="Comma-separated list of tags to exclude galleries (Default: empty)")
parser.add_argument("--language", type=str, help="Comma-separated list of languages to include (Default: english)")
parser.add_argument("--title-type", type=str, choices=["english", "japanese", "pretty"], help="Gallery title type for folder names (Default: pretty)")
parser.add_argument("--threads-galleries", type=int, help="Number of concurrent galleries (Default: 1)")
parser.add_argument("--threads-images", type=int, help="Threads per gallery (Default: 4)")
parser.add_argument("--use-tor", action="store_true", help="Route requests via Tor (Default: false)")
parser.add_argument("--dry-run", action="store_true", help="Simulate downloads and GraphQL without saving (Default: false)")
parser.add_argument("--verbose", action="store_true", help="Enable debug logging (Default: false)")

args = parser.parse_args()

# ===============================
# CONFIG MERGE: ENV + CLI
# ===============================
def get_config(name, default=None, is_bool=False):
    env_val = os.getenv(name)
    arg_val = getattr(args, name.lower(), None)
    if arg_val is not None:
        update_env(name, arg_val if not is_bool else str(arg_val))
        return arg_val
    if env_val is not None:
        if is_bool:
            return str(env_val).lower() in ("1", "true", "yes")
        if isinstance(default, int):
            try:
                return int(env_val)
            except ValueError:
                return default
        return str(env_val).strip('"').strip("'")
    return default

config = {
    "excluded_tags": get_config("EXCLUDE_TAGS", ""),
    "language": get_config("LANGUAGE", "english"),
    "title_type": get_config("TITLE_TYPE", "pretty"),
    "threads_galleries": get_config("THREADS_GALLERIES", 1),
    "threads_images": get_config("THREADS_IMAGES", 4),
    "use_tor": get_config("USE_TOR", False, True),
    "dry_run": get_config("NHENTAI_DRY_RUN", False, True),
    "verbose": get_config("NHENTAI_VERBOSE", False, True),
}

if args.excluded_tags:
    config["excluded_tags"] = args.excluded_tags
if args.language:
    config["language"] = args.language
if args.title_type:
    config["title_type"] = args.title_type
if args.threads_galleries is not None:
    config["threads_galleries"] = args.threads_galleries
if args.threads_images is not None:
    config["threads_images"] = args.threads_images
if args.use_tor:
    config["use_tor"] = True
if args.dry_run:
    config["dry_run"] = True
if args.verbose:
    config["verbose"] = True
    logger.setLevel(logging.DEBUG)

# ===============================
# GALLERIES HANDLING
# ===============================
config["galleries"] = parse_gallery_list(
    arg=args.galleries,
    artist_name=args.artist,
    group_name=args.group
)

def parse_gallery_list(arg=None, artist_arg=None, group_arg=None):
    # Combine gallery sources into one ascending list.
    # - arg: --galleries CLI argument (list of IDs or file)
    # - artist_arg: optional --artist flag "<NAME> [PAGE_START PAGE_END]"
    # - group_arg: optional --group flag "<NAME> [PAGE_START PAGE_END]"
    galleries = set()

    # --galleries from file or list
    if arg:
        if len(arg) == 1 and os.path.isfile(arg[0]):
            with open(arg[0], "r", encoding="utf-8") as f:
                galleries.update(int(line.strip()) for line in f if line.strip().isdigit())
        else:
            galleries.update(int(x) for x in arg if x.isdigit())

    # --range or env variable
    elif getattr(args, "range", None):
        galleries.update(range(args.range[0], args.range[1]+1))
    else:
        env_val = os.getenv("GALLERY_RANGE")
        if env_val:
            parts = env_val.strip().split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                galleries.update(range(int(parts[0]), int(parts[1])+1))

    # --artist parsing: "<NAME> [PAGE_START PAGE_END]"
    if artist_arg:
        parts = artist_arg.split()
        name = " ".join([p for p in parts if not p.isdigit()])
        digits = [int(p) for p in parts if p.isdigit()]
        page_start, page_end = (digits[0], digits[1]) if len(digits) == 2 else (1, None)
        ids = fetch_galleries_by_creator("artist", name, page_start, page_end)
        galleries.update(ids)

    # --group parsing: "<NAME> [PAGE_START PAGE_END]"
    if group_arg:
        parts = group_arg.split()
        name = " ".join([p for p in parts if not p.isdigit()])
        digits = [int(p) for p in parts if p.isdigit()]
        page_start, page_end = (digits[0], digits[1]) if len(digits) == 2 else (1, None)
        ids = fetch_galleries_by_creator("group", name, page_start, page_end)
        galleries.update(ids)

    return sorted(galleries)

config["galleries"] = parse_gallery_list()