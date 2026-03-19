# mangascraper/core/api/_progress.py

import time, threading, json, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib import request as urllib_request

####################################################################################################################
# RUNTIME PROGRESS
####################################################################################################################

_runtime_progress_lock = threading.Lock()
_runtime_progress = {
    "current_gallery_number": 0,
    "current_gallery_id": None,
    "total_galleries": 0,
    "pages_processed": 0,
    "total_pages": 0,
    "pages_per_second": 0,
    "eta_seconds": 0,
    "download_speed_bytes": 0,
    "updated_at": 0,
}
_runtime_progress_server = None
_runtime_progress_server_thread = None
_runtime_progress_token = ""

####################################################################################################################
# HTTP HANDLER
####################################################################################################################

class _RuntimeProgressRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _runtime_progress_token

        parsed = urlparse(self.path)
        if parsed.path != "/progress":
            self.send_response(404)
            self.end_headers()
            return

        if _runtime_progress_token:
            qs = parse_qs(parsed.query or "")
            provided = str((qs.get("token") or [""])[0])
            if provided != _runtime_progress_token:
                self.send_response(403)
                self.end_headers()
                return

        with _runtime_progress_lock:
            payload = dict(_runtime_progress)

        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

####################################################################################################################
# RUNTIME PROGRESS CLASS
####################################################################################################################

class RuntimeProgress:
    """Cross-process runtime progress transport (downloader host + dashboard poll)."""

    @staticmethod
    def start_server(port: int | None = None, token: str | None = None) -> bool:
        global _runtime_progress_server, _runtime_progress_server_thread, _runtime_progress_token

        if _runtime_progress_server is not None:
            return True

        import os
        env_port = str(os.getenv("MANGASCRAPER_PROGRESS_PORT", "")).strip()
        selected_port = port
        if selected_port is None and env_port:
            try:
                selected_port = int(env_port)
            except Exception:
                selected_port = None
        if selected_port is None:
            return False

        selected_token = token if token is not None else str(os.getenv("MANGASCRAPER_PROGRESS_TOKEN", "")).strip()
        _runtime_progress_token = selected_token or ""

        try:
            _runtime_progress_server = ThreadingHTTPServer(
                ("127.0.0.1", int(selected_port)),
                _RuntimeProgressRequestHandler,
            )
            _runtime_progress_server_thread = threading.Thread(
                target=_runtime_progress_server.serve_forever,
                daemon=True,
            )
            _runtime_progress_server_thread.start()
            return True
        except Exception:
            _runtime_progress_server = None
            _runtime_progress_server_thread = None
            return False

    @staticmethod
    def stop_server() -> None:
        global _runtime_progress_server, _runtime_progress_server_thread

        server = _runtime_progress_server
        thread = _runtime_progress_server_thread
        _runtime_progress_server = None
        _runtime_progress_server_thread = None

        if server is None:
            return
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        if thread is not None:
            try:
                thread.join(timeout=1)
            except Exception:
                pass

    @staticmethod
    def update(**kwargs) -> None:
        with _runtime_progress_lock:
            _runtime_progress.update(kwargs)
            _runtime_progress["updated_at"] = time.time()

    @staticmethod
    def snapshot() -> dict:
        with _runtime_progress_lock:
            return dict(_runtime_progress)

    @staticmethod
    def fetch(port: int | None, token: str | None, timeout_seconds: float = 0.35) -> dict:
        if not port:
            return {}
        query = urllib.parse.urlencode({"token": token or ""})
        url = f"http://127.0.0.1:{int(port)}/progress?{query}"
        try:
            with urllib_request.urlopen(url, timeout=timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}