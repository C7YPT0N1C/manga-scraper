#!/usr/bin/env python3
# mangascraper/dashboard/control_panel.py

import os
from flask import Flask, render_template
from flask_cors import CORS

from mangascraper.core import api as scraperapi
from mangascraper.dashboard.routes import scraper_routes
from mangascraper.dashboard.routes import database_routes
from mangascraper.dashboard.routes import gallery_routes

def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static"
    )

    # Allow CORS for API routes
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Register blueprints for API routes
    app.register_blueprint(scraper_routes, url_prefix="/api/scraper")
    app.register_blueprint(database_routes, url_prefix="/api/db")
    app.register_blueprint(gallery_routes, url_prefix="/api/gallery")

    # --- Web dashboard routes ---
    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/scraper")
    def scraper_page():
        return render_template("scraper.html")

    @app.route("/database")
    def database_page():
        entries = scraperapi.DB.Gallery.list()
        return render_template("database.html", entries=entries)

    @app.route("/gallery")
    def gallery_page():
        return render_template("gallery.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=6969, debug=True)