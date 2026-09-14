"""Report Service - daily / monthly analytics used by the admin dashboard."""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime

from models import query, get_conn


def init_tables():
    with get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY,
            kind TEXT, period TEXT, payload TEXT,
            generated_by TEXT, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY,
            appointment_id INTEGER, patient_id INTEGER,
            amount INTEGER, method TEXT, status TEXT DEFAULT 'Paid',
            created_at TEXT,
            FOREIGN KEY(appointment_id) REFERENCES appointments(id))""")


def daily(on_date=None):
    on_date = on_date or datetime.now().strftime("%Y-%m-%d")
    row = query("""SELECT COUNT(*) total,
                     SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) completed,
                     SUM(CASE WHEN status='Cancelled' THEN 1 ELSE 0 END) cancelled,
                     SUM(CASE WHEN checkin_status='Checked-In' THEN 1 ELSE 0 END) checked_in
                   FROM appointments WHERE appt_date=?""", (on_date,), one=True)
    revenue = query("""SELECT COALESCE(SUM(d.fee),0) rev FROM appointments a
                       JOIN doctors d ON d.id=a.doctor_id
                       WHERE a.appt_date=? AND a.status='Completed'""",
                    (on_date,), one=True)["rev"]
    return {"date": on_date, "total": row["total"] or 0,
            "completed": row["completed"] or 0, "cancelled": row["cancelled"] or 0,
            "checked_in": row["checked_in"] or 0, "revenue": revenue or 0}


def monthly(month=None):
    month = month or datetime.now().strftime("%Y-%m")
    rows = query("""SELECT appt_date d, COUNT(*) n,
                      SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) completed
                    FROM appointments WHERE appt_date LIKE ?
                    GROUP BY appt_date ORDER BY appt_date""", (f"{month}%",))
    revenue = query("""SELECT COALESCE(SUM(d.fee),0) rev FROM appointments a
                       JOIN doctors d ON d.id=a.doctor_id
                       WHERE a.appt_date LIKE ? AND a.status='Completed'""",
                    (f"{month}%",), one=True)["rev"]
    return {"month": month, "days": [dict(r) for r in rows], "revenue": revenue or 0}


def export_csv(rows, filename):
    """Write a CSV into exports/ and return the absolute path."""
    base = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "exports")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, filename)
    if not rows:
        open(path, "w").close()
        return path
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(dict(rows[0]).keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
    return path


def to_csv_string(rows):
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(dict(rows[0]).keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow(dict(r))
    return buf.getvalue()
