#!/usr/bin/env python3
# mangascraper/dashboard/routes/gallery_routes.py

import os, time, random

from flask import Blueprint, jsonify, send_from_directory, abort

from mangascraper.core.orchestrator import DEFAULT_DOWNLOAD_PATH

gallery_bp = Blueprint("gallery", __name__)

@gallery_bp.route("/list_creators", methods=["GET"])
def list_creators():
    creators = [
        name for name in os.listdir(DEFAULT_DOWNLOAD_PATH)
        if os.path.isdir(os.path.join(DEFAULT_DOWNLOAD_PATH, name))
    ]
    return jsonify({"creators": creators})

@gallery_bp.route("/list_galleries/<creator>", methods=["GET"])
def list_galleries(creator):
    creator_path = os.path.join(DEFAULT_DOWNLOAD_PATH, creator)
    if not os.path.exists(creator_path):
        abort(404)
    galleries = os.listdir(creator_path)
    return jsonify({"creator": creator, "galleries": galleries})

@gallery_bp.route("/view/<creator>/<gallery>/<filename>", methods=["GET"])
def view_image(creator, gallery, filename):
    """Serve gallery images to frontend reader."""
    gallery_path = os.path.join(DEFAULT_DOWNLOAD_PATH, creator, gallery)
    if not os.path.exists(gallery_path):
        abort(404)
    return send_from_directory(gallery_path, filename)