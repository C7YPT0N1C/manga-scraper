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
## Overview and Disclaimer
nhentai-scraper is a fully-featured Python scraper for **nhentai**, with extensions to extend functionality. Uses **[Filebrowser](https://github.com/filebrowser/filebrowser)** for remote file access from your browser! **Please go support them!**

The Suwayomi Extension automatically creates **[Suwayomi](https://github.com/Suwayomi/Suwayomi-Server)** a category for scraped galleries and adds them to it.

**DISCLAIMERS:**
- This is for local use ONLY. These scripts run as root and with the exception of the installer, nhentai's API, and Suwayomi, doesn't reach out to the internet. **Use at your own risk.**
- A lot of this code was originally written by ChatGPT because the original project was supposed to be just for me (I was lazy) and I started this at 2am on some fuckoff Friday night.
However, now that I am releasing this to the public, I have gone over the code line by line myself and have checked it thoroughly. Still, use this at your own discretion.
- I will ***NEVER*** make a version for Windows (unless someone joins in to do the development for it) so don't even bother asking lmfao

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
One Line Install: `wget -O nhscraper-install.sh https://git.zenithnetwork.online/C7YPT0N1C/nhentai-scraper/raw/branch/main/nhscraper-install.sh && sudo bash ./nhscraper-install.sh --install`

Alternative Install: Clone Repository.
```bash
# Clone the repository
git clone https://git.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git
cd nhentai-scraper

# Run the installer script
chmod +x nhscraper-install.sh
./nhscraper-install.sh

```

- Install: `nhscraper-install.sh (--install is optional)`
- Update Environment Variables: `nhscraper-install.sh --update-env` (lowk idk why this is still here, please **don't** use this command or edit the .env file manually.)
- Update: `nhscraper-install.sh --update`
- Uninstall: `nhscraper-install.sh --uninstall (or --remove)`

## Post Install
- FileBrowser available at: `http://<SERVER-IP-OR-DOMAIN>:8080/`
  - User: `admin`
  - Password created on install.
    - You can change the password at any time using `filebrowser users update admin --password "PASSWORD"` --database /opt/filebrowser/filebrowser.db --perm.admin
- Suwayomi Webpage available at: `http://<SERVER-IP-OR-DOMAIN>:4567/`
- Suwayomi GraphQL Page available at: `http://<SERVER-IP-OR-DOMAIN>:4567/api/graphql` (idk why you'd need this aside for development lmfao)

## Usage
### CLI Arguments
- An environemt file for the scraper `config.env` will be automatically created during installation and can be found at `/opt/nhentai-scraper/config.env`.
```bash
usage: nhentai-scraper [-h] [--install] [--update] [--update-env] [--uninstall] [--install-extension INSTALL_EXTENSION]
                       [--uninstall-extension UNINSTALL_EXTENSION] [--extension EXTENSION] [--mirrors MIRRORS] [--file [FILE]] [--range START END]
                       [--galleries GALLERIES] [--homepage ARGS [ARGS ...]] [--artist ARGS [ARGS ...]] [--group ARGS [ARGS ...]] [--tag ARGS [ARGS ...]]
                       [--character ARGS [ARGS ...]] [--parody ARGS [ARGS ...]] [--search ARGS [ARGS ...]] [--archive-all] [--excluded-tags EXCLUDED_TAGS]
                       [--language LANGUAGE] [--title-type {english,japanese,pretty}] [--threads-galleries THREADS_GALLERIES]
                       [--threads-images THREADS_IMAGES] [--max-retries MAX_RETRIES] [--min-sleep MIN_SLEEP] [--max-sleep MAX_SLEEP] [--use-tor]
                       [--skip-post-batch] [--skip-post-run] [--dry-run] [--calm | --debug]

NHentai scraper CLI

options:
  -h, --help            show this help message and exit
  --install             Install nhentai-scraper and dependencies
  --update              Update nhentai-scraper
  --update-env          Update the .env file
  --uninstall, --remove
                        Uninstall nhentai-scraper
  --install-extension INSTALL_EXTENSION
                        Install an extension by name
  --uninstall-extension UNINSTALL_EXTENSION
                        Uninstall an extension by name
  --extension EXTENSION
                        Extension to use (default: skeleton)
  --mirrors MIRRORS     Comma-separated list of NHentai mirror URLs (default: https://i.nhentai.net). Use this if the main site is down or to rotate mirrors.
  --file [FILE]         Path to a file containing gallery URLs or IDs (one per line).If no path is given, uses the default file.
  --range START END     Gallery ID range to download (default: 500000-600000)
  --galleries GALLERIES
                        Comma-separated gallery IDs to download. Must be incased in quotes if multiple. (e.g. '123456, 654321')
  --homepage ARGS [ARGS ...]
                        Page range or sort type of galleries to download from NHentai Homepage (default: 1 - 10)
  --artist ARGS [ARGS ...]
                        Download galleries by artist. Usage: --artist ARTIST_NAME [SORT_TYPE (default: date)] [START_PAGE (default: 1)] [END_PAGE (default:
                        10)] [ARCHIVAL_BOOL (default: False)] Can be repeated.
  --group ARGS [ARGS ...]
                        Download galleries by group. Usage: --group GROUP_NAME [SORT_TYPE (default: date)] [START_PAGE (default: 1)] [END_PAGE (default: 10)]
                        [ARCHIVAL_BOOL (default: False)] Can be repeated.
  --tag ARGS [ARGS ...]
                        Download galleries by tag. Usage: --tag TAG_NAME [SORT_TYPE (default: date)] [START_PAGE (default: 1)] [END_PAGE (default: 10)]
                        [ARCHIVAL_BOOL (default: False)] Can be repeated.
  --character ARGS [ARGS ...]
                        Download galleries by character. Usage: --character CHARACTER_NAME [SORT_TYPE (default: date)] [START_PAGE (default: 1)] [END_PAGE
                        (default: 10)] [ARCHIVAL_BOOL (default: False)] Can be repeated.
  --parody ARGS [ARGS ...]
                        Download galleries by parody. Usage: --parody PARODY_NAME [SORT_TYPE (default: date)] [START_PAGE (default: 1)] [END_PAGE (default:
                        10)] [ARCHIVAL_BOOL (default: False)] Can be repeated.
  --search ARGS [ARGS ...]
                        Download galleries by search. Usage: --search SEARCH_QUERY [SORT_TYPE (default: date)] [START_PAGE (default: 1)] [END_PAGE (default:
                        10)] [ARCHIVAL_BOOL (default: False)] Can be repeated. You can search for multiple terms at the same time, and this will return only
                        galleries that contain both terms. For example, "anal tanlines" finds all galleries that contain both "anal" and "tanlines". You can
                        exclude terms by prefixing them with "-". For example, "anal tanlines -yaoi" matches all galleries matching "anal" and "tanlines" but
                        not "yaoi". Exact searches can be performed by wrapping terms in double quotes. For example, "big breasts" only matches galleries with
                        "big breasts" somewhere in the title or in tags. These can be combined with tag namespaces for finer control over the query: "
                        parodies:railgun -tag:'big breasts'". You can search for galleries with a specific number of pages with "pages:20", or with a page
                        range: "pages:>20 pages:<=30". You can search for galleries uploaded within some timeframe with "uploaded:20d". Valid units are "h",
                        "d", "w", "m", "y". You can use ranges as well: "uploaded:>20d uploaded:<30d".
  --archive-all         Archive EVERYTHING from NHentai (all pages of homepage).
  --excluded-tags EXCLUDED_TAGS
                        Comma-separated list of tags to exclude galleries (default: 'snuff,cuntboy,guro,cuntbusting,scat,coprophagia,ai generated,vore')
  --language LANGUAGE   Comma-separated list of languages to include (default: 'english')
  --title-type {english,japanese,pretty}
                        What title type to use (default: english). Not using 'pretty' may lead to unsupported symbols in gallery names being replaced to be
                        filesystem compatible, although titles are cleaned to try and avoid this.
  --threads-galleries THREADS_GALLERIES
                        Number of threads downloading galleries at once (default: 2). Be careful setting this any higher than 2. You'll be better off
                        increasing the number of image threads.
  --threads-images THREADS_IMAGES
                        Number of threads per gallery downloading images at once (default: 10). You're better off increasing this value than increasing the
                        number of gallery threads. There isn't really a limit, but still be careful setting this any higher than 10
  --max-retries MAX_RETRIES
                        Maximum number of retry attempts for failed downloads (default: 3)
  --min-sleep MIN_SLEEP
                        Minimum amount of time each thread should sleep before starting a new download (default: 0.5). Set this to a higher number if you are
                        hitting API limits.
  --max-sleep MAX_SLEEP
                        Maximum amount of time each thread can sleep before starting a new download (default: 50.0). Setting this to a number lower than 50.0,
                        may result in hitting API limits.
  --use-tor             Use TOR network for downloads (default: True)
  --skip-post-batch     Skips the extra post batch actions that run occassionally during scrapes (default: False). Turning this off will make the scrape
                        complete quicker (depending on Extension used, number of galleries, etc).
  --skip-post-run       Skips the post download actions (default: False). For example, if you're using the Suwayomi extension, the download directory is still
                        cleaned, but things like updating Suwayomi are skipped.
  --dry-run             Simulate downloads without saving files (default: False)
  --calm                Enable calm logging (warnings and higher) (default: False)
  --debug               Enable debug logging (critical errors and lower) (default: False)
```

### Examples
```bash
# Default run (latest galleries)
nhentai-scraper

# Specify a gallery range
nhentai-scraper --range 500000 500100

# Custom thread count
nhentai-scraper --range 600000 600050 --threads-galleries 5 --threads-images 10

# Use the Suwayoi Extension, download galleries from artist "XYZ" (default page range, 1 - 10) and of tag "uncensored" from pages 1 - 10 (explicitly declared), excluding certain tags, using a certain language and using Tor
nhentai-scraper --extension suwayomi --artist "XYZ" --tag "uncensored" 1 10 --exclude-tags "snuff, lolicon, shotacon" --use-tor
```

## Documentation
Some dickhead said he'd do this later (I am the dickhead)