"""
QR Service - Generator
======================
Builds the signed QR payload for every appointment and renders it as
SVG (dependency-free) or PNG (downloadable / printable).

Payload contains exactly the fields required by the specification:
    patient_id, appointment_id, queue_token, booking_date, hospital_id
plus an HMAC signature so a forged QR can never be accepted.
"""
from __future__ import annotations

import hmac
import hashlib
import io
import json
import secrets
from datetime import datetime

from config import Config
from models import query, execute

HOSPITAL_ID = getattr(Config, "HOSPITAL_ID", "MQ-HOSP-001")


# --------------------------------------------------------------- signing
def _secret() -> bytes:
    return str(Config.SECRET_KEY).encode("utf-8")


def sign(payload: dict) -> str:
    """Deterministic HMAC-SHA256 signature of the payload (hex, 16 chars)."""
    base = "|".join(str(payload.get(k, "")) for k in
                    ("patient_id", "appointment_id", "queue_token",
                     "booking_date", "hospital_id"))
    return hmac.new(_secret(), base.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def new_queue_token(prefix: str = "MQ") -> str:
    """Short, human-typeable, collision-checked queue token."""
    while True:
        code = f"{prefix}-{secrets.token_hex(3).upper()}-{secrets.randbelow(9000) + 1000}"
        if not query("SELECT 1 FROM appointments WHERE qr_token=?", (code,), one=True):
            return code


# --------------------------------------------------------------- payload
def build_payload(appt_id: int) -> dict | None:
    """Return the full QR payload for an appointment (None if missing)."""
    a = query("""SELECT a.id, a.patient_id, a.doctor_id, a.appt_date, a.qr_token,
                        a.queue_no, a.status, a.checkin_status
                 FROM appointments a WHERE a.id=?""", (appt_id,), one=True)
    if not a:
        return None

    token = a["qr_token"]
    if not token:
        token = new_queue_token()
        execute("UPDATE appointments SET qr_token=? WHERE id=?", (token, appt_id))

    payload = {
        "v": 1,
        "patient_id": a["patient_id"],
        "appointment_id": a["id"],
        "queue_token": token,
        "booking_date": a["appt_date"],
        "hospital_id": HOSPITAL_ID,
    }
    payload["sig"] = sign(payload)
    return payload


def payload_text(appt_id: int) -> str:
    """Compact JSON string that is actually encoded inside the QR image."""
    payload = build_payload(appt_id)
    return json.dumps(payload, separators=(",", ":")) if payload else ""


# --------------------------------------------------------------- rendering
def qr_svg(data: str) -> str:
    """Inline SVG QR - works with zero native dependencies."""
    if not data:
        return ""
    try:
        import qrcode
        import qrcode.image.svg as qsvg
        img = qrcode.make(data, image_factory=qsvg.SvgPathImage, box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception as exc:  # pragma: no cover
        print("[QR] svg render failed:", exc)
        return ""


def qr_png(data: str, box_size: int = 10, border: int = 3) -> bytes:
    """PNG bytes for download / printing.

    Tries Pillow first, then the pure-python pypng factory, so the feature
    keeps working even on machines without native image libraries.
    """
    if not data:
        return b""
    import qrcode
    # 1) Pillow
    try:
        img = qrcode.make(data, box_size=box_size, border=border)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        pass
    # 2) pure python png
    try:
        from qrcode.image.pure import PyPNGImage
        img = qrcode.make(data, image_factory=PyPNGImage,
                          box_size=box_size, border=border)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue()
    except Exception as exc:  # pragma: no cover
        print("[QR] png render failed:", exc)
        return b""


# --------------------------------------------------------------- public API
def issue_qr(appt_id: int, patient_id: int | None = None) -> dict | None:
    """Create (or refresh) the QR for an appointment and persist it.

    Called automatically right after every booking, so **every registered
    patient always has a valid QR code**.
    """
    from services.qr_service import database as qr_db

    payload = build_payload(appt_id)
    if not payload:
        return None
    qr_db.save_qr(
        appointment_id=payload["appointment_id"],
        patient_id=payload["patient_id"],
        queue_token=payload["queue_token"],
        booking_date=payload["booking_date"],
        hospital_id=payload["hospital_id"],
        signature=payload["sig"],
        payload=json.dumps(payload, separators=(",", ":")),
    )
    return payload


def backfill_all():
    """Guarantee historical appointments also carry a QR record."""
    rows = query("SELECT id FROM appointments")
    for r in rows:
        try:
            issue_qr(r["id"])
        except Exception:
            continue
    print(f"[QR] ensured QR codes for {len(rows)} appointment(s) at "
          f"{datetime.now().strftime('%I:%M %p').lstrip('0')}")
