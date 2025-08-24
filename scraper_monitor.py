#!/usr/bin/env python3
from flask import Flask, jsonify
import json, time, threading, requests, os

STATUS_FILE = "/opt/nhentai-scraper/status.json"

app = Flask(__name__)

@app.route("/scraper_status")
def scraper_status():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE) as f:
                return jsonify(json.load(f))
        except:
            pass
    return jsonify({"running": False, "last_gallery": None})

@app.route("/check_ip")
def check_ip():
    """Check current Tor IP."""
    try:
        proxy = "socks5h://127.0.0.1:9050"
        session = requests.Session()
        session.proxies = {"http": proxy, "https": proxy}
        r = session.get("https://httpbin.org/ip", timeout=10)
        return jsonify({"tor_ip": r.json().get("origin")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def run_flask():
    app.run(host="0.0.0.0", port=5000)

if __name__=="__main__":
    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()
    while True:
        time.sleep(10)