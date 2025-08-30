# nhentai-scraper

- [**Overview and Disclaimer**](#overview-and-disclaimer)
- [**Features**](#features)
  - [**TO-DO LIST**](#to-do-list)
- [**Installation**](#installation)
  - [System Requirements](#system-requirements)
  - [Installation Commands](#installation-commands)
- [**Post Install**](#post-install)
- [**Usage**](#usage)
  a- [CLI Arguments](#cli-arguments)
  - [Examples](#examples)
- [**Documentation**](#documentation
  - [Directory Layout](#directory-layout)
  - [Systemd Services](#systemd-services)
  - [GraphQL API Queries](#graphql-api-queries)
  - [Flask Monitoring Endpoint](#flask-monitoring-endpoint)
  - [Data Flow Diagram](#data-flow-diagram)

## Overview and Disclaimer
nhentai-scraper is a fully-featured Python scraper for **nhentai** that downloads galleries, supports multi-artist/group galleries. Has its own API running as a systemd service.

Automatically creates **[Suwayomi](https://github.com/Suwayomi/Suwayomi-Server)** categories based on gallery tags, assigns galleries to their corresponding categories and uses **[Filebrowser](https://github.com/filebrowser/filebrowser)** for remote file access from your browser! **Please go support them!**

**DISCLAIMERS:**
- This is for local use ONLY. These scripts run as root and with the exception of the installer, nhentai's API, and Suwayomi doesn't reach out to the internet. **Use at your own risk.**
- A lot of this code was originally written by ChatGPT because the original project was supposed to be just for me (I was lazy) and I started this at 2am. However, now that I am releasing this to the public, I have gone over the code line by line myself and have check it thoroughly. Still, use this at your own discretion.

## Features
- [Suwayomi Features](https://github.com/Suwayomi/Suwayomi-Server?tab=readme-ov-file#what-is-suwayomi)
- [Filebrowser Features](https://github.com/filebrowser/filebrowser)
- Gallery download with language/tag filters (multi-threaded download support)
- Automatic retry of failed/skipped galleries
- Tor/VPN support
- `COMING SOON` Downloads sorted by artists and groups (downloads sorted by genres via Suwayomi)
  - Each gallery tag/genre is automatically created as a category in Suwayomi if it doesn't already exist.
  - Galleries are assigned to the corresponding categories based on their tags.
  - Categories can be reordered manually or later extended with logic to sort by the number of galleries per category.
  - Tags listed in `SUWAYOMI_IGNORED_CATEGORIES` (default: `Favs`) are not created as automated categories.
- `COMING SOON` Automatic Suwayomi metadata generation
- Flask monitoring endpoint (Systemd service)

## Installation
### System Requirements
- OS: `Ubuntu / Linux server or VM`
- RAM (scale based on need): `Minimum 2GB, Recommended 4GB or More.`
- Storage: `UNKNOWN`
  - **WARNING: I honest haven't tested this yet so please do assume you're going to need a *LOT* of storage, especially if you're not limiting the range of gallery IDs.**
- **REQUIRED**: `Python 3.9+, pip`
- **Optional**: `Tor (installed automatically) or VPN (OpenVPN, WireGuard)`

### Installation Commands
One Line Install: `wget -O nhscraper-install.sh https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper/raw/branch/dev/nhscraper-install.sh && sudo bash ./nhscraper-install.sh --install`

Alternative Install: Clone Repository.
```bash
# Clone the repository
git clone https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git
cd nhentai-scraper

# Run the installer script
chmod +x nhscraper-install.sh
./nhscraper-install.sh

```

- Install: `nhscraper-install.sh (--install is optional)`
- Update Environment Variables: `nhscraper-install.sh --update-env`
- Update: `nhscraper-install.sh --update`
- Uninstall: `nhscraper-install.sh --uninstall (or --remove)`

## Post Install
- Suwayomi Webpage available at: `http://<SERVER-IP-OR-DOMAIN>:4567/`
- Suwayomi GraphQL Page available at: `http://<SERVER-IP-OR-DOMAIN>:4567/api/graphql`
- FileBrowser available at: `http://<SERVER-IP-OR-DOMAIN>:8080/`
  - User: `admin`
  - Password created on install.
    - You can change the password at any time using `filebrowser users update admin --password "PASSWORD"` --database /opt/filebrowser/filebrowser.db --perm.admin

## Usage
### CLI Arguments
- An environemt file for the scraper `config.env` will be automatically created during installation and can be found at `/opt/nhentai-scraper/config.env`.
```bash
usage: nhentai-scraper [-h] [--start START] [--end END] [--excluded-tags EXCLUDED_TAGS] [--language LANGUAGE]
                       [--title-type {english,japanese,pretty}] [--threads-galleries THREADS_GALLERIES]
                       [--threads-images THREADS_IMAGES] [--use-tor] [--dry-run] [--verbose]

NHentai scraper with Suwayomi integration

options:
  -h, --help            show this help message and exit
  
  --start START         Starting gallery ID (Default: 592000)
  --end END             Ending gallery ID (Default: 600000
  --excluded-tags EXCLUDED_TAGS
                        Comma-separated list of tags to exclude galleries (Default: empty)
  --language LANGUAGE   Comma-separated list of languages to include (Default: english)
  --title-type {english,japanese,pretty}
                        Gallery title type for folder names (Default: pretty)
  --threads-galleries THREADS_GALLERIES
                        Number of concurrent galleries (Default: 1)
  --threads-images THREADS_IMAGES
                        Threads per gallery (Default: 4)
  --use-tor             Route requests via Tor (Default: false)
  --dry-run             Simulate downloads and GraphQL without saving (Default: false)
  --verbose             Enable debug logging (Default: false)
```

### Examples
```bash
# Default run (latest galleries) (assuming cookie already set)
nhentai-scraper

# Specify a gallery range
nhentai-scraper --start 500000 --end 500100

# Custom thread count
nhentai-scraper --start 600000 --end 600050 --threads-galleries 5 --threads-images 10

# Exclude certain tags, use a certain language and use Tor
nhentai-scraper --exclude-tags yaoi, shotacon --use-tor
```

## Documentation
### Folder Structure
```
nhentai-scraper/
├─ core/
│  ├─ __init__.py
│  ├─ config.py
│  ├─ db.py
│  ├─ downloader.py
│  ├─ fetchers.py
│  └─ logger.py
├─ extensions/
│  ├─ __init__.py
│  ├─ extension_loader.py
│  ├─ manifest.json
│  └─ [EXTENSION NAME]/
│     ├─ __init__.py
│     └─ [EXTENSION NAME]__nhsext.py
├─ __init__.py
├─ api.py
├─ cli.py
└─ nhscraper-install.sh
```

### Systemd Services

**I'd rather you kept everything (including Tor) turned on to be honest.**

Run `sudo systemctl daemon-reload` if service files have been manually changed.

- Suwayomi: `sudo systemctl start|stop suwayomi`
- Filebrowser: `sudo systemctl start|stop filebrowser`
- Nhentai Scraper: `sudo systemctl enable|start|stop nhentai-api`

### GraphQL API Queries
- Info will be added T-T...

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

### Data Flow Diagram:

The basics of the flow of data is: (CLI → Config → Downloader → Extensions → Output)

#### Installer Script: nhscraper-install.sh
- Checks root privileges
- Installs system packages (Python3, pip, git, tor, etc.)
- Clones/updates nhentai-scraper repo
- Creates virtual environment + installs Python dependencies
- Creates default .env with all CLI flag defaults:
  - range, galleries, artist, group, tag, parody
  - excluded-tags, language, title-type, title-sanitise
  - threads-galleries, threads-images, use-tor, dry-run, verbose
  - extension_download_path (default: "")
- Installs nhentai-scraper API systemd service
- Installs core extensions if flagged:
  - Calls install_extension() in extension module
  - Creates any required systemd services (e.g., suwayomi-server)
- Installer can also:
  - `--update`: fully overwrite files + dependencies
  - `--update-env`: update .env values individually
  - `--uninstall`: remove nhentai-scraper + services + venv
  - `--install-extension` / `--uninstall-extension`

#### CLI: nhscraper/cli.py
- Parses arguments:
  - `--extension` (select extension or 'none')
  - `--range` (start/end gallery IDs)
  - `--galleries` (list of gallery IDs)
  - `--artist/group/tag/parody` + start/end pages
  - `--excluded-tags`, `--language`
  - `--title-type`, `--title-sanitise`
  - `--threads-galleries`, `--threads-images`
  - `--use-tor`, `--dry-run`, `--verbose`
- Initializes config from CLI args + .env
- Determines active galleries list (combined from all flags)
- Sets EXTENSION_DOWNLOAD_PATH if extension is active

#### Config Loader: core/config.py
- Reads default .env values
- Updates config dictionary with CLI args overrides
- Provides helper functions:
  - `get_download_path()` returns NHENTAI_DIR/downloads
  - `get_extension_path()` returns EXTENSION_DOWNLOAD_PATH if set
- Ensures dynamic path resolution for extensions

#### Downloader: core/downloader.py
- Reads active gallery list
- Pre-download hooks (extensions)
- Iterates galleries using threads-galleries:
  - Fetch gallery metadata via NHentai API
  - Apply filters: language, excluded-tags
  - Resolve download folder: EXTENSION_DOWNLOAD_PATH > default DOWNLOAD_PATH
  - Pretty titles sanitized if enabled
  - Download images using threads-images
  - During-download hooks (extensions)
  - After-gallery-download hooks (extensions)
- After-all-downloads hooks (extensions)
- Post-download hooks (extensions, resets EXTENSION_DOWNLOAD_PATH)

#### Extension Loader: core/extension_loader.py
- Dynamically imports installed extensions
- Exposes pre_download_hook, during_download_hook, after_gallery_download, after_all_downloads, post_download_hook
- Extensions can:
  - Override EXTENSION_DOWNLOAD_PATH
  - Create metadata files
  - Create additional systemd services
  - Respect dry-run mode

#### NHentai API / Metadata Fetchers: core/fetchers.py
- `fetch_gallery_metadata(gallery_id, use_tor)`: requests gallery info
- Returns dict: title_pretty, title_english, title_japanese, images, artists, tags, language
- `download_image(url, target_path, use_tor)`: downloads image respecting dry-run

#### Extensions: extensions/
- Skeleton (skeleton__nhsext.py): example hooks, install/uninstall
- Suwayomi (suwayomi__nhsext.py):
  - Sets EXTENSION_DOWNLOAD_PATH
  - Generates metadata JSON per gallery
  - Creates suwayomi-server.service
  - Handles dry-run
  - Install/uninstall functions

#### API / Flask Monitoring: nhscraper/api.py
- Starts systemd-enabled service
- Exposes status endpoint `/scraper_status`
- Reports last_run, success/error, downloaded/skipped galleries
- Can interact with extensions hooks

#### FileBrowser / Suwayomi / Other Integrations
- Suwayomi reads metadata JSON
- Galleries categorized by tags
- FileBrowser provides web access to galleries

#### Uninstallation / Cleanup
- Installer with `--uninstall`:
  - Stops nhentai-scraper API service
  - Removes venv + repo files
  - Removes systemd services
  - Optionally cleans extension folders
- Extensions uninstalled separately via `--uninstall-extension <name>`