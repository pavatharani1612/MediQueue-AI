"""
MediQueue AI - Enhancement module (Phase 2)
============================================
Adds the following features WITHOUT touching any existing behaviour:

  1. Patient Feedback & Review System
  2. Smart Date & Time Slot Booking
  3. Automatic Re-Appointment Cancellation
  4. Email Approval Workflow (Waiting / Re-Appointment / Cancel)
  5. Auto-Cancel on No Response
  6. Live Patient Appointment Timeline
  7. Premium Admin Dashboard analytics
  8. Admin settings (slot interval, grace periods, working hours)

All new tables (feedback, cancel_requests, app_settings) are created in
`init_enhancement_tables()`. Existing tables are only ALTERed additively
with safe migrations.
"""
from datetime import datetime, timedelta, date, time as dtime
from time_utils import to_ampm, to_ampm_datetime
from flask import (
    render_template, request, redirect, url_for, session, flash, abort,
    jsonify, Response
)
from models import query, execute
from config import Config


# =========================================================
# DB migrations
# =========================================================
def init_enhancement_tables():
    # ---- Feedback / Reviews
    execute("""CREATE TABLE IF NOT EXISTS feedback(
        id INTEGER PRIMARY KEY,
        appointment_id INTEGER UNIQUE,
        patient_id INTEGER,
        doctor_id INTEGER,
        rating INTEGER NOT NULL,
        doctor_rating INTEGER,
        hospital_rating INTEGER,
        review TEXT,
        created_at TEXT,
        FOREIGN KEY(appointment_id) REFERENCES appointments(id),
        FOREIGN KEY(patient_id)     REFERENCES patients(id),
        FOREIGN KEY(doctor_id)      REFERENCES doctors(id)
    )""")

    # ---- Cancel requests (part of the approval workflow)
    execute("""CREATE TABLE IF NOT EXISTS cancel_requests(
        id INTEGER PRIMARY KEY,
        patient_id INTEGER,
        appointment_id INTEGER,
        doctor_id INTEGER,
        reason TEXT,
        status TEXT DEFAULT 'Pending',
        admin_action_time TEXT,
        created_at TEXT
    )""")

    # ---- App settings (admin configurable)
    execute("""CREATE TABLE IF NOT EXISTS app_settings(
        key TEXT PRIMARY KEY,
        value TEXT
    )""")

    # ---- Additive column migrations on appointments
    from models import get_conn
    with get_conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(appointments)").fetchall()}
        extras = [
            ("patient_responded",  "INTEGER DEFAULT 0"),
            ("is_reappointment",   "INTEGER DEFAULT 0"),
            ("cancel_reason",      "TEXT"),
            ("slot_time",          "TEXT"),   # HH:MM the patient picked
            ("auto_cancelled",     "INTEGER DEFAULT 0"),
            ("feedback_submitted", "INTEGER DEFAULT 0"),
        ]
        for name, ddl in extras:
            if name not in cols:
                c.execute(f"ALTER TABLE appointments ADD COLUMN {name} {ddl}")

    # ---- Seed default settings
    defaults = {
        "slot_interval_min":       "15",   # 10 / 15 / 20
        "reappt_grace_min":        "15",   # auto-cancel window for reappointments
        "no_response_min":         "30",   # cancel if no click within X mins
        "working_start":           "09:00",
        "working_end":             "21:00",
    }
    for k, v in defaults.items():
        if not query("SELECT 1 FROM app_settings WHERE key=?", (k,), one=True):
            execute("INSERT INTO app_settings(key,value) VALUES(?,?)", (k, v))


# =========================================================
# Settings helpers
# =========================================================
def get_setting(key, default=None):
    row = query("SELECT value FROM app_settings WHERE key=?", (key,), one=True)
    return row["value"] if row else default


def set_setting(key, value):
    if query("SELECT 1 FROM app_settings WHERE key=?", (key,), one=True):
        execute("UPDATE app_settings SET value=? WHERE key=?", (str(value), key))
    else:
        execute("INSERT INTO app_settings(key,value) VALUES(?,?)", (key, str(value)))


