#!/usr/bin/env python3
# nhscraper/dashboard/routes/scraper_routes.py

import os, time, random, threading

from flask import Blueprint, jsonify, request

from nhscraper.core import orchestrator

scraper_bp = Blueprint("scraper", __name__)

@scraper_bp.route("/status", methods=["GET"])
def status():
    """Return current scraper status."""
    return jsonify({
        "status": "running" if orchestrator.is_running else "stopped",
        "current_batch": orchestrator.current_batch,
        "total_batches": orchestrator.total_batches,
        "last_gallery": orchestrator.last_gallery_id,
    })

@scraper_bp.route("/start", methods=["POST"])
def start_scraper():
    """Start the scraper (accept CLI-like args as JSON)."""
    args = request.json or {}
    threading.Thread(target=orchestrator.start_scraper, kwargs=args).start()
    return jsonify({"message": "Scraper started.", "args": args})

@scraper_bp.route("/stop", methods=["POST"])
def stop_scraper():
    """Stop scraper gracefully."""
    orchestrator.stop_scraper()
    return jsonify({"message": "Scraper stopped."})