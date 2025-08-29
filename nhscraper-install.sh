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
        echo -e "\n[+] Python version OK: $PYTHON_VERSION"
    fi
}

install_system_packages() {
    echo -e "\n[*] Installing system packages..."
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

install_filebrowser() {
        echo -e "\n[*] Installing FileBrowser..."

    mkdir -p $FILEBROWSER_DIR

    # Download installer
    curl -fsSLO https://raw.githubusercontent.com/filebrowser/get/master/get.sh
    bash get.sh
    rm -f get.sh

    # Remove old database if it exists
    if [ -f "$FB_DB" ]; then
        echo "[*] Removing old FileBrowser database..."
        rm -f "$FB_DB"
    fi
    
    # Initialize default config in current user's home (~/.filebrowser)
    filebrowser config init --database /opt/filebrowser/filebrowser.db --address 0.0.0.0


    # Prompt for password
    echo -n "[?] Enter FileBrowser admin password: "
    read -s FILEBROWSER_PASS
    echo

    # Generate random password if empty
    if [ -z "$FILEBROWSER_PASS" ]; then
        FILEBROWSER_PASS=$(openssl rand -base64 16)
        echo "[!] No password entered. Generated random password: $FILEBROWSER_PASS"
        echo "[!] Please save this password!"
    fi

    # Create or update admin user in default database
    if filebrowser users list | grep -qw admin; then
        filebrowser users update admin --password "$FILEBROWSER_PASS" --database "$FILEBROWSER_DIR/filebrowser.db" --perm.admin
        echo "[*] Admin user password updated."
    else
        filebrowser users add admin "$FILEBROWSER_PASS" --database "$FILEBROWSER_DIR/filebrowser.db" --perm.admin
        echo "[*] Admin user created."
    fi

    echo -e "\n[+] FileBrowser installed. Access at http://<SERVER-IP>:8080 with username 'admin'."
    echo "[!] Please save this password: $FILEBROWSER_PASS"
}

install_scraper() {
    echo -e "\n[*] Installing nhentai-scraper..."
    #branch="main"
    branch="dev"  # Change to 'dev' for testing latest features

    if [ ! -d "$NHENTAI_DIR/.git" ]; then
        echo "[*] Cloning nhentai-scraper repo (branch: $branch)..."
        git clone --depth 1 --branch "$branch" https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR" || \
        git clone --depth 1 --branch "$branch" https://github.com/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR" || {
            echo "[!] Failed to clone nhentai-scraper repo."
            exit 1
        }
    else
        echo "[*] Updating existing repo (branch: $branch)..."
        git -C "$NHENTAI_DIR" fetch origin "$branch" && git -C "$NHENTAI_DIR" checkout "$branch" && git -C "$NHENTAI_DIR" pull || {
            echo "[!] Could not update repo on branch $branch"
        }
    fi

    # Setup Python venv
    if [ ! -d "$NHENTAI_DIR/venv" ]; then
        python3 -m venv "$NHENTAI_DIR/venv"
    fi
    source "$NHENTAI_DIR/venv/bin/activate"

    install_python_packages

    # Symlink CLI
    ln -sf "$NHENTAI_DIR/venv/bin/nhentai-scraper" /usr/local/bin/nhentai-scraper

    echo -e "\n[+] nhentai-scraper (branch: $branch) installed at $NHENTAI_DIR"
}

create_env_file() {
    echo -e "\n[*] Updating environment variables..."
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
    echo "[+] Environment updated."
}

create_systemd_services() {
    echo -e "\n[*] Setting up systemd services..."
        # FileBrowser
    echo "[*] Creating systemd service for FileBrowser..."
    if [ ! -f /etc/systemd/system/filebrowser.service ]; then
        sudo tee /etc/systemd/system/filebrowser.service > /dev/null <<EOF
[Unit]
Description=FileBrowser
After=network.target

[Service]
ExecStart=/usr/local/bin/filebrowser -d /opt/filebrowser/filebrowser.db -r / --address 0.0.0.0 --port 8080
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    # nhscraper-api
    echo "[*] Creating systemd service for nhscraper-api..."
    if [ ! -f /etc/systemd/system/nhscraper-api.service ]; then
        sudo tee /etc/systemd/system/nhscraper-api.service > /dev/null <<EOF
[Unit]
Description=NHentai Scraper API
After=network.target

[Service]
Type=simple
WorkingDirectory=$NHENTAI_DIR
ExecStart=$NHENTAI_DIR/venv/bin/python3 $NHENTAI_DIR/nhscraper/api.py
Restart=always
EnvironmentFile=$ENV_FILE

[Install]
WantedBy=multi-user.target
EOF
    fi

    systemctl daemon-reexec
    systemctl enable filebrowser nhscraper-api tor
    systemctl restart filebrowser nhscraper-api tor
    echo "[+] Systemd services 'filebrowser', 'nhscraper-api' 'tor' created and started."
}

print_links() {
    IP=$(hostname -I | awk '{print $1}')
    HOSTNAME=$(hostname)

    echo -e "\n[+] Access Links:"
    echo "FileBrowser: http://$IP:8080/files/opt/ (User: admin, Password: $FILEBROWSER_PASS)"
    echo "Scraper API Dashboard: http://$IP:5000/dashboard"
    echo "Scraper API Endpoint: http://$IP:5000/status"
    if [ ! -z "$HOSTNAME" ]; then
        echo -e "\n[+] DNS Hostname Links:"
        echo "FileBrowser: http://$HOSTNAME:8080/files/opt/ (User: admin, Password: $FILEBROWSER_PASS)"
        echo "Scraper API Dashboard: http://$HOSTNAME:5000/dashboard"
        echo "Scraper API Endpoint: http://$HOSTNAME:5000/status"
    fi
}

start_uninstall() {
    echo "[*] This will REMOVE nhentai-scraper, FileBrowser, and related services."
    echo "[!] TOR WILL NOT BE STOPPED OR REMOVED FOR SECURITY REASONS. IF YOU DO NOT WANT TOR, YOU MUST REMOVE IT MANUALLY."
    read -p "    Do you want to continue? (y/n): " choice
    case "$choice" in
        y|Y)
            echo "[*] Uninstalling..."

            echo ""
            # Remove Directories and files with status reporting
            for target in /opt/filebrowser/ "$NHENTAI_DIR"; do
                if [ -e "$target" ]; then
                    rm -rf "$target" && echo "[+] Removed: $target" || echo "[!] Failed to remove: $target"
                else
                    echo "[!] Not found (skipped): $target"
                fi
            done

            echo ""
            # Remove symlinks with status reporting
            for link in /usr/local/bin/filebrowser /usr/local/bin/nhentai-scraper; do
                if [ -L "$link" ] || [ -e "$link" ]; then
                    rm -f "$link" && echo "[+] Removed: $link" || echo "[!] Failed to remove: $link"
                else
                    echo "[!] Not found (skipped): $link"
                fi
            done

            # Reload systemd and stop services
            systemctl disable filebrowser nhscraper-api || true
            systemctl stop filebrowser nhscraper-api || true

            # Remove systemd services with status reporting
            for svc in /etc/systemd/system/filebrowser.service /etc/systemd/system/nhscraper-api.service; do
                if [ -e "$svc" ]; then
                    rm -f "$svc" && echo "[+] Removed: $svc" || echo "[!] Failed to remove: $svc"
                else
                    echo "[!] Not found (skipped): $svc"
                fi
            done

            echo -e "\n[*] Stopped and disabled services:"
            echo "    filebrowser"
            echo "    nhscraper-api"

            # Reload systemd
            systemctl daemon-reload

            echo -e "\n[+] Uninstallation complete."
            exit 0
            ;;
        *)
            echo -e "\n[!] Uninstallation aborted."
            exit 1
            ;;
    esac
}

start_update() {
    echo "[*] Updating repository and Python packages..."
    cd "$NHENTAI_DIR"
    git pull
    source "$NHENTAI_DIR/venv/bin/activate"
    pip install --upgrade pip setuptools wheel
    pip install --editable "$NHENTAI_DIR"
    echo "[+] Update complete"
}

start_install() {
    echo "[*] This will install nhentai-scraper, FileBrowser, and set up the API as a service."
    read -p "    Do you want to continue? (y/n): " choice
    case "$choice" in
        y|Y)
            echo -e "\n[*] Starting installation..."
            check_python_version
            install_system_packages
            install_filebrowser
            install_scraper
            create_env_file
            create_systemd_services
            print_links
            echo -e "\n[+] Installation complete!"
            exit 0
            ;;
        *)
            echo -e "\n[!] Installation aborted."
            exit 1
            ;;
    esac
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
        create_env_file
        ;;
    --update)
        start_update
        ;;
    --uninstall|--remove)
        start_uninstall
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