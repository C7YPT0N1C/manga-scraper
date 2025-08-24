# nhentai-scraper

## Overview and Disclaimer
nhentai-scraper is a fully-featured Python scraper for nhentai.net that downloads galleries, supports multi-artist/group galleries, automatically creates Suwayomi categories based on gallery tags, assigns galleries to their corresponding categories, and can run as a systemd service.

A lot of this code was originally written by ChatGPT because the original project was supposed to be just for me (I was lazy) and I started this at 2am. However, now that I am releasing this to the public, I have gone over the code line by line myself and have check it thoroughly. Still, use this at your own discretion.

## Features
- Gallery download with language/tag filters
- Downloads sorted by artists and groups (downloads sorted by genres via Suwayomi)
- Multi-threaded download supports
- Automatic retry of failed/skipped galleries
- Tor/VPN support
- Configurable Suwayomi root folder
- Automatic Suwayomi metadata generation
- Flask monitoring endpoint
- Systemd service & timer
- Dynamic IP / DNS friendly (use DDNS if monitoring remotely)

### Suwayomi Category Automation
- Each gallery tag/genre is automatically created as a category in Suwayomi if it doesn't already exist.
- Galleries are assigned to the corresponding categories based on their tags.
- Categories can be reordered manually or later extended with logic to sort by the number of galleries per category.
- Tags listed in `IGNORED_CATEGORIES` (default: `Favorites`) are not created as automated categories.

### Features Coming Soon:
- N/A?

## Installation
### System Requirements
- Linux server / VM
- Python 3.x
- pip
- Optional: Tor (`sudo apt install tor`) or VPN (OpenVPN, WireGuard)
- **WARNING - Storage: I honest haven't tested this yet so please do assume you're going to need a *LOT* of storage, especially if you're not limiting the range of gallery IDs.** 

### Installation Commands
- Install Command: `sudo ./install.sh`
- Update Command: `sudo ./install.sh update`
- Uninstall Command: `sudo ./install.sh uninstall`

## Post Install
- Suwayomi Webpage available at: `http://<SERVER-IP-OR-DOMAIN>:4567/`
- Suwayomi GraphQL Page available at: `http://<SERVER-IP-OR-DOMAIN>:4567/api/graphql`
- FileBrowser available at: `http://<SERVER-IP-OR-DOMAIN>:8080/`

### Systemd Service
Enable service and optional timer:
```bash
sudo systemctl daemon-reload
sudo systemctl enable nhentai-scraper.service
sudo systemctl start nhentai-scraper.service
sudo systemctl enable nhentai-scraper.timer
sudo systemctl start nhentai-scraper.timer
```

### Flask Monitoring Endpoint
- URL: `http://<SERVER-IP-OR-DOMAIN>:5000/scraper_status`
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

## Usage and Documentation
### CLI Arguments
- A config file for the scraper `config.json` will be automatically created after installation.
- `config.json` can be found at `/opt/nhentai-scraper/config.json`
- **WARNING: USE OF CLI ARGUMENTS WILL BE REFLECTED IN CONFIG.**
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

### Examples ()
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

## Directory Layout
Galleries (Suwayomi compatible) saved as:
```
ARTIST_FOLDER/
└─ DOUJIN_FOLDER/
   ├─ 1.jpg
   ├─ 2.jpg
   └─ details.json
```
Suwayomi metadata format:
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