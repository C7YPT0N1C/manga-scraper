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

## Installation
### System Requirements
- Linux server / VM
- Python 3.x
- pip
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
```bash
# Run with default root folder
python3 nhentai-scraper.py

# Specify root folder
python3 nhentai-scraper.py /opt/suwayomi/local/

# Specify root folder and max gallery ID
python3 nhentai-scraper.py /opt/suwayomi/local/ 500000
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

## Dynamic IP/DNS
- Fully compatible with dynamic IPs for outbound connections.
- Use a Dynamic DNS service for remote Flask monitoring if IP is not static.

## Notes
- Supports multi-threaded downloads
- Automatic retry of skipped galleries
- Metadata compatible with Suwayomi
- Dynamic IP / DNS friendly (use DDNS if monitoring remotely)
