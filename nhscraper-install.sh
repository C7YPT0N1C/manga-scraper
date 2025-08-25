#!/usr/bin/env bash
set -e

# ===============================
# VARIABLES
# ===============================
NHENTAI_DIR="/opt/nhentai-scraper"
SUWAYOMI_DIR="/opt/suwayomi"
FILEBROWSER_BIN="/usr/local/bin/filebrowser"
ENV_FILE="$NHENTAI_DIR/nhentai-scraper.env"

# ===============================
# FUNCTIONS
# ===============================
function install_system_packages() {
    echo "[*] Installing system packages..."
    apt-get update
    apt-get install -y python3 python3-pip python3-venv git build-essential curl wget dnsutils tor torsocks
}

function install_python_requirements() {
    echo "[*] Installing Python requirements in venv..."
    source "$NHENTAI_DIR/venv/bin/activate"
    pip install --upgrade pip setuptools wheel cloudscraper

    TMP_REQ=$(mktemp)
    cat > "$TMP_REQ" <<EOF
requests>=2.31.0
flask>=2.3.3
beautifulsoup4>=4.12.2
tqdm>=4.66.1
aiohttp>=3.9.2
gql[all]>=3.5.0
python-dotenv>=1.0
EOF

    pip install -r "$TMP_REQ"
    rm "$TMP_REQ"
    echo "[+] Python requirements installed."
}

function install_scraper() {
    echo "[*] Installing nhentai-scraper..."
    mkdir -p "$NHENTAI_DIR"
    cd "$NHENTAI_DIR"

    read -p "Install Beta Version instead of Stable? [y/N]: " beta
    branch="main"
    [[ "$beta" =~ ^[yY] ]] && branch="dev"

    if [ ! -d "$NHENTAI_DIR/.git" ]; then
        echo "[*] Cloning nhentai-scraper..."
        if git clone --depth 1 --branch "$branch" https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"; then
            echo "[+] Cloned from Gitea."
        else
            echo "[!] Gitea clone failed, trying GitHub..."
            git clone --depth 1 --branch "$branch" https://github.com/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"
        fi
    else
        git pull || echo "[!] Could not update scraper repo."
    fi

    if [ ! -d "$NHENTAI_DIR/venv" ]; then
        echo "[*] Creating venv..."
        python3 -m venv "$NHENTAI_DIR/venv"
        source "$NHENTAI_DIR/venv/bin/activate"
        "$NHENTAI_DIR/venv/bin/pip" install --upgrade pip setuptools wheel
    else
        source "$NHENTAI_DIR/venv/bin/activate"
    fi

    install_python_requirements
}

function install_suwayomi() {
    echo "[*] Installing Suwayomi via tar.gz..."
    mkdir -p "$SUWAYOMI_DIR"
    cd "$SUWAYOMI_DIR"

    TARA_URL="https://github.com/Suwayomi/Suwayomi-Server/releases/download/v2.1.1867/Suwayomi-Server-v2.1.1867-linux-x64.tar.gz"
    wget -O suwayomi-server.tar.gz "$TARA_URL"
    tar -xzf suwayomi-server.tar.gz --strip-components=1
    rm suwayomi-server.tar.gz

    mkdir -p "$SUWAYOMI_DIR/local"
    chmod 755 "$SUWAYOMI_DIR/local"
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

function create_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        echo "[*] Creating nhentai-scraper.env..."
        read -p "Enter your NHentai session cookie: " COOKIE
        cat >"$ENV_FILE" <<EOF
NHENTAI_COOKIE=$COOKIE
NHENTAI_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.5790.171 Safari/537.36"
NHENTAI_START_ID=500000
NHENTAI_END_ID=600000
THREADS_GALLERIES=3
THREADS_IMAGES=5
USE_TOR=false
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
ExecStart=/usr/local/bin/filebrowser -d /etc/filebrowser/filebrowser.db -r / --address 0.0.0.0 --port 8080
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    # API service
    if [ ! -f /etc/systemd/system/nhentai-api.service ]; then
        cat >/etc/systemd/system/nhentai-api.service <<EOF
[Unit]
Description=NHentai Scraper API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/nhentai-scraper
EnvironmentFile=/opt/nhentai-scraper/nhentai-scraper.env
ExecStart=/bin/bash -c "source /opt/nhentai-scraper/venv/bin/activate && exec python3 /opt/nhentai-scraper/nhentai-api.py"
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    systemctl daemon-reload
    systemctl enable suwayomi filebrowser nhentai-api
    systemctl start suwayomi filebrowser nhentai-api
}

function print_links() {
    IP=$(hostname -I | awk '{print $1}')
    HOSTNAME=$(hostname)

    echo -e "\n[*] Access Links:"
    echo "Suwayomi Web: http://$IP:4567/"
    echo "Suwayomi GraphQL: http://$IP:4567/api/graphql"
    echo "FileBrowser: http://$IP:8080/ (User: admin, Password: DefaultPassword123!)"
    echo "Scraper Flask status: http://$IP:5000/scraper_status"
    if [ ! -z "$HOSTNAME" ]; then
        echo -e "\nDNS Hostname Links:"
        echo "Suwayomi Web: http://$HOSTNAME:4567/"
        echo "Suwayomi GraphQL: http://$HOSTNAME:4567/api/graphql"
        echo "FileBrowser: http://$HOSTNAME:8080/"
        echo "Scraper Flask status: http://$HOSTNAME:5000/scraper_status"
    fi
}

function uninstall_all() {
    echo "[*] Stopping and disabling services..."
    systemctl stop nhentai-scraper nhentai-monitor filebrowser suwayomi || true
    systemctl disable nhentai-scraper nhentai-monitor filebrowser suwayomi || true

    echo "[*] Removing systemd service files..."
    rm -f /etc/systemd/system/nhentai-scraper.service
    rm -f /etc/systemd/system/nhentai-monitor.service
    rm -f /etc/systemd/system/filebrowser.service
    rm -f /etc/systemd/system/suwayomi.service
    systemctl daemon-reload

    echo "[*] Removing installed directories..."
    rm -rf "$NHENTAI_DIR"
    rm -rf "$SUWAYOMI_DIR"
    rm -rf /etc/filebrowser/filebrowser.db

    echo "[*] Uninstallation complete!"
}

function update_all() {
    echo "[*] Updating nhentai-scraper and Suwayomi..."
    install_scraper
    install_suwayomi
    echo "[*] Restarting services..."
    systemctl restart suwayomi filebrowser nhentai-scraper
    echo "[*] Update complete!"
}

# ===============================
# MAIN
# ===============================
echo "===================================================="
echo "           nhentai-scraper INSTALLER               "
echo "===================================================="
echo ""
echo "This installer will install, update, or uninstall the following components:"
echo "- nhentai-scraper"
echo "- Suwayomi Server"
echo "- FileBrowser"
echo ""
read -p "Do you want to proceed? [y/N]: " consent

case "$consent" in
    [yY]|[yY][eE][sS]) echo "[*] Consent given. Continuing..." ;;
    *) echo "[!] Operation cancelled by user."; exit 0 ;;
esac

if [[ "$1" == "uninstall" ]]; then
    uninstall_all
    exit 0
elif [[ "$1" == "update" ]]; then
    update_all
    print_links
    exit 0
fi

install_system_packages
install_scraper
install_suwayomi
install_filebrowser
create_env_file
create_systemd_services
print_links

echo "[*] Installation complete!"