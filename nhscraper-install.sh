#!/usr/bin/env bash
set -e

# ===============================
# VARIABLES
# ===============================
NHENTAI_DIR="/opt/nhentai-scraper"
RICTERZ_DIR="/opt/ricterz_nhentai"
SUWAYOMI_DIR="/opt/suwayomi"
FILEBROWSER_BIN="/usr/local/bin/filebrowser"
ENV_FILE="$NHENTAI_DIR/nhentai-scraper.env"
LOGS_DIR="$NHENTAI_DIR/logs"

# ===============================
# ROOT CHECK
# ===============================
if [[ $EUID -ne 0 ]]; then
    echo "[!] Please run this installer as root."
    exit 1
fi

# ===============================
# FUNCTIONS
# ===============================
function create_logs_dir() {
    mkdir -p "$LOGS_DIR"
    chmod 755 "$LOGS_DIR"
    echo "[+] Logs directory created at $LOGS_DIR"
}

function create_env_file() {
    mkdir -p "$(dirname "$ENV_FILE")"
    if [ ! -f "$ENV_FILE" ]; then
        cat >"$ENV_FILE" <<EOF
NHENTAI_COOKIE=
NHENTAI_USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36
NHENTAI_DRY_RUN=false
USE_TOR=false
NHENTAI_START_ID=500000
NHENTAI_END_ID=
THREADS_GALLERIES=3
THREADS_IMAGES=3
EOF
        echo "[+] Created default environment file at $ENV_FILE"
    fi
}

function update_env() {
    create_env_file
    source "$ENV_FILE" 2>/dev/null || true

    read -p "Enter new nhentai.net cookie (leave blank to keep current): " NEW_COOKIE
    read -p "Enter new browser User-Agent (leave blank to keep current): " NEW_UA
    read -p "Enable dry-run mode? (true/false, leave blank to keep current): " NEW_DRY
    read -p "Use Tor proxy? (true/false, leave blank to keep current): " NEW_TOR
    read -p "Enter start gallery ID (leave blank to keep current): " NEW_START
    read -p "Enter end gallery ID (leave blank to keep current): " NEW_END
    read -p "Enter number of threads to use per gallery (leave blank to keep current): " NEW_GALLERY_THREADS
    read -p "Enter number of threads to use per image (leave blank to keep current): " NEW_IMAGE_THREADS

    cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%F-%T)"
    echo "[*] Backup saved as $ENV_FILE.bak.$(date +%F-%T)"

    [[ -n "$NEW_COOKIE" ]] && sed -i "s|^NHENTAI_COOKIE=.*|NHENTAI_COOKIE=$NEW_COOKIE|" "$ENV_FILE"
    [[ -n "$NEW_UA" ]] && sed -i "s|^NHENTAI_USER_AGENT=.*|NHENTAI_USER_AGENT=$NEW_UA|" "$ENV_FILE"
    [[ -n "$NEW_DRY" ]] && sed -i "s|^NHENTAI_DRY_RUN=.*|NHENTAI_DRY_RUN=$NEW_DRY|" "$ENV_FILE"
    [[ -n "$NEW_TOR" ]] && sed -i "s|^USE_TOR=.*|USE_TOR=$NEW_TOR|" "$ENV_FILE"
    [[ -n "$NEW_START" ]] && sed -i "s|^NHENTAI_START_ID=.*|NHENTAI_START_ID=$NEW_START|" "$ENV_FILE"
    [[ -n "$NEW_END" ]] && sed -i "s|^NHENTAI_END_ID=.*|NHENTAI_END_ID=$NEW_END|" "$ENV_FILE"
    [[ -n "$NEW_GALLERY_THREADS" ]] && sed -i "s|^THREADS_GALLERIES=.*|THREADS_GALLERIES=$NEW_GALLERY_THREADS|" "$ENV_FILE"
    [[ -n "$NEW_IMAGE_THREADS" ]] && sed -i "s|^THREADS_IMAGES=.*|THREADS_IMAGES=$NEW_IMAGE_THREADS|" "$ENV_FILE"

    echo "[*] Updated environment file:"
    cat "$ENV_FILE"

    echo "[*] Reloading systemd services..."
    create_systemd_services
}

