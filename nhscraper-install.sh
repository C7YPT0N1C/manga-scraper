#!/usr/bin/env bash
set -e

# ===============================
# KEY
# ===============================
# [*] = Process / In Progress
# [+] = Success
# [!] = Warning/Error

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
SUWAYOMI_DIR="/opt/suwayomi"
FILEBROWSER_BIN="/usr/local/bin/filebrowser"
ENV_FILE="$NHENTAI_DIR/nhentai-scraper.env"
REQUIRED_PYTHON_VERSION="3.9" # Minimum required Python version # Updatable, update as needed.

# ===============================
# INSTALL FUNCTIONS
# ===============================
function check_python_version() {
    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')

    if [[ $(printf '%s\n' "$REQUIRED_PYTHON_VERSION" "$PYTHON_VERSION" | sort -V | head -n1) != "$REQUIRED_PYTHON_VERSION" ]]; then
        echo "[!] Python $REQUIRED_PYTHON_VERSION+ required. Detected: $PYTHON_VERSION"
        exit 1
    else
        echo -e "\n[!] Python version OK: $PYTHON_VERSION"
    fi
}

function install_system_packages() {
    echo -e "\n[*] Upgrading and Installing system packages..."
    apt update -y && apt full-upgrade -y && apt autoremove -y && apt clean -y
    apt-get update
    apt-get install -y python3 python3-pip python3-venv git build-essential curl wget dnsutils tor torsocks # Updatable, update as needed.
    echo "[+] System packages installed."
}    

function install_python_packages() {
    echo -e "\n[*] Installing Python requirements in venv..."
    source "$NHENTAI_DIR/venv/bin/activate"
    pip install --upgrade pip setuptools wheel # Updatable, update as needed.
    pip install .
    echo "[+] Python requirements installed."
}

# ----------------------------
# Install Programs
# ----------------------------
function install_scraper() { # Updatable, update as needed.
    echo -e "\n[*] Installing nhentai-scraper..."
    mkdir -p "$NHENTAI_DIR"
    cd "$NHENTAI_DIR"

    echo -e "\n[!] Install Beta Version instead of Stable? This is NOT recommended, are there is no guarantee it will be compatible."
    read -p "Procceed anyway? [y/N]: " beta
    branch="main"
    [[ "$beta" =~ ^[yY] ]] && branch="dev"

    if [ ! -d "$NHENTAI_DIR/.git" ]; then
        echo "[*] Cloning nhentai-scraper..."
        if git clone --depth 1 --branch "$branch" https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"; then
            echo "[+] Cloned from Gitea."
        else
            echo "[!] Gitea clone failed, trying GitHub..."
            if ! git clone --depth 1 --branch "$branch" https://github.com/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR"; then
                echo "[!] Both clone attempts failed. Please check your network connection."
                exit 1
            fi
        fi
    else
        git pull || echo "[!] Could not update scraper repo. Please check your network connection."
    fi

    if [ ! -d "$NHENTAI_DIR/venv" ]; then
        echo "[*] Creating venv..."
        python3 -m venv "$NHENTAI_DIR/venv"
        source "$NHENTAI_DIR/venv/bin/activate"
        "$NHENTAI_DIR/venv/bin/pip" install --upgrade pip setuptools wheel
    else
        source "$NHENTAI_DIR/venv/bin/activate"
    fi

    # Install via pyproject.toml (editable mode)
    pip install -e .

    install_python_packages

    # Create/refresh global symlink
    ln -sf "$NHENTAI_DIR/venv/bin/nh-scraper" /usr/local/bin/nh-scraper
    echo "[+] Global command 'nh-scraper' installed."
}

function install_suwayomi() { # Updatable, update as needed.
    echo -e "\n[*] Installing Suwayomi..."
    mkdir -p "$SUWAYOMI_DIR"
    cd "$SUWAYOMI_DIR"

    TARA_URL="https://github.com/Suwayomi/Suwayomi-Server/releases/download/v2.1.1867/Suwayomi-Server-v2.1.1867-linux-x64.tar.gz"
    wget -O suwayomi-server.tar.gz "$TARA_URL"
    tar -xzf suwayomi-server.tar.gz --strip-components=1
    rm suwayomi-server.tar.gz

    mkdir -p "$SUWAYOMI_DIR/local"
    chmod 755 "$SUWAYOMI_DIR/local"
    echo "[+] Suwayomi installed."
}

function install_filebrowser() { # Updatable, update as needed.
    echo -e "\n[*] Installing FileBrowser..."

    # Download installer
    curl -fsSLO https://raw.githubusercontent.com/filebrowser/get/master/get.sh
    bash get.sh
    rm -f get.sh

    # Initialize default config in current user's home (~/.filebrowser)
    filebrowser config init -a 0.0.0.0

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
        filebrowser users update admin --password "$FILEBROWSER_PASS" --perm.admin
        echo "[*] Admin user password updated."
    else
        filebrowser users add admin "$FILEBROWSER_PASS" --perm.admin
        echo "[*] Admin user created."
    fi

    echo "[+] FileBrowser installed. Access at http://<SERVER-IP>:8080 with username 'admin'."
}

function create_env_file() { # Updatable, update as needed.
    echo -e "\n[*] Updating nhentai-scraper Environment File..."
    echo "[!] This will overwrite current settings. CTRL + C now to cancel."
    read -p "Enter your NHentai session cookie: " COOKIE
    sudo tee "$ENV_FILE" > /dev/null <<EOF
NHENTAI_START_ID=500000
NHENTAI_END_ID=600000
EXCLUDE_TAGS=
LANGUAGE=english
SUWAYOMI_IGNORED_CATEGORIES=Favourites, Favs
NHENTAI_COOKIE=$COOKIE
NHENTAI_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.5790.171 Safari/537.36"
THREADS_GALLERIES=3
THREADS_IMAGES=5
USE_TOR=false
EOF
    echo "[+] Environment file created/updated at $ENV_FILE"
}