# =========================================================
# Slot generation
# =========================================================
def _parse_hhmm(s, fallback):
    try:
        h, m = s.split(":")
        return dtime(int(h), int(m))
    except Exception:
        return fallback


def generate_slots_for(doctor_id, on_date):
    """Return list of {time:'HH:MM', taken:bool} covering the working day."""
    interval = int(get_setting("slot_interval_min", "15") or 15)
    start = _parse_hhmm(get_setting("working_start", "09:00"), dtime(9, 0))
    end   = _parse_hhmm(get_setting("working_end",   "21:00"), dtime(21, 0))

    # Collect already-booked slot times for that doctor on that date.
    booked = {
        (r["slot_time"] or r["predicted_time"])
        for r in query(
            """SELECT slot_time, predicted_time FROM appointments
               WHERE doctor_id=? AND appt_date=?
                 AND status NOT IN ('Cancelled','Missed','Completed')""",
            (doctor_id, on_date),
        )
        if (r["slot_time"] or r["predicted_time"])
    }

    # Slots are always generated against the *current system time*: any slot
    # that has already passed today is returned as unavailable (never bookable).
    lead = int(get_setting("min_lead_minutes", "10") or 10)
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    is_today = str(on_date) == today_str
    earliest = now + timedelta(minutes=lead)
    try:
        day = datetime.strptime(str(on_date), "%Y-%m-%d").date()
    except Exception:
        day = date.today()

    slots = []
    cur = datetime.combine(day, start)
    end_dt = datetime.combine(day, end)
    while cur < end_dt:
        hhmm = cur.strftime("%H:%M")
        taken = hhmm in booked
        past = bool(day < now.date() or (is_today and cur <= earliest))
        slots.append({"time": hhmm, "label": to_ampm(hhmm), "taken": taken,
                      "past": past, "available": (not taken) and (not past)})
        cur += timedelta(minutes=interval)
    return slots


# =========================================================
# Emails
# =========================================================
def _mail(subject, to, title, html_body):
    """Small internal wrapper around email_service.send_email + _wrap."""
    from email_service import send_email, _wrap
    send_email(to, subject, _wrap(title, html_body))


def mail_feedback_thanks(pt):
    _mail("Thank you for your feedback", pt["email"], "Feedback Received",
          f"<p>Hello <b>{pt['name']}</b>,</p>"
          "<p>Thank you for sharing your experience with MediQueue AI. "
          "Your feedback helps us improve continuously.</p>")


def mail_action_request_admin(action, pt, doc, appt, reason=""):
    """Notify Admin that a patient submitted an action request."""
    admin_email = Config.SMTP_USER  # admin uses hospital SMTP mailbox by default
    _mail(
        f"[Approval Needed] {action.title()} request - {pt['name']}",
        admin_email,
        f"Patient {action.title()} Request",
        f"<p>A patient has submitted an approval request:</p>"
        f"<ul>"
        f"<li>Patient: <b>{pt['name']}</b></li>"
        f"<li>Appointment ID: <b>{appt['id']}</b> (Queue {appt['queue_no']})</li>"
        f"<li>Doctor: <b>{doc['name']}</b></li>"
        f"<li>Department: <b>{doc['department']}</b></li>"
        f"<li>Requested Action: <b>{action}</b></li>"
        f"<li>Scheduled: <b>{appt['appt_date']} {to_ampm(appt['predicted_time'])}</b></li>"
        f"<li>Reason: {reason or '—'}</li>"
        f"</ul>"
        f"<p>Please review this request in the Admin Dashboard "
        f"under <b>Pending Patient Requests</b>.</p>"
    )


def mail_action_decision(pt, action, approved, note=""):
    _mail(
        f"Your {action} request was {'approved' if approved else 'rejected'}",
        pt["email"],
        "Request Update",
        f"<p>Hello <b>{pt['name']}</b>,</p>"
        f"<p>Your <b>{action}</b> request has been "
        f"<b>{'approved' if approved else 'rejected'}</b> by the administrator.</p>"
        f"{'<p>'+note+'</p>' if note else ''}"
    )


