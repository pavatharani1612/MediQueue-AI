"""
MEDIQUEUE AI - Enterprise Hospital Queue Management System
==========================================================
Single entry point.

    python run.py            ->  http://127.0.0.1:5000
    Admin login              ->  admin / admin123

The enterprise layout keeps every module importable exactly as before by
registering the backend package directories on sys.path.
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(BASE_DIR, "backend")

# Modular layout: each layer is importable by its own name
# (e.g. `from services.qr_service import generator`).
for path in (BACKEND, os.path.join(BACKEND, "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

from app_factory import create_app  # noqa: E402

app = create_app()

# Optional real WebSocket transport. When flask-socketio is installed the
# realtime bus upgrades from SSE to WebSockets automatically.
socketio = None
try:
    from flask_socketio import SocketIO
    from services.realtime_service import bus as realtime_bus

    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    realtime_bus.attach_socketio(socketio)
    print("[REALTIME] Socket.IO enabled")
except Exception:
    print("[REALTIME] Socket.IO not installed - using built-in SSE transport")


if __name__ == "__main__":
    print("=" * 64)
    print(" MEDIQUEUE AI - Enterprise Edition")
    print(" URL          ->  http://127.0.0.1:5000")
    print(" Admin login  ->  admin / admin123")
    print(" QR Check-In  ->  http://127.0.0.1:5000/admin/qr-checkin")
    print("=" * 64)
    if socketio is not None:
        socketio.run(app, host="0.0.0.0", port=5000, debug=True,
                     use_reloader=False, allow_unsafe_werkzeug=True)
    else:
        app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
