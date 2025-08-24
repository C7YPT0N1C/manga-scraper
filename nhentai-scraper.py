#!/usr/bin/env python3
import argparse
import os, sys, json, time, re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests

# ===============================
# CONFIG / DEFAULTS
# ===============================
DEFAULT_ROOT = "/opt/suwayomi/local/"
DEFAULT_EXCLUDE_TAGS = ["snuff","guro","cuntboy","cuntbusting","ai generated"]
DEFAULT_INCLUDE_TAGS = []
DEFAULT_LANGUAGE = "english"
DEFAULT_THREADS_GALLERIES = 3
DEFAULT_THREADS_IMAGES = 5
DEFAULT_USE_TOR = False
DEFAULT_USE_VPN = False
DEFAULT_BASE_URL = "https://nhentai.net/g/"
STATUS_FILE = "/opt/nhentai-scraper/status.json"
SUWAYOMI_GRAPHQL = "http://localhost:4567/api/graphql"
IGNORED_CATEGORIES = ["Favorites"]

# ===============================
# UTILITIES
# ===============================
def sanitize_folder_name(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name)

def log(msg):
    print(f"{datetime.now().isoformat()} | {msg}", flush=True)

def save_status(**kwargs):
    status = {}
    if os.path.exists(STATUS_FILE):
        try:
            status = json.load(open(STATUS_FILE))
        except: pass
    status.update(kwargs)
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)

# ===============================
# GRAPHQL (Suwayomi)
# ===============================
def graphql_query(query, variables=None):
    payload = {"query": query, "variables": variables or {}}
    resp = requests.post(SUWAYOMI_GRAPHQL, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()

def get_categories():
    query = "query { categories { id name } }"
    result = graphql_query(query)
    return {c["name"]: c["id"] for c in result["data"]["categories"]}

def create_category(name):
    query = "mutation($name: String!) { createCategory(input: {name: $name}) { id name } }"
    result = graphql_query(query, {"name": name})
    return result["data"]["createCategory"]

def add_gallery(gallery_input):
    query = "mutation($gallery: GalleryInput!) { createGallery(input: $gallery) { id title } }"
    result = graphql_query(query, {"gallery": gallery_input})
    return result["data"]["createGallery"]

def report_to_suwayomi(meta):
    existing = get_categories()
    cat_ids = []
    for tag in meta["genre"]:
        if tag in IGNORED_CATEGORIES: continue
        if tag not in existing:
            cat = create_category(tag)
            cat_ids.append(cat["id"])
            existing[tag] = cat["id"]
        else:
            cat_ids.append(existing[tag])
    gallery_input = {
        "title": meta["title"],
        "author": meta["author"],
        "artist": meta["artist"],
        "description": meta["description"],
        "status": meta["status"],
        "genre": meta["genre"],
        "categories": cat_ids,
        "files": [f"{i+1}.jpg" for i in range(len(meta.get("images", [])))],
    }
    add_gallery(gallery_input)

# ===============================
# DOWNLOADER (RicterZ/nhentai CLI)
# ===============================
def check_tor(proxy="socks5h://127.0.0.1:9050"):
    """Check current IP through Tor."""
    try:
        import socks  # PySocks dependency
    except ImportError:
        print("[!] Missing PySocks. Install with: pip install pysocks requests[socks]")
        return None

    session = requests.Session()
    session.proxies = {"http": proxy, "https": proxy}
    try:
        r = session.get("https://httpbin.org/ip", timeout=10)
        ip = r.json().get("origin")
        print(f"[*] Tor IP detected: {ip}")
        return ip
    except Exception as e:
        print(f"[!] Tor check failed: {e}")
        return None

def download_gallery(gid, root, cookie, user_agent, use_tor=False, verbose=True):
    """Download a single gallery via RicterZ/nhentai CLI."""
    if verbose:
        print(f"[*] Starting gallery {gid}...")

    cmd = [
        "nhentai",
        "--useragent", user_agent,
        "--cookie", cookie,
        "--id", str(gid)
    ]

    if use_tor:
        cmd += ["--proxy", "socks5h://127.0.0.1:9050"]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if verbose:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[!] Gallery {gid} failed with exit code {e.returncode}")
        if verbose:
            print(e.stderr)
        return False

# ===============================
# METADATA CREATION
# ===============================
def generate_metadata(artist, title, tags):
    return {
        "title": title,
        "author": artist,
        "artist": artist,
        "description": f"An archive of {artist}'s works.",
        "genre": tags,
        "status": "1",
        "_status values": ["0=Unknown","1=Ongoing","2=Completed","3=Licensed"]
    }

# ===============================
# PARALLEL GALLERY DOWNLOAD
# ===============================
def download_range(start, end, root, cookie, user_agent, threads_galleries, threads_images, exclude_tags, include_tags, language, use_tor, verbose):
    with ThreadPoolExecutor(max_workers=threads_galleries) as executor:
        futures = {}
        for gid in range(start, end+1):
            futures[executor.submit(download_gallery, gid, root, cookie, user_agent, use_tor, verbose)] = gid
        for fut in as_completed(futures):
            gid = futures[fut]
            try:
                fut.result()
                save_status(last_gallery=gid)
            except Exception as e:
                log(f"{gid} | Error: {e}")

# ===============================
# CLI
# ===============================
if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--threads-galleries", type=int, default=DEFAULT_THREADS_GALLERIES)
    parser.add_argument("--threads-images", type=int, default=DEFAULT_THREADS_IMAGES)
    parser.add_argument("--exclude-tags", default=",".join(DEFAULT_EXCLUDE_TAGS))
    parser.add_argument("--include-tags", default=",".join(DEFAULT_INCLUDE_TAGS))
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--use-tor", action="store_true", default=DEFAULT_USE_TOR)
    parser.add_argument("--use-vpn", action="store_true", default=DEFAULT_USE_VPN)
    parser.add_argument("--verbose", action="store_true", default=False)
    parser.add_argument("--cookie", required=True)
    parser.add_argument("--useragent", required=True)
    args = parser.parse_args()

    ROOT = args.root
    os.makedirs(ROOT, exist_ok=True)

    exclude_tags = [t.strip().lower() for t in args.exclude_tags.split(",") if t.strip()]
    include_tags = [t.strip().lower() for t in args.include_tags.split(",") if t.strip()]

    START = args.start or 1
    END = args.end or START+10

    log(f"Downloading galleries {START} → {END} to {ROOT}")
    download_range(
        START, END, ROOT, args.cookie, args.useragent,
        args.threads_galleries, args.threads_images,
        exclude_tags, include_tags, args.language,
        args.use_tor, args.verbose
    )
    log("[*] Done!")