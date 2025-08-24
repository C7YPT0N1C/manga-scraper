#!/usr/bin/env python3
import os, sys, json, time, requests, re, threading
from datetime import datetime
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify
import argparse

# =========================
# DEFAULT CONFIG
# =========================
DEFAULT_CONFIG = {
    "ROOT_FOLDER": "/opt/suwayomi/local/",
    "EXCLUDED_TAGS": ["snuff","guro","cuntboy","cuntbusting","ai generated"],
    "INCLUDE_TAGS": [],
    "LANGUAGE_FILTER": "english",
    "MAX_THREADS_GALLERIES": 3,
    "MAX_THREADS_IMAGES": 5,
    "RETRY_LIMIT": 3,
    "SLEEP_BETWEEN_GALLERIES": 0.2,
    "VERBOSE": True,
    "BASE_URL": "https://nhentai.net/g/",
    "PROGRESS_FILE": "progress.json",
    "SKIPPED_LOG": "skipped.log",
    "SUWAYOMI_GRAPHQL": "http://localhost:4567/api/graphql",
    "SUWAYOMI_AUTH_HEADER": None,
    "USE_TOR": False,
    "TOR_PROXY": "socks5h://127.0.0.1:9050",
    "USE_VPN": False
}

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# =========================
# LOAD CONFIG FILE
# =========================
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        file_config = json.load(f)
else:
    file_config = {}

CONFIG = {**DEFAULT_CONFIG, **file_config}

# =========================
# ARGPARSE CLI
# =========================
parser = argparse.ArgumentParser(description="nhentai-scraper")
parser.add_argument("--root", type=str, help="Root folder for downloads")
parser.add_argument("--start", type=int, help="Start gallery ID")
parser.add_argument("--end", type=int, help="End gallery ID")
parser.add_argument("--threads-galleries", type=int, help="Gallery download threads")
parser.add_argument("--threads-images", type=int, help="Image download threads")
parser.add_argument("--exclude-tags", type=str, help="Comma-separated excluded tags")
parser.add_argument("--include-tags", type=str, help="Comma-separated required tags")
parser.add_argument("--language", type=str, help="Language filter")
parser.add_argument("--use-tor", action="store_true", help="Use Tor proxy")
parser.add_argument("--use-vpn", action="store_true", help="Use VPN interface")
parser.add_argument("--verbose", action="store_true", help="Verbose output")
args = parser.parse_args()

# =========================
# FINAL CONFIG MERGE
# =========================
ROOT_FOLDER = args.root or CONFIG["ROOT_FOLDER"]
MAX_THREADS_GALLERIES = args.threads_galleries or CONFIG["MAX_THREADS_GALLERIES"]
MAX_THREADS_IMAGES = args.threads_images or CONFIG["MAX_THREADS_IMAGES"]
EXCLUDED_TAGS = [t.strip().lower() for t in (args.exclude_tags.split(",") if args.exclude_tags else CONFIG["EXCLUDED_TAGS"]) if t.strip()]
INCLUDE_TAGS = [t.strip().lower() for t in (args.include_tags.split(",") if args.include_tags else CONFIG["INCLUDE_TAGS"]) if t.strip()]
LANGUAGE_FILTER = args.language or CONFIG["LANGUAGE_FILTER"]
USE_TOR = args.use_tor or CONFIG["USE_TOR"]
USE_VPN = args.use_vpn or CONFIG["USE_VPN"]
VERBOSE = args.verbose or CONFIG["VERBOSE"]
RETRY_LIMIT = CONFIG["RETRY_LIMIT"]
SLEEP_BETWEEN_GALLERIES = CONFIG["SLEEP_BETWEEN_GALLERIES"]
BASE_URL = CONFIG["BASE_URL"]
PROGRESS_FILE = CONFIG["PROGRESS_FILE"]
SKIPPED_LOG = CONFIG["SKIPPED_LOG"]
SUWAYOMI_GRAPHQL = CONFIG["SUWAYOMI_GRAPHQL"]
SUWAYOMI_AUTH_HEADER = CONFIG["SUWAYOMI_AUTH_HEADER"]
TOR_PROXY = CONFIG["TOR_PROXY"]
PROXIES = {"http": TOR_PROXY, "https": TOR_PROXY} if USE_TOR else None

# =========================
# FLASK STATUS SERVER
# =========================
status = {"last_run": None, "success": False, "downloaded": 0, "skipped": 0, "error": None}
app = Flask(__name__)
@app.route("/scraper_status")
def scraper_status(): return jsonify(status)
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5000), daemon=True).start()

# =========================
# UTILITIES
# =========================
def sanitize_folder_name(name): return re.sub(r'[\\/*?:"<>|]', "_", name)

def log_skipped(root, gallery_id, reason):
    status["skipped"] += 1
    with open(os.path.join(root, SKIPPED_LOG), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} | {gallery_id} | {reason}\n")
    if VERBOSE: print(f"[-] Skipped {gallery_id}: {reason}")

def load_progress(): return json.load(open(PROGRESS_FILE)) if os.path.exists(PROGRESS_FILE) else {"last_id": 0}
def save_progress(last_id): json.dump({"last_id": last_id}, open(PROGRESS_FILE,"w"))

def fetch_page(url):
    for _ in range(RETRY_LIMIT):
        try:
            r = requests.get(url, timeout=10, proxies=PROXIES if USE_TOR else None)
        except:
            time.sleep(1)
            continue
        if r.status_code==200: return r.text
        elif r.status_code==404: return None
    return None

