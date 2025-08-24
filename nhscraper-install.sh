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
    apt-get install -y python3 python3-pip python3-venv python3.12-venv git build-essential curl wget dnsutils tor torsocks
    echo "[+] System packages installed."
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
    beta=${beta,,}  # lowercase
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

    # Create venv
    if [ ! -d "$NHENTAI_DIR/venv" ]; then
        echo "[*] Creating virtual environment..."
        python3 -m venv "$NHENTAI_DIR/venv"
        source "$NHENTAI_DIR/venv/bin/activate"
        pip install --upgrade pip setuptools wheel cloudscraper
        echo "[+] Virtual environment created at $NHENTAI_DIR/venv"
    else
        source "$NHENTAI_DIR/venv/bin/activate"
    fi

    # Install Python requirements
    pip install --upgrade pip setuptools wheel
    pip install requests flask beautifulsoup4 tqdm aiohttp gql[all] nhentai
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
    cat >/etc/systemd/system/suwayomi.service <<EOF
[Unit]
Description=Suwayomi Server
After=network.target

[Service]
Type=simple
WorkingDirectory=$SUWAYOMI_DIR
ExecStart=/bin/bash ./suwayomi-server.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    # FileBrowser
    cat >/etc/systemd/system/filebrowser.service <<EOF
[Unit]
Description=FileBrowser
After=network.target

[Service]
ExecStart=$FILEBROWSER_BIN -d /etc/filebrowser/filebrowser.db -r / --address 0.0.0.0 --port 8080
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF

    # nhentai-scraper
    cat >/etc/systemd/system/nhentai-scraper.service <<EOF
[Unit]
Description=NHentai Scraper
After=network.target

[Service]
Type=simple
WorkingDirectory=$NHENTAI_DIR
ExecStart=/bin/bash -c "source $NHENTAI_DIR/venv/bin/activate && exec python3 $NHENTAI_DIR/nhentai_scraper.py --start 500000 --threads-galleries 3 --threads-images 5"
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable suwayomi filebrowser nhentai-scraper
    systemctl start suwayomi filebrowser nhentai-scraper
}

function print_links() {
    IP=$(hostname -I | awk '{print $1}')
    echo -e "\n[*] Access Links:"
    echo "Suwayomi Web: http://$IP:4567/"
    echo "Suwayomi GraphQL: http://$IP:4567/api/graphql"
    echo "FileBrowser: http://$IP:8080/"
}

# ===============================
# MAIN
# ===============================

echo "===================================================="
echo "           NHentai Scraper INSTALLER               "
echo "===================================================="
echo ""
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