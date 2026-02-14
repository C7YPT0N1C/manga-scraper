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
- Some flags now have prettier aliases. Old flags still work, but show a deprecation warning.

Quick overview of common flags:
- Installer: `--install`, `--update`, `--update-env`, `--uninstall`
- Extensions: `--ext`, `--install-extension`, `--uninstall-extension`
- Sources: `--input`, `--id-range`, `--ids`, `--homepage`, `--latest`, `--popular`, `--popular-today`, `--popular-week`
- Filters: `--excluded-tags`, `--language`, `--title-type`
- Output: `--output-format`
- Runtime: `--use-tor`, `--skip-post-batch`, `--skip-post-run`, `--dry-run`, `--calm`, `--debug`

Deprecated aliases:
- `--file` -> `--input`
- `--range` -> `--id-range`
- `--galleries` -> `--ids`
- `--format` -> `--output-format`
- `--mirrors` -> `--mirror-urls`
- `--extension` -> `--ext`

Full `--help` output:
```text
usage: manga-scraper [-h] [--install] [--update] [--update-env] [--uninstall]
                     [--install-extension INSTALL_EXTENSION]
                     [--uninstall-extension UNINSTALL_EXTENSION]
                     [--ext EXTENSION] [--mirror-urls MIRRORS] [--input [FILE]]
                     [--id-range START END] [--ids GALLERIES]
                     [--homepage ARGS [ARGS ...]] [--latest [START [END]]]
                     [--popular [START [END]]] [--popular-today [START [END]]]
                     [--popular-week [START [END]]]
                     [--artist ARGS [ARGS ...]] [--group ARGS [ARGS ...]]
                     [--tag ARGS [ARGS ...]] [--character ARGS [ARGS ...]]
                     [--parody ARGS [ARGS ...]] [--search ARGS [ARGS ...]]
                     [--archive ARGS [ARGS ...]] [--archive-all]
                     [--excluded-tags EXCLUDED_TAGS] [--language LANGUAGE]
                     [--title-type {english,japanese,pretty}]
                     [--output-folder OUTPUT_FOLDER]
                     [--output-format {directory,zip,cbz}]
                     [--threads-galleries THREADS_GALLERIES]
                     [--threads-images THREADS_IMAGES] [--max-retries MAX_RETRIES]
                     [--min-sleep MIN_SLEEP] [--max-sleep MAX_SLEEP] [--use-tor]
                     [--skip-post-batch] [--skip-post-run] [--dry-run]
                     [--calm | --debug]

Manga scraper CLI

options:
  -h, --help            show this help message and exit

Installer / updater:
  --install             Install manga-scraper and dependencies (default: False)
  --update              Update manga-scraper (default: False)
  --update-env          Update the .env file (default: False)
  --uninstall, --remove
                        Uninstall manga-scraper (default: False)

Extensions:
  --install-extension INSTALL_EXTENSION
                        Install an extension by name (default: None)
  --uninstall-extension UNINSTALL_EXTENSION
                        Uninstall an extension by name (default: None)
  --ext EXTENSION       Extension to use (default: skeleton)

Gallery selection:
  --mirror-urls MIRRORS
                        Comma-separated list of NHentai mirror URLs (default: https://i.nhentai.net)
  --input [FILE]        Path to a file containing gallery URLs or IDs (one per line)
                        (default: /root/Doujinshi_IDs.txt)
  --id-range START END  Gallery ID range to download (default: None)
  --ids GALLERIES       Comma-separated gallery IDs to download (default: None)
  --homepage ARGS [ARGS ...]
                        Homepage selection: [SORT] [START] [END]. SORT: date|recent|popular_today|popular_week|popular|all_time. (default: None)
  --latest [START [END]]
                        Homepage latest (recent). Optional START END or END only. (default: None)
  --popular [START [END]]
                        Homepage popular (all time). Optional START END or END only. (default: None)
  --popular-today [START [END]]
                        Homepage popular today. Optional START END or END only. (default: None)
  --popular-week [START [END]]
                        Homepage popular this week. Optional START END or END only. (default: None)
  --artist ARGS [ARGS ...]
                        Download by artist. Usage: --artist NAME [SORT] [START] [END] [ARCHIVE]. Repeatable. (default: None)
  --group ARGS [ARGS ...]
                        Download by group. Usage: --group NAME [SORT] [START] [END] [ARCHIVE]. Repeatable. (default: None)
  --tag ARGS [ARGS ...]
                        Download by tag. Usage: --tag NAME [SORT] [START] [END] [ARCHIVE]. Repeatable. (default: None)
  --character ARGS [ARGS ...]
                        Download by character. Usage: --character NAME [SORT] [START] [END] [ARCHIVE]. Repeatable. (default: None)
  --parody ARGS [ARGS ...]
                        Download by parody. Usage: --parody NAME [SORT] [START] [END] [ARCHIVE]. Repeatable. (default: None)
  --search ARGS [ARGS ...]
                        Download by search query. Usage: --search QUERY [SORT] [START] [END] [ARCHIVE]. Repeatable. (default: None)
  --archive ARGS [ARGS ...]
                        Like --search, but downloads every gallery in the results. (default: None)
  --archive-all         Archive everything from NHentai (all homepage pages). (default: False)

Filters:
  --excluded-tags EXCLUDED_TAGS
                        Comma-separated list of tags to exclude galleries (default: snuff,cuntboy,guro,cuntbusting,scat,coprophagia,ai generated,vore,miniguy)
  --language LANGUAGE   Comma-separated list of languages to include (default: english)
  --title-type {english,japanese,pretty}
                        Title type to use (default: english)

Output:
  --output-folder OUTPUT_FOLDER
                        Override the download folder for this run (default: None)
  --output-format {directory,zip,cbz}
                        Output format for downloaded galleries (default: directory)

Performance:
  --threads-galleries THREADS_GALLERIES
                        Number of concurrent gallery downloads (default: 2)
  --threads-images THREADS_IMAGES
                        Number of concurrent image downloads per gallery (default: 10)
  --max-retries MAX_RETRIES
                        Maximum retry attempts for failed downloads (default: 3)
  --min-sleep MIN_SLEEP
                        Minimum sleep before starting a new download (default: 0.5)
  --max-sleep MAX_SLEEP
                        Maximum sleep before starting a new download (default: 50.0)

Runtime:
  --use-tor             Use TOR network for downloads (default: True)
  --skip-post-batch     Skip periodic post-batch actions (default: False)
  --skip-post-run       Skip post-run actions (default: False)
  --dry-run             Simulate downloads without saving files (default: False)

Logging:
  --calm                Enable calm logging (default: False)
  --debug               Enable debug logging (default: False)
```

