#!/usr/bin/env bash
set -e

# ===============================
# VARIABLES
# ===============================
NHENTAI_DIR="/opt/nhentai-scraper"
RICTERZ_DIR="/opt/ricterz_nhentai"
SUWAYOMI_DIR="/opt/suwayomi"
FILEBROWSER_BIN="/usr/local/bin/filebrowser"
ENV_FILE="/etc/nhentai-scraper/nhentai-scraper.env"

LOGS_DIR="$NHENTAI_DIR/logs"
CONFIG_FILE="$NHENTAI_DIR/config.json"

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
# NHentai Scraper Environment
NHENTAI_COOKIE=""
NHENTAI_USER_AGENT=""
NHENTAI_DRY_RUN="false"
EOF
        echo "[+] Created default environment file at $ENV_FILE"
    fi
}

function update_env() {
    create_env_file

    source "$ENV_FILE" 2>/dev/null || true
    echo "Current environment values:"
    echo "NHENTAI_COOKIE=${NHENTAI_COOKIE:-<not set>}"
    echo "NHENTAI_USER_AGENT=${NHENTAI_USER_AGENT:-<not set>}"
    echo "NHENTAI_DRY_RUN=${NHENTAI_DRY_RUN:-false}"

    read -p "Enter new nhentai.net cookie (leave blank to keep current): " NEW_COOKIE
    read -p "Enter new browser User-Agent (leave blank to keep current): " NEW_UA
    read -p "Enable dry-run mode? (true/false, leave blank to keep current): " NEW_DRY

    cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%F-%T)"
    echo "[*] Backup saved as $ENV_FILE.bak.$(date +%F-%T)"

    if [ ! -z "$NEW_COOKIE" ]; then
        sed -i "/^NHENTAI_COOKIE=/d" "$ENV_FILE"
        echo "NHENTAI_COOKIE='$NEW_COOKIE'" >> "$ENV_FILE"
    fi
    if [ ! -z "$NEW_UA" ]; then
        sed -i "/^NHENTAI_USER_AGENT=/d" "$ENV_FILE"
        echo "NHENTAI_USER_AGENT='$NEW_UA'" >> "$ENV_FILE"
    fi
    if [ ! -z "$NEW_DRY" ]; then
        sed -i "/^NHENTAI_DRY_RUN=/d" "$ENV_FILE"
        echo "NHENTAI_DRY_RUN='$NEW_DRY'" >> "$ENV_FILE"
    fi

    echo "[*] Updated environment file:"
    cat "$ENV_FILE"

    echo "[*] Reloading systemd services..."
    systemctl daemon-reload
    systemctl restart nhentai-scraper nhentai-monitor
    systemctl status nhentai-scraper nhentai-monitor --no-pager
    echo "[+] Environment update complete."
}

function uninstall_all() {
    echo "[*] Stopping and disabling services..."
    systemctl stop nhentai-scraper nhentai-monitor suwayomi filebrowser || true
    systemctl disable nhentai-scraper nhentai-monitor suwayomi filebrowser || true

    echo "[*] Removing directories..."
    rm -rf "$NHENTAI_DIR" "$RICTERZ_DIR" "$SUWAYOMI_DIR"
    rm -f /etc/systemd/system/nhentai-*.service
    rm -f /etc/filebrowser/filebrowser.db
    rm -f "$ENV_FILE"

    systemctl daemon-reload
    echo "[+] Uninstallation complete."
    exit 0
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
        echo "[*] RicterZ nhentai repo exists. Pulling latest..."
        cd "$RICTERZ_DIR"
        git pull || echo "[!] Could not update RicterZ nhentai repo."
    fi
}

function install_scraper() {
    echo "[*] Installing nhentai-scraper..."
    mkdir -p "$NHENTAI_DIR"
    create_logs_dir
    create_env_file
    cd "$NHENTAI_DIR"

    read -p "Install Beta Version instead of Stable? [y/N]: " beta
    beta=${beta,,}
    BRANCH="main"
    if [[ "$beta" == "y" || "$beta" == "yes" ]]; then
        BRANCH="dev"
        echo "[*] Installing Beta Version (dev branch)..."
    fi

    if [ ! -d "$NHENTAI_DIR/.git" ]; then
        if git clone --depth 1 --branch "$BRANCH" https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"; then
            echo "[+] Cloned nhentai-scraper from Gitea."
        else
            echo "[!] Gitea clone failed, trying GitHub..."
            git clone --depth 1 --branch "$BRANCH" https://github.com/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"
        fi
    else
        git fetch origin "$BRANCH"
        git reset --hard "origin/$BRANCH"
    fi

    check_venv
    if [ ! -d "$NHENTAI_DIR/venv" ]; then
        echo "[*] Creating Python virtual environment..."
        python3 -m venv "$NHENTAI_DIR/venv"
    fi

    source "$NHENTAI_DIR/venv/bin/activate"

    echo "[*] Installing Python requirements..."
    pip install --upgrade pip setuptools wheel pysocks requests[socks] cloudscraper requests flask beautifulsoup4 tqdm aiohttp gql[all] nhentai
    echo "[+] Python requirements installed."
}

