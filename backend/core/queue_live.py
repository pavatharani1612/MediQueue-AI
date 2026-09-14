"""
MediQueue AI - Live Queue Engine
================================
Near-real-time queue tracking backed by the *actual* SQLite database.

Exposes:
    GET /api/patient/queue-live       JSON snapshot for the logged-in patient
    GET /patient/live                 full-screen live queue tracker page

All values (current token, your token, patients ahead, estimated wait,
predicted consultation time, doctor status) are computed from live data -
nothing is hard-coded.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import jsonify, render_template, request, session, redirect, url_for, flash

from models import query, execute
import ai_engine

OPEN = ("Waiting", "InProgress", "Emergency-PendingVerification")

STATUS_LABEL = {
    "Waiting": "Waiting",
    "InProgress": "Consulting",
    "Completed": "Completed",
    "Cancelled": "Cancelled",
    "Missed": "Missed",
    "Emergency-PendingVerification": "Emergency - awaiting verification",
}


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _ampm(value):
    """Format HH:MM / ISO / datetime as a friendly 12-hour AM/PM string."""
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%I:%M %p").lstrip("0")
    text = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return text


def _fmt_wait(minutes):
    minutes = max(0, int(minutes or 0))
    if minutes == 0:
        return "It's your turn"
    if minutes < 60:
        return f"{minutes} minutes"
    h, m = divmod(minutes, 60)
    return f"{h} hr {m} min" if m else f"{h} hr"


def doctor_status(doctor_id):
    doc = query("SELECT * FROM doctors WHERE id=?", (doctor_id,), one=True)
    if not doc:
        return "Unknown"
    if not doc["available"]:
        return "Unavailable"
    busy = query("""SELECT 1 FROM appointments WHERE doctor_id=? AND appt_date=?
                    AND status='InProgress' LIMIT 1""",
                 (doctor_id, _today()), one=True)
    return "Consulting" if busy else "Available"


def snapshot_for_appointment(appt_id, patient_id=None):
    """Live snapshot for one appointment. Ownership is enforced when
    ``patient_id`` is supplied."""
    a = query("""SELECT a.*, d.name AS doctor_name, d.department AS dept,
                        d.available AS doc_available
                 FROM appointments a JOIN doctors d ON d.id = a.doctor_id
                 WHERE a.id=?""", (appt_id,), one=True)
    if not a:
        return None, "Appointment not found"
    if patient_id is not None and a["patient_id"] != patient_id:
        return None, "This appointment does not belong to you"

    ctx = ai_engine.queue_context(a["doctor_id"], a["id"])
    queue_rows = ctx["queue"]

    current = next((r for r in queue_rows if r["status"] == "InProgress"), None)
    if current is None and queue_rows:
        current = queue_rows[0]

    status = a["status"] or "Waiting"
    if status in ("Completed", "Cancelled", "Missed"):
        wait_minutes = 0
        consult_at = a["actual_start"] or a["predicted_time"]
        ahead = 0
        position = None
    else:
        pred = ai_engine.predict_wait_context(
            a["doctor_id"], a["id"], priority=a["priority"] or "Normal", ctx=ctx)
        wait_minutes = pred["wait_minutes"]
        consult_at = pred["predicted_consult_at"]
        ahead = pred["patients_ahead"]
        position = pred["position"]
        execute("UPDATE appointments SET predicted_wait_time=?, predicted_time=? WHERE id=?",
                (wait_minutes, pred["predicted_consult_at"].strftime("%H:%M"), a["id"]))

    checkin_status = None
    try:
        checkin_status = a["checkin_status"]
    except Exception:
        checkin_status = None

    queue_state = STATUS_LABEL.get(status, status)
    if status == "Waiting" and (checkin_status or "Booked") != "Checked-In":
        queue_state = "Booked (not checked in)"

    return {
        "appointment_id": a["id"],
        "your_token": a["queue_no"],
        "current_token": current["queue_no"] if current else "-",
        "current_status": STATUS_LABEL.get(current["status"], current["status"]) if current else "-",
        "patients_ahead": ahead,
        "position": position,
        "queue_length": ctx["queue_length"],
        "estimated_wait_minutes": wait_minutes,
        "estimated_wait_text": _fmt_wait(wait_minutes),
        "predicted_consultation": _ampm(consult_at),
        "doctor": a["doctor_name"],
        "doctor_id": a["doctor_id"],
        "department": a["dept"],
        "doctor_status": doctor_status(a["doctor_id"]),
        "queue_status": queue_state,
        "appointment_status": STATUS_LABEL.get(status, status),
        "raw_status": status,
        "priority": a["priority"] or "Normal",
        "emergency": (a["priority"] == "Emergency"),
        "emergency_verified": bool(a["emergency_verified"] or 0),
        "checkin_status": checkin_status or "Booked",
        "appt_date": a["appt_date"],
        "appt_time": _ampm(a["slot_time"] or a["predicted_time"]),
        "delay_minutes": a["delay_minutes"] or 0,
        "avg_consult_minutes": ctx["avg_consult"],
        "server_time": datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }, None


def active_appointments(patient_id):
    return query("""SELECT a.id FROM appointments a
                    WHERE a.patient_id=? AND a.status IN ('Waiting','InProgress','Emergency-PendingVerification')
                    ORDER BY a.appt_date ASC, a.id ASC""", (patient_id,))


# ------------------------------------------------------------------ routes
def register_queue_live_routes(app):

    @app.route("/api/patient/queue-live", methods=["GET"])
    def api_patient_queue_live():
        """Live queue JSON for the logged-in patient.

        Never 403s a valid patient session; unauthenticated callers get a
        clean 401 and cross-patient access attempts get a 403.
        """
        if session.get("role") != "patient" or not session.get("uid"):
            return jsonify({"ok": False, "error": "Patient login required",
                            "login_url": "/login"}), 401

        pid = session["uid"]
        raw_id = request.args.get("appointment_id") or request.args.get("appt_id")

        if raw_id:
            try:
                appt_id = int(raw_id)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Invalid appointment id"}), 400
            owner = query("SELECT patient_id FROM appointments WHERE id=?",
                          (appt_id,), one=True)
            if not owner:
                return jsonify({"ok": False, "error": "Appointment not found"}), 404
            if owner["patient_id"] != pid:
                return jsonify({"ok": False, "error": "Not your appointment"}), 403
            ids = [appt_id]
        else:
            ids = [r["id"] for r in active_appointments(pid)]

        items = []
        for aid in ids:
            snap, err = snapshot_for_appointment(aid, patient_id=pid)
            if snap:
                items.append(snap)

        return jsonify({
            "ok": True,
            "count": len(items),
            "appointments": items,
            "server_time": datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
            "ts": datetime.now().isoformat(timespec="seconds"),
        })

    @app.route("/patient/live")
    def patient_live():
        if session.get("role") != "patient":
            flash("Please log in as a patient.", "warning")
            return redirect(url_for("login"))
        rows = query("""SELECT a.*, d.name AS dname, d.department AS ddept
                        FROM appointments a JOIN doctors d ON d.id=a.doctor_id
                        WHERE a.patient_id=? AND a.status IN ('Waiting','InProgress','Emergency-PendingVerification')
                        ORDER BY a.id DESC""", (session["uid"],))
        return render_template("patient_live.html", appts=rows)

    return app
