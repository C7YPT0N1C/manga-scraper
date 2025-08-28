#!/usr/bin/env bash
# nhscraper-install.sh
# Installer for nhentai-scraper and FileBrowser with extension support

set -e

# ===============================
# ROOT CHECK
# ===============================
if [[ $EUID -ne 0 ]]; then
    echo "[!] Please run as root: sudo ./nhscraper-install.sh --install"
    exit 1
fi

# ===============================
# VARIABLES
# ===============================
NHENTAI_DIR="/opt/nhentai-scraper"
FILEBROWSER_DIR="/opt/filebrowser"
FILEBROWSER_BIN="/usr/local/bin/filebrowser"
ENV_FILE="$NHENTAI_DIR/nhentai-scraper.env"
REQUIRED_PYTHON_VERSION="3.9"

# ===============================
# FUNCTIONS
# ===============================

check_python_version() {
    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    if [[ $(printf '%s\n' "$REQUIRED_PYTHON_VERSION" "$PYTHON_VERSION" | sort -V | head -n1) != "$REQUIRED_PYTHON_VERSION" ]]; then
        echo "[!] Python $REQUIRED_PYTHON_VERSION+ required. Detected: $PYTHON_VERSION"
        exit 1
    else
        echo "[+] Python version OK: $PYTHON_VERSION"
    fi
}

install_system_packages() {
    echo "[*] Installing system packages..."
    apt update -y && apt full-upgrade -y && apt autoremove -y && apt clean -y
    apt-get install -y python3 python3-pip python3-venv git build-essential curl wget dnsutils tor torsocks
    echo "[+] System packages installed."
}

install_python_packages() {
    echo "[*] Installing Python requirements..."
    source "$NHENTAI_DIR/venv/bin/activate"
    "$NHENTAI_DIR/venv/bin/pip" install --upgrade pip setuptools wheel
    "$NHENTAI_DIR/venv/bin/pip" install --editable "$NHENTAI_DIR" "requests[socks]" "pysocks" "tqdm"
    export PATH="$NHENTAI_DIR/venv/bin:$PATH"
    echo "[+] Python packages installed."
}

install_scraper() {
    echo "[*] Installing nhentai-scraper..."
    mkdir -p "$NHENTAI_DIR"
    cd "$NHENTAI_DIR"
    branch="main"
    if [ ! -d "$NHENTAI_DIR/.git" ]; then
        git clone --depth 1 --branch "$branch" https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR" || \
        git clone --depth 1 --branch "$branch" https://github.com/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"
    else
        git pull || echo "[!] Could not update scraper repo"
    fi

    if [ ! -d "$NHENTAI_DIR/venv" ]; then
        python3 -m venv "$NHENTAI_DIR/venv"
    fi
    source "$NHENTAI_DIR/venv/bin/activate"
    install_python_packages
    ln -sf "$NHENTAI_DIR/venv/bin/nhentai-scraper" /usr/local/bin/nhentai-scraper
    echo "[+] nhentai-scraper installed."
}

install_filebrowser() {
    echo "[*] Installing FileBrowser..."
    mkdir -p $FILEBROWSER_DIR
    curl -fsSLO https://raw.githubusercontent.com/filebrowser/get/master/get.sh
    bash get.sh
    rm -f get.sh
    filebrowser config init --database /opt/filebrowser/filebrowser.db --address 0.0.0.0
    echo "[+] FileBrowser installed."
}

create_env_file() {
    echo "[*] Creating environment file..."
    sudo tee "$ENV_FILE" > /dev/null <<EOF
# NHentai Scraper Configuration
NHENTAI_DIR=/opt/nhentai-scraper
DOWNLOAD_PATH=/opt/nhentai-scraper/downloads
EXTENSION_DOWNLOAD_PATH=
THREADS_GALLERIES=1
THREADS_IMAGES=4
USE_TOR=false
NHENTAI_DRY_RUN=false
GRAPHQL_URL=http://127.0.0.1:4567/api/graphql
NHENTAI_MIRRORS=https://i.nhentai.net

# CLI default flags
RANGE_START=592000
RANGE_END=600000
GALLERIES=
ARTIST=
GROUP=
TAG=
PARODY=
EXCLUDED_TAGS=
LANGUAGE=english
TITLE_TYPE=english
TITLE_SANITISE=true
VERBOSE=false
EOF
    echo "[+] Environment file created at $ENV_FILE"
}

start_install() {
    check_python_version
    install_system_packages
    install_scraper
    install_filebrowser
    create_env_file
    echo "[+] Installation complete!"
}

# ===============================
# MAIN
# ===============================
echo "===================================================="
echo "           nhentai-scraper INSTALLER               "
echo "===================================================="

case "$1" in
    --install)
        start_install
        ;;
    --update-env)
        echo "[*] Updating environment variables..."
        create_env_file
        echo "[+] Environment updated"
        ;;
    --update)
        echo "[*] Updating repository and Python packages..."
        cd "$NHENTAI_DIR"
        git pull
        source "$NHENTAI_DIR/venv/bin/activate"
        pip install --upgrade pip setuptools wheel
        pip install --editable "$NHENTAI_DIR"
        echo "[+] Update complete"
        ;;
    --uninstall|--remove)
        echo "[*] Uninstalling scraper..."
        systemctl stop nhscraper-api || true
        systemctl disable nhscraper-api || true
        rm -rf "$NHENTAI_DIR"
        echo "[+] nhentai-scraper uninstalled"
        ;;
    *)
        echo "[!] Invalid or missing argument. Options:"
        echo "    --install"
        echo "    --install-extension <name>"
        echo "    --uninstall-extension <name>"
        echo "    --update-env"
        echo "    --update"
        echo "    --uninstall / --remove"
        exit 1
        ;;
esac