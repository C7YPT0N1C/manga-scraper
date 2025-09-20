#!/usr/bin/env bash
# nhscraper-install.sh
# Installer for nhentai-scraper and FileBrowser with extension support

set -e

# ===============================
# ROOT CHECK
# ===============================
if [[ $EUID -ne 0 ]]; then
    echo "Please run as root: sudo ./nhscraper-install.sh --install"
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
        echo "Python $REQUIRED_PYTHON_VERSION+ required. Detected: $PYTHON_VERSION"
        exit 1
    else
        echo -e "\nPython version OK: $PYTHON_VERSION"
    fi
}

install_system_packages() {
    echo -e "\nInstalling system packages..."
    apt update -y && apt full-upgrade -y && apt autoremove -y && apt clean -y
    apt-get install -y python3 python3-pip python3-venv git build-essential curl wget dnsutils tor torsocks
    echo "System packages installed."
}

install_python_packages() {
    echo "Installing Python requirements..."
    source "$NHENTAI_DIR/venv/bin/activate"
    "$NHENTAI_DIR/venv/bin/pip" install --upgrade pip setuptools wheel
    "$NHENTAI_DIR/venv/bin/pip" install --editable "$NHENTAI_DIR" "requests[socks]" "tqdm"
    export PATH="$NHENTAI_DIR/venv/bin:$PATH"
    echo "Python packages installed."
}

install_filebrowser() {
        echo -e "\nInstalling FileBrowser..."

    mkdir -p $FILEBROWSER_DIR

    # Download installer
    curl -fsSLO https://raw.githubusercontent.com/filebrowser/get/master/get.sh
    bash get.sh
    rm -f get.sh

    # Remove old database if it exists
    if [ -f "$FB_DB" ]; then
        echo "Removing old FileBrowser database..."
        rm -f "$FB_DB"
    fi
    
    # Initialise default config in current user's home (~/.filebrowser)
    filebrowser config init --database /opt/filebrowser/filebrowser.db --address 0.0.0.0


    # Prompt for password
    echo -n "[?] Enter FileBrowser admin password: "
    read -s FILEBROWSER_PASS
    echo

    # Generate random password if empty
    if [ -z "$FILEBROWSER_PASS" ]; then
        FILEBROWSER_PASS=$(openssl rand -base64 16)
        echo "No password entered. Generated random password: $FILEBROWSER_PASS"
        echo "Please save this password!"
    fi

    # Create or update admin user in default database
    if filebrowser users list | grep -qw admin; then
        filebrowser users update admin --password "$FILEBROWSER_PASS" --database "$FILEBROWSER_DIR/filebrowser.db" --perm.admin
        echo "Admin user password updated."
    else
        filebrowser users add admin "$FILEBROWSER_PASS" --database "$FILEBROWSER_DIR/filebrowser.db" --perm.admin
        echo "Admin user created."
    fi

    echo -e "\nFileBrowser installed. Access at http://<SERVER-IP>:8080 with username 'admin'."
    echo "Please save this password: $FILEBROWSER_PASS"
}

install_scraper() {
    echo -e "\nInstalling nhentai-scraper..."
    #branch="main"
    branch="dev-testing"  # Change to 'dev' for testing latest features

    if [ ! -d "$NHENTAI_DIR/.git" ]; then
        echo "Cloning nhentai-scraper repo (branch: $branch)..."
        git clone --depth 1 --branch "$branch" https://code.zenithnetwork.online/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR" || \
        git clone --depth 1 --branch "$branch" https://github.com/C7YPT0N1C/nhentai-scraper.git "$NHENTAI_DIR" || {
            echo "Failed to clone nhentai-scraper repo."
            exit 1
        }
    else
        echo "Updating existing repo (branch: $branch)..."
        git -C "$NHENTAI_DIR" fetch origin "$branch" && git -C "$NHENTAI_DIR" checkout "$branch" && git -C "$NHENTAI_DIR" pull || {
            echo "Could not update repo on branch $branch"
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

    # Symlink Local Manifest
    ln -sf "$NHENTAI_DIR/nhscraper/extensions/local_manifest.json" $NHENTAI_DIR/local_manifest.json

    echo -e "\nnhentai-scraper (branch: $branch) installed at $NHENTAI_DIR"
}

