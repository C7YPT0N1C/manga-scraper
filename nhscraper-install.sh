#!/usr/bin/env bash
set -e

# ===============================
# VARIABLES
# ===============================
NHENTAI_DIR="/opt/nhentai-scraper"
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
# DISCLAIMER
# ===============================
echo "===================================================="
echo "           nhentai-scraper INSTALLER               "
echo "===================================================="
echo ""
echo "This will install/update:"
echo "- C7YPT0N1C/nhentai-scraper"
echo "- Suwayomi Server"
echo "- FileBrowser"
echo ""
read -p "Proceed? [y/N]: " consent
case "$consent" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "[!] Cancelled."; exit 0 ;;
esac

# ===============================
# FUNCTIONS
# ===============================
function create_logs_dir() {
    mkdir -p "$LOGS_DIR"
    chmod 755 "$LOGS_DIR"
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
NHENTAI_END_ID=600000
THREADS_GALLERIES=3
THREADS_IMAGES=3
EOF
    fi
}

function uninstall_all() {
    systemctl stop nhentai-scraper nhentai-monitor suwayomi filebrowser || true
    systemctl disable nhentai-scraper nhentai-monitor suwayomi filebrowser || true
    rm -rf "$NHENTAI_DIR" "$SUWAYOMI_DIR"
    rm -f /etc/systemd/system/nhentai-*.service
    rm -f "$ENV_FILE"
    systemctl daemon-reload
    exit 0
}

function install_system_packages() {
    apt-get update
    apt-get install -y python3 python3-pip python3-venv git build-essential curl wget tor torsocks
}

function check_venv() {
    if ! python3 -m venv --help >/dev/null 2>&1; then
        apt-get install -y python3-venv
    fi
}

function install_scraper() {
    mkdir -p "$NHENTAI_DIR"
    cd "$NHENTAI_DIR"

    read -p "Install Beta Version? [y/N]: " beta
    BRANCH="main"
    [[ "$beta" =~ ^[yY] ]] && BRANCH="dev"

    if [ ! -d "$NHENTAI_DIR/.git" ]; then
        if git clone --depth 1 --branch "$BRANCH" https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"; then
            echo "[+] Cloned from Gitea."
        else
            git clone --depth 1 --branch "$BRANCH" https://github.com/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"
        fi
    else
        git fetch origin "$BRANCH"
        git reset --hard "origin/$BRANCH"
    fi

    check_venv
    if [ ! -d "$NHENTAI_DIR/venv" ]; then
        python3 -m venv "$NHENTAI_DIR/venv"
    fi

    source "$NHENTAI_DIR/venv/bin/activate"
    pip install --upgrade pip setuptools wheel python-dotenv pysocks requests requests[socks] cloudscraper flask beautifulsoup4 tqdm aiohttp gql[all]

    create_logs_dir
    create_env_file
}

function create_systemd_services() {
    cat >/etc/systemd/system/nhentai-scraper.service <<EOF
[Unit]
Description=NHentai Scraper
After=network.target tor.service
Wants=tor.service

[Service]
Type=simple
WorkingDirectory=/opt/nhentai-scraper
EnvironmentFile=/opt/nhentai-scraper/nhentai-scraper.env
ExecStart=/opt/nhentai-scraper/venv/bin/python3 /opt/nhentai-scraper/nhentai-scraper.py \\
    --start \${NHENTAI_START_ID} --end \${NHENTAI_END_ID} \\
    --threads-galleries \${THREADS_GALLERIES} --threads-images \${THREADS_IMAGES} \\
    \${NHENTAI_COOKIE:+--cookie \${NHENTAI_COOKIE}} \\
    \${NHENTAI_USER_AGENT:+--user-agent "\${NHENTAI_USER_AGENT}"} \\
    \$( [ "\${NHENTAI_DRY_RUN}" = "true" ] && echo --dry-run ) \\
    \$( [ "\${USE_TOR}" = "true" ] && echo --use-tor ) \\
    --verbose
Restart=on-failure
RestartSec=10
User=root
StandardOutput=append:/opt/nhentai-scraper/logs/nhentai-scraper.log
StandardError=append:/opt/nhentai-scraper/logs/nhentai-scraper.log

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable nhentai-scraper
    systemctl start nhentai-scraper
}

# ===============================
# MAIN MENU
# ===============================
echo "1) Install/Update"
echo "2) Uninstall"
read -p "Choose: " choice
case $choice in
    1) install_system_packages; install_scraper; create_systemd_services ;;
    2) uninstall_all ;;
    *) echo "Invalid choice"; exit 1 ;;
esac