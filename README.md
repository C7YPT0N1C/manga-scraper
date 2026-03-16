# manga-scraper

- [**Overview and Disclaimer**](#overview-and-disclaimer)
- [**Related Repository**](#related-repository)
- [**Project Note (Vibecoding Disclaimer)**](#project-note-vibecoding-disclaimer)
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
manga-scraper is a Python scraper for a range of manga / doujin sites (such as Mangadex, nhentai), with extensions to extend functionality.

Uses **[Filebrowser](https://github.com/filebrowser/filebrowser)** for remote file access from your browser! **Please go support them!**

The **[Suwayomi](https://github.com/Suwayomi/Suwayomi-Server)** Extension automatically installs Suwayomi, creates a category for scraped galleries and adds them to it.

This project supports two main ways of working:
- CLI mode for automated scraping jobs.
- Dashboard mode for easier browser-based control (runtime, search, queue, logs, and local gallery browsing).

## Related Repository
The companion extension repository is:

- **[manga-scraper-extensions](https://github.com/C7YPT0N1C/manga-scraper-extensions)**

Use that repository to review extension templates, available extension modules, and extension manifest details.

## Project Note (Vibecoding Disclaimer)
Parts of this project are vibecoded.

That said, there have also been significant manual edits and ongoing maintenance done by the project owner. In particular, the dashboard is basically fully vibecoded, while core behaviour, fixes, and refinements have also been manually reviewed and adjusted by hand over time.

**DISCLAIMERS:**
- This project is intended for local use only. Do not expose it directly to the public internet. **These scripts run as root, so use at your own risk.**
- A Windows version is not currently planned.

## Features
- Core scraping workflows for nhentai and extension-based sources
- Automation-focused CLI with query/filter/output controls
- Dashboard for configuration, searching, queue management, runtime status, local gallery browsing, and logs
- Extension support (install, remove, and switch active extension)
- Output format support: `directory`, `zip`, and `cbz`
- Multi-threaded downloads with retry handling
- Disk space estimation and run-time space checks
- Optional Tor/VPN-friendly networking configuration
- Integrated ecosystem support for Filebrowser and Suwayomi

## Important Notes and Known Pitfalls
- Suwayomi is NOT a mass downloader, saving large amounts of galleries will make it tweak out if the server doesn't have enough resources.
- Additional improvements are planned.

## Installation
### System Requirements
- OS: `Ubuntu / Linux server or VM`
- RAM: `Recommended: ~4GB (scale based on need)`
- Storage: **`A typical doujin is ~16MB; plan capacity accordingly.`**
- Optional: `chafa` (for interactive "Read a gallery" mode)

### Installation Commands
One Line Install: `wget -O mangascraper-install.sh https://github.com/C7YPT0N1C/manga-scraper/raw/branch/main/mangascraper-install.sh && sudo bash ./mangascraper-install.sh --install`

Alternative Install: Clone Repository.
```bash
# Clone the repository
git clone https://github.com/C7YPT0N1C/manga-scraper.git
cd manga-scraper

# Run the installer script
chmod +x mangascraper-install.sh
./mangascraper-install.sh

```

- Install: `mangascraper-install.sh (--install is optional)`
- Update Environment Variables: `mangascraper-install.sh --update-env` (legacy option; please avoid using this command or editing the .env file manually.)
- Update: `mangascraper-install.sh --update`
- Uninstall: `mangascraper-install.sh --uninstall (or --remove)`

## Post Install
- FileBrowser available at: `http://<SERVER-IP-OR-DOMAIN>:8080/`
  - User: `admin`
  - Password created on install.
    - You can change the password at any time using `filebrowser users update admin --password "PASSWORD"` --database /opt/filebrowser/filebrowser.db --perm.admin
- Suwayomi Webpage available at: `http://<SERVER-IP-OR-DOMAIN>:4567/`
- Suwayomi GraphQL Page available at: `http://<SERVER-IP-OR-DOMAIN>:4567/api/graphql` (primarily useful for development and debugging).

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