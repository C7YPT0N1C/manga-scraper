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
# ROOT CHECK
# ===============================
if [[ $EUID -ne 0 ]]; then
    echo "[!] Please run this installer as root."
    exit 1
fi

# ===============================
# FUNCTIONS
# ===============================

function install_system_packages() {
    echo "[*] Installing system packages..."
    apt-get update
    # SYSTEM REQUIREMENTS
    apt-get install -y python3 python3-pip python3-venv python3.12-venv git build-essential curl wget dnsutils tor torsocks
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
    cd "$NHENTAI_DIR"

    read -p "Install Beta Version instead of Stable? [y/N]: " beta
    beta=${beta,,}
    if [[ "$beta" == "y" || "$beta" == "yes" ]]; then
        BRANCH="dev"
        echo "[*] Installing Beta Version (dev branch)..."
    else
        BRANCH="main"
        echo "[*] Installing Stable Version (main branch)..."
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

    # PYTHON REQUIREMENTS
    echo "[*] Installing Python requirements..."
    pip install --upgrade pip setuptools wheel cloudscraper requests flask beautifulsoup4 tqdm aiohttp gql[all] nhentai
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
    if [ ! -f /etc/systemd/system/nhentai-scraper.service ]; then
        cat >/etc/systemd/system/nhentai-scraper.service <<EOF
[Unit]
Description=NHentai Scraper
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/nhentai-scraper
ExecStart=/bin/bash -c "source /opt/nhentai-scraper/venv/bin/activate && exec python3 /opt/nhentai-scraper/nhentai_scraper.py --start 400000 --end 400010 --threads-galleries 3 --threads-images 5"
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    # Monitor service
    if [ ! -f /etc/systemd/system/nhentai-monitor.service ]; then
        cat >/etc/systemd/system/nhentai-monitor.service <<EOF
[Unit]
Description=NHentai Scraper Monitor
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/nhentai-scraper
ExecStart=/bin/bash -c "source /opt/nhentai-scraper/venv/bin/activate && exec python3 /opt/nhentai-scraper/monitor.py"
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    systemctl daemon-reload
    systemctl enable suwayomi filebrowser nhentai-scraper nhentai-monitor
    systemctl start suwayomi filebrowser nhentai-scraper nhentai-monitor
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

# ===============================
# MAIN
# ===============================
echo "===================================================="
echo "           NHentai Scraper INSTALLER               "
echo "===================================================="
read -p "Do you want to proceed? [y/N]: " consent
case "$consent" in
    [yY]|[yY][eE][sS]) echo "[*] Continuing...";;
    *) echo "[!] Cancelled"; exit 0;;
esac

install_system_packages
install_ricterz_nhentai
install_scraper
install_suwayomi
install_filebrowser
create_systemd_services
print_links

echo "[*] Installation complete!"