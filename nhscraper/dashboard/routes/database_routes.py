#!/usr/bin/env python3
# nhscraper/dashboard/routes/database_routes.py

import os, time, random

from flask import Blueprint, jsonify, request

from nhscraper.core import database

db_bp = Blueprint("database", __name__)

@db_bp.route("/list", methods=["GET"])
def list_all():
    status = request.args.get("status")
    galleries = database.list_galleries(status=status)
    return jsonify({"galleries": galleries})

@db_bp.route("/get/<int:gallery_id>", methods=["GET"])
def get_gallery(gallery_id):
    status = database.get_gallery_status(gallery_id)
    return jsonify({"gallery_id": gallery_id, "status": status})