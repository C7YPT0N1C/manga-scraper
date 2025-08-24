#!/usr/bin/env python3
from flask import Flask, jsonify
import requests
import time
import threading
from scraper_core import load_config, make_session  # Make sure scraper_core is in PYTHONPATH

app = Flask(__name__)

scraper_status = {
    "running": False,
    "last_checked": None,
    "last_gallery": None,
    "errors": [],
    "using_tor": False,
}

@app.route("/scraper_status")
def status():
    return jsonify(scraper_status)

@app.route("/check_ip")
def check_ip():
    """Check what IP the scraper is using (to confirm Tor proxy)."""
    try:
        cfg = load_config()
        session = make_session(cfg)
        if scraper_status["using_tor"]:
            session.proxies.update({
                "http": "socks5h://127.0.0.1:9050",
                "https": "socks5h://127.0.0.1:9050",
            })
        r = session.get("https://httpbin.org/ip", timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def run_flask():
    app.run(host="0.0.0.0", port=5000, threaded=True)

def start_monitor():
    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()
    print("[*] Monitor started on http://0.0.0.0:5000")

if __name__ == "__main__":
    start_monitor()
    scraper_status["running"] = True
    try:
        while True:
            scraper_status["last_checked"] = time.strftime("%Y-%m-%d %H:%M:%S")
            time.sleep(10)
    except KeyboardInterrupt:
        scraper_status["running"] = False
        print("[*] Monitor stopped")