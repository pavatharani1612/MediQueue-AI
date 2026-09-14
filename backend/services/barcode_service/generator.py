"""Barcode Service - generator (Code128, optional dependency `python-barcode`)."""
from __future__ import annotations

import io

from models import query, execute, get_conn
from datetime import datetime


def init_tables():
    with get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS barcodes(
            id INTEGER PRIMARY KEY,
            appointment_id INTEGER,
            patient_id INTEGER,
            code TEXT UNIQUE,
            status TEXT DEFAULT 'Active',
            created_at TEXT,
            FOREIGN KEY(appointment_id) REFERENCES appointments(id))""")


def code_for(appt_id: int) -> str:
    """Deterministic barcode value derived from the appointment id."""
    a = query("SELECT id, patient_id, appt_date FROM appointments WHERE id=?",
              (appt_id,), one=True)
    if not a:
        return ""
    code = f"MQ{a['patient_id']:05d}{a['id']:06d}"
    if not query("SELECT 1 FROM barcodes WHERE appointment_id=?", (appt_id,), one=True):
        execute("""INSERT INTO barcodes(appointment_id,patient_id,code,status,created_at)
                   VALUES(?,?,?, 'Active', ?)""",
                (appt_id, a["patient_id"], code,
                 datetime.now().isoformat(timespec="seconds")))
    return code


def barcode_svg(value: str) -> str:
    """Code128 SVG. Falls back to a readable text badge if lib is unavailable."""
    if not value:
        return ""
    try:
        import barcode
        from barcode.writer import SVGWriter
        buf = io.BytesIO()
        barcode.get("code128", value, writer=SVGWriter()).write(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:
        return (f"<svg xmlns='http://www.w3.org/2000/svg' width='320' height='70'>"
                f"<rect width='320' height='70' fill='#fff'/>"
                f"<text x='160' y='44' font-family='monospace' font-size='20' "
                f"text-anchor='middle'>{value}</text></svg>")


def barcode_png(value: str) -> bytes:
    try:
        import barcode
        from barcode.writer import ImageWriter
        buf = io.BytesIO()
        barcode.get("code128", value, writer=ImageWriter()).write(buf)
        return buf.getvalue()
    except Exception:
        return b""