create_env_file() {
    # Update defaults in Config.py
    echo -e "\nUpdating environment variables..."
    echo "Creating environment file..."
    sudo tee "$ENV_FILE" > /dev/null <<EOF
# NHentai Scraper Configuration

# Custom (Username and Password must be manually set for now)
# TEST
AUTH_USERNAME = "Username"
AUTH_PASSWORD = "Password"

# Directories
NHENTAI_DIR=/opt/nhentai-scraper

# Default Paths
DOWNLOAD_PATH=
DOUJIN_TXT_PATH=

# Extensions
EXTENSION=
EXTENSION_DOWNLOAD_PATH=

# APIs and Mirrors
NHENTAI_API_BASE=
NHENTAI_MIRRORS=

# Gallery ID selection
HOMEPAGE_RANGE_START=
HOMEPAGE_RANGE_END=
RANGE_START=
RANGE_END=
GALLERIES=

# Filters
EXCLUDED_TAGS=
LANGUAGE=
TITLE_TYPE=

# Threads
THREADS_GALLERIES=
THREADS_IMAGES=
MAX_RETRIES=
MIN_SLEEP=
MAX_SLEEP=

# Download Options
USE_TOR=true
DRY_RUN=false
VERBOSE=false
DEBUG=false
EOF
    echo "Environment file created at $ENV_FILE"
    echo "Environment updated."
}

create_systemd_services() {
    echo -e "\nSetting up systemd services..."
        # FileBrowser
    echo "Creating systemd service for FileBrowser..."
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
    echo "Creating systemd service for nhscraper-api..."
    if [ ! -f /etc/systemd/system/nhscraper-api.service ]; then
        sudo tee /etc/systemd/system/nhscraper-api.service > /dev/null <<EOF
[Unit]
Description=NHentai Scraper API
After=network.target

[Service]
Type=simple
WorkingDirectory=$NHENTAI_DIR
ExecStart=$NHENTAI_DIR/venv/bin/python3 $NHENTAI_DIR/nhscraper/core/api.py
Restart=always
EnvironmentFile=$ENV_FILE

[Install]
WantedBy=multi-user.target
EOF
    fi

    systemctl daemon-reexec
    systemctl enable filebrowser nhscraper-api tor
    systemctl restart filebrowser nhscraper-api tor
    echo "Systemd services 'filebrowser', 'nhscraper-api' 'tor' created and started."
}

print_links() {
    IP=$(hostname -I | awk '{print $1}')
    HOSTNAME=$(hostname)

    echo -e "\nAccess Links:"
    echo "FileBrowser: http://$IP:8080/files/opt/ (User: admin, Password: $FILEBROWSER_PASS)"
    echo "Scraper API Dashboard: http://$IP:5000/dashboard"
    echo "Scraper API Endpoint: http://$IP:5000/status"
    if [ ! -z "$HOSTNAME" ]; then
        echo -e "\nDNS Hostname Links:"
        echo "FileBrowser: http://$HOSTNAME:8080/files/opt/ (User: admin, Password: $FILEBROWSER_PASS)"
        echo "Scraper API Dashboard: http://$HOSTNAME:5000/dashboard"
        echo "Scraper API Endpoint: http://$HOSTNAME:5000/status"
    fi
}

