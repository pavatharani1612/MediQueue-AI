"""Smoke tests: boots the app and exercises the new enterprise endpoints."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from run import app


def client():
    app.config["TESTING"] = True
    return app.test_client()


def login_admin(c):
    return c.post("/login", data={"role": "admin", "username": "admin",
                                  "password": "admin123"}, follow_redirects=True)


def test_public_pages():
    c = client()
    for url in ("/", "/about", "/doctors", "/login", "/api/health"):
        assert c.get(url).status_code == 200


def test_admin_qr_checkin_and_logs():
    c = client()
    login_admin(c)
    for url in ("/admin", "/admin/qr-checkin", "/admin/audit-logs",
                "/admin/settings-history", "/admin/analytics",
                "/admin/settings-plus", "/api/admin/enterprise-stats"):
        assert c.get(url).status_code == 200


def test_invalid_qr_is_rejected():
    c = client()
    login_admin(c)
    res = c.post("/api/admin/qr-scan", json={"payload": "NOT-A-REAL-TOKEN"})
    data = res.get_json()
    assert data["ok"] is False and data["message"].startswith("Invalid QR")


def test_slots_are_ampm_and_future_only():
    c = client()
    board = c.get("/api/slots/live?doctor_id=1").get_json()
    assert board["available_count"] >= 0
    for slot in board["available_slots"]:
        assert slot["label"].endswith(("AM", "PM"))
        assert slot["past"] is False and slot["taken"] is False
