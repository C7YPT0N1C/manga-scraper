#!/usr/bin/env python3
from flask import Flask, jsonify
import json

STATUS_FILE = "/opt/nhentai-scraper/status.json"
app = Flask(__name__)

def load_status():
    try:
        with open(STATUS_FILE, "r") as f:
            return json.load(f)
    except:
        return {"last_run": None, "downloaded": 0, "skipped": 0, "success": False, "error": None}

@app.route("/scraper_status")
def scraper_status():
    return jsonify(load_status())

if __name__=="__main__":
    app.run(host="0.0.0.0", port=5000)