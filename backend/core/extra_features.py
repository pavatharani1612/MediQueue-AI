"""
MediQueue AI - Enhancement module.

Adds:
  * Waiting 5 Minutes request flow (patient -> admin -> doctor)
  * Reappointment request flow (patient -> admin)
  * Automatic queue recalculation after approvals
  * Emergency priority auto-shift confirmation
  * Additional email notifications with clickable buttons

All new tables (delay_requests, reappointment_requests) are created on
init_extra_tables() call; existing schema is not modified.
"""
import hmac, hashlib
from datetime import datetime
from time_utils import to_ampm, to_ampm_datetime
from flask import (request, redirect, url_for, session, flash, abort,
                   render_template_string, Response)
from config import Config
from models import query, execute
from queue_manager import apply_delay, recalculate_queue, add_appointment
from email_service import (send_email, _wrap, mail_appointment, mail_cancel)


# ------------------------------------------------------------------ DB
def init_extra_tables():
    execute("""CREATE TABLE IF NOT EXISTS delay_requests(
        id INTEGER PRIMARY KEY,
        patient_id INTEGER,
        appointment_id INTEGER,
        doctor_id INTEGER,
        request_type TEXT DEFAULT 'wait5',
        requested_minutes INTEGER DEFAULT 5,
        status TEXT DEFAULT 'PendingAdmin',
        admin_action_time TEXT,
        doctor_action_time TEXT,
        created_at TEXT
    )""")
    execute("""CREATE TABLE IF NOT EXISTS reappointment_requests(
        id INTEGER PRIMARY KEY,
        patient_id INTEGER,
        appointment_id INTEGER,
        doctor_id INTEGER,
        status TEXT DEFAULT 'Pending',
        new_date TEXT,
        new_time TEXT,
        new_doctor_id INTEGER,
        admin_action_time TEXT,
        created_at TEXT
    )""")


# ------------------------------------------------------------------ helpers
def _notify(role, uid, title, msg):
    execute("""INSERT INTO notifications(role,user_id,title,message,created_at)
               VALUES(?,?,?,?,?)""",
            (role, uid, title, msg, datetime.now().isoformat()))


def _log(role, uid, action, details=""):
    execute("""INSERT INTO history(actor_role,actor_id,action,details,created_at)
               VALUES(?,?,?,?,?)""",
            (role, uid, action, details, datetime.now().isoformat()))


# ------------------------------------------------------------------ emails
def mail_wait5_submitted(pt):
    send_email(pt["email"], "Waiting 5 Minutes Request Submitted",
        _wrap("Request Submitted",
              f"<p>Hello <b>{pt['name']}</b>,</p>"
              "<p>Your request for an additional 5 minutes has been "
              "submitted for approval. You will be notified once the admin "
              "and doctor review it.</p>"))


def mail_wait5_approved(pt, new_time):
    send_email(pt["email"], "Waiting 5 Minutes Approved",
        _wrap("Request Approved",
              f"<p>Hello <b>{pt['name']}</b>,</p>"
              f"<p>Your 5-minute wait request has been approved. "
              f"Your updated appointment time is <b>{new_time}</b>.</p>"))


def mail_wait5_rejected(pt, by="doctor"):
    send_email(pt["email"], "Waiting 5 Minutes Declined",
        _wrap("Request Declined",
              f"<p>Hello <b>{pt['name']}</b>,</p>"
              f"<p>Your Waiting 5 Minutes request was declined by the {by}. "
              "The queue remains unchanged. Please arrive on time.</p>"))


def mail_reappoint_submitted(pt):
    send_email(pt["email"], "Reappointment Request Submitted",
        _wrap("Reappointment Requested",
              f"<p>Hello <b>{pt['name']}</b>,</p>"
              "<p>Your reappointment request has been submitted. "
              "The admin will confirm a new date and time shortly.</p>"))


def mail_reappoint_approved(pt, doc, new_date, new_time):
    send_email(pt["email"], "Updated Appointment - MediQueue AI",
        _wrap("Reappointment Confirmed",
              f"<p>Hello <b>{pt['name']}</b>,</p>"
              f"<p>Your reappointment with <b>{doc['name']}</b> "
              f"({doc['department']}) has been confirmed.</p>"
              f"<ul><li>New Date: <b>{new_date}</b></li>"
              f"<li>New Time: <b>{new_time}</b></li></ul>"
              "<p>Your previous appointment has been cancelled.</p>"))