function install_suwayomi() {
    echo "[*] Installing Suwayomi..."
    mkdir -p "$SUWAYOMI_DIR"
    cd "$SUWAYOMI_DIR"
    TARA_URL="https://github.com/Suwayomi/Suwayomi-Server/releases/download/v2.1.1867/Suwayomi-Server-v2.1.1867-linux-x64.tar.gz"
    wget -O suwayomi-server.tar.gz "$TARA_URL"
    tar -xzf suwayomi-server.tar.gz --strip-components=1
    rm suwayomi-server.tar.gz
    mkdir -p "$SUWAYOMI_DIR/local"
    chmod 755 "$SUWAYOMI_DIR/local"
    echo "[+] Suwayomi installed at $SUWAYOMI_DIR"
}

function install_filebrowser() {
    echo "[*] Installing FileBrowser..."
    curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | bash
    mkdir -p /etc/filebrowser
    if [ ! -f /etc/filebrowser/filebrowser.db ]; then
        $FILEBROWSER_BIN -d /etc/filebrowser/filebrowser.db config init
        $FILEBROWSER_BIN -d /etc/filebrowser/filebrowser.db users add admin "DefaultPassword123!"
    fi
}

function create_systemd_services() {
    echo "[*] Creating systemd services..."

    PYTHON_BIN="$NHENTAI_DIR/venv/bin/python3"
    RICTERZ_BIN="$RICTERZ_DIR/nhentai/cmdline.py"  # <- updated to use cmdline.py

    # Suwayomi
    if [ ! -f /etc/systemd/system/suwayomi.service ]; then
        cat >/etc/systemd/system/suwayomi.service <<EOF
[Unit]
Description=Suwayomi Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/suwayomi
ExecStart=/bin/bash ./suwayomi-server.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
    fi

    # FileBrowser
    if [ ! -f /etc/systemd/system/filebrowser.service ]; then
        cat >/etc/systemd/system/filebrowser.service <<EOF
[Unit]
Description=FileBrowser
After=network.target

[Service]
ExecStart=/usr/local/bin/filebrowser -d /etc/filebrowser/filebrowser.db -r / --address 0.0.0.0 --port 8080
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    # Scraper service
    cat >/etc/systemd/system/nhentai-scraper.service <<EOF
[Unit]
Description=NHentai Scraper
After=network.target tor.service
Wants=tor.service

[Service]
Type=simple
WorkingDirectory=$NHENTAI_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PYTHON_BIN $RICTERZ_BIN --start 500000 --end 500010 --threads-galleries 3 --threads-images 5 --cookie "\$NHENTAI_COOKIE" --user-agent "\$NHENTAI_USER_AGENT" --verbose
Restart=on-failure
RestartSec=10
User=root
StandardOutput=append:$LOGS_DIR/nhentai-scraper.log
StandardError=append:$LOGS_DIR/nhentai-scraper.log

[Install]
WantedBy=multi-user.target
EOF

    # Monitor service
    cat >/etc/systemd/system/nhentai-monitor.service <<EOF
[Unit]
Description=NHentai Scraper Monitor
After=network.target nhentai-scraper.service tor.service
Wants=nhentai-scraper.service tor.service

[Service]
Type=simple
WorkingDirectory=$NHENTAI_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PYTHON_BIN $NHENTAI_DIR/scraper_monitor.py
Restart=on-failure
RestartSec=10
User=root
StandardOutput=append:$LOGS_DIR/nhentai-monitor.log
StandardError=append:$LOGS_DIR/nhentai-monitor.log

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable nhentai-scraper nhentai-monitor
    systemctl start nhentai-scraper nhentai-monitor
}

function print_links() {
    IP=$(hostname -I | awk '{print $1}')
    echo -e "\n[*] Access Links:"
    echo "Suwayomi Web: http://$IP:4567/"
    echo "Suwayomi GraphQL: http://$IP:4567/api/graphql"
    echo "FileBrowser: http://$IP:8080/"
    echo "Scraper Flask status: http://$IP:5000/scraper_status"
}

# ===============================
# MAIN
# ===============================
case "$1" in
    --update-env) update_env ;;
    --uninstall) uninstall_all ;;
    --install) 
        install_system_packages
        install_ricterz_nhentai
        install_scraper
        install_suwayomi
        install_filebrowser
        create_systemd_services
        print_links
        ;;
    *)
        echo "Usage: $0 --install | --update-env | --uninstall"
        exit 1
        ;;
esac