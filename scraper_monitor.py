#!/usr/bin/env python3
from flask import Flask, jsonify
import threading
import time
from nhentai_scraper import scraper_status, VERBOSE, log
import requests

app = Flask(__name__)

@app.route("/scraper_status")
def status():
    return jsonify(scraper_status)

@app.route("/check_ip")
def check_ip():
    """Check current public IP to confirm Tor/VPN usage."""
    try:
        session = requests.Session()
        proxies = {"http": "socks5h://127.0.0.1:9050",
                   "https": "socks5h://127.0.0.1:9050"} if scraper_status["using_tor"] else None
        r = session.get("https://httpbin.org/ip", proxies=proxies, timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def run_flask():
    app.run(host="0.0.0.0", port=5000)

def start_monitor():
    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()
    log("[*] Monitor running on :5000")

if __name__ == "__main__":
    start_monitor()
    while True:
        scraper_status["last_checked"] = time.strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(10)