def mail_emergency_shift(pt, new_time):
    send_email(pt["email"], "Queue Updated - Emergency Priority",
        _wrap("Queue Shifted",
              f"<p>Hello <b>{pt['name']}</b>,</p>"
              "<p>An emergency patient has been prioritised. "
              f"Your updated predicted time is <b>{new_time}</b>. "
              "Thank you for your patience.</p>"))


# ------------------------------------------------------------------ routes
def register_extra_routes(app):

    # -------- PATIENT: Silent Waiting 5 Minutes (from email button) --------
    # No page, no login, no redirect. Returns HTTP 204 so the click completes
    # silently inside the email client. Signed with an HMAC of the appt id.
    @app.route("/api/wait5/<int:appt_id>", methods=["GET", "POST"])
    def api_wait5(appt_id):
        # Minimal success page shown after clicking the email button.
        # No confirmation UI, no login, no redirect - the request is
        # processed immediately in the background and the patient only
        # sees a short acknowledgement message.
        def _msg(text, ok=True):
            color = "#16a34a" if ok else "#6b7280"
            html = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>MediQueue AI</title></head>"
                "<body style='font-family:Segoe UI,Arial,sans-serif;"
                "background:#f8fafc;margin:0;padding:40px 16px;text-align:center'>"
                "<div style='max-width:440px;margin:0 auto;background:#fff;"
                "border-radius:14px;padding:32px 24px;"
                "box-shadow:0 6px 20px rgba(0,0,0,.06)'>"
                f"<div style='font-size:44px'>{'✅' if ok else 'ℹ️'}</div>"
                f"<h2 style='color:{color};margin:14px 0 8px'>MediQueue AI</h2>"
                f"<p style='color:#334155;font-size:16px;margin:0'>{text}</p>"
                "</div></body></html>"
            )
            return Response(html, mimetype="text/html")

        token = request.args.get("t", "")
        expected = hmac.new(
            Config.SECRET_KEY.encode(),
            f"wait5:{appt_id}".encode(),
            hashlib.sha256,
        ).hexdigest()[:24]
        if not token or not hmac.compare_digest(token, expected):
            return Response(status=204)  # tampered link -> silent no-op

        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a:
            return Response(status=204)
        # Duplicate guard: ignore later clicks but still show a friendly note.
        dup = query("""SELECT 1 FROM delay_requests
                       WHERE appointment_id=?
                         AND status IN('PendingAdmin','PendingDoctor','Approved')""",
                    (appt_id,), one=True)
        if dup:
            return _msg("Your delay request has already been submitted.", ok=False)

        pt = query("SELECT * FROM patients WHERE id=?", (a["patient_id"],), one=True)
        doc = query("SELECT * FROM doctors WHERE id=?", (a["doctor_id"],), one=True)
        if not pt or not doc:
            return Response(status=204)

        req_time = datetime.now().isoformat()
        execute("""INSERT INTO delay_requests
            (patient_id,appointment_id,doctor_id,request_type,requested_minutes,
             status,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (pt["id"], appt_id, a["doctor_id"], "wait5", 5, "PendingAdmin",
             req_time))
        # Mark the appointment as awaiting admin approval.
        execute("UPDATE appointments SET status='Waiting for Approval' WHERE id=?",
                (appt_id,))

        # Build detailed notification message (Patient Name, Appointment ID,
        # Queue Number, Requested Extra Time, Request Time).
        try:
            pretty_time = to_ampm_datetime(datetime.fromisoformat(req_time))
        except Exception:
            pretty_time = req_time
        detail = (
            f"Patient Name: {pt['name']} | "
            f"Appointment ID: {appt_id} | "
            f"Queue Number: {a['queue_no']} | "
            f"Requested Extra Time: 5 Minutes | "
            f"Request Time: {pretty_time}"
        )
        # Admin bell notification
        _notify("admin", 0, "Waiting 5 Minutes Request", detail)
        # Assigned doctor bell notification
        _notify("doctor", doc["id"], "Waiting 5 Minutes Request", detail)
        _log("patient", pt["id"], "Wait5 Requested (email)", f"appt#{appt_id}")
        mail_wait5_submitted(pt)
        return _msg("Your request has been received successfully. "
                    "The doctor/admin will review it shortly.")


    # -------- PATIENT: Waiting 5 Minutes --------
    @app.route("/patient/wait5/<int:appt_id>")
    def patient_wait5(appt_id):
        if session.get("role") != "patient":
            flash("Please log in as patient to submit this request.", "warning")
            return redirect(url_for("login"))
        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a or a["patient_id"] != session["uid"]:
            abort(403)
        # Prevent duplicate pending
        dup = query("""SELECT 1 FROM delay_requests
                       WHERE appointment_id=? AND status IN('PendingAdmin','PendingDoctor')""",
                    (appt_id,), one=True)
        if dup:
            flash("Your Waiting 5 Minutes request is already pending.", "info")
            return redirect(url_for("patient_queue"))
        pt = query("SELECT * FROM patients WHERE id=?", (a["patient_id"],), one=True)
        execute("""INSERT INTO delay_requests
            (patient_id,appointment_id,doctor_id,request_type,requested_minutes,
             status,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (pt["id"], appt_id, a["doctor_id"], "wait5", 5, "PendingAdmin",
             datetime.now().isoformat()))
        _notify("admin", 0, "Waiting 5 Minutes Request",
                f"{pt['name']} (Q{a['queue_no']}) requested +5 min.")
        _log("patient", pt["id"], "Wait5 Requested", f"appt#{appt_id}")
        mail_wait5_submitted(pt)
        flash("Your Waiting 5 Minutes request has been submitted.", "success")
        return redirect(url_for("patient_queue"))

    # -------- PATIENT: Reappointment --------
    @app.route("/patient/reappoint-request/<int:appt_id>")
    def patient_reappoint_request(appt_id):
        if session.get("role") != "patient":
            flash("Please log in as patient to request a reappointment.", "warning")
            return redirect(url_for("login"))
        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a or a["patient_id"] != session["uid"]:
            abort(403)
        dup = query("""SELECT 1 FROM reappointment_requests
                       WHERE appointment_id=? AND status='Pending'""",
                    (appt_id,), one=True)
        if dup:
            flash("Your reappointment request is already pending.", "info")
            return redirect(url_for("patient_queue"))
        pt = query("SELECT * FROM patients WHERE id=?", (a["patient_id"],), one=True)
        execute("""INSERT INTO reappointment_requests
            (patient_id,appointment_id,doctor_id,status,created_at)
            VALUES(?,?,?,?,?)""",
            (pt["id"], appt_id, a["doctor_id"], "Pending",
             datetime.now().isoformat()))
        _notify("admin", 0, "Reappointment Request",
                f"{pt['name']} (Q{a['queue_no']}) requested reappointment.")
        _log("patient", pt["id"], "Reappointment Requested", f"appt#{appt_id}")
        mail_reappoint_submitted(pt)
        flash("Your reappointment request has been submitted for admin review.",
              "success")
        return redirect(url_for("patient_queue"))

    # -------- ADMIN: Delay requests list & actions --------
    @app.route("/admin/requests")
    def admin_requests():
        if session.get("role") != "admin":
            return redirect(url_for("login"))
        wait5 = query("""SELECT r.*, p.name AS pname, a.queue_no, d.name AS dname
                         FROM delay_requests r
                         JOIN patients p ON p.id=r.patient_id
                         JOIN appointments a ON a.id=r.appointment_id
                         JOIN doctors d ON d.id=r.doctor_id
                         ORDER BY r.id DESC""")
        reappt = query("""SELECT r.*, p.name AS pname, p.email AS pemail,
                                 a.queue_no, a.reason, d.name AS dname,
                                 d.department AS ddept
                         FROM reappointment_requests r
                         JOIN patients p ON p.id=r.patient_id
                         JOIN appointments a ON a.id=r.appointment_id
                         JOIN doctors d ON d.id=r.doctor_id
                         ORDER BY r.id DESC""")
        doctors = query("SELECT id,name,department FROM doctors ORDER BY name")
        return render_template_string(_ADMIN_REQUESTS_TPL,
                                      wait5=wait5, reappt=reappt, doctors=doctors)

    @app.route("/admin/wait5/<int:rid>/<action>", methods=["GET", "POST"])
    def admin_wait5_action(rid, action):
        if session.get("role") != "admin":
            return redirect(url_for("login"))
        r = query("SELECT * FROM delay_requests WHERE id=?", (rid,), one=True)
        if not r:
            abort(404)
        pt = query("SELECT * FROM patients WHERE id=?", (r["patient_id"],), one=True)
        doc = query("SELECT * FROM doctors WHERE id=?", (r["doctor_id"],), one=True)
        a = query("SELECT * FROM appointments WHERE id=?", (r["appointment_id"],), one=True)
        if action == "approve":
            # Admin approval is final: extend doctor's consult time by 5 min,
            # recalculate every subsequent patient's predicted time, notify
            # the assigned doctor, and email the patient.
            apply_delay(doc["id"], r["requested_minutes"],
                        reason=f"Patient wait+{r['requested_minutes']} approved by admin",
                        delayed_by=f"admin:{session.get('uid', 0)}")
            execute("""UPDATE delay_requests
                       SET status='Approved',
                           admin_action_time=?,
                           doctor_action_time=?
                       WHERE id=?""",
                    (datetime.now().isoformat(),
                     datetime.now().isoformat(), rid))
            execute("UPDATE appointments SET status='Waiting' WHERE id=?",
                    (r["appointment_id"],))
            new_a = query("SELECT * FROM appointments WHERE id=?",
                          (r["appointment_id"],), one=True)
            new_time = to_ampm(new_a["predicted_time"] if new_a else a["predicted_time"])
            _notify("doctor", doc["id"], "Waiting 5 Minutes Approved",
                    f"Admin approved +5 min for {pt['name']}. "
                    f"Your consultation window has been extended.")
            _notify("patient", pt["id"], "Waiting 5 Minutes Approved",
                    f"Your new predicted time: {new_time}")
            # Notify every shifted patient
            waiting = query("""SELECT p.name AS pname, a.predicted_time, a.patient_id
                               FROM appointments a JOIN patients p ON p.id=a.patient_id
                               WHERE a.doctor_id=? AND a.status='Waiting' AND a.id!=?""",
                            (doc["id"], r["appointment_id"]))
            for w in waiting:
                _notify("patient", w["patient_id"], "Queue Time Updated",
                        f"New predicted time: {to_ampm(w['predicted_time'])}")
            mail_wait5_approved(pt, new_time)
            flash("Wait+5 approved. Doctor notified and queue recalculated.", "success")
        elif action == "reject":
            execute("""UPDATE delay_requests SET status='RejectedAdmin',
                       admin_action_time=? WHERE id=?""",
                    (datetime.now().isoformat(), rid))
            execute("UPDATE appointments SET status='Waiting' WHERE id=?",
                    (r["appointment_id"],))
            _notify("patient", pt["id"], "Waiting 5 Minutes Rejected",
                    "Admin declined your +5 minute request.")
            mail_wait5_rejected(pt, by="admin")
            flash("Request rejected.", "info")
        return redirect(url_for("admin_requests"))

    # -------- ADMIN: Reappointment approval --------
    @app.route("/admin/reappointment/<int:rid>/approve", methods=["POST"])
    def admin_reappt_approve(rid):
        if session.get("role") != "admin":
            return redirect(url_for("login"))
        r = query("SELECT * FROM reappointment_requests WHERE id=?", (rid,), one=True)
        if not r:
            abort(404)
        new_date = request.form.get("new_date") or datetime.now().strftime("%Y-%m-%d")
        new_time = request.form.get("new_time") or "10:00"
        new_doc_id = int(request.form.get("new_doctor_id") or r["doctor_id"])
        a = query("SELECT * FROM appointments WHERE id=?", (r["appointment_id"],), one=True)
        pt = query("SELECT * FROM patients WHERE id=?", (r["patient_id"],), one=True)
        doc = query("SELECT * FROM doctors WHERE id=?", (new_doc_id,), one=True)
        # Cancel old
        execute("UPDATE appointments SET status='Cancelled' WHERE id=?",
                (r["appointment_id"],))
        # Create new
        new_id, q, pred_dt = add_appointment(
            pt["id"], new_doc_id, a["reason"], a["priority"] or "Normal")
        execute("UPDATE appointments SET appt_date=?, predicted_time=?, slot_time=?, is_reappointment=1 WHERE id=?",
                (new_date, new_time, new_time, new_id))
        execute("""UPDATE reappointment_requests
                   SET status='Approved', new_date=?, new_time=?,
                       new_doctor_id=?, admin_action_time=? WHERE id=?""",
                (new_date, new_time, new_doc_id, datetime.now().isoformat(), rid))
        mail_reappoint_approved(pt, doc, new_date, new_time)
        _notify("patient", pt["id"], "Reappointment Confirmed",
                f"New date {new_date} {new_time} with {doc['name']}")
        _notify("doctor", new_doc_id, "New Reappointment", pt["name"])
        _log("admin", session.get("uid", 0), "Reappointment Approved",
             f"req#{rid} -> appt#{new_id}")
        flash("Reappointment confirmed and patient notified.", "success")
        return redirect(url_for("admin_requests"))

    @app.route("/admin/reappointment/<int:rid>/reject")
    def admin_reappt_reject(rid):
        if session.get("role") != "admin":
            return redirect(url_for("login"))
        r = query("SELECT * FROM reappointment_requests WHERE id=?", (rid,), one=True)
        if not r:
            abort(404)
        execute("""UPDATE reappointment_requests SET status='Rejected',
                   admin_action_time=? WHERE id=?""",
                (datetime.now().isoformat(), rid))
        pt = query("SELECT * FROM patients WHERE id=?", (r["patient_id"],), one=True)
        _notify("patient", pt["id"], "Reappointment Rejected",
                "Admin rejected your reappointment request.")
        flash("Reappointment request rejected.", "info")
        return redirect(url_for("admin_requests"))

    # -------- DOCTOR: Wait5 approve/reject --------
    @app.route("/doctor/wait5/<int:rid>/<action>", methods=["GET", "POST"])
    def doctor_wait5_action(rid, action):
        if session.get("role") != "doctor":
            return redirect(url_for("login"))
        r = query("SELECT * FROM delay_requests WHERE id=?", (rid,), one=True)
        if not r or r["doctor_id"] != session["uid"]:
            abort(403)
        pt = query("SELECT * FROM patients WHERE id=?", (r["patient_id"],), one=True)
        doc = query("SELECT * FROM doctors WHERE id=?", (r["doctor_id"],), one=True)
        a = query("SELECT * FROM appointments WHERE id=?", (r["appointment_id"],), one=True)
        if action == "approve":
            # Apply +5 min delay to entire remaining queue for this doctor.
            apply_delay(doc["id"], r["requested_minutes"],
                        reason=f"Patient wait+{r['requested_minutes']} approved",
                        delayed_by=f"doctor:{doc['id']}")
            execute("""UPDATE delay_requests SET status='Approved',
                       doctor_action_time=? WHERE id=?""",
                    (datetime.now().isoformat(), rid))
            # Fetch updated time
            new_a = query("SELECT * FROM appointments WHERE id=?",
                          (r["appointment_id"],), one=True)
            new_time = to_ampm(new_a["predicted_time"] if new_a else a["predicted_time"])
            mail_wait5_approved(pt, new_time)
            _notify("patient", pt["id"], "Waiting 5 Minutes Approved",
                    f"Your new time: {new_time}")
            # Notify all shifted patients
            waiting = query("""SELECT p.email, p.name AS pname, a.predicted_time,
                                      a.patient_id
                               FROM appointments a JOIN patients p ON p.id=a.patient_id
                               WHERE a.doctor_id=? AND a.status='Waiting' AND a.id!=?""",
                            (doc["id"], r["appointment_id"]))
            for w in waiting:
                _notify("patient", w["patient_id"], "Queue Time Updated",
                        f"New predicted time: {to_ampm(w['predicted_time'])}")
            flash("Wait+5 approved. Queue recalculated.", "success")
        elif action == "reject":
            execute("""UPDATE delay_requests SET status='RejectedDoctor',
                       doctor_action_time=? WHERE id=?""",
                    (datetime.now().isoformat(), rid))
            mail_wait5_rejected(pt, by="doctor")
            _notify("patient", pt["id"], "Waiting 5 Minutes Rejected",
                    "Doctor declined your +5 minute request.")
            flash("Request rejected.", "info")
        return redirect(url_for("doctor_requests"))

    @app.route("/doctor/requests")
    def doctor_requests():
        if session.get("role") != "doctor":
            return redirect(url_for("login"))
        rows = query("""SELECT r.*, p.name AS pname, a.queue_no
                        FROM delay_requests r
                        JOIN patients p ON p.id=r.patient_id
                        JOIN appointments a ON a.id=r.appointment_id
                        WHERE r.doctor_id=?
                        ORDER BY r.id DESC""", (session["uid"],))
        return render_template_string(_DOCTOR_REQUESTS_TPL, rows=rows)


# ------------------------------------------------------------------ inline tpl
_ADMIN_REQUESTS_TPL = r"""
{% extends "base.html" %}{% block content %}
<div class="container">
  <h1 class="section-title">Patient Requests</h1>

  <h2 style="margin-top:24px">⏱️ Waiting 5 Minutes</h2>
  <div class="card" style="overflow-x:auto">
  <table class="table">
    <thead><tr><th>#</th><th>Patient</th><th>Queue</th><th>Doctor</th>
    <th>Minutes</th><th>Status</th><th>Actions</th></tr></thead>
    <tbody>
    {% for r in wait5 %}
      <tr>
        <td>{{r.id}}</td><td>{{r.pname}}</td><td>{{r.queue_no}}</td>
        <td>{{r.dname}}</td><td>{{r.requested_minutes}}</td>
        <td><span class="pill">{{r.status}}</span></td>
        <td>
          {% if r.status=='PendingAdmin' %}
          <a class="btn sm" href="/admin/wait5/{{r.id}}/approve">Approve</a>
          <a class="btn danger sm" href="/admin/wait5/{{r.id}}/reject">Reject</a>
          {% else %}—{% endif %}
        </td>
      </tr>
    {% else %}<tr><td colspan="7" style="color:var(--muted)">No requests.</td></tr>
    {% endfor %}
    </tbody>
  </table></div>

  <h2 style="margin-top:32px">🔁 Reappointment Requests</h2>
  <div class="grid c2">
  {% for r in reappt %}
    <div class="card">
      <h3 style="margin:0">{{r.pname}} — Q{{r.queue_no}}</h3>
      <p style="color:var(--muted)">Current: {{r.dname}} · {{r.ddept}} · {{r.reason}}</p>
      <p>Status: <span class="pill">{{r.status}}</span></p>
      {% if r.status=='Pending' %}
      <form method="POST" action="/admin/reappointment/{{r.id}}/approve"
            style="display:flex;flex-direction:column;gap:8px">
        <label>New Date <input type="date" name="new_date" required></label>
        <label>New Time <input type="time" name="new_time" required></label>
        <label>Doctor
          <select name="new_doctor_id" required>
            {% for d in doctors %}
              <option value="{{d.id}}" {% if d.id==r.doctor_id %}selected{% endif %}>
                {{d.name}} ({{d.department}})
              </option>
            {% endfor %}
          </select>
        </label>
        <div style="display:flex;gap:8px">
          <button class="btn sm">Approve & Confirm</button>
          <a class="btn danger sm" href="/admin/reappointment/{{r.id}}/reject">Reject</a>
        </div>
      </form>
      {% else %}
      <p>New: {{r.new_date}} {{r.new_time}}</p>
      {% endif %}
    </div>
  {% else %}<p style="color:var(--muted)">No reappointment requests.</p>
  {% endfor %}
  </div>
</div>
{% endblock %}
"""

_DOCTOR_REQUESTS_TPL = r"""
{% extends "base.html" %}{% block content %}
<div class="container">
  <h1 class="section-title">Patient Requests</h1>
  <div class="card" style="overflow-x:auto">
  <table class="table">
    <thead><tr><th>#</th><th>Patient</th><th>Queue</th><th>Minutes</th>
    <th>Status</th><th>Actions</th></tr></thead>
    <tbody>
    {% for r in rows %}
      <tr>
        <td>{{r.id}}</td><td>{{r.pname}}</td><td>{{r.queue_no}}</td>
        <td>{{r.requested_minutes}}</td>
        <td><span class="pill">{{r.status}}</span></td>
        <td>
          {% if r.status=='PendingDoctor' %}
          <a class="btn sm" href="/doctor/wait5/{{r.id}}/approve">Approve Delay</a>
          <a class="btn danger sm" href="/doctor/wait5/{{r.id}}/reject">Reject Delay</a>
          {% else %}—{% endif %}
        </td>
      </tr>
    {% else %}<tr><td colspan="6" style="color:var(--muted)">No requests.</td></tr>
    {% endfor %}
    </tbody>
  </table></div>
</div>
{% endblock %}
"""
