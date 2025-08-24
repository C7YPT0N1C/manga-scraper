#!/usr/bin/env python3
import os, json, time, re
from datetime import datetime
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, cloudscraper

# === CONFIG ===
CONFIG_FILE = "/opt/nhentai-scraper/config.json"
BASE_URL = "https://nhentai.net/g/"
RETRY_LIMIT = 3
SLEEP_BETWEEN_GALLERIES = 0.2
PROGRESS_FILE = "progress.json"
SKIPPED_LOG = "skipped.log"

# Suwayomi GraphQL
SUWAYOMI_GRAPHQL = "http://localhost:4567/api/graphql"
SUWAYOMI_AUTH_HEADER = None  # optional

# VPN/Tor
USE_TOR = False
TOR_PROXY = "socks5h://127.0.0.1:9050"
PROXIES = {"http": TOR_PROXY, "https": TOR_PROXY} if USE_TOR else None

# === CONFIG UTILITIES ===
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

config = load_config()

def save_progress(last_id):
    json.dump({"last_id": last_id}, open(PROGRESS_FILE,"w"))

def load_progress():
    return json.load(open(PROGRESS_FILE)) if os.path.exists(PROGRESS_FILE) else {"last_id": 0}

def sanitize_folder_name(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name)

def log_skipped(root, gallery_id, reason):
    with open(os.path.join(root, SKIPPED_LOG), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} | {gallery_id} | {reason}\n")
    print(f"[-] Skipped {gallery_id}: {reason}")

# === FETCHING ===
def fetch_page(url):
    scraper = cloudscraper.create_scraper()
    headers = {"User-Agent": config["user_agent"]}
    cookies = {"session": config["cookie"]}
    for _ in range(RETRY_LIMIT):
        try:
            if USE_TOR:
                r = scraper.get(url, headers=headers, cookies=cookies, proxies=PROXIES, timeout=10)
            else:
                r = scraper.get(url, headers=headers, cookies=cookies, timeout=10)
        except Exception:
            time.sleep(1)
            continue
        if r.status_code == 200: return r.text
        elif r.status_code == 404: return None
    return None

# === PARSE GALLERY ===
def parse_gallery(gallery_id, root, language_filter="english", excluded_tags=[], include_tags=[]):
    html = fetch_page(f"{BASE_URL}{gallery_id}/")
    if not html: log_skipped(root,gallery_id,"404/fetch failed"); return None
    soup = BeautifulSoup(html,"html.parser")
    lang_tag = soup.find("span", class_="language")
    if not lang_tag or language_filter not in lang_tag.text.lower(): log_skipped(root,gallery_id,f"Non-{language_filter}"); return None
    tags = [t.text.lower() for t in soup.select(".tags a.tag")]
    if any(tag in excluded_tags for tag in tags): log_skipped(root,gallery_id,"Excluded tags"); return None
    if include_tags and not any(tag in include_tags for tag in tags): log_skipped(root,gallery_id,"Missing required tags"); return None
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

def download_image(url, path):
    if os.path.exists(path): return
    scraper = cloudscraper.create_scraper()
    headers = {"User-Agent": config["user_agent"]}
    cookies = {"session": config["cookie"]}
    for _ in range(RETRY_LIMIT):
        try:
            if USE_TOR:
                r = scraper.get(url, headers=headers, cookies=cookies, proxies=PROXIES, timeout=10)
            else:
                r = scraper.get(url, headers=headers, cookies=cookies, timeout=10)
            with open(path, "wb") as f: f.write(r.content)
            break
        except Exception: time.sleep(1)

# === GALLERY DOWNLOAD ===
def download_gallery(g, gallery_root, max_threads_images=5):
    for artist in g["artists"]:
        artist_folder = os.path.join(gallery_root, artist)
        doujin_folder = os.path.join(artist_folder, g["title"])
        os.makedirs(doujin_folder, exist_ok=True)
        with ThreadPoolExecutor(max_workers=max_threads_images) as ex:
            futures=[ex.submit(download_image, img, os.path.join(doujin_folder, f"{i+1}.{img.split('.')[-1].split('?')[0]}")) for i,img in enumerate(g["images"])]
            for _ in as_completed(futures): pass

def download_galleries_parallel(start_id, end_id, root, language_filter="english", excluded_tags=[], include_tags=[], max_threads_galleries=3, max_threads_images=5):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=max_threads_galleries) as ex:
        futures = {ex.submit(parse_gallery, gid, root, language_filter, excluded_tags, include_tags): gid for gid in range(start_id, end_id+1)}
        for fut in as_completed(futures):
            gid = futures[fut]
            g = fut.result()
            if g: download_gallery(g, root, max_threads_images)
            save_progress(gid)
            time.sleep(SLEEP_BETWEEN_GALLERIES)