#!/usr/bin/env python3
# mangascraper/dashboard/routes/database_routes.py

import os, time, random
from flask import Blueprint, jsonify, request

from mangascraper.core import api as scraperapi

db_bp = Blueprint("database", __name__)

@db_bp.route("/list", methods=["GET"])
def list_all():
    status = request.args.get("status")
    galleries = scraperapi.Db.Gallery.list_by_status(status) if status else scraperapi.Db.Gallery.list()
    return jsonify({"galleries": galleries})

@db_bp.route("/get/<int:gallery_id>", methods=["GET"])
def get_gallery(gallery_id):
    status = scraperapi.Get.gallery_status(gallery_id)
    return jsonify({"gallery_id": gallery_id, "status": status})