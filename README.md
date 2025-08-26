# nhentai-scraper

- [**Overview and Disclaimer**](#overview-and-disclaimer)
- [**Features**](#features)
  - [**TO-DO LIST**](#to-do-list)
- [**Installation**](#installation)
  - [System Requirements](#system-requirements)
  - [Installation Commands](#installation-commands)
- [**Post Install**](#post-install)
- [**Usage**](#usage)
  - [Cookies and User Agents](#cookies-and-user-agents)
  - [CLI Arguments](#cli-arguments)
  - [Examples](#examples)
- [**Documentation**](#documentation)
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

## Installation
### System Requirements
- OS: `Ubuntu / Linux server or VM`
- RAM (scale based on need): `Minimum 2GB, Recommended 4GB or More.`
- Storage: `UNKNOWN`
  - **WARNING: I honest haven't tested this yet so please do assume you're going to need a *LOT* of storage, especially if you're not limiting the range of gallery IDs.**
- **REQUIRED**: `Python 3.9+, pip`
- **Optional**: `Tor (installed automatically) or VPN (OpenVPN, WireGuard)`

### Installation Commands
One Line Install: `rm ./nhscraper-install.sh && wget https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper/raw/branch/dev/nhscraper-install.sh && sudo bash ./nhscraper-install.sh --install`

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
### Cookies and User Agents
**⚠️IMPORTANT⚠️**: To bypass the nhentai frequency limit, you should use the `--cookie` option to store your `cookie` in `nhentai-scraper's environment file`.

You can also use the `--user-agent` option to store your `user-agent` in `nhentai-scraper's environment file`, however, there's is one already being used by default (`"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.5790.171 Safari/537.36"`).

    nhentai-scraper --useragent "USER AGENT of YOUR BROWSER"
    nhentai-scraper --cookie "YOUR COOKIE FROM nhentai.net"

**NOTE:**

- The format of the cookie is `"csrftoken=TOKEN; sessionid=ID; cf_clearance=CLOUDFLARE"`
- `cf_clearance` cookie and useragent must be set if you encounter "blocked by cloudflare captcha" error. Make sure you use the same IP and useragent as when you got it.

Please refer to [RicterZ/nhentai's README](https://github.com/RicterZ/nhentai/blob/master/README.rst) to learn how to acquire both your cookie and your user-agent.

### CLI Arguments
- An environemt file for the scraper `config.env` will be automatically created during installation and can be found at `/opt/nhentai-scraper/config.env`.
- Some CLI arguments used (like `--cookie` or `--user-agent`) will be saved in `config.env`.
```bash
USAGE:
--help START_ID                           Display Usage Help

--start START_ID                          Starting gallery ID (Default: 500000)
--end  END_ID                             Ending gallery ID (Default: 600000)
--excluded-tags TAG1, TAG2, TAG3          Comma-separated list of tags to exclude galleries (e.g: video game, yaoi, cosplay) (Default: none)
--language LANG1, LANG2                   Comma-separated list of languages to include (e.g: english, japanese) (Default: english)
--cookie COOKIE                           nhentai cookie string (REQUIRED AS A FLAG OR IN ENVIRONMENT FILE: (/opt/nhentai-scraper/config.env) )
--user-agent USER_AGENT                   Browser User-Agent
--threads-galleries NUM_OF_GALLERIES      Number of concurrent galleries to be downloaded (Default: 3)
--threads-images THREADS_PER_GALLERY      Threads per gallery (Default: 5)

OPTIONAL:
--dry-run                                 Simulate downloads and GraphQL without downloading anything.
--use-tor                                 Route requests via Tor. Requires Tor to be running on (localhost:9050)
--verbose                                 Enable debug logging.
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
### Directory Layout
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
- [ ] Make sure tags with multiple words (e.g. big ass) parse properly
- [ ] Ensure variables are formatted correctly (env file and the use of quotes around variables, "cookie" vs cookie)
- [ ] Add gallery download with language/tag filters
- [ ] Sort downloads by artists and groups
- [ ] Add multi-threaded download support
- [ ] Implement automatic retry of failed/skipped galleries
- [ ] Add Flask monitoring endpoint
- [ ] Create systemd service & timer

Suwayomi:
- [ ] Use GraphQL to automatically change Suwayomi settings on install
- [ ] Sort downloaded galleries into Suwayomi categories by tags)
- [ ] Generate Suwayomi metadata automatically
- [ ] Automatically create a category in Suwayomi for each gallery tag/genre if it doesn't already exist
- [ ] Assign galleries to corresponding categories based on their tags
- [ ] Allow manual reordering of categories or extend logic to sort by number of galleries per category
- [ ] Exclude tags listed in `SUWAYOMI_IGNORED_CATEGORIES` (default: `Favourites, Favs`) from automated categories

Other Tasks:
- [ ] Implement automatic cookie updates?
- [ ] Allow users to select specific VPN server (via launch argument)?
- [ ] Add more launch arguments / API endpoints
- [ ] Improve documentation
- [ ] Add better debugging (e.g., reasons for gallery skip)
- [ ] New workflow?: suwayomi extension → API request → scraper → downloader → import to Suwayomi