# mangascraper/dashboard_utils/socketio_.py
from __future__ import annotations
import threading, time
from typing import Optional

socketio = None

def init_app(app, poll_interval: float = 2.0):
    """Initialise SocketIO for the Flask `app` and start a background emitter that
    publishes scraper status to connected clients. Call this after app creation
    and blueprint registration.
    """
    global socketio
    if socketio is not None:
        return socketio

    try:
        from flask_socketio import SocketIO
    except Exception:
        raise

    socketio = SocketIO(app, cors_allowed_origins='*')

    def _bg_loop():
        last_payload = None
        # Adaptive backoff: start at `poll_interval` and back off up to `max_sleep`
        # when no changes are observed to reduce DB load.
        max_sleep = max(10.0, poll_interval * 5.0)
        current_sleep = float(poll_interval)
        while True:
            try:
                # Query the central ScraperService directly to avoid creating an
                # application context and to use the single source of truth.
                from mangascraper.core.scraper_service import get_default_service
                try:
                    service = get_default_service()
                    payload = service.get_status(include_progress=True)
                except Exception:
                    payload = None

                if payload and payload != last_payload:
                    try:
                        socketio.emit('scraper:status', payload, namespace='/scraper')
                    except Exception:
                        pass
                    last_payload = payload
                    # reset sleep when there is an update
                    current_sleep = float(poll_interval)
                else:
                    # increase sleep when no change observed, up to max_sleep
                    current_sleep = min(max_sleep, current_sleep * 1.5)
            except Exception:
                # swallow background errors and retry
                current_sleep = min(max_sleep, current_sleep * 1.5)
            time.sleep(current_sleep)

    t = threading.Thread(target=_bg_loop, daemon=True, name='socketio-scraper-emitter')
    t.start()

    return socketio
