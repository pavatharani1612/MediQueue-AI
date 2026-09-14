"""Barcode Service - validator."""
from __future__ import annotations

from datetime import datetime

from models import query


def validate(code: str) -> dict:
    code = (code or "").strip()
    row = query("""SELECT b.*, a.status, a.appt_date, a.checkin_status,
                          a.id AS appointment_id, p.name AS pname
                   FROM barcodes b
                   JOIN appointments a ON a.id=b.appointment_id
                   JOIN patients p ON p.id=b.patient_id
                   WHERE b.code=? COLLATE NOCASE""", (code,), one=True)
    if not row:
        return {"ok": False, "code": "invalid", "message": "Invalid Barcode",
                "appointment": None}
    if row["status"] in ("Completed", "Cancelled", "Missed"):
        return {"ok": False, "code": "expired", "message": "Appointment Expired",
                "appointment": row}
    if (row["appt_date"] or "") != datetime.now().strftime("%Y-%m-%d"):
        return {"ok": False, "code": "expired", "message": "Appointment Expired",
                "appointment": row}
    if (row["checkin_status"] or "Booked") == "Checked-In":
        return {"ok": False, "code": "already", "message": "Already Checked In",
                "appointment": row}
    return {"ok": True, "code": "ok", "message": "Barcode verified", "appointment": row}
