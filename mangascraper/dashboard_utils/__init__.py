"""Dashboard package for MangaScraper web UI."""

from flask import Flask
from .routes import reader_bp

def create_app():
    app = Flask(__name__)
    app.config.from_object('app.config.Config')

    app.register_blueprint(reader_bp)

    return app

