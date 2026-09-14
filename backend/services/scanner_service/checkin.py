"""
Scanner Service - check-in engine
================================
Single source of truth for "patient physically arrived".
Used by the admin QR scanner, the reception desk and manual token entry.
"""
from __future__ import annotations

from datetime import datetime

from models import query, execute, get_conn
from services.notification_service import notifications
from services.realtime_service import bus


def init_tables():
    with get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS checkins(
            id INTEGER PRIMARY KEY,
            patient_id INTEGER,
            appointment_id INTEGER,
            scan_time TEXT,
            date TEXT,
            admin_name TEXT,
            method TEXT DEFAULT 'qr',
            status TEXT,                 -- ok / already / invalid / expired / wrong_day
            message TEXT,
            ip_address TEXT,
            FOREIGN KEY(patient_id)     REFERENCES patients(id),
            FOREIGN KEY(appointment_id) REFERENCES appointments(id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_checkin_date ON checkins(date)")


def log_scan(appointment_id, patient_id, admin_name, method, status,
             message="", ip=None):
    now = datetime.now()
    return execute("""INSERT INTO checkins(patient_id,appointment_id,scan_time,date,
                        admin_name,method,status,message,ip_address)
                      VALUES(?,?,?,?,?,?,?,?,?)""",
                   (patient_id, appointment_id, now.isoformat(timespec="seconds"),
                    now.strftime("%Y-%m-%d"), admin_name, method, status, message, ip))


def check_in(appt_id, admin_name="admin", method="qr", ip=None):
    """Idempotent check-in that pushes the patient into the live queue."""
    a = query("""SELECT a.*, p.name AS pname, p.email, d.name AS dname
                 FROM appointments a
                 JOIN patients p ON p.id=a.patient_id
                 JOIN doctors  d ON d.id=a.doctor_id
                 WHERE a.id=?""", (appt_id,), one=True)
    if not a:
        return False, "Appointment not found.", None

    if (a["checkin_status"] or "Booked") == "Checked-In":
        log_scan(appt_id, a["patient_id"], admin_name, method, "already",
                 "Already Checked In", ip)
        return False, "Already Checked In", a

    now = datetime.now()
    execute("""UPDATE appointments
               SET checkin_status='Checked-In', checkin_time=?, checked_in_by=?,
                   status = CASE WHEN status='Emergency-PendingVerification'
                                 THEN status ELSE 'Waiting' END
               WHERE id=?""", (now.isoformat(timespec="seconds"), admin_name, appt_id))

    log_scan(appt_id, a["patient_id"], admin_name, method, "ok",
             "Check-in successful", ip)

    # legacy log table kept intact for backwards compatibility
    try:
        execute("""INSERT INTO checkin_logs(appointment_id,method,staff,result,created_at)
                   VALUES(?,?,?,?,?)""",
                (appt_id, method, admin_name, "success", now.isoformat()))
    except Exception:
        pass

    # Recalculate queue + AI waiting time so every dashboard is instantly correct.
    try:
        import ai_engine
        ai_engine.repredict_doctor_queue(a["doctor_id"])
    except Exception:
        pass
    try:
        from queue_manager import recalculate_queue
        recalculate_queue(a["doctor_id"])
    except Exception:
        pass

    notifications.push("patient", a["patient_id"], "Check-In Completed",
                       f"You are checked in for Dr {a['dname']} (Token {a['queue_no']}).")
    notifications.push("doctor", a["doctor_id"], "Patient Checked In",
                       f"{a['pname']} has arrived (Token {a['queue_no']}).")
    notifications.push("admin", 0, "QR Successfully Scanned",
                       f"{a['pname']} checked in by {admin_name}.")

    bus.publish("checkin", {"appointment_id": appt_id, "patient_id": a["patient_id"],
                            "doctor_id": a["doctor_id"], "queue_no": a["queue_no"]})

    try:
        from services.mail_service.send_mail import send_checkin_mail
        send_checkin_mail(a)
    except Exception:
        pass

    a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
    return True, "Check-in successful", a


# ------------------------------------------------------------------ reporting
def today_checkins(limit=100):
    return query("""SELECT c.*, p.name AS patient_name, a.queue_no, a.department
                    FROM checkins c
                    LEFT JOIN patients p ON p.id=c.patient_id
                    LEFT JOIN appointments a ON a.id=c.appointment_id
                    WHERE c.date=? ORDER BY c.id DESC LIMIT ?""",
                 (datetime.now().strftime("%Y-%m-%d"), limit))


def counts_today():
    today = datetime.now().strftime("%Y-%m-%d")
    row = query("""SELECT
                     SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) ok,
                     SUM(CASE WHEN method='qr' THEN 1 ELSE 0 END) qr,
                     SUM(CASE WHEN method='barcode' THEN 1 ELSE 0 END) barcode,
                     COUNT(*) total
                   FROM checkins WHERE date=?""", (today,), one=True)
    return {"checked_in": row["ok"] or 0, "qr_scans": row["qr"] or 0,
            "barcode_scans": row["barcode"] or 0, "scans": row["total"] or 0}


# =========================================================================
# STAGE 2 - Consultation scanning
# =========================================================================
# Workflow:  Booked -> Checked-In -> Waiting -> Consultation Started -> Completed
WORKFLOW = ["Booked", "Checked-In", "Waiting", "Consultation Started", "Completed"]


def consultation_scan(appt_id, admin_name="doctor", method="qr", ip=None):
    """Second-level scan performed by the doctor / doctor assistant.

    First scan  -> Consultation Started
    Second scan -> Completed (and the next patient moves up automatically)
    """
    a = query("""SELECT a.*, p.name AS pname, p.email, d.name AS dname
                 FROM appointments a
                 JOIN patients p ON p.id=a.patient_id
                 JOIN doctors  d ON d.id=a.doctor_id
                 WHERE a.id=?""", (appt_id,), one=True)
    if not a:
        return False, "Appointment not found.", None

    state = (a["checkin_status"] or "Booked")
    now = datetime.now()
    stamp = now.isoformat(timespec="seconds")

    if state in ("Booked", "", None):
        log_scan(appt_id, a["patient_id"], admin_name, method, "not_checked_in",
                 "Patient not checked in yet", ip)
        return False, "Patient has not checked in yet.", a

    if state == "Completed":
        log_scan(appt_id, a["patient_id"], admin_name, method, "already",
                 "Consultation already completed", ip)
        return False, "Consultation already completed.", a

    if state in ("Consultation Started", "Consulting", "InProgress"):
        # ---- finish the consultation ----
        execute("""UPDATE appointments
                   SET checkin_status='Completed', status='Completed',
                       consult_end_time=?, consult_ended_by=?, actual_end=?
                   WHERE id=?""", (stamp, admin_name, stamp, appt_id))
        log_scan(appt_id, a["patient_id"], admin_name, method, "ok",
                 "Consultation completed", ip)
        notifications.push("patient", a["patient_id"], "Consultation Completed",
                           f"Your consultation with Dr {a['dname']} is complete.")
        notifications.push("doctor", a["doctor_id"], "Consultation Completed",
                           f"{a['pname']} (Token {a['queue_no']}) completed.")
        message = "Consultation Completed"
    else:
        # ---- start the consultation ----
        execute("""UPDATE appointments
                   SET checkin_status='Consultation Started', status='InProgress',
                       consult_start_time=?, consult_started_by=?, actual_start=?
                   WHERE id=?""", (stamp, admin_name, stamp, appt_id))
        log_scan(appt_id, a["patient_id"], admin_name, method, "ok",
                 "Consultation started", ip)
        notifications.push("patient", a["patient_id"], "Consultation Started",
                           f"Dr {a['dname']} is ready for you (Token {a['queue_no']}).")
        notifications.push("doctor", a["doctor_id"], "Consultation Started",
                           f"{a['pname']} (Token {a['queue_no']}) is in consultation.")
        message = "Consultation Started"

    # queue moves automatically for everybody else
    try:
        from queue_manager import recalculate_queue
        recalculate_queue(a["doctor_id"])
    except Exception:
        pass
    try:
        import ai_engine
        ai_engine.repredict_doctor_queue(a["doctor_id"])
    except Exception:
        pass

    bus.publish("consultation", {"appointment_id": appt_id,
                                 "doctor_id": a["doctor_id"],
                                 "queue_no": a["queue_no"],
                                 "state": message})

    a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
    return True, message, a


def workflow_state(appt_id):
    a = query("SELECT checkin_status FROM appointments WHERE id=?",
              (appt_id,), one=True)
    return (a["checkin_status"] if a else None) or "Booked"
