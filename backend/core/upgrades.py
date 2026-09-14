"""
MediQueue AI - Upgrades module
==============================
Adds, without touching existing behaviour:

*  QR appointment pass + secure QR / manual check-in desk (/staff/checkin)
*  Appointment workflow: Booked -> Checked-In -> Waiting -> Consulting -> Completed
*  Secure one-click email action links (confirm / cancel / reschedule / reappoint)

All new database columns are added with safe, idempotent ALTER TABLE
migrations - existing records are never deleted or reset.
"""
from __future__ import annotations

import io
import secrets
from datetime import datetime

from flask import (render_template, request, session, redirect, url_for,
                   flash, jsonify, Response, abort)

from models import query, execute, get_conn
from config import Config
import ai_engine

CHECKIN_FLOW = ["Booked", "Checked-In", "Waiting", "Consulting", "Completed"]


# ---------------------------------------------------------------- migration
def init_upgrade_tables():
    with get_conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(appointments)").fetchall()}
        extras = [
            ("qr_token",        "TEXT"),
            ("action_token",    "TEXT"),
            ("appt_time",       "TEXT"),
            ("checkin_status",  "TEXT DEFAULT 'Booked'"),
            ("consult_start_time", "TEXT"),
            ("consult_end_time",   "TEXT"),
            ("consult_started_by", "TEXT"),
            ("consult_ended_by",   "TEXT"),
            ("checkin_time",    "TEXT"),
            ("checked_in_by",   "TEXT"),
            ("actual_wait_minutes", "INTEGER"),
        ]
        for name, ddl in extras:
            if name not in cols:
                c.execute(f"ALTER TABLE appointments ADD COLUMN {name} {ddl}")
        c.execute("""CREATE TABLE IF NOT EXISTS checkin_logs(
            id INTEGER PRIMARY KEY,
            appointment_id INTEGER,
            method TEXT,
            staff TEXT,
            result TEXT,
            created_at TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_appt_qr ON appointments(qr_token)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_appt_action ON appointments(action_token)")

    # Backfill tokens for appointments created before this upgrade.
    for row in query("SELECT id FROM appointments WHERE qr_token IS NULL OR qr_token=''"):
        execute("UPDATE appointments SET qr_token=? WHERE id=?",
                (_new_token("MQ"), row["id"]))
    for row in query("SELECT id FROM appointments WHERE action_token IS NULL OR action_token=''"):
        execute("UPDATE appointments SET action_token=? WHERE id=?",
                (secrets.token_urlsafe(24), row["id"]))
    execute("UPDATE appointments SET checkin_status='Booked' WHERE checkin_status IS NULL")
    # Mirror the chosen slot time into appt_time for reporting/history.
    execute("""UPDATE appointments SET appt_time = COALESCE(slot_time, predicted_time)
               WHERE appt_time IS NULL""")


def _new_token(prefix="MQ"):
    """Short, human-typeable, collision-checked appointment code."""
    while True:
        code = f"{prefix}-{secrets.token_hex(3).upper()}-{secrets.randbelow(9000)+1000}"
        if not query("SELECT 1 FROM appointments WHERE qr_token=?", (code,), one=True):
            return code


def ensure_tokens(appt_id):
    """Guarantee an appointment has a QR code + email action token."""
    a = query("SELECT id, qr_token, action_token FROM appointments WHERE id=?",
              (appt_id,), one=True)
    if not a:
        return None, None
    qr = a["qr_token"]
    act = a["action_token"]
    if not qr:
        qr = _new_token("MQ")
        execute("UPDATE appointments SET qr_token=? WHERE id=?", (qr, appt_id))
    if not act:
        act = secrets.token_urlsafe(24)
        execute("UPDATE appointments SET action_token=? WHERE id=?", (act, appt_id))
    return qr, act


# ---------------------------------------------------------------- QR image
def qr_svg(data: str) -> str:
    """Render a QR code as inline SVG (no Pillow / native deps required)."""
    try:
        import qrcode
        import qrcode.image.svg as qsvg
        img = qrcode.make(data, image_factory=qsvg.SvgPathImage,
                          box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------- check-in
def perform_checkin(appt_id, method="qr", staff="staff"):
    """Idempotent, duplicate-safe check-in. Returns (ok, message, appointment)."""
    a = query("""SELECT a.*, p.name AS pname, p.email, p.phone, d.name AS dname
                 FROM appointments a
                 JOIN patients p ON p.id=a.patient_id
                 JOIN doctors  d ON d.id=a.doctor_id
                 WHERE a.id=?""", (appt_id,), one=True)
    if not a:
        return False, "Appointment not found.", None

    if a["status"] in ("Cancelled", "Missed"):
        _log_checkin(appt_id, method, staff, "rejected: " + a["status"])
        return False, f"Cannot check in - appointment is {a['status']}.", a
    if a["status"] == "Completed":
        _log_checkin(appt_id, method, staff, "rejected: completed")
        return False, "This consultation is already completed.", a
    if (a["checkin_status"] or "Booked") == "Checked-In":
        _log_checkin(appt_id, method, staff, "duplicate")
        return False, (f"Already checked in at "
                       f"{_ampm(a['checkin_time'])}. Duplicate check-in blocked."), a

    now = datetime.now()
    execute("""UPDATE appointments
               SET checkin_status='Checked-In', checkin_time=?, checked_in_by=?,
                   status = CASE WHEN status='Emergency-PendingVerification'
                                 THEN status ELSE 'Waiting' END
               WHERE id=?""", (now.isoformat(), staff, appt_id))
    _log_checkin(appt_id, method, staff, "success")

    # Queue changed -> re-run the AI prediction for everybody.
    try:
        ai_engine.repredict_doctor_queue(a["doctor_id"])
    except Exception:
        pass

    execute("""INSERT INTO notifications(role,user_id,title,message,created_at)
               VALUES('patient',?,?,?,?)""",
            (a["patient_id"], "Checked in",
             f"You are checked in for Dr {a['dname']} (Token {a['queue_no']}).",
             now.isoformat()))
    try:
        from email_service import send_email, _wrap
        send_email(a["email"], "MediQueue AI - Check-in confirmed",
                   _wrap("Check-in confirmed",
                         f"<p>Hello {a['pname']},</p>"
                         f"<p>You are checked in for <b>Dr {a['dname']}</b>.</p>"
                         f"<p>Token: <b>{a['queue_no']}</b><br>"
                         f"Checked in at: <b>{_ampm(now)}</b></p>"
                         f"<p>Please wait in the lounge - your live queue status is "
                         f"available in your dashboard.</p>"))
    except Exception:
        pass

    a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
    return True, "Check-in successful.", a


def _log_checkin(appt_id, method, staff, result):
    execute("""INSERT INTO checkin_logs(appointment_id,method,staff,result,created_at)
               VALUES(?,?,?,?,?)""",
            (appt_id, method, staff, result, datetime.now().isoformat()))


def _ampm(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%I:%M %p").lstrip("0")
    text = str(value)
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return text


def _find_by_code(code):
    code = (code or "").strip()
    if not code:
        return None
    row = query("""SELECT * FROM appointments WHERE qr_token=? COLLATE NOCASE""",
                (code,), one=True)
    if row:
        return row
    if code.isdigit():
        return query("SELECT * FROM appointments WHERE id=?", (int(code),), one=True)
    return None


# ---------------------------------------------------------------- routes
def register_upgrade_routes(app):

    def _staff_ok():
        return session.get("role") in ("admin", "doctor", "staff")

    # ---------- Patient: QR appointment pass ----------
    @app.route("/patient/appointment/<int:appt_id>/pass")
    def appointment_pass(appt_id):
        if session.get("role") != "patient":
            flash("Please log in as a patient.", "warning")
            return redirect(url_for("login"))
        a = query("""SELECT a.*, d.name AS dname, d.department AS ddept, d.fee,
                            p.name AS pname, p.phone, p.email
                     FROM appointments a
                     JOIN doctors d ON d.id=a.doctor_id
                     JOIN patients p ON p.id=a.patient_id
                     WHERE a.id=?""", (appt_id,), one=True)
        if not a:
            abort(404)
        if a["patient_id"] != session["uid"]:
            abort(403)
        ensure_tokens(appt_id)
        # Reuse the exact same QR Pass shown in Patient Dashboard -> Queue Status.
        return redirect(url_for("patient_qr_page", appt_id=appt_id))

    @app.route("/patient/pass/<int:appt_id>.svg")
    def appointment_pass_svg(appt_id):
        if session.get("role") != "patient":
            abort(401)
        a = query("SELECT patient_id, qr_token FROM appointments WHERE id=?",
                  (appt_id,), one=True)
        if not a:
            abort(404)
        if a["patient_id"] != session["uid"]:
            abort(403)
        qr, _ = ensure_tokens(appt_id)
        url = request.url_root.rstrip("/") + url_for("staff_checkin_scan", token=qr)
        return Response(qr_svg(url) or "<svg xmlns='http://www.w3.org/2000/svg'/>",
                        mimetype="image/svg+xml")

    # ---------- Staff check-in desk ----------
    @app.route("/staff/checkin", methods=["GET", "POST"])
    def staff_checkin():
        if not _staff_ok():
            flash("Staff / admin login required for the check-in desk.", "warning")
            return redirect(url_for("login"))

        result = None
        appt = None
        if request.method == "POST":
            code = (request.form.get("code") or "").strip()
            row = _find_by_code(code)
            if not row:
                result = {"ok": False, "msg": "No appointment found for that code."}
            elif session.get("role") == "doctor" and row["doctor_id"] != session.get("uid"):
                result = {"ok": False, "msg": "This appointment belongs to another doctor."}
            else:
                ok, msg, appt = perform_checkin(
                    row["id"], method="manual",
                    staff=f"{session.get('role')}:{session.get('uid')}")
                result = {"ok": ok, "msg": msg}
                appt = query("""SELECT a.*, p.name AS pname, p.age, p.phone,
                                       d.name AS dname
                                FROM appointments a
                                JOIN patients p ON p.id=a.patient_id
                                JOIN doctors d ON d.id=a.doctor_id
                                WHERE a.id=?""", (row["id"],), one=True)

        today = datetime.now().strftime("%Y-%m-%d")
        if session.get("role") == "doctor":
            recent = query("""SELECT a.*, p.name AS pname, d.name AS dname
                              FROM appointments a
                              JOIN patients p ON p.id=a.patient_id
                              JOIN doctors d ON d.id=a.doctor_id
                              WHERE a.appt_date=? AND a.doctor_id=?
                              ORDER BY a.id DESC LIMIT 40""", (today, session["uid"]))
        else:
            recent = query("""SELECT a.*, p.name AS pname, d.name AS dname
                              FROM appointments a
                              JOIN patients p ON p.id=a.patient_id
                              JOIN doctors d ON d.id=a.doctor_id
                              WHERE a.appt_date=?
                              ORDER BY a.id DESC LIMIT 40""", (today,))
        return render_template("staff_checkin.html", result=result, appt=appt,
                               recent=recent, ampm=_ampm)

    @app.route("/staff/checkin/scan/<token>")
    def staff_checkin_scan(token):
        if not _staff_ok():
            flash("Scan received - please log in as staff/admin to complete check-in.",
                  "warning")
            return redirect(url_for("login"))
        row = _find_by_code(token)
        if not row:
            flash("Invalid or expired QR code.", "danger")
            return redirect(url_for("staff_checkin"))
        ok, msg, _ = perform_checkin(row["id"], method="qr",
                                     staff=f"{session.get('role')}:{session.get('uid')}")
        flash(msg, "success" if ok else "warning")
        return redirect(url_for("staff_checkin"))

    @app.route("/api/staff/checkin", methods=["POST"])
    def api_staff_checkin():
        if not _staff_ok():
            return jsonify({"ok": False, "error": "Staff session required"}), 401
        data = request.get_json(silent=True) or request.form
        row = _find_by_code(data.get("code"))
        if not row:
            return jsonify({"ok": False, "error": "Appointment not found"}), 404
        if session.get("role") == "doctor" and row["doctor_id"] != session.get("uid"):
            return jsonify({"ok": False, "error": "Not your patient"}), 403
        ok, msg, _ = perform_checkin(row["id"], method="qr-api",
                                     staff=f"{session.get('role')}:{session.get('uid')}")
        return jsonify({"ok": ok, "message": msg, "appointment_id": row["id"]})

    # ---------- Secure email action links ----------
    @app.route("/appointment/action/<token>/<action>")
    def appointment_email_action(token, action):
        a = query("""SELECT a.*, p.name AS pname, p.email, d.name AS dname
                     FROM appointments a
                     JOIN patients p ON p.id=a.patient_id
                     JOIN doctors d ON d.id=a.doctor_id
                     WHERE a.action_token=?""", (token,), one=True)
        if not a or action not in ("confirm", "cancel", "reschedule", "reappoint"):
            return render_template("email_action.html", ok=False,
                                   title="Invalid link",
                                   message="This action link is invalid or has expired."), 404

        if a["status"] in ("Completed", "Cancelled") and action != "reappoint":
            return render_template("email_action.html", ok=False,
                                   title="No longer available",
                                   message=f"This appointment is already {a['status']}.")

        now = datetime.now().isoformat()
        if action == "confirm":
            execute("UPDATE appointments SET patient_responded=1 WHERE id=?", (a["id"],))
            title, msg = "Attendance confirmed", \
                f"Thank you {a['pname']} - your appointment with Dr {a['dname']} is confirmed."
        elif action == "cancel":
            execute("UPDATE appointments SET status='Cancelled', patient_responded=1 WHERE id=?",
                    (a["id"],))
            try:
                ai_engine.repredict_doctor_queue(a["doctor_id"])
            except Exception:
                pass
            title, msg = "Appointment cancelled", \
                "Your appointment has been cancelled and the queue has been updated."
        else:  # reschedule / reappoint - hand over to the secure in-app flow
            execute("UPDATE appointments SET patient_responded=1 WHERE id=?", (a["id"],))
            if session.get("role") == "patient" and session.get("uid") == a["patient_id"]:
                return redirect(url_for("patient_book"))
            title, msg = ("Please sign in",
                          "For your security, please log in to your MediQueue AI account "
                          "to complete the reschedule / reappointment request.")

        execute("""INSERT INTO notifications(role,user_id,title,message,created_at)
                   VALUES('patient',?,?,?,?)""",
                (a["patient_id"], title, msg, now))
        return render_template("email_action.html", ok=True, title=title, message=msg)

    return app
