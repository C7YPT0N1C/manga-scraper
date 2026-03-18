# mangascraper/dashboard_utils/routes/reader_routes.py
from flask import Blueprint, redirect, url_for, render_template, abort

reader_bp = Blueprint('reader', __name__)

@reader_bp.route('/creators/<creator_name>/reader/')
def creator_reader_redirect(creator_name):
    return redirect(url_for('creators.creator_page', creator_name=creator_name))

@reader_bp.route('/creators/<creator_name>/reader/<int:gallery_id>/')
def creator_reader_page(creator_name, gallery_id):
    from .data_routes import _gallery_meta_by_id, _gallery_id_from_location, _resolve_gallery_path, _archive_pages
    import os
    meta = _gallery_meta_by_id(gallery_id)
    # Try to resolve gallery path
    root, path = _resolve_gallery_path(creator_name, meta['title'])
    pages = []
    if path:
        if os.path.isdir(path):
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
            pages = [name for name in sorted(os.listdir(path)) if os.path.splitext(name)[1].lower() in image_exts]
        elif path.endswith('.cbz') or path.endswith('.zip'):
            pages = _archive_pages(path)
    return render_template('reader.html', creator_name=creator_name, gallery_id=gallery_id, gallery=meta, pages=pages)

@reader_bp.route('/collections/<int:collection_id>/reader/')
def collection_reader_redirect(collection_id):
    return redirect(url_for('collections.collection_page', collection_id=collection_id))

@reader_bp.route('/collections/<int:collection_id>/reader/<int:gallery_id>/')
def collection_reader_page(collection_id, gallery_id):
    from .data_routes import _gallery_meta_by_id, _gallery_id_from_location, _resolve_gallery_path, _archive_pages
    import os
    meta = _gallery_meta_by_id(gallery_id)
    # Try to resolve gallery path
    # For collections, we don't have creator name, so we use meta['creators'][0] if available
    creator_name = meta['creators'][0] if meta['creators'] else None
    root, path = _resolve_gallery_path(creator_name, meta['title']) if creator_name else (None, None)
    pages = []
    if path:
        if os.path.isdir(path):
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
            pages = [name for name in sorted(os.listdir(path)) if os.path.splitext(name)[1].lower() in image_exts]
        elif path.endswith('.cbz') or path.endswith('.zip'):
            pages = _archive_pages(path)
    return render_template('reader.html', collection_id=collection_id, gallery_id=gallery_id, gallery=meta, pages=pages)