# =========================
# PARSE & DOWNLOAD FUNCTIONS
# =========================
def parse_gallery(gallery_id, root):
    html = fetch_page(f"{BASE_URL}{gallery_id}/")
    if not html: log_skipped(root,gallery_id,"404/fetch failed"); return None
    soup = BeautifulSoup(html,"html.parser")
    lang_tag = soup.find("span", class_="language")
    if not lang_tag or LANGUAGE_FILTER not in lang_tag.text.lower(): log_skipped(root,gallery_id,f"Non-{LANGUAGE_FILTER}"); return None
    tags = [t.text.lower() for t in soup.select(".tags a.tag")]
    if any(tag in EXCLUDED_TAGS for tag in tags): log_skipped(root,gallery_id,"Excluded tags"); return None
    if INCLUDE_TAGS and not any(tag in INCLUDE_TAGS for tag in tags): log_skipped(root,gallery_id,"Missing required tags"); return None
    artist_tags = soup.select(".artist a") + soup.select(".group a")
    artists=[]
    for tag in artist_tags:
        name = sanitize_folder_name(tag.text.strip())
        if name: artists.extend([sanitize_folder_name(a.strip()) for a in name.split("|")])
    if not artists: artists=["Unknown Artist"]
    title_tag = soup.select_one(".title h1,.title h2")
    title = sanitize_folder_name(title_tag.text.strip()) if title_tag else f"Gallery_{gallery_id}"
    images = [img.get("data-src") or img.get("src") for img in soup.select(".thumb-container img")]
    images = [src.replace("t.nhentai.net","i.nhentai.net").replace("t.",".") for src in images]
    return {"id":gallery_id,"artists":artists,"title":title,"tags":tags,"images":images}

def download_image(url,path):
    if os.path.exists(path): return
    for _ in range(RETRY_LIMIT):
        try: open(path,"wb").write(requests.get(url,timeout=10,proxies=PROXIES if USE_TOR else None).content); break
        except: time.sleep(1)

def download_gallery(g,gallery_root):
    for artist in g["artists"]:
        artist_folder = os.path.join(gallery_root,artist)
        doujin_folder = os.path.join(artist_folder,g["title"])
        os.makedirs(doujin_folder, exist_ok=True)
        metadata = {
            "title": g["title"],
            "author": artist,
            "artist": artist,
            "description": f"An archive of {artist}'s works.",
            "genre": g["tags"],
            "status": "1",
            "_status values": ["0=Unknown","1=Ongoing","2=Completed","3=Licensed"]
        }
        with open(os.path.join(doujin_folder,"details.json"),"w",encoding="utf-8") as f: json.dump(metadata,f,ensure_ascii=False,indent=2)
        with ThreadPoolExecutor(max_workers=MAX_THREADS_IMAGES) as ex:
            futures=[ex.submit(download_image,img,os.path.join(doujin_folder,f"{i+1}.{img.split('.')[-1].split('?')[0]}")) for i,img in enumerate(g["images"])]
            for _ in as_completed(futures): pass
        status["downloaded"] += 1
        if VERBOSE: print(f"[+] Downloaded '{g['title']}' by {artist}")

def retry_skipped(root):
    log_file = os.path.join(root,SKIPPED_LOG)
    if not os.path.exists(log_file): return
    temp_log=os.path.join(root,"skipped_temp.log"); os.rename(log_file,temp_log)
    for line in open(temp_log,"r",encoding="utf-8").readlines():
        try: gid=int(line.strip().split("|")[1].strip()); gallery=parse_gallery(gid,root)
        except: continue
        if gallery: download_gallery(gallery,root); time.sleep(SLEEP_BETWEEN_GALLERIES)
    os.remove(temp_log); print("[*] Retry complete.")

def detect_latest_id():
    try:
        html=fetch_page("https://nhentai.net/")
        soup=BeautifulSoup(html,"html.parser")
        links=soup.select("a.cover")
        ids=[int(a.get("href").split("/")[2]) for a in links if "/g/" in a.get("href")]
        return max(ids)
    except: return None

def download_galleries(start_id,max_id,root):
    with ThreadPoolExecutor(max_workers=MAX_THREADS_GALLERIES) as ex:
        futures={ex.submit(parse_gallery,gid,root):gid for gid in range(start_id,max_id+1)}
        for fut in as_completed(futures):
            gid=futures[fut]; g=fut.result()
            if g: download_gallery(g,root)
            save_progress(gid); time.sleep(SLEEP_BETWEEN_GALLERIES)

def update_suwayomi():
    try:
        requests.post(SUWAYOMI_GRAPHQL,json={"query":"mutation { refreshLibrary }"},headers=SUWAYOMI_AUTH_HEADER or {},proxies=PROXIES if USE_TOR else None)
        if VERBOSE: print("[*] Suwayomi library updated.")
    except Exception as e:
        print(f"[!] Suwayomi error: {e}")

# =========================
# MAIN
# =========================
if __name__=="__main__":
    start_id = args.start or (load_progress().get("last_id",0)+1)
    end_id = args.end or detect_latest_id()
    if not end_id: print("[!] Could not detect latest gallery ID."); sys.exit(1)
    print(f"[*] Downloading galleries {start_id} → {end_id} into {ROOT_FOLDER}")
    try:
        download_galleries(start_id,end_id,ROOT_FOLDER)
        retry_skipped(ROOT_FOLDER)
        update_suwayomi()
        status.update({"last_run":datetime.now().isoformat(),"success":True})
        print("[*] Done!")
    except Exception as e:
        status.update({"last_run":datetime.now().isoformat(),"success":False,"error":str(e)})
        raise