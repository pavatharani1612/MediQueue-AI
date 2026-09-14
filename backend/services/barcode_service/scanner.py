"""Barcode Service - scanner (reuses the shared check-in engine)."""
from __future__ import annotations

from services.barcode_service import validator
from services.scanner_service import checkin as checkin_service


def scan(code: str, admin_name="admin", ip=None) -> dict:
    result = validator.validate(code)
    appt = result["appointment"]
    if not result["ok"]:
        checkin_service.log_scan(
            appointment_id=appt["appointment_id"] if appt else None,
            patient_id=appt["patient_id"] if appt else None,
            admin_name=admin_name, method="barcode",
            status=result["code"], message=result["message"], ip=ip)
        return {"ok": False, "code": result["code"], "message": result["message"]}

    ok, message, _ = checkin_service.check_in(
        appt["appointment_id"], admin_name=admin_name, method="barcode", ip=ip)
    return {"ok": ok, "code": "ok" if ok else "error", "message": message,
            "patient": appt["pname"], "appointment_id": appt["appointment_id"]}
