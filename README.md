# nhentai-scraper

## Overview
nhentai-scraper is a fully-featured Python scraper for nhentai.net that downloads galleries, supports multi-artist/group galleries, saves Suwayomi-compatible metadata, and can run as a systemd service.

## Features
- Gallery download with language/tag filters
- Multi-artist/group duplication
- Suwayomi metadata generation
- Tor/VPN support
- Flask monitoring endpoint
- Systemd service & timer
- Retry failed/skipped galleries
- Configurable root folder

## Notes
- Supports multi-threaded downloads
- Automatic retry of skipped galleries
- Metadata compatible with Suwayomi
- Dynamic IP / DNS friendly (use DDNS if monitoring remotely)
- Fully compatible with dynamic IPs for outbound connections.
- Use a Dynamic DNS service for remote Flask monitoring if IP is not static.

## Installation
### System Requirements
- Linux server / VM
- Python 3.x
- pip
- Optional: [Suwayomi-server](https://github.com/Suwayomi/Suwayomi-Server)
- Optional: Tor (`sudo apt install tor`) or VPN (OpenVPN, WireGuard)

### Python Modules
```
pip install requests[socks] beautifulsoup4 flask
```

## Directory Layout
```
nhentai-scraper/
├─ nhentai-scraper.py
├─ README.md
├─ .gitignore
├─ systemd/
│   ├─ nhentai-scraper.service
│   └─ nhentai-scraper.timer
```
Galleries saved as:
```
ARTIST_FOLDER/
└─ DOUJIN_FOLDER/
   ├─ 1.jpg
   ├─ 2.jpg
   └─ metadata.json
```
Suwayomi metadata format:
```json
{
  "title": "AUTHOR_NAME",
  "author": "AUTHOR_NAME",
  "artist": "AUTHOR_NAME",
  "description": "An archive of AUTHOR_NAME's works.",
  "genre": ["tags_here"],
  "status": "0",
  "_status values": ["0=Unknown","1=Ongoing","2=Completed","3=Licensed"]
}
```

## Usage
### CLI Arguments
```bash
--root             Root folder (default: /opt/suwayomi/local/)
--start            Start gallery ID (default: last progress +1)
--end              End gallery ID (default: latest ID)
--threads-galleries Number of gallery threads (default: 3)
--threads-images   Number of image threads per gallery (default: 5)
--exclude-tags     Comma-separated tags to skip (default: snuff,guro,cuntboy,cuntbusting,ai generated)
--include-tags     Comma-separated tags to require
--language         Language filter (default: english)
--use-tor          Route requests through Tor
--use-vpn          Use system VPN
--verbose          Enable detailed logging
```
### Examples
```bash
# Default run (latest galleries)
python3 nhentai-scraper.py

# Specify a gallery range
python3 nhentai-scraper.py --start 500000 --end 500100

# Custom root and threads
python3 nhentai-scraper.py --root /mnt/nhentai --start 600000 --end 600050 --threads-galleries 5 --threads-images 10

# Use Tor and exclude certain tags
python3 nhentai-scraper.py --use-tor --exclude-tags "yaoi,shotacon"

```

## Configuration
- `DEFAULT_ROOT` — default `/opt/suwayomi/local/`, can be overridden via CLI
- `LANGUAGE_FILTER`, `EXCLUDED_TAGS`, `INCLUDE_TAGS`
- VPN/Tor toggles
- Suwayomi GraphQL endpoint

## Systemd Service
Enable service and optional timer:
```bash
sudo systemctl daemon-reload
sudo systemctl enable nhentai-scraper.service
sudo systemctl start nhentai-scraper.service
sudo systemctl enable nhentai-scraper.timer
sudo systemctl start nhentai-scraper.timer
```

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