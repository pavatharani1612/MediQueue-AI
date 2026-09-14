"""
Settings Service
================
Wraps app_settings writes so that **every** admin change is stored
permanently in `settings_history` (old value, new value, admin, date,
time, IP) and is broadcast live to all dashboards.
"""
from __future__ import annotations

from datetime import datetime

from models import query, execute, get_conn
from services.realtime_service import bus
from services.audit_service import audit

DEFAULTS = {
    "hospital_name":        "MediQueue AI Hospital",
    "hospital_logo":        "",
    "theme":                "light",
    "slot_interval_min":    "15",
    "working_start":        "09:00",
    "working_end":          "21:00",
    "min_lead_minutes":     "10",
    "queue_rules":          "priority-emergency-first",
    "appointment_rules":    "1-active-appointment-per-doctor-per-day",
    "notifications_enabled": "1",
    "departments_note":     "",
}


def init_tables():
    with get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS app_settings(
            key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings_history(
            id INTEGER PRIMARY KEY,
            setting_key TEXT,
            old_value TEXT,
            new_value TEXT,
            admin_name TEXT,
            date TEXT,
            time TEXT,
            created_at TEXT,
            ip_address TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_settings_hist ON settings_history(setting_key)")
    for key, value in DEFAULTS.items():
        if not query("SELECT 1 FROM app_settings WHERE key=?", (key,), one=True):
            execute("INSERT INTO app_settings(key,value) VALUES(?,?)", (key, value))


def get(key, default=""):
    row = query("SELECT value FROM app_settings WHERE key=?", (key,), one=True)
    return row["value"] if row else (DEFAULTS.get(key, default) or default)


def all_settings() -> dict:
    values = dict(DEFAULTS)
    for row in query("SELECT key, value FROM app_settings"):
        values[row["key"]] = row["value"]
    return values


def set(key, value, admin_name=None):  # noqa: A003 - keep the intuitive name
    """Update a setting, record the history row and push a live update."""
    old = get(key, "")
    new = "" if value is None else str(value).strip()
    if str(old) == new:
        return False

    if query("SELECT 1 FROM app_settings WHERE key=?", (key,), one=True):
        execute("UPDATE app_settings SET value=? WHERE key=?", (new, key))
    else:
        execute("INSERT INTO app_settings(key,value) VALUES(?,?)", (key, new))

    _role, name, _b, _d, ip = audit._client()
    now = datetime.now()
    execute("""INSERT INTO settings_history(setting_key,old_value,new_value,admin_name,
                 date,time,created_at,ip_address)
               VALUES(?,?,?,?,?,?,?,?)""",
            (key, str(old), new, admin_name or name, now.strftime("%Y-%m-%d"),
             now.strftime("%I:%M %p").lstrip("0"),
             now.isoformat(timespec="seconds"), ip))

    audit.log("Settings Updated", "settings", key, f"{old} -> {new}")
    bus.publish("settings", {"key": key, "old": str(old), "new": new})
    return True


def set_many(data: dict, admin_name=None) -> int:
    changed = 0
    for key, value in data.items():
        if set(key, value, admin_name=admin_name):
            changed += 1
    if changed:
        bus.publish("settings_bulk", {"changed": changed})
    return changed


def history(limit=200, page=1):
    offset = (max(page, 1) - 1) * limit
    return query("""SELECT * FROM settings_history ORDER BY id DESC
                    LIMIT ? OFFSET ?""", (limit, offset))


def history_total() -> int:
    row = query("SELECT COUNT(*) n FROM settings_history", one=True)
    return row["n"] if row else 0
