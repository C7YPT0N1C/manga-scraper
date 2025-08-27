#!/usr/bin/env python3
# nhscraper/nhscraper_api.py
# DESCRIPTION: Flask API for dashboard, settings, and status endpoints
# Called by: user via browser or monitoring tool
# Calls: config.py, db.py, graphql_api.py
# FUNCTION: Serve /dashboard, /status, /settings; handle session and auth

from flask import Flask, request, session, redirect, url_for, render_template_string, jsonify, Response
from werkzeug.security import check_password_hash, generate_password_hash
import os
from nhscraper.config import config, update_dashboard_password, set_env_var

app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "change_me")
PASSWORD_HASH = config["dashboard_pass_hash"]

# ----------------------------
# Basic Auth for monitoring
# ----------------------------
def check_basic_auth():
    auth = request.authorization
    if not auth:
        return False
    return PASSWORD_HASH and check_password_hash(PASSWORD_HASH, auth.password)

# ----------------------------
# Login Page
# ----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if PASSWORD_HASH and check_password_hash(PASSWORD_HASH, pw):
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        return render_template_string(LOGIN_TEMPLATE, error="Invalid password")
    return render_template_string(LOGIN_TEMPLATE, error=None)

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))

# ----------------------------
# Dashboard (protected)
# ----------------------------
@app.route("/dashboard")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template_string(DASHBOARD_TEMPLATE)

# ----------------------------
# Status JSON
# ----------------------------
@app.route("/status")
def status():
    if not session.get("logged_in") and not check_basic_auth():
        return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="nhscraper"'})
    return jsonify({
        "running": [],  # placeholder, populate from downloader
        "completed": [],
        "failed": []
    })

# ----------------------------
# Settings Page
# ----------------------------
@app.route("/settings", methods=["GET", "POST"])
def settings():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if request.method == "POST":
        new_pw = request.form.get("new_password")
        if new_pw and len(new_pw) >= 6:
            new_hash = generate_password_hash(new_pw)
            update_dashboard_password(new_hash)
        # Update other config values
        for key in ["threads_galleries", "threads_images", "dry_run", "excluded_tags", "language"]:
            val = request.form.get(key)
            if val is not None:
                if key in ["excluded_tags", "language"]:
                    val = val.split(",")
                set_env_var(key.upper(), val)
    return render_template_string(SETTINGS_TEMPLATE, config=config)

# ----------------------------
# Templates
# ----------------------------
LOGIN_TEMPLATE = """..."""
DASHBOARD_TEMPLATE = """..."""
SETTINGS_TEMPLATE = """..."""