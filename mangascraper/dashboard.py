#!/usr/bin/env python3
# mangascraper/dashboard.py

import os
from flask import Flask, render_template, redirect
from flask_cors import CORS

from mangascraper.core.api import api as scraperapi
from mangascraper.core import orchestrator
from mangascraper.dashboard_utils.routes.scraper_routes import scraper_bp
from mangascraper.dashboard_utils.routes.data_routes import db_bp, gallery_bp, collections_bp

def create_app():
    base_dir = os.path.dirname(__file__)
    template_folder = os.path.join(base_dir, "dashboard_utils", "templates")
    static_folder = os.path.join(base_dir, "dashboard_utils", "static")
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
    app.register_blueprint(collections_bp, url_prefix="/api/collections")

    # --- Web dashboard routes ---
    @app.route("/")
    def index():
        return render_template(
            "creators.html",
            gallery_viewer_config=orchestrator.DASHBOARD_GALLERY_VIEW_CONFIG,
            initial_creator_slug="",
            initial_gallery_id="",
        )

    @app.route("/creators")
    def creators_index_redirect():
        return redirect("/creators/")

    @app.route("/creators/")
    def creators_page():
        return render_template(
            "creators.html",
            gallery_viewer_config=orchestrator.DASHBOARD_GALLERY_VIEW_CONFIG,
            initial_creator_slug="",
            initial_gallery_id="",
        )

    @app.route("/creators/<path:creator_slug>")
    @app.route("/creators/<path:creator_slug>/")
    def creator_page(creator_slug):
        return render_template(
            "creators.html",
            gallery_viewer_config=orchestrator.DASHBOARD_GALLERY_VIEW_CONFIG,
            initial_creator_slug=str(creator_slug or ""),
            initial_gallery_id="",
        )

    @app.route("/creators/<path:creator_slug>/<int:gallery_id>")
    @app.route("/creators/<path:creator_slug>/<int:gallery_id>/")
    def creator_gallery_page(creator_slug, gallery_id):
        return render_template(
            "creators.html",
            gallery_viewer_config=orchestrator.DASHBOARD_GALLERY_VIEW_CONFIG,
            initial_creator_slug=str(creator_slug or ""),
            initial_gallery_id=int(gallery_id),
        )

    @app.route("/scraper")
    def scraper_page():
        return render_template("scraper.html", gallery_viewer_config=orchestrator.DASHBOARD_OTHER_VIEWS_CONFIG)

    @app.route("/database")
    def database_page():
        return render_template("diagnostics.html", gallery_viewer_config=orchestrator.DASHBOARD_OTHER_VIEWS_CONFIG)

    @app.route("/diagnostics")
    def logs_page():
        return render_template("diagnostics.html", gallery_viewer_config=orchestrator.DASHBOARD_OTHER_VIEWS_CONFIG)

    @app.route("/collections")
    def collections_index_redirect():
        return redirect("/collections/")

    @app.route("/collections/")
    def collections_page():
        return render_template(
            "collections.html",
            gallery_viewer_config=orchestrator.DASHBOARD_COLLECTION_VIEW_CONFIG,
            initial_collection_id="",
            initial_gallery_id="",
        )

    @app.route("/collections/<int:collection_id>/")
    def collection_page(collection_id):
        return render_template(
            "collections.html",
            gallery_viewer_config=orchestrator.DASHBOARD_COLLECTION_VIEW_CONFIG,
            initial_collection_id=int(collection_id),
            initial_gallery_id="",
        )

    @app.route("/collections/<int:collection_id>/<int:gallery_id>/")
    def collection_gallery_page(collection_id, gallery_id):
        return render_template(
            "collections.html",
            gallery_viewer_config=orchestrator.DASHBOARD_COLLECTION_VIEW_CONFIG,
            initial_collection_id=int(collection_id),
            initial_gallery_id=int(gallery_id),
        )

    # Reader routes (new separate reader pages)
    @app.route('/reader/creators/<path:creator_slug>/<int:gallery_id>/')
    def reader_creator_page(creator_slug, gallery_id):
        return render_template(
            'reader.html',
            gallery_viewer_config=orchestrator.DASHBOARD_GALLERY_VIEW_CONFIG,
            reader_context={
                'type': 'creators',
                'creator': str(creator_slug),
                'gallery_id': int(gallery_id),
            },
        )

    @app.route('/reader/creators/<path:creator_slug>/')
    def reader_creator_redirect(creator_slug):
        return redirect(f'/creators/{creator_slug}/')

    @app.route('/reader/collections/<int:collection_id>/<int:gallery_id>/')
    def reader_collection_page(collection_id, gallery_id):
        return render_template(
            'reader.html',
            gallery_viewer_config=orchestrator.DASHBOARD_COLLECTION_VIEW_CONFIG,
            reader_context={
                'type': 'collections',
                'collection_id': int(collection_id),
                'gallery_id': int(gallery_id),
            },
        )

    @app.route('/reader/collections/<int:collection_id>/')
    def reader_collection_redirect(collection_id):
        return redirect(f'/collections/{collection_id}/')

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(
        host=orchestrator.DASHBOARD_HOST,
        port=orchestrator.DASHBOARD_PORT,
        debug=orchestrator.DASHBOARD_DEBUG,
        use_reloader=orchestrator.DASHBOARD_USE_RELOADER,
    )