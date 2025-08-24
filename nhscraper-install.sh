#!/usr/bin/env bash
set -e

# ===============================
# VARIABLES
# ===============================
NHENTAI_DIR="/opt/nhentai-scraper"
RICTERZ_DIR="/opt/ricterz_nhentai"
SUWAYOMI_DIR="/opt/suwayomi"
FILEBROWSER_BIN="/usr/local/bin/filebrowser"
CONFIG_FILE="$NHENTAI_DIR/config.json"

# ===============================
# FUNCTIONS (REQUIREMENTS ARE HERE)
# ===============================
function install_system_packages() {
    echo "[*] Installing system packages..."
    apt-get update
    apt-get install -y python3 python3-pip python3-venv git build-essential curl wget dnsutils
}

function install_python_requirements() {
    echo "[*] Installing Python requirements in venv..."
    # Make sure pip inside venv is used
    source "$NHENTAI_DIR/venv/bin/activate"
    pip install --upgrade pip setuptools wheel

    TMP_REQ=$(mktemp)
    cat > "$TMP_REQ" <<EOF
requests>=2.31.0
flask>=2.3.3
beautifulsoup4>=4.12.2
tqdm>=4.66.1
aiohttp>=3.9.2
gql[all]>=3.5.0
nhentai>=0.5.25
EOF

    pip install -r "$TMP_REQ"
    rm "$TMP_REQ"
    echo "[+] Python requirements installed."
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
    if [ ! -d "$NHENTAI_DIR" ]; then
        mkdir -p "$NHENTAI_DIR"
    fi
    cd "$NHENTAI_DIR"

    # Clone repo
    if [ ! -d "$NHENTAI_DIR/.git" ]; then
        echo "[*] Cloning nhentai-scraper..."
        if git clone --depth 1 https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"; then
            echo "[+] Cloned from Gitea."
        else
            echo "[!] Gitea clone failed, trying GitHub..."
            git clone --depth 1 https://github.com/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"
        fi
    else
        git pull || echo "[!] Could not update scraper repo."
    fi

    # Create venv if missing
    if [ ! -d "$NHENTAI_DIR/venv" ]; then
        echo "[*] Creating venv..."
        python3 -m venv "$NHENTAI_DIR/venv"
        source "$NHENTAI_DIR/venv/bin/activate"
        "$NHENTAI_DIR/venv/bin/pip" install --upgrade pip setuptools wheel
        echo "[+] Created venv at $NHENTAI_DIR/venv"
    else
        source "$NHENTAI_DIR/venv/bin/activate"
    fi

    # Install Python requirements
    install_python_requirements
}

function install_suwayomi() {
    echo "[*] Installing Suwayomi via tar.gz..."
    mkdir -p "$SUWAYOMI_DIR"
    cd "$SUWAYOMI_DIR"

    # Download latest tar.gz release
    TARA_URL="https://github.com/Suwayomi/Suwayomi-Server/releases/download/v2.1.1867/Suwayomi-Server-v2.1.1867-linux-x64.tar.gz"
    wget -O suwayomi-server.tar.gz "$TARA_URL"

    # Extract
    tar -xzf suwayomi-server.tar.gz --strip-components=1
    rm suwayomi-server.tar.gz

    # Create local folder
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
        $FILEBROWSER_BIN -d /etc/filebrowser/filebrowser.db users add admin admin
    fi
}

function create_config_file() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "[*] Creating default config.json..."
        cat >"$CONFIG_FILE" <<EOF
{
  "ROOT_FOLDER": "/opt/suwayomi/local/",
  "EXCLUDED_TAGS": ["snuff","guro","cuntboy","cuntbusting","ai generated"],
  "INCLUDE_TAGS": [],
  "LANGUAGE_FILTER": "english",
  "MAX_THREADS_GALLERIES": 3,
  "MAX_THREADS_IMAGES": 5,
  "RETRY_LIMIT": 3,
  "SLEEP_BETWEEN_GALLERIES": 0.2,
  "VERBOSE": true,
  "BASE_URL": "https://nhentai.net/g/",
  "PROGRESS_FILE": "progress.json",
  "SKIPPED_LOG": "skipped.log",
  "SUWAYOMI_GRAPHQL": "http://localhost:4567/api/graphql",
  "SUWAYOMI_AUTH_HEADER": null,
  "USE_TOR": false,
  "TOR_PROXY": "socks5h://127.0.0.1:9050",
  "USE_VPN": false
}
EOF
    fi
}

