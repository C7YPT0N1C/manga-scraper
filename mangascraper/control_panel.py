#!/usr/bin/env python3
# mangascraper/control_panel.py

import os

from flask import Flask, render_template
from flask_cors import CORS

from mangascraper.core import api as scraperapi
from mangascraper.dashboard.routes.scraper_routes import scraper_bp
from mangascraper.dashboard.routes.data_routes import db_bp, gallery_bp


DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 6969
DASHBOARD_DEBUG = True
DASHBOARD_USE_RELOADER = True

GALLERY_VIEWER_CONFIG = {
    "creatorPageSize": 25,
    "galleryPageSize": 25,
    "tileMinWidthPx": 120,
    "tileAspectRatio": "2 / 3",
    "searchPlaceholder": "Search creators or galleries...",
}


def _dashboard_asset_paths() -> tuple[str, str]:
    """Resolve template/static folders whether this file lives in mangascraper/ or mangascraper/dashboard/."""
    base_dir = os.path.dirname(__file__)

    direct_templates = os.path.join(base_dir, "templates")
    direct_static = os.path.join(base_dir, "static")
    if os.path.isdir(direct_templates) and os.path.isdir(direct_static):
        return direct_templates, direct_static

    nested_templates = os.path.join(base_dir, "dashboard", "templates")
    nested_static = os.path.join(base_dir, "dashboard", "static")
    return nested_templates, nested_static

def create_app():
    template_folder, static_folder = _dashboard_asset_paths()
    app = Flask(
        __name__,
        template_folder=template_folder,
        static_folder=static_folder,
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
        return render_template("gallery.html", gallery_viewer_config=GALLERY_VIEWER_CONFIG)

    @app.route("/scraper")
    def scraper_page():
        return render_template("scraper.html")

    @app.route("/database")
    def database_page():
        return render_template("database.html")

    @app.route("/logs")
    def logs_page():
        return render_template("logs.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        debug=DASHBOARD_DEBUG,
        use_reloader=DASHBOARD_USE_RELOADER,
    )