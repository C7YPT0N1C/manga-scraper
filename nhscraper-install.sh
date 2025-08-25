#!/usr/bin/env bash
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
        echo "[*] Python version OK: $PYTHON_VERSION"
    fi
}

function install_system_packages() {
    echo "[*] Upgrading and Installing system packages..."
    apt update -y && apt full-upgrade -y && apt autoremove -y && apt clean -y
    apt-get update
    apt-get install -y python3 python3-pip python3-venv git build-essential curl wget dnsutils tor torsocks # Updatable, update as needed.
    echo "[+] System packages installed."
}    

function install_python_packages() {
    echo "[*] Installing Python requirements in venv..."
    source "$NHENTAI_DIR/venv/bin/activate"
    pip install --upgrade pip setuptools wheel # Updatable, update as needed.
    pip install .
    echo "[+] Python requirements installed."
}

# ----------------------------
# Install Programs
# ----------------------------
function install_scraper() { # Updatable, update as needed.
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

function install_filebrowser() { # Updatable, update as needed.
    echo "[*] Installing FileBrowser..."

    # Download installer script instead of piping directly to bash
    curl -fsSLO https://raw.githubusercontent.com/filebrowser/get/master/get.sh

    # Optional: Verify checksum (recommended if you maintain your own checksum file)
    # echo "expected_sha256  get.sh" | sha256sum -c -

    # Run the installer explicitly
    bash get.sh

    # Clean up script
    rm -f get.sh

    # Setup FileBrowser config
    mkdir -p /etc/nhentai-scraper/filebrowser
    filebrowser config init -a 0.0.0.0

    # Prompt for password instead of hardcoding
    echo -n "[?] Enter FileBrowser admin password: "
    read -s FILEBROWSER_PASS
    echo

    # If empty, generate a random secure password
    if [ -z "$FILEBROWSER_PASS" ]; then
        FILEBROWSER_PASS=$(openssl rand -base64 16)
        echo "[!] No password entered. Generated random password: $FILEBROWSER_PASS"
        echo "[!] Please save this password!"
    fi

    # Create admin user
    filebrowser users add admin "$FILEBROWSER_PASS" --perm.admin
    echo "[+] FileBrowser installed. Access it at http://<SERVER-IP>:8080 with username 'admin'."
}

function create_env_file() { # Updatable, update as needed.
    echo "[*] Updating nhentai-scraper Environment File..."
    echo "[*] This will overwrite current settings. CTRL + C now to cancel."
    read -p "Enter your NHentai session cookie: " COOKIE
    cat >"$ENV_FILE" <<EOF
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

function create_systemd_services() { # Updatable, update as needed.
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
Description=NHentai Scraper API.
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/nhentai-scraper/nhentai-scraper
EnvironmentFile=/opt/nhentai-scraper/nhentai-api.env
ExecStart=/opt/nhentai-scraper/venv/bin/python /opt/nhentai-scraper/nhentai-scraper/nhentai-api.py
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
    echo "FileBrowser: http://$IP:8080/ (User: admin, Password: $FILEBROWSER_PASS)"
    echo "Scraper API Endpoint: http://$IP:5000/status"
    if [ ! -z "$HOSTNAME" ]; then
        echo -e "\nDNS Hostname Links:"
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
    echo ""
    echo "[*] Starting installation..."
    echo "[*] This may take a while depending on your internet speed and system performance."

    install_system_packages
    install_scraper
    install_suwayomi
    install_filebrowser
    create_env_file
    create_systemd_services
    print_links

    echo "[*] Installation complete!"
}

function update_all() {
    echo "[*] Updating nhentai-scraper and Suwayomi..."
    install_scraper
    install_suwayomi
    ln -sf "$NHENTAI_DIR/venv/bin/nh-scraper" /usr/local/bin/nh-scraper # refresh symlink

    echo "[*] Restarting services..."
    systemctl restart suwayomi filebrowser nhentai-api
    echo "[*] Update complete!"
}

function update_env() {
    echo "[*] Testing 'UPDATE ENV'!"
    create_env_file
}

function uninstall_all() {
    echo "[*] Stopping and disabling services..."
    systemctl stop suwayomi filebrowser nhentai-api || true
    systemctl disable suwayomi filebrowser nhentai-api || true

    echo "[*] Removing systemd service files..."
    rm -f /etc/systemd/system/suwayomi.service
    rm -f /etc/systemd/system/filebrowser.service
    rm -f /etc/systemd/system/nhentai-api.service
    
    systemctl daemon-reload

    echo "[*] Removing installed directories..."
    rm -rf "$NHENTAI_DIR"
    rm -rf "$SUWAYOMI_DIR"
    rm -rf /etc/filebrowser/filebrowser.db

    echo "[*] Uninstallation complete!"
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
echo "- Suwayomi Server"
echo "- FileBrowser"
echo ""

AUTO_YES=false
for arg in "$@"; do
    if [[ "$arg" == "-y" || "$arg" == "--yes" ]]; then
        AUTO_YES=true
    fi
done

if [ "$AUTO_YES" = true ]; then
    echo "[*] Auto-consent given via -y flag."
else
    read -p "Do you want to proceed? [y/N]: " consent
    case "$consent" in
        [yY]|[yY][eE][sS]) echo "[*] Consent given. Continuing..." ;;
        *) echo "[!] Operation cancelled by user."; exit 0 ;;
    esac
fi

if [[ "$1" == "--install" ]]; then
    start_install
    exit 0
elif [[ "$1" == "--update-env" ]]; then
    update_env
    exit 0
elif [[ "$1" == "--update" ]]; then
    update_all
    print_links
    exit 0
elif [[ "$1" == "--uninstall" ]]; then
    uninstall_all
    exit 0
fi