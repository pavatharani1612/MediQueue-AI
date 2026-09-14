"""
Audit Service
=============
Permanent, append-only record of every administrative activity:
login, logout, doctor added/deleted, patient updated, queue reset,
QR scan, barcode scan, prediction, appointment approved/cancelled,
settings updated - with admin name, date, time, browser, device and IP.
"""
from __future__ import annotations

from datetime import datetime

from models import query, execute, get_conn
from services.realtime_service import bus


def init_tables():
    with get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS audit_logs(
            id INTEGER PRIMARY KEY,
            actor_role TEXT,
            actor_name TEXT,
            action TEXT,
            entity TEXT,
            entity_id TEXT,
            details TEXT,
            date TEXT,
            time TEXT,
            created_at TEXT,
            browser TEXT,
            device TEXT,
            ip_address TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_audit_date ON audit_logs(date)")
        c.execute("""CREATE TABLE IF NOT EXISTS login_history(
            id INTEGER PRIMARY KEY,
            role TEXT, user_id INTEGER, username TEXT,
            event TEXT,                     -- login / logout / failed
            date TEXT, time TEXT, created_at TEXT,
            browser TEXT, device TEXT, ip_address TEXT)""")


# ------------------------------------------------------------------ helpers
def _client():
    """Extract actor + client fingerprint from the current Flask request."""
    try:
        from flask import request, session
        ua = request.headers.get("User-Agent", "") or ""
        ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
              or request.remote_addr or "-")
        role = session.get("role") or "guest"
        name = session.get("name") or role
        return role, name, _browser(ua), _device(ua), ip
    except Exception:
        return "system", "system", "-", "-", "-"


def _browser(ua: str) -> str:
    ua_l = ua.lower()
    for key, label in (("edg", "Edge"), ("chrome", "Chrome"), ("safari", "Safari"),
                       ("firefox", "Firefox"), ("opera", "Opera")):
        if key in ua_l:
            return label
    return "Unknown"


def _device(ua: str) -> str:
    ua_l = ua.lower()
    if "android" in ua_l:
        return "Android"
    if "iphone" in ua_l or "ipad" in ua_l:
        return "iOS"
    if "windows" in ua_l:
        return "Windows"
    if "mac os" in ua_l:
        return "macOS"
    if "linux" in ua_l:
        return "Linux"
    return "Unknown"


# ------------------------------------------------------------------ writes
def log(action, entity="", entity_id="", details="",
        actor_role=None, actor_name=None):
    role, name, browser, device, ip = _client()
    now = datetime.now()
    execute("""INSERT INTO audit_logs(actor_role,actor_name,action,entity,entity_id,
                 details,date,time,created_at,browser,device,ip_address)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (actor_role or role, actor_name or name, action, entity, str(entity_id),
             details, now.strftime("%Y-%m-%d"),
             now.strftime("%I:%M %p").lstrip("0"),
             now.isoformat(timespec="seconds"), browser, device, ip))
    bus.publish("audit", {"action": action, "actor": actor_name or name,
                          "details": details})


def log_login(role, user_id, username, event="login"):
    _r, _n, browser, device, ip = _client()
    now = datetime.now()
    execute("""INSERT INTO login_history(role,user_id,username,event,date,time,
                 created_at,browser,device,ip_address)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (role, user_id, username, event, now.strftime("%Y-%m-%d"),
             now.strftime("%I:%M %p").lstrip("0"),
             now.isoformat(timespec="seconds"), browser, device, ip))
    log(f"{event.capitalize()}", "auth", user_id, f"{role}: {username}",
        actor_role=role, actor_name=username)


# ------------------------------------------------------------------ reads
def recent(limit=200, action=None, page=1, per_page=None):
    per_page = per_page or limit
    offset = (max(page, 1) - 1) * per_page
    if action:
        return query("""SELECT * FROM audit_logs WHERE action LIKE ?
                        ORDER BY id DESC LIMIT ? OFFSET ?""",
                     (f"%{action}%", per_page, offset))
    return query("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                 (per_page, offset))


def total(action=None) -> int:
    if action:
        row = query("SELECT COUNT(*) n FROM audit_logs WHERE action LIKE ?",
                    (f"%{action}%",), one=True)
    else:
        row = query("SELECT COUNT(*) n FROM audit_logs", one=True)
    return row["n"] if row else 0


def login_history(limit=100):
    return query("SELECT * FROM login_history ORDER BY id DESC LIMIT ?", (limit,))