### Examples
```bash
# Default run (latest galleries)
manga-scraper

# Specify a gallery range
manga-scraper --id-range 500000 500100

# Custom thread count
manga-scraper --id-range 600000 600050 --threads-galleries 5 --threads-images 10

# Homepage shortcuts
manga-scraper --latest 1 3
manga-scraper --popular-week 1 2

# Use the Suwayomi Extension, download galleries from artist "XYZ" (default page range, 1 - 10) and of tag "uncensored" from pages 1 - 10 (explicitly declared), excluding certain tags, using a certain language and using Tor
manga-scraper --ext suwayomi --artist "XYZ" --tag "uncensored" 1 10 --excluded-tags "snuff, lolicon, shotacon" --use-tor

# Output format
manga-scraper --ids "123456,654321" --output-format cbz

# Override output folder
manga-scraper --output-folder /mnt/storage --ids "123456,654321"
```

## Documentation
### Quickstart
- Install with the script, then run `manga-scraper` to fetch the default homepage range.
- Downloads go to `/opt/manga-scraper/downloads` unless your extension overrides the path.

### Configuration
- The config file is `/opt/manga-scraper/manga-scraper.env`.
- CLI flags override config values for that run and update the env file.
- Common keys: `EXTENSION`, `NHENTAI_MIRRORS`, `GALLERY_FORMAT`, `THREADS_GALLERIES`, `THREADS_IMAGES`.

### Gallery Selection
- Use `--input` with a file containing IDs or NHentai URLs (one per line).
- Use `--homepage` or the shortcut flags (`--latest`, `--popular`, `--popular-today`, `--popular-week`).
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