function create_systemd_services() {
    echo "[*] Creating systemd services..."

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
ExecStart=$FILEBROWSER_BIN -d /etc/filebrowser/filebrowser.db -r $SUWAYOMI_DIR/local
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    # Scraper
    if [ ! -f /etc/systemd/system/nhentai-scraper.service ]; then
        cat >/etc/systemd/system/nhentai-scraper.service <<EOF
[Unit]
Description=nhentai-scraper
After=network.target

[Service]
WorkingDirectory=$NHENTAI_DIR
ExecStart=/usr/bin/env python3 $NHENTAI_DIR/scraper.py
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    systemctl daemon-reload
    systemctl enable suwayomi filebrowser nhentai-scraper
    systemctl start suwayomi filebrowser nhentai-scraper
}

function print_links() {
    IP=$(hostname -I | awk '{print $1}')
    HOSTNAME=$(hostname)

    echo -e "\n[*] Access Links:"
    echo "Suwayomi Web: http://$IP:4567/"
    echo "Suwayomi GraphQL: http://$IP:4567/api/graphql"
    echo "FileBrowser: http://$IP:8080/"
    echo "Scraper Flask status: http://$IP:5000/scraper_status"
    if [ ! -z "$HOSTNAME" ]; then
        echo "Suwayomi Web: http://$HOSTNAME:4567/"
        echo "Suwayomi GraphQL: http://$HOSTNAME:4567/api/graphql"
        echo "FileBrowser: http://$HOSTNAME:8080/"
        echo "Scraper Flask status: http://$HOSTNAME:5000/scraper_status"
    fi
}

function uninstall_all() {
    echo "[*] Stopping and disabling services..."
    systemctl stop nhentai-scraper filebrowser suwayomi || true
    systemctl disable nhentai-scraper filebrowser suwayomi || true

    echo "[*] Removing systemd service files..."
    rm -f /etc/systemd/system/nhentai-scraper.service
    rm -f /etc/systemd/system/filebrowser.service
    rm -f /etc/systemd/system/suwayomi.service
    systemctl daemon-reload

    echo "[*] Removing installed directories..."
    rm -rf "$NHENTAI_DIR"
    rm -rf "$SUWAYOMI_DIR"
    rm -rf "$RICTERZ_DIR"
    rm -rf /etc/filebrowser/filebrowser.db

    echo "[*] Uninstallation complete!"
}

function update_all() {
    echo "[*] Updating nhentai-scraper, RicterZ nhentai, and Suwayomi..."
    install_nhentai_repo
    install_scraper
    install_suwayomi
    echo "[*] Restarting services..."
    systemctl restart suwayomi filebrowser nhentai-scraper
    echo "[*] Update complete!"
}

# ===============================
# MAIN
# ===============================

# Disclaimer
echo "===================================================="
echo "           nhentai-scraper INSTALLER               "
echo "===================================================="
echo ""
echo "DISCLAIMER:"
echo "This installer will install, update, or uninstall the following components:"
echo "- RicterZ/nhentai scraper"
echo "- Suwayomi Server"
echo "- FileBrowser"
echo ""
echo "Existing installations in /opt/ may be overwritten."
echo "Ensure you have backups of any important data."
echo ""
read -p "Do you want to proceed? [y/N]: " consent

case "$consent" in
    [yY]|[yY][eE][sS])
        echo "[*] Consent given. Continuing..."
        ;;
    *)
        echo "[!] Operation cancelled by user."
        exit 0
        ;;
esac

# Handle uninstall or update modes first
if [[ "$1" == "uninstall" ]]; then
    uninstall_all
    exit 0
elif [[ "$1" == "update" ]]; then
    update_all
    print_links
    exit 0
fi

# Default: install everything
install_system_packages
install_ricterz_nhentai
install_scraper
install_suwayomi
install_filebrowser
create_config_file
create_systemd_services
print_links

echo "[*] Installation complete!"