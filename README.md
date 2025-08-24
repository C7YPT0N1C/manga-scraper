# nhentai-scraper

## Overview
`nhentai-scraper` is a fully-featured Python scraper for nhentai.net that downloads galleries, supports multi-artist/group galleries, saves Suwayomi-compatible metadata, and can run as a systemd service. It now comes with a **full installer** that also sets up RicterZ/nhentai, Suwayomi-Server, and FileBrowser, including systemd autostart.

---

## Features
- Gallery download with language/tag filters  
- Multi-artist/group duplication  
- Suwayomi-compatible metadata (`details.json`)  
- Tor / VPN support  
- Flask monitoring endpoint (`/scraper_status`)  
- Systemd services for autostart  
- Retry failed/skipped galleries automatically  
- Configurable root folder via `config.json` or CLI  
- Multi-threaded downloads for galleries and images  
- Automatic retry of skipped galleries  
- RicterZ/nhentai repo integration  
- FileBrowser installation for web access to downloaded galleries  
- Installer supports `install`, `update`, and `uninstall` modes  
- Clickable links printed at the end of install/update  

---

## Installation

### System Requirements
- Linux server / VM  
- Python 3.x + pip  
- Optional: Tor (`sudo apt install tor`) or system VPN (OpenVPN, WireGuard)

> The installer handles:
> - Cloning `RicterZ/nhentai` to `/opt/ricterz_nhentai`  
> - Installing `nhentai-scraper` to `/opt/nhentai-scraper`  
> - Installing Suwayomi-Server to `/opt/suwayomi`  
> - Installing FileBrowser  
> - Creating systemd services for autostart  

### Run Installer
```bash
sudo bash install.sh
```

#### Update
```bash
sudo bash install.sh update
```

#### Uninstall
```bash
sudo bash install.sh uninstall
```

---

## Configuration

All scraper settings are in `config.json`:

```jsonc
{
  "ROOT_FOLDER": "/opt/suwayomi/local/",        // Where galleries are downloaded
  "EXCLUDED_TAGS": ["snuff","guro","cuntboy","cuntbusting","ai generated"],
  "INCLUDE_TAGS": [],                           // Only download if at least one tag matches
  "LANGUAGE_FILTER": "english",
  "MAX_THREADS_GALLERIES": 3,
  "MAX_THREADS_IMAGES": 5,
  "RETRY_LIMIT": 3,
  "SLEEP_BETWEEN_GALLERIES": 0.2,
  "VERBOSE": true,
  "BASE_URL": "https://nhentai.net/g/",
  "PROGRESS_FILE": "progress.json",
  "SKIPPED_LOG": "skipped.log",
  "SUWAYOMI_GRAPHQL": "http://localhost:4567/api/graphql",
  "SUWAYOMI_AUTH_HEADER": null,
  "USE_TOR": false,
  "TOR_PROXY": "socks5h://127.0.0.1:9050",
  "USE_VPN": false
}
```

- CLI arguments **override** config values.  
- Config path: `/opt/nhentai-scraper/config.json` (default).  
- Custom root folders or gallery ranges can be set via CLI.

---

## Python Modules

Installer automatically installs required modules:

```text
requests[socks]
beautifulsoup4
flask
tqdm
aiohttp
gql[all]
nhentai
```

No manual installation needed.

---

## Directory Layout

### Repository
```
/opt/nhentai-scraper/
├─ scraper.py
├─ install.sh
├─ config.json
├─ README.md
```

### Galleries (Suwayomi compatible)
```
ARTIST_FOLDER/
└─ DOUJIN_FOLDER/
   ├─ 1.jpg
   ├─ 2.jpg
   └─ details.json
```

### Suwayomi metadata (`details.json`)
```json
{
  "title": "AUTHOR_NAME",
  "author": "AUTHOR_NAME",
  "artist": "AUTHOR_NAME",
  "description": "An archive of AUTHOR_NAME's works.",
  "genre": ["tags_here"],
  "status": "1",
  "_status values": ["0=Unknown","1=Ongoing","2=Completed","3=Licensed"]
}
```

---

## Usage

### CLI Arguments
```bash
--root             Root folder (default from config.json)
--start            Start gallery ID (default: last progress +1)
--end              End gallery ID (default: latest ID)
--threads-galleries Number of gallery threads
--threads-images   Number of image threads per gallery
--exclude-tags     Comma-separated tags to skip
--include-tags     Comma-separated tags to require
--language         Language filter
--use-tor          Route requests through Tor
--use-vpn          Use system VPN
--verbose          Enable detailed logging
```

### Examples
```bash
# Default run (latest galleries)
python3 scraper.py

# Specify a gallery range
python3 scraper.py --start 500000 --end 500100

# Custom root and threads
python3 scraper.py --root /mnt/nhentai --start 600000 --end 600050 --threads-galleries 5 --threads-images 10

# Use Tor and exclude certain tags
python3 scraper.py --use-tor --exclude-tags "yaoi,shotacon"
```

---

## Systemd Services

After installation, services are automatically enabled and started:

```bash
sudo systemctl daemon-reload
sudo systemctl enable nhentai-scraper suwayomi filebrowser
sudo systemctl start nhentai-scraper suwayomi filebrowser
```

- Scraper autostarts and monitors progress.  
- FileBrowser provides web access to downloaded galleries.  
- Suwayomi server provides GraphQL library updates.

---

## Web Access Links

Installer prints clickable links:

| Service | Default URL (IP) | Hostname URL |
|---------|-----------------|--------------|
| Suwayomi Web | `http://<SERVER_IP>:4567/` | `http://<HOSTNAME>:4567/` |
| Suwayomi GraphQL | `http://<SERVER_IP>:4567/api/graphql` | `http://<HOSTNAME>:4567/api/graphql` |
| FileBrowser | `http://<SERVER_IP>:8080/` | `http://<HOSTNAME>:8080/` |
| Scraper Flask status | `http://<SERVER_IP>:5000/scraper_status` | `http://<HOSTNAME>:5000/scraper_status` |

---

## Flask Monitoring Endpoint

- URL: `http://<SERVER_IP>:5000/scraper_status`
- JSON output example:
```json
{
  "last_run": "2025-08-24T02:00:00",
  "success": true,
  "downloaded": 15,
  "skipped": 3,
  "error": null
}
```

---

## Notes
- Installer handles all dependencies and autostart services.  
- Update mode safely pulls latest commits for Scraper, RicterZ nhentai, and Suwayomi without erasing downloaded galleries.  
- Uninstall mode fully removes scraper, Suwayomi, RicterZ repo, FileBrowser DB, and systemd services.  
- `config.json` is the main configuration; CLI args override values.  
- No JSON comments are used in the actual config — explanations are in this README.