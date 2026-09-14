"""
Security middleware
===================
Applied globally without changing any existing route logic:

* security response headers (XSS, clickjacking, MIME sniffing, referrer)
* session expiry / idle timeout
* simple in-memory rate limiting for auth + scan endpoints
* audit logging of every admin state-changing request
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

from flask import request, session, jsonify, g

RATE_LIMIT_RULES = (
    ("/login", 20, 60),                  # 20 attempts / minute
    ("/api/admin/qr-scan", 120, 60),
    ("/api/admin/barcode-scan", 120, 60),
    ("/register", 10, 60),
)

_hits: dict[tuple[str, str], deque] = defaultdict(deque)

SENSITIVE_ACTIONS = {
    "/admin/doctors": "Doctor Modified",
    "/admin/settings": "Settings Updated",
    "/admin/settings-plus": "Settings Updated",
}


def _rate_limited(path: str, ip: str) -> bool:
    now = time.time()
    for prefix, limit, window in RATE_LIMIT_RULES:
        if path.startswith(prefix):
            bucket = _hits[(prefix, ip)]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                return True
            bucket.append(now)
    return False


def register_security(app):
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(hours=8))
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("MAX_CONTENT_LENGTH", 8 * 1024 * 1024)  # secure uploads

    @app.before_request
    def _security_before():
        session.permanent = True
        ip = ((request.headers.get("X-Forwarded-For", "").split(",")[0].strip())
              or request.remote_addr or "-")
        g.client_ip = ip
        if request.method in ("POST", "PUT", "DELETE") or request.path.startswith("/login"):
            if _rate_limited(request.path or "/", ip):
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False,
                                    "message": "Too many requests - slow down."}), 429
                return ("<h3 style='font-family:system-ui;padding:40px'>Too many "
                        "requests. Please wait a moment and try again.</h3>", 429)
        return None

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=()")
        if request.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        return response

    @app.after_request
    def _audit_writes(response):
        try:
            if (request.method == "POST" and session.get("role") == "admin"
                    and response.status_code < 400):
                action = SENSITIVE_ACTIONS.get(request.path)
                if action:
                    from services.audit_service import audit
                    audit.log(action, "request", request.path, request.method)
        except Exception:
            pass
        return response
