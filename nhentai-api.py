#!/usr/bin/env python3
from flask import Flask, jsonify
import subprocess, threading, time

app = Flask(__name__)

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

# ------------------------
# API CALLS
# ------------------------
status = {
    "running_galleries": [],
    "last_checked": None,
    "last_gallery": None,
    "tor_ip": None,
    "errors": [],
}

def check_tor():
    try:
        cmd = ["curl", "-s", "--socks5-hostname", "127.0.0.1:9050", "https://httpbin.org/ip"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            status["tor_ip"] = r.stdout.strip()
        else:
            status["tor_ip"] = f"Error: {r.stderr.strip()}"
    except Exception as e:
        status["tor_ip"] = str(e)

# ------------------------
# API ENDPOINTS
# ------------------------
@app.route("/status")
def scraper_status():
    return jsonify(status)

@app.route("/check_ip")
def check_ip():
    check_tor()
    return jsonify({"tor_ip": status["tor_ip"]})

def run_flask():
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    log("[*] Starting monitor on port 5000")
    threading.Thread(target=run_flask, daemon=True).start()
    while True:
        status["last_checked"] = time.strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(10)
        