def mail_auto_cancel(pt, doc, reason):
    _mail("Appointment Auto-Cancelled - MediQueue AI", pt["email"],
          "Appointment Auto-Cancelled",
          f"<p>Hello <b>{pt['name']}</b>,</p>"
          f"<p>Your appointment with <b>{doc['name']}</b> "
          f"({doc['department']}) has been automatically cancelled.</p>"
          f"<p><b>Reason:</b> {reason}</p>"
          f"<p>You may book a new appointment anytime from your dashboard.</p>")


def mail_admin_auto_cancel(doc, pt, reason):
    _mail("[Auto-Cancel] Appointment automatically cancelled",
          Config.SMTP_USER, "Auto-Cancelled Appointment",
          f"<p>An appointment has been automatically cancelled by the system:</p>"
          f"<ul><li>Patient: <b>{pt['name']}</b></li>"
          f"<li>Doctor: <b>{doc['name']}</b></li>"
          f"<li>Reason: <b>{reason}</b></li></ul>")


# =========================================================
# Small helpers
# =========================================================
def _notify(role, uid, title, msg):
    execute("INSERT INTO notifications(role,user_id,title,message,created_at) VALUES(?,?,?,?,?)",
            (role, uid, title, msg, datetime.now().isoformat()))


def _log(role, uid, action, details=""):
    execute("INSERT INTO history(actor_role,actor_id,action,details,created_at) VALUES(?,?,?,?,?)",
            (role, uid, action, details, datetime.now().isoformat()))


def _require(role):
    if session.get("role") != role:
        return redirect(url_for("login"))
    return None