start_uninstall() {
    echo ""
    echo "===================================================="
    echo "           nhentai-scraper UNINSTALLER              "
    echo "===================================================="
    echo "This will REMOVE nhentai-scraper, FileBrowser, and related services."
    echo "TOR WILL NOT BE STOPPED OR REMOVED FOR SECURITY REASONS. IF YOU DO NOT WANT TOR, YOU MUST REMOVE IT MANUALLY."
    read -p "    Do you want to continue? (y/n): " choice
    case "$choice" in
        y|Y)
            echo "Uninstalling..."

            echo ""
            # Remove Directories and files with status reporting
            for target in /opt/filebrowser/ "$NHENTAI_DIR"; do
                if [ -e "$target" ]; then
                    rm -rf "$target" && echo "Removed: $target" || echo "Failed to remove: $target"
                else
                    echo "Not found (skipped): $target"
                fi
            done

            echo ""
            # Remove symlinks with status reporting
            for link in /usr/local/bin/filebrowser /usr/local/bin/nhentai-scraper; do
                if [ -L "$link" ] || [ -e "$link" ]; then
                    rm -f "$link" && echo "Removed: $link" || echo "Failed to remove: $link"
                else
                    echo "Not found (skipped): $link"
                fi
            done

            # Reload systemd and stop services
            systemctl disable filebrowser nhscraper-api || true
            systemctl stop filebrowser nhscraper-api || true

            # Remove systemd services with status reporting
            for svc in /etc/systemd/system/filebrowser.service /etc/systemd/system/nhscraper-api.service; do
                if [ -e "$svc" ]; then
                    rm -f "$svc" && echo "Removed: $svc" || echo "Failed to remove: $svc"
                else
                    echo "Not found (skipped): $svc"
                fi
            done

            echo -e "\nStopped and disabled services:"
            echo "    filebrowser"
            echo "    nhscraper-api"

            # Reload systemd
            systemctl daemon-reload

            echo -e "\nUninstallation complete."
            exit 0
            ;;
        *)
            echo -e "\nUninstallation aborted."
            exit 1
            ;;
    esac
}

start_update() {
    echo ""
    echo "===================================================="
    echo "           nhentai-scraper UPDATER                  "
    echo "===================================================="
    read -p "Are you sure you want to update? (y/N): " confirm
    confirm=${confirm,,}  # lowercase input

    if [[ "$confirm" != "y" && "$confirm" != "yes" ]]; then
        echo "Update cancelled."
        return
    fi

    echo "Which branch would you like to update to? (default: main)"
    read -p "Enter branch name: " branch
    branch=${branch:-main}  # default to main if empty

    echo "Updating repository to branch '$branch'..."
    cd "$NHENTAI_DIR" || { echo "Error: could not cd into $NHENTAI_DIR"; return 1; }

    # Reset and fetch branch (force overwrite local changes)
    git fetch origin
    git reset --hard "origin/$branch" || { echo "Branch '$branch' not found!"; return 1; }

    # Update Python environment
    source "$NHENTAI_DIR/venv/bin/activate"
    pip install --upgrade pip setuptools wheel
    pip install --editable "$NHENTAI_DIR"

    check_python_version
    install_system_packages
    create_env_file
    create_systemd_services
    print_links

    echo "Update complete (branch: $branch)"
}

update_env_file() {
    echo ""
    echo "===================================================="
    echo "           nhentai-scraper .ENV UPDATER             "
    echo "===================================================="
    read -p "Are you sure you want to update the .env file? (y/N): " confirm
    confirm=${confirm,,}  # lowercase input

    if [[ "$confirm" != "y" && "$confirm" != "yes" ]]; then
        echo ".env update cancelled."
        return
    fi

    create_env_file
}

start_install() {
    echo ""
    echo "This will install nhentai-scraper, FileBrowser, and set up the API as a service."
    read -p "    Do you want to continue? (y/n): " choice
    case "$choice" in
        y|Y)
            echo -e "\nStarting installation..."
            check_python_version
            install_system_packages
            install_filebrowser
            install_scraper
            create_env_file
            create_systemd_services
            print_links
            echo -e "\nInstallation complete!"

            # Run nhentai-scraper commands after installation to initialise config, files, etc
            nhentai-scraper --help
            nhentai-scraper --install-extension skeleton
            nhentai-scraper --install-extension suwayomi

            exit 0
            ;;
        *)
            echo -e "\nInstallation aborted."
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
        update_env_file
        ;;
    --update)
        start_update
        ;;
    --uninstall|--remove)
        start_uninstall
        ;;
    *)
        echo "Invalid or missing argument. Options:"
        echo "    --install"
        echo "    --update-env"
        echo "    --update"
        echo "    --uninstall / --remove"
        exit 1
        ;;
esac