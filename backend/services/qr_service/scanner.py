"""QR Service - scanner (server side of the admin camera scanner)."""
from __future__ import annotations

from services.qr_service import validator, database as qr_db
from services.scanner_service import checkin as checkin_service


def scan(raw_payload: str, admin_name: str = "admin", method: str = "qr",
         ip: str | None = None, stage: str = "checkin"):
    """Validate a scanned QR then check the patient in.

    Returns a JSON-ready dict consumed directly by the admin scanner UI.
    """
    result = validator.validate(raw_payload, stage=stage)
    appt = result["appointment"]

    if not result["ok"]:
        checkin_service.log_scan(
            appointment_id=appt["id"] if appt else None,
            patient_id=appt["patient_id"] if appt else None,
            admin_name=admin_name, method=method,
            status=result["code"], message=result["message"], ip=ip)
        return {"ok": False, "code": result["code"], "message": result["message"],
                "patient": appt["pname"] if appt else None,
                "token": appt["qr_token"] if appt else None}

    if stage == "consult":
        ok, message, appt2 = checkin_service.consultation_scan(
            appt["id"], admin_name=admin_name, method=method, ip=ip)
    else:
        ok, message, appt2 = checkin_service.check_in(
            appt["id"], admin_name=admin_name, method=method, ip=ip)
    if ok:
        qr_db.mark_scanned(appt["qr_token"])

    return {
        "ok": ok,
        "code": "ok" if ok else "error",
        "message": message,
        "patient": appt["pname"],
        "patient_id": appt["patient_id"],
        "appointment_id": appt["id"],
        "token": appt["qr_token"],
        "doctor": appt["dname"],
        "department": appt["ddept"],
        "queue_no": appt["queue_no"],
        "stage": stage,
        "status": (appt2["checkin_status"] if (ok and appt2 is not None
                   and "checkin_status" in appt2.keys()) else None),
    }
