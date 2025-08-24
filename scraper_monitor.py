#!/usr/bin/env python3
from flask import Flask, jsonify
import threading, time
from scraper_core import load_config, make_session, download_galleries_parallel

app = Flask(__name__)

# Shared status dictionary
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
    """Return the IP the scraper is using (Tor or normal)."""
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

def run_scraper(cfg, start_id, end_id):
    """Run the scraper in a thread, updating status."""
    scraper_status["running"] = True
    scraper_status["using_tor"] = cfg.get("use_tor", False)
    try:
        for gid in range(start_id, end_id + 1):
            try:
                # download_gallery returns True if success, False if skipped/error
                success = download_galleries_parallel(gid, gid, cfg["root"])
                scraper_status["last_gallery"] = gid
                if not success:
                    scraper_status["errors"].append(f"Gallery {gid} failed")
            except Exception as e:
                scraper_status["errors"].append(f"{gid}: {str(e)}")
            scraper_status["last_checked"] = time.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        scraper_status["running"] = False

def start_monitor(start_id=500000, end_id=500010):
    # Load scraper config
    cfg = load_config()

    # Start Flask monitor thread
    threading.Thread(target=run_flask, daemon=True).start()
    print("[*] Monitor running on http://0.0.0.0:5000")

    # Start scraper thread
    threading.Thread(target=run_scraper, args=(cfg, start_id, end_id), daemon=True).start()

    # Keep main thread alive
    try:
        while True:
            scraper_status["last_checked"] = time.strftime("%Y-%m-%d %H:%M:%S")
            time.sleep(5)
    except KeyboardInterrupt:
        scraper_status["running"] = False
        print("[*] Monitor stopped")

if __name__ == "__main__":
    start_monitor()