function reload_systemd_services() { # Updatable, update as needed.
    systemctl daemon-reload
    systemctl restart filebrowser # Just in case FileBrowser's installer started it already  
    for svc in suwayomi filebrowser nhentai-api tor; do
        if systemctl list-unit-files | grep -qw "${svc}.service"; then
            systemctl enable "$svc"
            systemctl start "$svc"
            echo "[+] $svc enabled and started."
        else
            echo "[!] $svc.service not found, skipping."
        fi
    done
}

function create_systemd_services() { # Updatable, update as needed.
    echo -e "\n[*] Creating systemd services..."

    # Suwayomi
    if [ ! -f /etc/systemd/system/suwayomi.service ]; then
        sudo tee /etc/systemd/system/suwayomi.service > /dev/null <<EOF
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
        sudo tee /etc/systemd/system/filebrowser.service > /dev/null <<EOF
[Unit]
Description=FileBrowser
After=network.target

[Service]
ExecStart=/usr/local/bin/filebrowser -r / --address 0.0.0.0 --port 8080
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    # API service
    echo "[*] Creating nhentai-api.service..."

    if [ ! -f /etc/systemd/system/nhentai-api.service ]; then
        sudo tee /etc/systemd/system/nhentai-api.service > /dev/null <<EOF
[Unit]
Description=NHentai Scraper API.
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/nhentai-scraper/nhentai-scraper
EnvironmentFile=/opt/nhentai-scraper/nhentai-scraper.env
ExecStart=/opt/nhentai-scraper/venv/bin/python /opt/nhentai-scraper/nhentai-scraper/nhentai-api.py
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
    fi

    if [ -f /etc/systemd/system/nhentai-api.service ]; then
        echo "[+] nhentai-api.service created."
    else
        echo "[!] Failed to create nhentai-api.service."

        exit 1
    fi

    reload_systemd_services
    echo "[+] systemd services created and started."
}

function print_links() {
    IP=$(hostname -I | awk '{print $1}')
    HOSTNAME=$(hostname)

    echo -e "\n[+] Access Links:"
    echo "Suwayomi Web: http://$IP:4567/"
    echo "Suwayomi GraphQL: http://$IP:4567/api/graphql"
    echo "FileBrowser: http://$IP:8080/ (User: admin, Password: $FILEBROWSER_PASS)"
    echo "Scraper API Endpoint: http://$IP:5000/status"
    if [ ! -z "$HOSTNAME" ]; then
        echo -e "\n[+] DNS Hostname Links:"
        echo "Suwayomi Web: http://$HOSTNAME:4567/"
        echo "Suwayomi GraphQL: http://$HOSTNAME:4567/api/graphql"
        echo "FileBrowser: http://$HOSTNAME:8080/"
        echo "Scraper API Endpoint: http://$HOSTNAME:5000/status"
    fi
}

# ===============================
# INSTALLER ARGUMENTS
# ===============================
function start_install() {
    check_python_version
    echo -e "\n[*] Starting installation..."
    echo "[!] This may take a while depending on your internet speed and system performance."

    install_system_packages
    install_scraper
    install_suwayomi
    install_filebrowser
    create_env_file
    create_systemd_services
    print_links

    echo -e "\n[+] Installation complete!"
}

function update_all() {
    echo -e "\n[*] Updating nhentai-scraper and Suwayomi..."
    install_scraper
    install_suwayomi
    ln -sf "$NHENTAI_DIR/venv/bin/nh-scraper" /usr/local/bin/nh-scraper # refresh symlink

    echo -e "\n[*] Restarting services..."
    reload_systemd_services
    echo -e "\n[+] Update complete!"
}

function update_env() {
    echo -e "\n[*] Updating Environment File!"
    create_env_file
    reload_systemd_services
}

function uninstall_all() {
    echo -e "\n[*] Stopping and disabling services..."
    systemctl stop suwayomi filebrowser nhentai-api || true
    systemctl disable suwayomi filebrowser nhentai-api || true

    echo -e "\n[*] Removing systemd service files..."
    rm -f /etc/systemd/system/suwayomi.service
    rm -f /etc/systemd/system/filebrowser.service
    rm -f /etc/systemd/system/nhentai-api.service
    
    systemctl daemon-reload

    echo -e "\n[*] Removing installed directories..."
    rm -rf "$NHENTAI_DIR"
    rm -rf "$SUWAYOMI_DIR"
    rm -rf /etc/filebrowser/filebrowser.db

    echo -e "\n[+] Uninstallation complete!"
}

# ===============================
# MAIN
# ===============================
echo "===================================================="
echo "           nhentai-scraper INSTALLER               "
echo "===================================================="
echo ""
echo "This installer will install, update, or uninstall the following components:"
echo "- C7YPT0N1C/nhentai-scraper"
echo "- Suwayomi/Suwayomi-Server"
echo "- fileBrowser/fileBrowser"
echo ""

read -p "Do you want to proceed? [y/N]: " consent
case "$consent" in
    [yY]|[yY][eE][sS]) echo "[*] Consent given. Continuing..." ;;
    *) echo "[!] Operation cancelled by user."; exit 0 ;;
esac

if [[ -z "$1" || "$1" == "--install" ]]; then
    start_install
    exit 0
elif [[ "$1" == "--update-env" ]]; then
    update_env
    exit 0
elif [[ "$1" == "--update" ]]; then
    update_all
    print_links
    exit 0
elif [[ "$1" == "--uninstall" || "$1" == "--remove" ]]; then
    uninstall_all
    exit 0
fi