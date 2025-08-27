#!/usr/bin/env bash
# nhscraper-install.sh
# DESCRIPTION: Installer / updater for nhentai-scraper.
# Called by: user via CLI
# Calls: Python scripts, pip, systemctl
# FUNCTION: Install dependencies, set up .env, configure services, create database, optional dashboard password

set -e

INSTALL_DIR="/opt/nhentai-scraper"
ENV_FILE="$INSTALL_DIR/config.env"
DB_FILE="$INSTALL_DIR/nhscraper.db"

echo "[+] Starting nhentai-scraper installer..."

# --- Install dependencies ---
echo "[+] Installing dependencies..."
sudo apt update
sudo apt install -y python3 python3-pip python3-venv tor unzip wget curl sqlite3

# --- Create install directory ---
sudo mkdir -p $INSTALL_DIR
sudo chown $USER:$USER $INSTALL_DIR

# --- Copy files ---
echo "[+] Copying files..."
cp -r ./nhscraper/* $INSTALL_DIR/

# --- Set up Python environment ---
python3 -m venv $INSTALL_DIR/venv
source $INSTALL_DIR/venv/bin/activate
pip install --upgrade pip
pip install -r $INSTALL_DIR/requirements.txt

# --- Initialize .env ---
if [ ! -f "$ENV_FILE" ]; then
    echo "[+] Creating default config.env..."
    touch "$ENV_FILE"
    echo "DASHBOARD_PASS_HASH=" >> "$ENV_FILE"
    echo "THREADS_GALLERIES=1" >> "$ENV_FILE"
    echo "THREADS_IMAGES=4" >> "$ENV_FILE"
    echo "DRY_RUN=False" >> "$ENV_FILE"
    echo "EXCLUDED_TAGS=" >> "$ENV_FILE"
    echo "LANGUAGE=english" >> "$ENV_FILE"
fi

# --- Initialize SQLite DB ---
python3 - <<END
import nhscraper.db as db
db.init_db()
END

# --- Prompt for dashboard password ---
read -s -p "Enter dashboard password: " DASH_PASS
echo
read -s -p "Confirm dashboard password: " DASH_PASS2
echo
if [ "$DASH_PASS" != "$DASH_PASS2" ]; then
    echo "[!] Passwords do not match, aborting."
    exit 1
fi

# --- Hash and store password ---
python3 - <<END
from werkzeug.security import generate_password_hash
import os
from dotenv import set_key
ENV_FILE="$ENV_FILE"
hash_val = generate_password_hash("$DASH_PASS")
set_key(ENV_FILE, "DASHBOARD_PASS_HASH", hash_val)
print("[+] Dashboard password set.")
END

# --- Create systemd service ---
echo "[+] Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/nhentai-api.service"
sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=NHentai Scraper API
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/cli.py --server
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nhentai-api
sudo systemctl start nhentai-api

echo "[+] Installation complete."
echo "[+] Access dashboard at http://<server-ip>:5000/dashboard"