# manga-scraper

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
## Overview and Disclaimer
manga-scraper is a Python scraper for a range of manga / doujin sites (such as Mangadex, nhentai), with extensions to extend functionality. Uses **[Filebrowser](https://github.com/filebrowser/filebrowser)** for remote file access from your browser! **Please go support them!**

The **[Suwayomi](https://github.com/Suwayomi/Suwayomi-Server)** Extension automatically installs Suwayomi, creates a category for scraped galleries and adds them to it.

**DISCLAIMERS:**
- This is for local use ONLY. Do NOT try to forward this over the internet. **These scripts run as root, so use at your own risk.**
- A windows version is ***probably*** never going to happen, I'll be so honest

## Features
- [Filebrowser Features](https://github.com/filebrowser/filebrowser)
- [Suwayomi Features](https://github.com/Suwayomi/Suwayomi-Server?tab=readme-ov-file#what-is-suwayomi)
- Multi-threaded gallery downloads with tag/language filters
- Automatic retry of failed/skipped galleries
- Tor / VPN support
- `MORE COMING SOON`

## Important Notes and Known Pitfalls
- Suwayomi is NOT a mass downloader, saving large amounts of galleries will make it tweak out if the server doesn't have enough resources.
- I'll add other shit later lmfaooooo

## Installation
### System Requirements
- OS: `Ubuntu / Linux server or VM`
- RAM: `Recommended: ~4GB (scale based on need)`
- Storage: **`1 Doujin is ~16MB, so you do the math.`**

### Installation Commands
One Line Install: `wget -O mangascraper-install.sh https://git.anthrosys.online/C7YPT0N1C/manga-scraper/raw/branch/main/mangascraper-install.sh && sudo bash ./mangascraper-install.sh --install`

Alternative Install: Clone Repository.
```bash
# Clone the repository
git clone https://git.anthrosys.online/C7YPT0N1C/manga-scraper.git
cd manga-scraper

# Run the installer script
chmod +x mangascraper-install.sh
./mangascraper-install.sh

```

- Install: `mangascraper-install.sh (--install is optional)`
- Update Environment Variables: `mangascraper-install.sh --update-env` (lowk idk why this is still here, please **don't** use this command or edit the .env file manually.)
- Update: `mangascraper-install.sh --update`
- Uninstall: `mangascraper-install.sh --uninstall (or --remove)`

## Post Install
- FileBrowser available at: `http://<SERVER-IP-OR-DOMAIN>:8080/`
  - User: `admin`
  - Password created on install.
    - You can change the password at any time using `filebrowser users update admin --password "PASSWORD"` --database /opt/filebrowser/filebrowser.db --perm.admin
- Suwayomi Webpage available at: `http://<SERVER-IP-OR-DOMAIN>:4567/`
- Suwayomi GraphQL Page available at: `http://<SERVER-IP-OR-DOMAIN>:4567/api/graphql` (idk why you'd need this aside for development lmfao)

## Usage
### CLI Arguments
- An environment file for the scraper `config.env` will be automatically created during installation and can be found at `/opt/manga-scraper/config.env`.

Quick overview of common flags:
- Installer: `--install`, `--update`, `--update-env`, `--uninstall`
- Extensions: `--extension`, `--install-extension`, `--uninstall-extension`
- Sources: `--mirrors`, `--file`, `--id-range`, `--ids`, `--homepage`, `--artist`, `--group`, `--tag`, `--character`, `--parody`, `--search`, `--archive`
- Filters: `--excluded-tags`, `--language`, `--title-type`
- Output: `--output-folder`, `--output-format`
- Runtime: `--use-tor`, `--skip-post-batch`, `--skip-post-run`, `--dry-run`, `--show-summary`
- Logging: `--calm`, `--debug`

Full `--help` output:
Run `manga-scraper --help` for the current argument list.

### Examples
```bash
# Default run (latest galleries)
manga-scraper

# Specify a gallery range
manga-scraper --id-range 500000 500100

# Custom thread count
manga-scraper --id-range 600000 600050 --threads-galleries 5 --threads-images 10

# Homepage
manga-scraper --homepage recent 1 3
manga-scraper --homepage popular_week 1 2

# Use the Suwayomi Extension, download galleries from artist "XYZ" (default page range, 1 - 10) and of tag "uncensored" from pages 1 - 10 (explicitly declared), excluding certain tags, using a certain language and using Tor
manga-scraper --extension suwayomi --artist "XYZ" --tag "uncensored" 1 10 --excluded-tags "snuff, lolicon, shotacon" --use-tor

# Output format
manga-scraper --ids "123456,654321" --output-format cbz

# Override output folder
manga-scraper --output-folder /mnt/storage --ids "123456,654321"
```

## Documentation
### Space Monitoring & Graceful Shutdown
The scraper automatically estimates the total download size before starting and checks available disk space:

**Features:**
- **Size Estimation**: Estimates total download size for all galleries based on image types (jpg, png, webp, gif)
- **Before Download**: Shows size estimate and available space before any downloads begin
- **Interactive Prompt**: If space is insufficient, shows how many galleries can fit and asks if you want to proceed
- **Progress Tracking**: Displays cumulative estimated vs actual sizes during download
- **Completion Report**: Shows total space used, average per gallery at the end of the run

**User Flow:**
1. Scraper estimates total size needed
2. Checks available disk space
3. If insufficient space: shows how many galleries fit, asks "Download X galleries? (y/n):"
4. User can proceed with partial download or cancel
5. Progress bar shows estimated vs actual bytes being used

**Examples:**
```bash
# Simple download - will estimate and check space automatically
manga-scraper --homepage recent 1 5

# Download specific galleries - estimates before starting
manga-scraper --ids "123456,654321,789012"

# Batch download - checks space at the beginning of each batch
manga-scraper --id-range 500000 500100
```

### Quickstart
- Install with the script, then run `manga-scraper` to fetch the default homepage range.
- Downloads go to `/opt/manga-scraper/downloads` unless your extension overrides the path.

### Configuration
- The config file is `/opt/manga-scraper/manga-scraper.env`.
- CLI flags override config values for that run and update the env file.
- Common keys: `EXTENSION`, `NHENTAI_MIRRORS`, `GALLERY_FORMAT`, `THREADS_GALLERIES`, `THREADS_IMAGES`.

### Gallery Selection
- Use `--file` with a file containing IDs or NHentai URLs (one per line).
- Use `--homepage` with an optional sort and page range.
- Query flags (`--artist`, `--group`, `--tag`, `--character`, `--parody`, `--search`) accept an optional sort and page range.

### Output Formats
- `directory`: keeps the gallery as a folder of images.
- `zip` or `cbz`: archives the gallery and removes the original folder after post-processing.

### Extensions
- Install or remove extensions with `--install-extension` and `--uninstall-extension`.
- Extensions live under `mangascraper/extensions/` and can define hooks like pre/post-download handlers.

### Troubleshooting
- Check logs in `/tmp/manga-scraper/logs` for detailed errors.
- If downloads fail, try lowering threads or adding mirror URLs.
- If you see permission errors, verify paths and run with appropriate privileges.