function install_system_packages() {
    echo "[*] Installing system packages..."
    apt-get update
    apt-get install -y python3 python3-pip python3-venv git build-essential curl wget dnsutils tor torsocks
    echo "[+] System packages installed."
}

function check_venv() {
    if ! python3 -m venv --help >/dev/null 2>&1; then
        echo "[!] python3-venv missing, installing..."
        apt-get install -y python3-venv
    fi
}

function install_ricterz_nhentai() {
    if [ ! -d "$RICTERZ_DIR" ]; then
        echo "[*] Cloning RicterZ nhentai repository..."
        git clone https://github.com/RicterZ/nhentai.git "$RICTERZ_DIR"
    else
        echo "[*] RicterZ repo exists, pulling latest..."
        cd "$RICTERZ_DIR"
        git pull || echo "[!] Could not update RicterZ repo."
    fi
}

function install_scraper() {
    echo "[*] Installing nhentai-scraper..."
    mkdir -p "$NHENTAI_DIR"
    cd "$NHENTAI_DIR"

    BRANCH="main"
    if [ ! -d "$NHENTAI_DIR/.git" ]; then
        git clone --depth 1 --branch "$BRANCH" https://github.com/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"
    else
        git fetch origin "$BRANCH"
        git reset --hard "origin/$BRANCH"
    fi

    check_venv
    [ ! -d "$NHENTAI_DIR/venv" ] && python3 -m venv "$NHENTAI_DIR/venv"

    source "$NHENTAI_DIR/venv/bin/activate"
    pip install --upgrade pip python-dotenv requests requests[socks] cloudscraper beautifulsoup4 tqdm aiohttp gql[all] nhentai

    create_logs_dir
    create_env_file
}

# ===============================
# SYSTEMD SERVICES
# ===============================
function create_systemd_services() {
    PYTHON_BIN="$NHENTAI_DIR/venv/bin/python3"

    # NHentai Scraper Service
    cat >/etc/systemd/system/nhentai-scraper.service <<EOF
[Unit]
Description=NHentai Scraper
After=network.target tor.service
Wants=tor.service

[Service]
Type=simple
WorkingDirectory=$NHENTAI_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PYTHON_BIN $NHENTAI_DIR/nhentai-scraper.py --start \$NHENTAI_START_ID --end \$NHENTAI_END_ID --threads-galleries \$THREADS_GALLERIES --threads-images \$THREADS_IMAGES --cookie \$NHENTAI_COOKIE --user-agent "\$NHENTAI_USER_AGENT" \$([ "\$NHENTAI_DRY_RUN" = "true" ] && echo "--dry-run") \$([ "\$USE_TOR" = "true" ] && echo "--use-tor") --verbose
Restart=on-failure
RestartSec=10
User=root
StandardOutput=append:$LOGS_DIR/nhentai-scraper.log
StandardError=append:$LOGS_DIR/nhentai-scraper.log

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable nhentai-scraper
    systemctl start nhentai-scraper
}

# ===============================
# MAIN
# ===============================
case "$1" in
    --update-env) update_env ;;
    --install) 
        install_system_packages
        install_ricterz_nhentai
        install_scraper
        create_systemd_services
        echo "[*] Installation complete. You can edit environment with --update-env"
        ;;
    --uninstall)
        echo "[*] Stopping and removing services..."
        systemctl stop nhentai-scraper || true
        systemctl disable nhentai-scraper || true
        rm -rf "$NHENTAI_DIR" "$RICTERZ_DIR"
        rm -f /etc/systemd/system/nhentai-scraper.service
        systemctl daemon-reload
        echo "[+] Uninstalled."
        ;;
    *)
        echo "Usage: $0 --install | --update-env | --uninstall"
        exit 1
        ;;
esac