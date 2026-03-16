#!/usr/bin/env python3
# mangascraper/dashboard/routes/database_routes.py

from flask import Blueprint, jsonify, request

from mangascraper.core import api as scraperapi

db_bp = Blueprint("database", __name__)

@db_bp.route("/list", methods=["GET"])
def list_all():
    status = request.args.get("status")
    rows = scraperapi.DB.Gallery.list_by_status(status) if status else scraperapi.DB.Gallery.list()

    galleries = []
    for row in rows:
        if isinstance(row, dict):
            galleries.append({
                "id": row.get("id"),
                "status": row.get("status"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
            })
            continue

        if isinstance(row, (list, tuple)) and len(row) >= 4:
            galleries.append({
                "id": row[0],
                "status": row[1],
                "started_at": row[2],
                "completed_at": row[3],
            })

    return jsonify({"galleries": galleries})

@db_bp.route("/get/<int:gallery_id>", methods=["GET"])
def get_gallery(gallery_id):
    status = scraperapi.Get.gallery_status(gallery_id)
    return jsonify({"gallery_id": gallery_id, "status": status})