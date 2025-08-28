# nhentai-scraper

- [**Overview and Disclaimer**](#overview-and-disclaimer)
- [**Features**](#features)
  - [Important Notes and Known Pitfalls](#important-notes-and-known-pitfalls)
  - [**TO-DO LIST**](#to-do-list)
- [**Installation**](#installation)
  - [System Requirements](#system-requirements)
  - [Installation Commands](#installation-commands)
- [**Post Install**](#post-install)
- [**Usage**](#usage)
  - [CLI Arguments](#cli-arguments)
  - [Examples](#examples)
- [**Documentation**](#documentation)
  - [High Level Flow](#high-level-flow)
  - [Directory Layout](#directory-layout)
  - [Systemd Services](#systemd-services)
  - [GraphQL API Queries](#graphql-api-queries)
  - [Flask Monitoring Endpoint](#flask-monitoring-endpoint)

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

## Important Notes and Known Pitfalls
- Downloads are skipped if a gallery folder exists and **every page has at least one valid image file**. (Bug fix required: see below.)
- When running with `threads_galleries>1`, terminal progress bars (`tqdm`) can interfere. The code can switch off progress bars automatically for multi-galley concurrency.
- Logging is verbose by default (DEBUG). `--verbose` currently sets the log level as an override; code checks `config["dry_run"]` before network or write actions.
- GraphQL calls are verified: success is detected by checking `data` returned and `errors` entries.

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
### High level flow
1. `cli.py` is the user entrypoint.
2. `config.py` parses CLI flags + `.env`, sets `config` and builds the gallery list.
3. `downloader.py` runs `main()` — iterates gallery IDs and calls `process_gallery()`.
4. `nhscraper_api.py` contains shared network utilities, indexing state and the Flask status API.
5. `graphql_api.py` contains calls to Suwayomi via GraphQL.

Possible alternative integration?:
- Suwayomi extension → POST to `nhscraper_api` endpoint (e.g. `/import`) → triggers `downloader`/`graphql_api` flows to import galleries and update the Suwayomi library.


### Folder Structure
```
nhentai-scraper/
├─ core/
│  ├─ __init__.py
│  ├─ config.py
│  ├─ logger.py
│  ├─ db.py
│  ├─ downloader.py
│  └─ fetchers.py
├─ extensions/
│  ├─ __init__.py
│  ├─ extension_loader.py
│  └─ skeleton/
│     └─ skeleton__nhsext.py
├─ api.py
└─ cli.py

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

### TO-DO LIST
(Basically just me looking at features / everything in the readme and make sure it's accurate lol)

- [ ] Look over / rewrite code
- [x] Make sure tags with multiple words (e.g. big ass) parse properly
- [x] Ensure variables are formatted correctly (env file and the use of quotes around variables, "cookie" vs cookie)
- [ ] Add gallery download with language/tag filters
- [ ] Clean gallery download output folder (artist - doujin title)
- [ ] Sort downloads by artists **and** groups if there are no artists.
- [x] Add multi-threaded download support
- [x] Implement automatic retry of failed/skipped galleries
- [x] Add Flask monitoring endpoint

Suwayomi:
- [ ] Use GraphQL to automatically change Suwayomi settings on install
- [ ] Sort downloaded galleries into Suwayomi categories by tags)
- [x] Generate Suwayomi metadata automatically
- [ ] Automatically create a category in Suwayomi for each gallery tag/genre if it doesn't already exist
- [ ] Assign galleries to corresponding categories based on their tags
- [ ] Allow manual reordering of categories or extend logic to sort by number of galleries per category
- [ ] Exclude tags listed in `SUWAYOMI_IGNORED_CATEGORIES` (default: `Favourites, Favs`) from automated categories

Other Tasks:
- [x] Implement automatic cookie updates?
- [ ] Allow users to select specific VPN server (via launch argument)?
- [ ] Add more launch arguments / API endpoints
- [ ] Improve documentation
- [ ] Add better debugging (e.g., reasons for gallery skip)
- [ ] New workflow?: Suwayomi extension → `nhscraper_api.py` request → `config.py` / `downloader.py` / `graphql_api.py` functions → Import to Suwayomi Library?