# =========================================================
# Route registration
# =========================================================
def register_enhancement_routes(app):

    # ------------------------------------------------------------------
    # 1. Slot API - used by smart booking form
    # ------------------------------------------------------------------
    @app.route("/api/slots")
    def api_slots():
        try:
            doctor_id = int(request.args.get("doctor_id"))
        except (TypeError, ValueError):
            return jsonify({"slots": [], "error": "bad doctor_id"}), 400
        on_date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
        slots = generate_slots_for(doctor_id, on_date)
        return jsonify({
            "date": on_date,
            "interval": int(get_setting("slot_interval_min", "15") or 15),
            "slots": slots,
            "available_count": sum(1 for s in slots if s["available"]),
        })

    # ------------------------------------------------------------------
    # 2. Patient - submit feedback
    # ------------------------------------------------------------------
    @app.route("/patient/feedback/<int:appt_id>", methods=["GET", "POST"])
    def patient_feedback(appt_id):
        r = _require("patient")
        if r: return r

        a = query("""SELECT a.*, d.name AS dname, d.department AS ddept
                     FROM appointments a JOIN doctors d ON d.id=a.doctor_id
                     WHERE a.id=?""", (appt_id,), one=True)
        if not a or a["patient_id"] != session["uid"]:
            abort(403)
        if a["status"] != "Completed":
            flash("You can only leave feedback after your consultation is completed.", "warning")
            return redirect(url_for("patient_dashboard"))

        existing = query("SELECT * FROM feedback WHERE appointment_id=?",
                         (appt_id,), one=True)

        if request.method == "POST":
            try:
                rating          = max(1, min(5, int(request.form.get("rating", 5))))
                doctor_rating   = max(1, min(5, int(request.form.get("doctor_rating", rating))))
                hospital_rating = max(1, min(5, int(request.form.get("hospital_rating", rating))))
            except ValueError:
                flash("Please provide valid ratings.", "danger")
                return redirect(url_for("patient_feedback", appt_id=appt_id))
            review = (request.form.get("review") or "").strip()[:1000]

            if existing:
                execute("""UPDATE feedback SET rating=?, doctor_rating=?, hospital_rating=?,
                                              review=?, created_at=? WHERE id=?""",
                        (rating, doctor_rating, hospital_rating, review,
                         datetime.now().isoformat(), existing["id"]))
            else:
                execute("""INSERT INTO feedback(appointment_id,patient_id,doctor_id,
                                                rating,doctor_rating,hospital_rating,
                                                review,created_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (appt_id, a["patient_id"], a["doctor_id"],
                         rating, doctor_rating, hospital_rating, review,
                         datetime.now().isoformat()))
            execute("UPDATE appointments SET feedback_submitted=1 WHERE id=?", (appt_id,))
            pt = query("SELECT * FROM patients WHERE id=?", (a["patient_id"],), one=True)
            try:    mail_feedback_thanks(pt)
            except Exception: pass
            _notify("doctor", a["doctor_id"], "New Review",
                    f"{pt['name']} rated their consultation {rating}★")
            _notify("admin", 0, "New Feedback",
                    f"{pt['name']} → Dr {a['dname']}: {rating}★")
            _log("patient", pt["id"], "Feedback Submitted", f"appt#{appt_id} rating={rating}")
            flash("Thank you! Your feedback has been submitted.", "success")
            return redirect(url_for("patient_feedback_list"))

        return render_template("patient_feedback.html", a=a, existing=existing)

    @app.route("/patient/feedback")
    def patient_feedback_list():
        r = _require("patient")
        if r: return r
        rows = query("""SELECT f.*, d.name AS dname, d.department AS ddept, a.queue_no
                        FROM feedback f
                        JOIN doctors d ON d.id=f.doctor_id
                        JOIN appointments a ON a.id=f.appointment_id
                        WHERE f.patient_id=? ORDER BY f.id DESC""", (session["uid"],))
        # pending feedback (completed appointments without feedback)
        pending = query("""SELECT a.id, a.queue_no, d.name AS dname, d.department AS ddept
                           FROM appointments a JOIN doctors d ON d.id=a.doctor_id
                           WHERE a.patient_id=? AND a.status='Completed'
                             AND COALESCE(a.feedback_submitted,0)=0
                           ORDER BY a.id DESC""", (session["uid"],))
        return render_template("patient_feedback_list.html", rows=rows, pending=pending)

    # ------------------------------------------------------------------
    # 3. Patient - Live timeline
    # ------------------------------------------------------------------
    @app.route("/patient/timeline/<int:appt_id>")
    def patient_timeline(appt_id):
        r = _require("patient")
        if r: return r
        a = query("""SELECT a.*, d.name AS dname, d.department AS ddept,
                            p.name AS pname
                     FROM appointments a JOIN doctors d ON d.id=a.doctor_id
                     JOIN patients p ON p.id=a.patient_id
                     WHERE a.id=?""", (appt_id,), one=True)
        if not a or a["patient_id"] != session["uid"]:
            abort(403)
        return render_template("patient_timeline.html", a=a)

    @app.route("/api/timeline/<int:appt_id>")
    def api_timeline(appt_id):
        if session.get("role") != "patient":
            return jsonify({"error": "auth"}), 403
        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a or a["patient_id"] != session["uid"]:
            return jsonify({"error": "forbidden"}), 403

        steps = ["Booked", "Confirmed", "Waiting", "Doctor Called",
                 "Consultation Started", "Completed"]
        status = a["status"] or "Waiting"
        # Map current status -> active step index
        if   status == "Cancelled":            active = -1
        elif status == "Completed":            active = 5
        elif status == "InProgress":           active = 4
        elif status == "Waiting for Approval": active = 3
        elif status == "Waiting":              active = 2
        elif status == "Emergency-PendingVerification": active = 1
        else:                                  active = 0
        return jsonify({
            "steps": steps,
            "active": active,
            "status": status,
            "cancelled": status == "Cancelled",
            "cancel_reason": a["cancel_reason"],
            "predicted_time": to_ampm(a["predicted_time"]),
        })

    # ------------------------------------------------------------------
    # 4. Email approval workflow - patient action → admin request
    # ------------------------------------------------------------------
    @app.route("/patient/request/<int:appt_id>/<action>")
    def patient_request(appt_id, action):
        """Replaces the immediate-effect /patient/response/<id>/<action>
        for waiting/reappoint/cancel by sending an approval request instead.
        `continue` still goes to the original route.
        """
        if session.get("role") != "patient":
            flash("Please log in to submit this request.", "warning")
            return redirect(url_for("login"))
        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a or a["patient_id"] != session["uid"]:
            abort(403)
        execute("UPDATE appointments SET patient_responded=1 WHERE id=?", (appt_id,))
        pt  = query("SELECT * FROM patients WHERE id=?", (a["patient_id"],), one=True)
        doc = query("SELECT * FROM doctors  WHERE id=?", (a["doctor_id"],),  one=True)
        reason = (request.args.get("reason") or "").strip()

        if action == "waiting":
            # reuse existing delay_requests infra
            dup = query("""SELECT 1 FROM delay_requests WHERE appointment_id=?
                           AND status IN('PendingAdmin','PendingDoctor')""",
                        (appt_id,), one=True)
            if dup:
                flash("Your waiting request is already pending admin approval.", "info")
            else:
                execute("""INSERT INTO delay_requests(patient_id,appointment_id,doctor_id,
                          request_type,requested_minutes,status,created_at)
                          VALUES(?,?,?,?,?,?,?)""",
                        (pt["id"], appt_id, doc["id"], "wait5", 5, "PendingAdmin",
                         datetime.now().isoformat()))
                execute("UPDATE appointments SET status='Waiting for Approval' WHERE id=?",
                        (appt_id,))
                _notify("admin", 0, "Waiting Request",
                        f"{pt['name']} (Q{a['queue_no']}) requested to wait.")
                try:    mail_action_request_admin("Waiting", pt, doc, a, reason)
                except Exception: pass
                flash("Your waiting request has been sent to the admin for approval.", "success")

        elif action == "reappoint":
            dup = query("""SELECT 1 FROM reappointment_requests
                           WHERE appointment_id=? AND status='Pending'""",
                        (appt_id,), one=True)
            if dup:
                flash("Your re-appointment request is already pending.", "info")
            else:
                execute("""INSERT INTO reappointment_requests
                    (patient_id,appointment_id,doctor_id,status,created_at)
                    VALUES(?,?,?,?,?)""",
                    (pt["id"], appt_id, doc["id"], "Pending",
                     datetime.now().isoformat()))
                _notify("admin", 0, "Re-Appointment Request",
                        f"{pt['name']} (Q{a['queue_no']}) requested re-appointment.")
                try:    mail_action_request_admin("Re-Appointment", pt, doc, a, reason)
                except Exception: pass
                flash("Your re-appointment request has been sent to the admin.", "success")

        elif action == "cancel":
            dup = query("SELECT 1 FROM cancel_requests WHERE appointment_id=? AND status='Pending'",
                        (appt_id,), one=True)
            if dup:
                flash("Your cancel request is already pending admin approval.", "info")
            else:
                execute("""INSERT INTO cancel_requests(patient_id,appointment_id,doctor_id,
                                                       reason,status,created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (pt["id"], appt_id, doc["id"], reason or "Patient requested cancellation",
                         "Pending", datetime.now().isoformat()))
                execute("UPDATE appointments SET status='Waiting for Approval' WHERE id=?",
                        (appt_id,))
                _notify("admin", 0, "Cancel Request",
                        f"{pt['name']} (Q{a['queue_no']}) requested cancellation.")
                try:    mail_action_request_admin("Cancel", pt, doc, a, reason)
                except Exception: pass
                flash("Your cancel request has been sent to the admin for approval.", "success")

        else:
            flash("Unknown action.", "warning")

        return redirect(url_for("patient_queue"))

    # ------------------------------------------------------------------
    # 5. Admin - cancel request approvals
    # ------------------------------------------------------------------
    @app.route("/admin/cancel-request/<int:rid>/<action>", methods=["GET", "POST"])
    def admin_cancel_request(rid, action):
        r = _require("admin")
        if r: return r
        rec = query("SELECT * FROM cancel_requests WHERE id=?", (rid,), one=True)
        if not rec: abort(404)
        pt  = query("SELECT * FROM patients WHERE id=?", (rec["patient_id"],), one=True)
        doc = query("SELECT * FROM doctors  WHERE id=?", (rec["doctor_id"],),  one=True)
        appt_id = rec["appointment_id"]

        if action == "approve":
            execute("""UPDATE appointments SET status='Cancelled',
                                              cancel_reason=? WHERE id=?""",
                    (rec["reason"] or "Cancelled by patient (approved)", appt_id))
            execute("UPDATE cancel_requests SET status='Approved', admin_action_time=? WHERE id=?",
                    (datetime.now().isoformat(), rid))
            from queue_manager import recalculate_queue
            recalculate_queue(doc["id"])
            try:    mail_action_decision(pt, "cancel", True)
            except Exception: pass
            _notify("patient", pt["id"], "Cancel Approved",
                    "Your appointment has been cancelled.")
            _notify("doctor", doc["id"], "Cancel Approved", pt["name"])
            flash("Cancellation approved.", "success")
        else:  # reject
            execute("UPDATE cancel_requests SET status='Rejected', admin_action_time=? WHERE id=?",
                    (datetime.now().isoformat(), rid))
            execute("UPDATE appointments SET status='Waiting' WHERE id=?", (appt_id,))
            try:    mail_action_decision(pt, "cancel", False,
                                         "Please keep your appointment as scheduled.")
            except Exception: pass
            _notify("patient", pt["id"], "Cancel Rejected",
                    "Your cancel request was rejected.")
            flash("Cancellation request rejected.", "info")
        return redirect(url_for("admin_pending_requests"))

    # ------------------------------------------------------------------
    # 6. Admin - Pending patient requests (unified panel)
    # ------------------------------------------------------------------
    @app.route("/admin/pending-requests")
    def admin_pending_requests():
        r = _require("admin")
        if r: return r
        wait5 = query("""SELECT r.*, p.name AS pname, a.queue_no, d.name AS dname
                         FROM delay_requests r
                         JOIN patients p ON p.id=r.patient_id
                         JOIN appointments a ON a.id=r.appointment_id
                         JOIN doctors d ON d.id=r.doctor_id
                         WHERE r.status IN('PendingAdmin','PendingDoctor')
                         ORDER BY r.id DESC""")
        reappt = query("""SELECT r.*, p.name AS pname, a.queue_no, a.reason,
                                 d.name AS dname, d.department AS ddept
                         FROM reappointment_requests r
                         JOIN patients p ON p.id=r.patient_id
                         JOIN appointments a ON a.id=r.appointment_id
                         JOIN doctors d ON d.id=r.doctor_id
                         WHERE r.status='Pending'
                         ORDER BY r.id DESC""")
        cancels = query("""SELECT r.*, p.name AS pname, a.queue_no,
                                  d.name AS dname, d.department AS ddept
                          FROM cancel_requests r
                          JOIN patients p ON p.id=r.patient_id
                          JOIN appointments a ON a.id=r.appointment_id
                          JOIN doctors d ON d.id=r.doctor_id
                          WHERE r.status='Pending'
                          ORDER BY r.id DESC""")
        doctors = query("SELECT id,name,department FROM doctors ORDER BY name")
        return render_template("admin_pending.html",
                               wait5=wait5, reappt=reappt, cancels=cancels,
                               doctors=doctors)

    # ------------------------------------------------------------------
    # 7. Admin - Feedback analytics
    # ------------------------------------------------------------------
    @app.route("/admin/feedback")
    def admin_feedback():
        r = _require("admin")
        if r: return r
        total = query("SELECT COUNT(*) c FROM feedback", one=True)["c"]
        avg = query("SELECT ROUND(AVG(rating),2) a FROM feedback", one=True)["a"] or 0
        avg_doc = query("SELECT ROUND(AVG(doctor_rating),2) a FROM feedback", one=True)["a"] or 0
        avg_hos = query("SELECT ROUND(AVG(hospital_rating),2) a FROM feedback", one=True)["a"] or 0
        by_doc = query("""SELECT d.id, d.name, d.department,
                                 COUNT(f.id) c,
                                 ROUND(AVG(f.rating),2) avg_r,
                                 ROUND(AVG(f.doctor_rating),2) avg_dr
                          FROM doctors d LEFT JOIN feedback f ON f.doctor_id=d.id
                          GROUP BY d.id
                          HAVING c > 0
                          ORDER BY avg_r DESC, c DESC""")
        recent = query("""SELECT f.*, p.name AS pname, d.name AS dname,
                                 d.department AS ddept
                          FROM feedback f
                          JOIN patients p ON p.id=f.patient_id
                          JOIN doctors  d ON d.id=f.doctor_id
                          ORDER BY f.id DESC LIMIT 25""")
        dist = {i: 0 for i in range(1, 6)}
        for r_ in query("SELECT rating, COUNT(*) c FROM feedback GROUP BY rating"):
            dist[r_["rating"]] = r_["c"]
        return render_template("admin_feedback.html",
                               total=total, avg=avg, avg_doc=avg_doc, avg_hos=avg_hos,
                               by_doc=by_doc, recent=recent, dist=dist)

    # ------------------------------------------------------------------
    # 8. Doctor - own reviews
    # ------------------------------------------------------------------
    @app.route("/doctor/feedback")
    def doctor_feedback():
        r = _require("doctor")
        if r: return r
        did = session["uid"]
        total = query("SELECT COUNT(*) c FROM feedback WHERE doctor_id=?", (did,), one=True)["c"]
        avg   = query("SELECT ROUND(AVG(rating),2) a FROM feedback WHERE doctor_id=?", (did,), one=True)["a"] or 0
        avg_d = query("SELECT ROUND(AVG(doctor_rating),2) a FROM feedback WHERE doctor_id=?", (did,), one=True)["a"] or 0
        rows  = query("""SELECT f.*, p.name AS pname, a.queue_no
                         FROM feedback f JOIN patients p ON p.id=f.patient_id
                         JOIN appointments a ON a.id=f.appointment_id
                         WHERE f.doctor_id=? ORDER BY f.id DESC""", (did,))
        return render_template("doctor_feedback.html",
                               total=total, avg=avg, avg_d=avg_d, rows=rows)

    # ------------------------------------------------------------------
    # 9. Admin - Settings
    # ------------------------------------------------------------------
    @app.route("/admin/settings", methods=["GET", "POST"])
    def admin_settings():
        r = _require("admin")
        if r: return r
        if request.method == "POST":
            for key in ("slot_interval_min", "reappt_grace_min",
                        "no_response_min", "working_start", "working_end"):
                v = request.form.get(key)
                if v is not None:
                    set_setting(key, v.strip())
            flash("Settings updated.", "success")
            return redirect(url_for("admin_settings"))
        vals = {k: get_setting(k, "") for k in (
            "slot_interval_min", "reappt_grace_min", "no_response_min",
            "working_start", "working_end"
        )}
        return render_template("admin_settings.html", vals=vals)

    # ------------------------------------------------------------------
    # 10. Enhanced admin dashboard analytics (widgets JSON)
    # ------------------------------------------------------------------
    @app.route("/api/admin/analytics")
    def api_admin_analytics():
        if session.get("role") != "admin":
            return jsonify({"error": "auth"}), 403
        today = datetime.now().strftime("%Y-%m-%d")
        pending_req = (
            query("SELECT COUNT(*) c FROM delay_requests WHERE status IN('PendingAdmin','PendingDoctor')", one=True)["c"] +
            query("SELECT COUNT(*) c FROM reappointment_requests WHERE status='Pending'", one=True)["c"] +
            query("SELECT COUNT(*) c FROM cancel_requests WHERE status='Pending'", one=True)["c"]
        )
        auto_cancel = query("SELECT COUNT(*) c FROM appointments WHERE auto_cancelled=1", one=True)["c"]
        today_rev = query("""SELECT COALESCE(SUM(d.fee),0) s FROM appointments a
                             JOIN doctors d ON d.id=a.doctor_id
                             WHERE a.status='Completed' AND a.appt_date=?""",
                          (today,), one=True)["s"] or 0
        avg_wait = query("""SELECT ROUND(AVG(predicted_wait_time),1) a
                            FROM appointments WHERE appt_date=?""",
                         (today,), one=True)["a"] or 0
        fb_total = query("SELECT COUNT(*) c FROM feedback", one=True)["c"]
        fb_avg = query("SELECT ROUND(AVG(rating),2) a FROM feedback", one=True)["a"] or 0
        # monthly
        monthly = query("""SELECT substr(appt_date,1,7) m, COUNT(*) c
                           FROM appointments
                           WHERE appt_date IS NOT NULL AND appt_date != ''
                           GROUP BY m ORDER BY m DESC LIMIT 6""")
        return jsonify({
            "pending_requests": pending_req,
            "auto_cancel_count": auto_cancel,
            "today_revenue": today_rev,
            "avg_wait_time": avg_wait,
            "feedback_total": fb_total,
            "feedback_avg": fb_avg,
            "monthly": [{"month": r["m"], "count": r["c"]} for r in list(reversed(monthly))],
        })


# =========================================================
# Background auto-cancel jobs (called from scheduler.py)
# =========================================================
def auto_cancel_reappointments(app):
    """Cancel a re-appointment if the patient has not arrived within
    the configured grace period after the scheduled time."""
    from email_service import mail_cancel
    with app.app_context():
        grace = int(get_setting("reappt_grace_min", "15") or 15)
        now = datetime.now()
        rows = query("""SELECT a.*, p.email, p.name AS pname, d.name AS dname,
                                d.department AS ddept
                       FROM appointments a
                       JOIN patients p ON p.id=a.patient_id
                       JOIN doctors  d ON d.id=a.doctor_id
                       WHERE a.is_reappointment=1
                         AND a.status IN('Waiting','Waiting for Approval')
                         AND a.appt_date=?""",
                    (now.strftime("%Y-%m-%d"),))
        for a in rows:
            try:
                hh, mm = (a["predicted_time"] or "00:00").split(":")
                sched = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                if (now - sched).total_seconds() >= grace * 60:
                    reason = "Patient did not arrive for Re-Appointment."
                    execute("""UPDATE appointments
                               SET status='Cancelled', auto_cancelled=1,
                                   cancel_reason=? WHERE id=?""",
                            (reason, a["id"]))
                    _notify("patient", a["patient_id"], "Auto-Cancelled", reason)
                    _notify("doctor",  a["doctor_id"],  "Auto-Cancelled",
                            f"{a['pname']} (re-appt) — {reason}")
                    _notify("admin", 0, "Auto-Cancelled",
                            f"{a['pname']} - {reason}")
                    pt  = {"name": a["pname"], "email": a["email"]}
                    doc = {"name": a["dname"], "department": a["ddept"]}
                    try:    mail_auto_cancel(pt, doc, reason)
                    except Exception: pass
                    try:    mail_admin_auto_cancel(doc, pt, reason)
                    except Exception: pass
            except Exception:
                continue


def auto_cancel_no_response(app):
    """Cancel appointments where a reminder was sent and the patient
    has not clicked any button within the configured window."""
    with app.app_context():
        wait = int(get_setting("no_response_min", "30") or 30)
        now = datetime.now()
        rows = query("""SELECT a.*, p.email, p.name AS pname, d.name AS dname,
                                d.department AS ddept
                       FROM appointments a
                       JOIN patients p ON p.id=a.patient_id
                       JOIN doctors  d ON d.id=a.doctor_id
                       WHERE a.status='Waiting'
                         AND COALESCE(a.reminder_sent,0)=1
                         AND COALESCE(a.patient_responded,0)=0
                         AND a.appt_date=?""",
                    (now.strftime("%Y-%m-%d"),))
        for a in rows:
            try:
                hh, mm = (a["predicted_time"] or "00:00").split(":")
                sched = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                # Cancel if we're already past the appointment time by `wait` minutes
                # AND the patient never confirmed.
                if (now - sched).total_seconds() >= wait * 60:
                    reason = "Appointment cancelled due to no response from patient."
                    execute("""UPDATE appointments
                               SET status='Cancelled', auto_cancelled=1,
                                   cancel_reason=? WHERE id=?""",
                            (reason, a["id"]))
                    _notify("patient", a["patient_id"], "Auto-Cancelled", reason)
                    _notify("doctor",  a["doctor_id"],  "Auto-Cancelled",
                            f"{a['pname']} - {reason}")
                    _notify("admin", 0, "Auto-Cancelled", f"{a['pname']} - {reason}")
                    pt  = {"name": a["pname"], "email": a["email"]}
                    doc = {"name": a["dname"], "department": a["ddept"]}
                    try:    mail_auto_cancel(pt, doc, reason)
                    except Exception: pass
                    try:    mail_admin_auto_cancel(doc, pt, reason)
                    except Exception: pass
            except Exception:
                continue
