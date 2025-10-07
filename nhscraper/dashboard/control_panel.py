#!/usr/bin/env python3
# nhscraper/dashboard/control_panel.py

import os
from flask import Flask, render_template
from flask_cors import CORS

from nhscraper.core import database

from nhscraper.dashboard.routes.scraper_routes import scraper_bp
from nhscraper.dashboard.routes.database_routes import db_bp
from nhscraper.dashboard.routes.gallery_routes import gallery_bp

def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static"
    )

    # Allow CORS for API routes
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Register blueprints for API routes
    app.register_blueprint(scraper_bp, url_prefix="/api/scraper")
    app.register_blueprint(db_bp, url_prefix="/api/db")
    app.register_blueprint(gallery_bp, url_prefix="/api/gallery")

    # --- Web dashboard routes ---
    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/scraper")
    def scraper_page():
        return render_template("scraper.html")

    @app.route("/database")
    def database_page():
        entries = database.list_galleries()
        return render_template("database.html", entries=entries)

    @app.route("/gallery")
    def gallery_page():
        return render_template("gallery.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=6969, debug=True)