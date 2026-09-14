"""Convenience entry point.

Both of these start the same Flask application:

    python app.py     ->  http://127.0.0.1:5000
    python run.py     ->  http://127.0.0.1:5000
"""
from __future__ import annotations

from run import app, socketio

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
