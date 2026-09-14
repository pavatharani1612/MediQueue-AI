"""QR Service - validator.

Verifies a scanned payload before any check-in is allowed:
signature, hospital, appointment existence, booking date and expiry.
"""
from __future__ import annotations

import json
from datetime import datetime

from models import query
from services.qr_service import generator, database as qr_db

INVALID = "Invalid QR Code"
EXPIRED = "Appointment Expired"
ALREADY = "Already Checked In"


def parse(raw: str) -> dict | None:
    """Accept either the JSON payload or a bare queue token / check-in URL."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except Exception:
            return None
    # URL form: .../staff/checkin/scan/<token>
    token = raw.rsplit("/", 1)[-1] if "/" in raw else raw
    return {"queue_token": token, "legacy": True}


def validate(raw: str, stage: str = "checkin") -> dict:
    """Return {ok, code, message, appointment}.

    code is one of: ok / invalid / expired / already / wrong_day
    """
    data = parse(raw)
    if not data:
        return {"ok": False, "code": "invalid", "message": INVALID, "appointment": None}

    token = str(data.get("queue_token") or "").strip()
    if not token:
        return {"ok": False, "code": "invalid", "message": INVALID, "appointment": None}

    appt = query("""SELECT a.*, p.name AS pname, p.phone, p.email,
                           d.name AS dname, d.department AS ddept
                    FROM appointments a
                    JOIN patients p ON p.id=a.patient_id
                    JOIN doctors  d ON d.id=a.doctor_id
                    WHERE a.qr_token=? COLLATE NOCASE""", (token,), one=True)
    if not appt:
        return {"ok": False, "code": "invalid", "message": INVALID, "appointment": None}

    # Signature check (skipped for legacy URL/token-only QRs already in the wild).
    if not data.get("legacy"):
        expected = generator.sign({
            "patient_id": data.get("patient_id"),
            "appointment_id": data.get("appointment_id"),
            "queue_token": token,
            "booking_date": data.get("booking_date"),
            "hospital_id": data.get("hospital_id"),
        })
        if data.get("sig") != expected:
            return {"ok": False, "code": "invalid",
                    "message": INVALID + " (signature mismatch)", "appointment": appt}
        if int(data.get("appointment_id") or 0) != int(appt["id"]):
            return {"ok": False, "code": "invalid", "message": INVALID, "appointment": appt}

    record = qr_db.get_by_token(token)
    if record and record["status"] == "Expired":
        return {"ok": False, "code": "expired", "message": EXPIRED, "appointment": appt}

    if appt["status"] in ("Cancelled", "Missed"):
        return {"ok": False, "code": "expired",
                "message": f"Appointment {appt['status']}", "appointment": appt}
    if appt["status"] == "Completed":
        return {"ok": False, "code": "expired", "message": EXPIRED, "appointment": appt}

    today = datetime.now().strftime("%Y-%m-%d")
    if (appt["appt_date"] or "") < today:
        return {"ok": False, "code": "expired", "message": EXPIRED, "appointment": appt}
    if (appt["appt_date"] or "") > today:
        return {"ok": False, "code": "wrong_day",
                "message": f"Appointment is booked for {appt['appt_date']}, not today.",
                "appointment": appt}

    state = (appt["checkin_status"] or "Booked")

    if stage == "consult":
        # Stage 2 - consultation scan. The patient must be checked in first.
        if state in ("Booked", "", None):
            return {"ok": False, "code": "not_checked_in",
                    "message": "Patient has not checked in yet. Scan at "
                               "Check-In first.", "appointment": appt}
        if state == "Completed":
            return {"ok": False, "code": "already",
                    "message": "Consultation already completed.",
                    "appointment": appt}
        return {"ok": True, "code": "ok", "message": "QR verified",
                "appointment": appt}

    if state != "Booked":
        return {"ok": False, "code": "already", "message": ALREADY, "appointment": appt}

    return {"ok": True, "code": "ok", "message": "QR verified", "appointment": appt}
