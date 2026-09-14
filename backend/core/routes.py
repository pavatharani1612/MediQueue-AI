import csv, io
from datetime import datetime
from time_utils import to_ampm, to_ampm_datetime
from functools import wraps
from flask import render_template, request, redirect, url_for, session, flash, jsonify, Response, abort
from werkzeug.security import check_password_hash, generate_password_hash
from models import query, execute
from config import Config
from queue_manager import (add_appointment, apply_delay, recalculate_queue,
                           live_queue, approve_emergency, reject_emergency)
from prediction import get_meta, predict_wait
from email_service import mail_registration, mail_appointment, mail_cancel, mail_delay, mail_complete


def role_required(role):
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            cur = session.get("role")
            if cur == role:
                return fn(*a, **kw)
            # Logged in with a DIFFERENT role -> bounce them to their own dashboard.
            if cur in ("admin", "doctor", "patient"):
                return redirect(url_for(f"{cur}_dashboard"))
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return inner
    return deco


def _log(role, uid, action, details=""):
    execute(
        "INSERT INTO history(actor_role,actor_id,action,details,created_at) VALUES(?,?,?,?,?)",
        (role, uid, action, details, datetime.now().isoformat())
    )


def _notify(role, uid, title, msg):
    execute(
        "INSERT INTO notifications(role,user_id,title,message,created_at) VALUES(?,?,?,?,?)",
        (role, uid, title, msg, datetime.now().isoformat())
    )


def register_routes(app):

    @app.route("/")
    def index():
        stats = {
            "patients": query("SELECT COUNT(*) c FROM patients", one=True)["c"],
            "doctors": query("SELECT COUNT(*) c FROM doctors", one=True)["c"],
            "depts": query("SELECT COUNT(*) c FROM departments", one=True)["c"],
            "appts": query("SELECT COUNT(*) c FROM appointments", one=True)["c"],
        }
        return render_template("index.html", stats=stats, depts=query("SELECT * FROM departments"))

    @app.route("/about")
    def about():
        return render_template("about.html")

    @app.route("/departments")
    def departments():
        return render_template("departments.html", depts=query("SELECT * FROM departments"))

    @app.route("/doctors")
    def doctors():
        dept = request.args.get("dept", "")
        if dept:
            docs = query("SELECT * FROM doctors WHERE department=? ORDER BY name", (dept,))
        else:
            docs = query("SELECT * FROM doctors ORDER BY department,name")
        return render_template("doctors.html", docs=docs, dept=dept, depts=query("SELECT * FROM departments"))

    @app.route("/contact")
    def contact():
        return render_template("contact.html")

    @app.route("/faq")
    def faq():
        return render_template("faq.html")

    @app.route("/emergency")
    def emergency():
        return render_template("emergency.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            role = request.form["role"]
            u = request.form["username"].strip()
            p = request.form["password"]

            if role == "admin":
                a = query("SELECT * FROM admin WHERE username=?", (u,), one=True)
                if a and check_password_hash(a["password"], p):
                    session.update(role="admin", uid=a["id"], name="Administrator")
                    return redirect(url_for("admin_dashboard"))

            elif role == "doctor":
                d = query("SELECT * FROM doctors WHERE username=?", (u,), one=True)
                if d and check_password_hash(d["password"], p):
                    session.update(role="doctor", uid=d["id"], name=d["name"])
                    return redirect(url_for("doctor_dashboard"))

            else:
                pt = query("SELECT * FROM patients WHERE email=?", (u,), one=True)
                if pt and check_password_hash(pt["password"], p):
                    session.update(role="patient", uid=pt["id"], name=pt["name"])
                    return redirect(url_for("patient_dashboard"))

            flash("Invalid credentials.", "danger")

        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            f = request.form

            if f["password"] != f["confirm"]:
                flash("Passwords do not match.", "danger")
                return redirect(url_for("register"))

            if query("SELECT 1 FROM patients WHERE email=?", (f["email"],), one=True):
                flash("Email already registered.", "warning")
                return redirect(url_for("register"))

            pid = execute(
                "INSERT INTO patients(name,age,gender,phone,email,address,blood_group,password,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (f["name"], int(f["age"]), f["gender"], f["phone"], f["email"], f["address"], f["blood_group"],
                 generate_password_hash(f["password"]), datetime.now().isoformat())
            )

            mail_registration({"name": f["name"], "email": f["email"]})
            _log("patient", pid, "Registered")

            flash("Registration successful.", "success")
            return redirect(url_for("login"))

        return render_template("register.html")

    # ---------------- PATIENT ----------------

    @app.route("/patient")
    @role_required("patient")
    def patient_dashboard():
        pid = session["uid"]
        appts = query("SELECT a.*, d.name AS dname, d.department AS ddept FROM appointments a JOIN doctors d ON d.id=a.doctor_id WHERE a.patient_id=? ORDER BY a.id DESC LIMIT 20", (pid,))
        notifs = query("SELECT * FROM notifications WHERE role='patient' AND user_id=? ORDER BY id DESC LIMIT 20", (pid,))
        return render_template("patient_dashboard.html", appts=appts, notifs=notifs)

    @app.route("/patient/book", methods=["GET", "POST"])
    @role_required("patient")
    def patient_book():
        if request.method == "POST":
            f = request.form
            priority = f.get("priority", "Normal") or "Normal"
            em_reason = f.get("emergency_reason") if priority == "Emergency" else None
            em_symptoms = f.get("emergency_symptoms") if priority == "Emergency" else None

            if priority == "Emergency" and (not em_reason or not em_symptoms):
                flash("Emergency reason and symptoms are required.", "danger")
                return redirect(url_for("patient_book"))

            appt_id, q, pred_dt = add_appointment(
                session["uid"], int(f["doctor_id"]), f["reason"], priority,
                emergency_reason=em_reason, emergency_symptoms=em_symptoms,
            )
            # Smart slot booking overlay (Phase 2)
            _slot_date = (f.get("slot_date") or "").strip()
            _slot_time = (f.get("slot_time") or "").strip()
            if _slot_date and _slot_time:
                execute("UPDATE appointments SET appt_date=?, predicted_time=?, slot_time=? WHERE id=?",
                        (_slot_date, _slot_time, _slot_time, appt_id))
                from datetime import datetime as _dt
                try: pred_dt = _dt.strptime(_slot_date+" "+_slot_time, "%Y-%m-%d %H:%M")
                except: pass

            # QR appointment pass + secure email action token
            try:
                from upgrades import ensure_tokens
                ensure_tokens(appt_id)
                execute("UPDATE appointments SET appt_time=COALESCE(slot_time,predicted_time), "
                        "checkin_status=COALESCE(checkin_status,'Booked') WHERE id=?", (appt_id,))
            except Exception:
                pass
            # Queue changed -> context-aware AI re-prediction for everyone
            try:
                from ai_engine import repredict_doctor_queue
                repredict_doctor_queue(int(f["doctor_id"]))
            except Exception:
                pass

            doc = query("SELECT * FROM doctors WHERE id=?", (int(f["doctor_id"]),), one=True)
            pt = query("SELECT * FROM patients WHERE id=?", (session["uid"],), one=True)

            _tok = query("SELECT action_token FROM appointments WHERE id=?", (appt_id,), one=True)
            mail_appointment(pt, doc, {"queue_no": q, "reason": f["reason"], "id": appt_id,
                                       "action_token": (_tok["action_token"] if _tok else None)},
                             to_ampm(pred_dt))

            _notify("patient", pt["id"], "Booked", f"Queue {q} ({priority})")
            if priority == "Emergency":
                _notify("doctor", doc["id"], "🚨 Emergency Request",
                        f"{pt['name']} — awaiting your verification (Q{q})")
                _notify("admin", 0, "🚨 Emergency Request",
                        f"{pt['name']} → Dr {doc['name']} (Q{q})")
            else:
                _notify("doctor", doc["id"], "New Patient", pt["name"])
            _log("patient", pt["id"], "Booked", f"Queue {q} ({priority})")

            if priority == "Emergency":
                flash(f"Emergency request submitted (Q{q}). Awaiting doctor/admin verification.", "warning")
            else:
                flash(f"Booked. Queue {q}", "success")
            return redirect(url_for("patient_queue"))

        # Optional reappointment prefill (department/doctor/reason query params)
        prefill = {
            "department": (request.args.get("department") or "").strip(),
            "doctor_id": (request.args.get("doctor_id") or "").strip(),
            "reason": (request.args.get("reason") or "").strip(),
        }
        return render_template("patient_book.html", depts=query("SELECT * FROM departments"),
                               prefill=prefill)

    @app.route("/patient/queue")
    @role_required("patient")
    def patient_queue():
        appts = query("SELECT a.*, d.name AS dname, d.department AS ddept FROM appointments a JOIN doctors d ON d.id=a.doctor_id WHERE a.patient_id=? AND a.status IN('Waiting','InProgress')", (session["uid"],))
        return render_template("patient_queue.html", appts=appts)

    @app.route("/patient/history")
    @role_required("patient")
    def patient_history():
        pid = session["uid"]
        f_status = (request.args.get("status") or "").strip()
        f_dept   = (request.args.get("dept") or "").strip()
        f_doctor = (request.args.get("doctor") or "").strip()
        f_from   = (request.args.get("from") or "").strip()
        f_to     = (request.args.get("to") or "").strip()

        sql = ["""SELECT a.*, d.name AS dname, d.department AS ddept
                  FROM appointments a JOIN doctors d ON d.id=a.doctor_id
                  WHERE a.patient_id=?"""]
        params = [pid]
        if f_status == "Upcoming":
            sql.append("AND a.status IN('Waiting','InProgress','Emergency-PendingVerification')")
        elif f_status:
            sql.append("AND a.status=?"); params.append(f_status)
        if f_dept:
            sql.append("AND a.department=?"); params.append(f_dept)
        if f_doctor:
            sql.append("AND a.doctor_id=?"); params.append(int(f_doctor))
        if f_from:
            sql.append("AND a.appt_date>=?"); params.append(f_from)
        if f_to:
            sql.append("AND a.appt_date<=?"); params.append(f_to)
        sql.append("ORDER BY a.appt_date DESC, a.id DESC")
        rows = query(" ".join(sql), tuple(params))

        counts = {
            "all": len(rows),
            "upcoming":  sum(1 for r in rows if r["status"] in ("Waiting","InProgress","Emergency-PendingVerification")),
            "completed": sum(1 for r in rows if r["status"] == "Completed"),
            "cancelled": sum(1 for r in rows if r["status"] == "Cancelled"),
            "missed":    sum(1 for r in rows if r["status"] == "Missed"),
            "reappointed": sum(1 for r in rows if (r["is_reappointment"] or 0)),
        }
        my_docs = query("""SELECT DISTINCT d.id, d.name FROM appointments a
                           JOIN doctors d ON d.id=a.doctor_id
                           WHERE a.patient_id=? ORDER BY d.name""", (pid,))
        my_depts = query("""SELECT DISTINCT department FROM appointments
                            WHERE patient_id=? AND department IS NOT NULL
                            ORDER BY department""", (pid,))
        return render_template("patient_history.html", rows=rows, counts=counts,
                               my_docs=my_docs, my_depts=my_depts,
                               f={"status": f_status, "dept": f_dept, "doctor": f_doctor,
                                  "from": f_from, "to": f_to})

    @app.route("/patient/profile", methods=["GET", "POST"])
    @role_required("patient")
    def patient_profile():
        pid = session["uid"]

        if request.method == "POST":
            f = request.form
            execute("UPDATE patients SET name=?,age=?,gender=?,phone=?,address=?,blood_group=? WHERE id=?",
                    (f["name"], int(f["age"]), f["gender"], f["phone"], f["address"], f["blood_group"], pid))
            flash("Updated", "success")
            return redirect(url_for("patient_profile"))

        return render_template("patient_profile.html", pt=query("SELECT * FROM patients WHERE id=?", (pid,), one=True))

    @app.route("/patient/notifications")
    @role_required("patient")
    def patient_notifications():
        rows = query("SELECT * FROM notifications WHERE role='patient' AND user_id=? ORDER BY id DESC", (session["uid"],))
        execute("UPDATE notifications SET is_read=1 WHERE role='patient' AND user_id=?", (session["uid"],))
        return render_template("notifications.html", rows=rows)

    # ---------------- DOCTOR ----------------

    @app.route("/doctor")
    @role_required("doctor")
    def doctor_dashboard():
        did = session["uid"]
        q = live_queue(did)

        def _bucket(rows, pri, verified=None):
            out = []
            for x in rows:
                if x["status"] in ("Completed", "Cancelled"):
                    continue
                if x["priority"] != pri:
                    continue
                if verified is not None and (x["emergency_verified"] or 0) != verified:
                    continue
                out.append(x)
            return out

        emergency_verified = _bucket(q, "Emergency", verified=1)
        emergency_pending = [x for x in q if x["priority"] == "Emergency"
                             and (x["emergency_verified"] or 0) == 0
                             and x["status"] == "Emergency-PendingVerification"]
        urgent_q = _bucket(q, "Urgent")
        normal_q = _bucket(q, "Normal")

        stats = {
            "waiting": sum(1 for x in q if x["status"] == "Waiting"),
            "inprog": sum(1 for x in q if x["status"] == "InProgress"),
            "done": sum(1 for x in q if x["status"] == "Completed"),
            "cancel": sum(1 for x in q if x["status"] == "Cancelled"),
            "emergency": len(emergency_verified) + len(emergency_pending),
            "urgent": len(urgent_q),
            "normal": len(normal_q),
        }
        # Patient feedback summary - reuses the existing feedback table/service data.
        try:
            fb_total = query("SELECT COUNT(*) c FROM feedback WHERE doctor_id=?", (did,), one=True)["c"]
            fb_avg = query("SELECT ROUND(AVG(rating),2) a FROM feedback WHERE doctor_id=?", (did,), one=True)["a"] or 0
            fb_avg_d = query("SELECT ROUND(AVG(doctor_rating),2) a FROM feedback WHERE doctor_id=?", (did,), one=True)["a"] or 0
            fb_rows = query("""SELECT f.*, p.name AS pname, a.queue_no
                               FROM feedback f
                               JOIN patients p ON p.id=f.patient_id
                               JOIN appointments a ON a.id=f.appointment_id
                               WHERE f.doctor_id=? ORDER BY f.id DESC LIMIT 4""", (did,))
        except Exception:
            fb_total, fb_avg, fb_avg_d, fb_rows = 0, 0, 0, []

        return render_template(
            "doctor_dashboard.html", queue=q, stats=stats,
            fb_total=fb_total, fb_avg=fb_avg, fb_avg_d=fb_avg_d, fb_rows=fb_rows,
            emergency_verified=emergency_verified,
            emergency_pending=emergency_pending,
            urgent_q=urgent_q, normal_q=normal_q,
        )

    @app.route("/doctor/emergency/<int:appt_id>/<action>", methods=["GET", "POST"])
    @role_required("doctor")
    def doctor_emergency(appt_id, action):
        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a or a["doctor_id"] != session["uid"]:
            abort(403)
        pt = query("SELECT * FROM patients WHERE id=?", (a["patient_id"],), one=True)
        if action == "approve":
            approve_emergency(appt_id, "doctor", session["uid"])
            _notify("patient", pt["id"], "Emergency Approved",
                    f"Q{a['queue_no']} verified as Emergency")
            _log("doctor", session["uid"], "Emergency Approved", f"appt#{appt_id}")
            flash("Emergency approved.", "success")
        elif action == "reject":
            new_pri = request.values.get("new_priority", "Normal")
            reject_emergency(appt_id, new_pri, "doctor", session["uid"])
            _notify("patient", pt["id"], "Emergency Rejected",
                    f"Reclassified as {new_pri}")
            _log("doctor", session["uid"], "Emergency Rejected",
                 f"appt#{appt_id} -> {new_pri}")
            flash(f"Emergency rejected — reclassified as {new_pri}.", "info")
        return redirect(url_for("doctor_dashboard"))


    # ✅ FIXED: full delay modal - minutes + reason (mandatory), custom minutes,
    # logs to doctor_delays, emails every affected patient with new details.
    @app.route("/doctor/delay", methods=["POST"])
    @role_required("doctor")
    def doctor_delay():
        doc = query("SELECT * FROM doctors WHERE id=?", (session["uid"],), one=True)

        raw = (request.form.get("minutes") or "").strip()
        if raw == "custom":
            try:
                mins = int(request.form.get("custom_minutes") or 0)
            except ValueError:
                mins = 0
        else:
            try:
                mins = int(raw or 0)
            except ValueError:
                mins = 0

        reason = (request.form.get("reason") or "").strip()
        if request.form.get("reason") == "Other":
            reason = (request.form.get("reason_other") or "Other").strip()

        # Both fields are mandatory
        if mins <= 0 or not reason:
            msg = "Delay duration and reason are required."
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"ok": False, "error": msg}), 400
            flash(msg, "danger")
            return redirect(url_for("doctor_dashboard"))

        apply_delay(doc["id"], mins, reason=reason, delayed_by=f"doctor:{doc['id']}")

        waiting = query("""
            SELECT a.*, p.email, p.name AS pname
            FROM appointments a
            JOIN patients p ON p.id=a.patient_id
            WHERE a.doctor_id=? AND a.status IN('Waiting','Emergency-PendingVerification')
        """, (doc["id"],))

        for w in waiting:
            mail_delay(
                {"name": w["pname"], "email": w["email"]},
                doc,
                to_ampm(w["predicted_time"]),
                delay_minutes=mins,
                reason=reason,
                predicted_wait=w["predicted_wait_time"],
            )
            _notify("patient", w["patient_id"], "Queue delayed",
                    f"Dr {doc['name']} added a {mins}-min delay. New time: {to_ampm(w['predicted_time'])}")

        _log("doctor", doc["id"], "Delay Applied", f"{mins} min - {reason}")

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "minutes": mins, "reason": reason,
                            "affected": len(waiting)})

        flash(f"Delay applied ({mins} min - {reason}).", "success")
        return redirect(url_for("doctor_dashboard"))

    # Doctor consultation history - reuses the existing appointments data.
    @app.route("/doctor/history")
    @role_required("doctor")
    def doctor_history():
        did = session["uid"]
        f_status = (request.args.get("status") or "").strip()
        f_from = (request.args.get("from") or "").strip()
        f_to = (request.args.get("to") or "").strip()

        sql = ["""SELECT a.*, p.name AS pname, d.name AS dname, d.department AS department
                  FROM appointments a
                  JOIN doctors d ON d.id=a.doctor_id
                  LEFT JOIN patients p ON p.id=a.patient_id
                  WHERE a.doctor_id=?"""]
        params = [did]
        if f_status:
            sql.append("AND a.status=?"); params.append(f_status)
        if f_from:
            sql.append("AND a.appt_date>=?"); params.append(f_from)
        if f_to:
            sql.append("AND a.appt_date<=?"); params.append(f_to)
        sql.append("ORDER BY a.appt_date DESC, a.id DESC")
        rows = query(" ".join(sql), tuple(params))

        def _c(status=None):
            if status:
                return query("SELECT COUNT(*) c FROM appointments WHERE doctor_id=? AND status=?",
                             (did, status), one=True)["c"]
            return query("SELECT COUNT(*) c FROM appointments WHERE doctor_id=?",
                         (did,), one=True)["c"]

        counts = {
            "all": _c(),
            "completed": _c("Completed"),
            "cancelled": _c("Cancelled"),
            "missed": _c("Missed"),
        }
        return render_template("doctor_history.html", rows=rows, counts=counts,
                               f={"status": f_status, "from": f_from, "to": f_to})

    @app.route("/doctor/action/<int:appt_id>/<action>", methods=["GET", "POST"])
    @role_required("doctor")
    def doctor_action(appt_id, action):

        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a or a["doctor_id"] != session["uid"]:
            abort(403)

        pt = query("SELECT * FROM patients WHERE id=?", (a["patient_id"],), one=True)
        doc = query("SELECT * FROM doctors WHERE id=?", (a["doctor_id"],), one=True)

        now = datetime.now()

        def _repredict():
            recalculate_queue(doc["id"])
            try:
                from ai_engine import repredict_doctor_queue
                repredict_doctor_queue(doc["id"])
            except Exception:
                pass

        if action == "start":
            # Capture the real waiting time (check-in / booking -> consultation start)
            ref = a["checkin_time"] or a["created_at"]
            actual_wait = None
            try:
                actual_wait = max(0, int((now - datetime.fromisoformat(ref)).total_seconds() // 60))
            except Exception:
                actual_wait = None
            execute("""UPDATE appointments SET status='InProgress', actual_start=?,
                       actual_wait_minutes=?, checkin_status='Consulting' WHERE id=?""",
                    (now.isoformat(), actual_wait, appt_id))
            _notify("patient", pt["id"], "Consultation started",
                    f"Dr {doc['name']} has started your consultation.")
            _repredict()

        elif action == "complete":
            mins = None
            if a["actual_start"]:
                try:
                    mins = max(1, int((now - datetime.fromisoformat(a["actual_start"])).total_seconds() // 60))
                except Exception:
                    mins = None
            execute("""UPDATE appointments SET status='Completed', actual_end=?,
                       consult_minutes=COALESCE(?, consult_minutes),
                       checkin_status='Completed' WHERE id=?""",
                    (now.isoformat(), mins, appt_id))
            mail_complete(pt, doc)
            _notify("patient", pt["id"], "Consultation completed",
                    f"Your consultation with Dr {doc['name']} is complete.")
            _repredict()

        elif action == "missed":
            execute("""UPDATE appointments SET status='Missed', checkin_status='Missed'
                       WHERE id=?""", (appt_id,))
            _notify("patient", pt["id"], "Marked as missed",
                    f"You missed your appointment (Q{a['queue_no']}). "
                    f"You can request a re-appointment from your dashboard.")
            try:
                from email_service import send_email, _wrap
                base = request.url_root.rstrip("/")
                tok = a["action_token"] or ""
                send_email(pt["email"], "MediQueue AI - Appointment missed",
                           _wrap("Appointment missed",
                                 f"<p>Hello {pt['name']},</p>"
                                 f"<p>You were marked as <b>missed</b> for your appointment "
                                 f"with Dr {doc['name']} (Token {a['queue_no']}).</p>"
                                 f"<p><a href='{base}/appointment/action/{tok}/reappoint' "
                                 f"style='background:#2563eb;color:#fff;padding:10px 18px;"
                                 f"border-radius:8px;text-decoration:none'>Request re-appointment</a></p>"))
            except Exception:
                pass
            _log("doctor", doc["id"], "Marked Missed", f"appt#{appt_id}")
            _repredict()

        elif action == "cancel":
            execute("UPDATE appointments SET status='Cancelled' WHERE id=?", (appt_id,))
            mail_cancel(pt, doc)
            _repredict()

        return redirect(url_for("doctor_dashboard"))

    # ---------------- API ----------------

    @app.route("/api/doctors/<dept>")
    def api_doctors(dept):
        docs = query("SELECT id,name,fee,experience FROM doctors WHERE department=? AND available=1 ORDER BY name", (dept,))
        return jsonify([dict(d) for d in docs])

    # -------- Live queue JSON (for AJAX auto-refresh) --------

    def _queue_payload(doctor_id):
        q = live_queue(doctor_id)
        def _row(a):
            return {
                "id": a["id"],
                "queue_no": a["queue_no"],
                "patient_name": a["patient_name"],
                "age": a["age"],
                "phone": a["phone"],
                "reason": a["reason"],
                "priority": a["priority"],
                "emergency_verified": a["emergency_verified"] or 0,
                "emergency_reason": a["emergency_reason"],
                "emergency_symptoms": a["emergency_symptoms"],
                "predicted_time": to_ampm(a["predicted_time"]),
                "predicted_wait_time": a["predicted_wait_time"],
                "status": a["status"],
                "delay_minutes": a["delay_minutes"] or 0,
            }
        rows = [_row(x) for x in q]
        stats = {
            "waiting": sum(1 for x in q if x["status"] == "Waiting"),
            "inprog":  sum(1 for x in q if x["status"] == "InProgress"),
            "done":    sum(1 for x in q if x["status"] == "Completed"),
            "cancel":  sum(1 for x in q if x["status"] == "Cancelled"),
            "emergency": sum(1 for x in q if x["priority"] == "Emergency"),
            "urgent":    sum(1 for x in q if x["priority"] == "Urgent"),
            "normal":    sum(1 for x in q if x["priority"] == "Normal"),
            "emergency_pending": sum(1 for x in q if x["status"] == "Emergency-PendingVerification"),
        }
        return {"queue": rows, "stats": stats, "ts": datetime.now().isoformat()}

    @app.route("/api/doctor/live")
    @role_required("doctor")
    def api_doctor_live():
        return jsonify(_queue_payload(session["uid"]))

    @app.route("/api/patient/live")
    @role_required("patient")
    def api_patient_live():
        rows = query("""SELECT a.*, d.name AS dname, d.department AS ddept
                        FROM appointments a JOIN doctors d ON d.id=a.doctor_id
                        WHERE a.patient_id=? AND a.status IN('Waiting','InProgress','Emergency-PendingVerification')
                        ORDER BY a.id DESC""", (session["uid"],))
        # Position within each doctor's queue today
        out = []
        for a in rows:
            q = live_queue(a["doctor_id"])
            pos = next((i+1 for i, x in enumerate(q) if x["id"] == a["id"]), None)
            out.append({
                "id": a["id"],
                "queue_no": a["queue_no"],
                "doctor": a["dname"],
                "department": a["ddept"],
                "priority": a["priority"],
                "emergency_verified": a["emergency_verified"] or 0,
                "predicted_time": to_ampm(a["predicted_time"]),
                "predicted_wait_time": a["predicted_wait_time"],
                "status": a["status"],
                "position": pos,
            })
        return jsonify({"appointments": out, "ts": datetime.now().isoformat()})

    @app.route("/api/admin/live")
    @role_required("admin")
    def api_admin_live():
        today = datetime.now().strftime("%Y-%m-%d")
        s = {
            "today":     query("SELECT COUNT(*) c FROM appointments WHERE appt_date=?", (today,), one=True)["c"],
            "waiting":   query("SELECT COUNT(*) c FROM appointments WHERE status='Waiting'", one=True)["c"],
            "inprog":    query("SELECT COUNT(*) c FROM appointments WHERE status='InProgress'", one=True)["c"],
            "completed": query("SELECT COUNT(*) c FROM appointments WHERE status='Completed'", one=True)["c"],
            "cancelled": query("SELECT COUNT(*) c FROM appointments WHERE status='Cancelled'", one=True)["c"],
            "emergency_total":   query("SELECT COUNT(*) c FROM appointments WHERE priority='Emergency' AND appt_date=?", (today,), one=True)["c"],
            "emergency_pending": query("SELECT COUNT(*) c FROM appointments WHERE status='Emergency-PendingVerification'", one=True)["c"],
            "urgent_total":      query("SELECT COUNT(*) c FROM appointments WHERE priority='Urgent' AND appt_date=?", (today,), one=True)["c"],
            "normal_total":      query("SELECT COUNT(*) c FROM appointments WHERE priority='Normal' AND appt_date=?", (today,), one=True)["c"],
        }
        return jsonify({"stats": s, "ts": datetime.now().isoformat()})

    # ---------------- PATIENT RESPONSE ----------------

    @app.route("/patient/response/<int:appt_id>/<action>")
    @role_required("patient")
    def patient_response(appt_id, action):
        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a or a["patient_id"] != session["uid"]:
            abort(403)
        pt = query("SELECT * FROM patients WHERE id=?", (a["patient_id"],), one=True)
        doc = query("SELECT * FROM doctors WHERE id=?", (a["doctor_id"],), one=True)

        if action == "cancel":
            execute("UPDATE appointments SET status='Cancelled' WHERE id=?", (appt_id,))
            mail_cancel(pt, doc)
            recalculate_queue(doc["id"])
            try:
                from ai_engine import repredict_doctor_queue
                repredict_doctor_queue(doc["id"])
            except Exception:
                pass
            _notify("doctor", doc["id"], "Patient Cancelled", pt["name"])
            _notify("admin", 0, "Patient Cancelled", f"{pt['name']} cancelled Q{a['queue_no']}")
            flash("Appointment cancelled.", "info")

        elif action == "reappoint":
            execute("UPDATE appointments SET status='Cancelled' WHERE id=?", (appt_id,))
            new_id, q, pred_dt = add_appointment(pt["id"], doc["id"], a["reason"], a["priority"] or "Normal")
            try:
                from upgrades import ensure_tokens
                ensure_tokens(new_id)
            except Exception:
                pass
            _tok = query("SELECT action_token FROM appointments WHERE id=?", (new_id,), one=True)
            mail_appointment(pt, doc, {"queue_no": q, "reason": a["reason"], "id": new_id,
                                       "action_token": (_tok["action_token"] if _tok else None)},
                             to_ampm(pred_dt))
            _notify("doctor", doc["id"], "Patient Rescheduled", pt["name"])
            flash(f"Re-appointed. New queue {q}", "success")

        elif action == "continue":
            flash("Confirmed. Please continue waiting.", "success")

        return redirect(url_for("patient_queue"))

    # ---------------- ADMIN ----------------

    @app.route("/admin")
    @role_required("admin")
    def admin_dashboard():
        today = datetime.now().strftime("%Y-%m-%d")
        s = {
            "patients": query("SELECT COUNT(*) c FROM patients", one=True)["c"],
            "doctors": query("SELECT COUNT(*) c FROM doctors", one=True)["c"],
            "depts": query("SELECT COUNT(*) c FROM departments", one=True)["c"],
            "today": query("SELECT COUNT(*) c FROM appointments WHERE appt_date=?", (today,), one=True)["c"],
            "completed": query("SELECT COUNT(*) c FROM appointments WHERE status='Completed'", one=True)["c"],
            "cancelled": query("SELECT COUNT(*) c FROM appointments WHERE status='Cancelled'", one=True)["c"],
            "pending": query("SELECT COUNT(*) c FROM appointments WHERE status='Waiting'", one=True)["c"],
            "missed": query("SELECT COUNT(*) c FROM appointments WHERE status='Missed'", one=True)["c"],
            "appts": query("SELECT COUNT(*) c FROM appointments", one=True)["c"],
            "inprog": query("SELECT COUNT(*) c FROM appointments WHERE status='InProgress'", one=True)["c"],
            "checked_in": query("SELECT COUNT(*) c FROM appointments WHERE checkin_status='Checked-In'", one=True)["c"],
            "revenue": (query("SELECT COALESCE(SUM(d.fee),0) s FROM appointments a JOIN doctors d ON d.id=a.doctor_id WHERE a.status='Completed'", one=True)["s"]) or 0,
        }
        # ---- Priority statistics ----
        s["emergency_total"] = query(
            "SELECT COUNT(*) c FROM appointments WHERE priority='Emergency' AND appt_date=?",
            (today,), one=True)["c"]
        s["urgent_total"] = query(
            "SELECT COUNT(*) c FROM appointments WHERE priority='Urgent' AND appt_date=?",
            (today,), one=True)["c"]
        s["normal_total"] = query(
            "SELECT COUNT(*) c FROM appointments WHERE priority='Normal' AND appt_date=?",
            (today,), one=True)["c"]
        s["emergency_pending"] = query(
            "SELECT COUNT(*) c FROM appointments WHERE status='Emergency-PendingVerification'",
            one=True)["c"]

        def _avg_wait(where):
            rows = query(
                f"SELECT consult_minutes FROM appointments WHERE {where} AND appt_date=?",
                (today,))
            vals = [r["consult_minutes"] or 0 for r in rows]
            return round(sum(vals) / len(vals), 1) if vals else 0

        s["avg_emergency_wait"] = 3 if s["emergency_total"] else 0
        s["avg_queue_wait"] = _avg_wait("priority IN('Normal','Urgent')")

        by_dept = query("SELECT department, COUNT(*) c FROM appointments GROUP BY department ORDER BY c DESC")
        recent = query("""SELECT a.queue_no,a.status,a.department,a.priority,
                                 a.emergency_verified, p.name AS pname, d.name AS dname
                          FROM appointments a JOIN patients p ON p.id=a.patient_id
                          JOIN doctors d ON d.id=a.doctor_id ORDER BY a.id DESC LIMIT 15""")
        emergencies = query("""SELECT a.*, p.name AS pname, p.age, d.name AS dname
                               FROM appointments a JOIN patients p ON p.id=a.patient_id
                               JOIN doctors d ON d.id=a.doctor_id
                               WHERE a.priority='Emergency'
                               ORDER BY a.emergency_verified DESC, a.id DESC LIMIT 40""")
        return render_template("admin_dashboard.html", s=s, by_dept=by_dept,
                               recent=recent, meta=get_meta(),
                               emergencies=emergencies)

    @app.route("/admin/emergency/<int:appt_id>/<action>", methods=["GET", "POST"])
    @role_required("admin")
    def admin_emergency(appt_id, action):
        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a:
            abort(404)
        pt = query("SELECT * FROM patients WHERE id=?", (a["patient_id"],), one=True)
        if action == "approve":
            approve_emergency(appt_id, "admin", session["uid"])
            _notify("patient", pt["id"], "Emergency Approved (Admin)", f"Q{a['queue_no']}")
            _notify("doctor", a["doctor_id"], "Emergency Verified",
                    f"{pt['name']} approved by Admin")
            flash("Emergency approved.", "success")
        elif action == "reject":
            new_pri = request.values.get("new_priority", "Normal")
            reject_emergency(appt_id, new_pri, "admin", session["uid"])
            _notify("patient", pt["id"], "Emergency Rejected (Admin)",
                    f"Reclassified as {new_pri}")
            flash(f"Emergency reclassified as {new_pri}.", "info")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/priority/<int:appt_id>", methods=["POST"])
    @role_required("admin")
    def admin_set_priority(appt_id):
        new_pri = request.form.get("priority", "Normal")
        if new_pri not in ("Emergency", "Urgent", "Normal"):
            new_pri = "Normal"
        a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
        if not a:
            abort(404)
        verified = 1 if new_pri == "Emergency" else 0
        token = a["queue_no"]
        if (a["priority"] or "") != new_pri:
            token = None
            from queue_manager import next_token as _nt
            token = _nt(a["department"], new_pri)
        execute("""UPDATE appointments SET priority=?, queue_no=?,
                   emergency_verified=?, emergency_status=?, status='Waiting'
                   WHERE id=?""",
                (new_pri, token, verified,
                 "Verified" if verified else "Manual", appt_id))
        recalculate_queue(a["doctor_id"])
        flash(f"Priority set to {new_pri}", "success")
        return redirect(url_for("admin_dashboard"))


    @app.route("/admin/doctors", methods=["GET", "POST"])
    @role_required("admin")
    def admin_doctors():
        if request.method == "POST":
            f = request.form
            code = f"D{(query('SELECT COUNT(*) c FROM doctors', one=True)['c']+1):03d}"
            execute("""INSERT INTO doctors
                (doc_code,name,department,qualification,experience,specialization,fee,avg_time,available,photo,username,password)
                VALUES(?,?,?,?,?,?,?,?,1,'',?,?)""",
                (code, f["name"], f["department"], f["qualification"],
                 int(f["experience"]), f["specialization"], int(f["fee"]),
                 int(f["avg_time"]), code.lower(), generate_password_hash("doctor123")))
            flash(f"Doctor added ({code} / doctor123)", "success")
            return redirect(url_for("admin_doctors"))
        docs = query("SELECT * FROM doctors ORDER BY department,name")
        depts = query("SELECT * FROM departments")
        return render_template("admin_doctors.html", docs=docs, depts=depts)

    @app.route("/admin/doctors/delete/<int:did>")
    @role_required("admin")
    def admin_doctors_delete(did):
        execute("DELETE FROM doctors WHERE id=?", (did,))
        flash("Doctor deleted", "info")
        return redirect(url_for("admin_doctors"))

    @app.route("/admin/patients")
    @role_required("admin")
    def admin_patients():
        rows = query("SELECT * FROM patients ORDER BY id DESC")
        return render_template("admin_patients.html", rows=rows)

    @app.route("/admin/appointments")
    @role_required("admin")
    def admin_appointments():
        rows = query("""SELECT a.*, p.name AS pname, d.name AS dname
                        FROM appointments a JOIN patients p ON p.id=a.patient_id
                        JOIN doctors d ON d.id=a.doctor_id ORDER BY a.id DESC LIMIT 500""")
        return render_template("admin_appts.html", rows=rows)

    @app.route("/admin/departments", methods=["GET", "POST"])
    @role_required("admin")
    def admin_departments():
        """Departments master list with doctor + appointment counts."""
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            desc = (request.form.get("description") or "").strip()
            if name:
                try:
                    execute("INSERT INTO departments(name, description) VALUES(?,?)",
                            (name, desc))
                    flash(f"Department '{name}' added.", "success")
                except Exception:
                    flash("That department already exists.", "warning")
            return redirect(url_for("admin_departments"))
        rows = query("""SELECT dp.*,
                          (SELECT COUNT(*) FROM doctors d WHERE d.department=dp.name) AS doctors,
                          (SELECT COUNT(*) FROM appointments a WHERE a.department=dp.name) AS appts
                        FROM departments dp ORDER BY dp.name""")
        return render_template("admin_departments.html", rows=rows)

    @app.route("/admin/emails")
    @role_required("admin")
    def admin_emails():
        rows = query("SELECT * FROM email_logs ORDER BY id DESC LIMIT 300")
        return render_template("admin_emails.html", rows=rows)

    @app.route("/admin/history")
    @role_required("admin")
    def admin_history():
        """Unified history centre: patient visits, appointments and QR scans."""
        visits = query("""SELECT a.id, a.queue_no, a.appt_date, a.appt_time,
                                 a.predicted_time, a.department, a.status,
                                 a.checkin_status, a.checkin_time,
                                 a.consult_start_time, a.consult_end_time,
                                 p.name AS pname, p.phone, d.name AS dname,
                                 d.department AS ddept, a.appt_date AS visit_date
                          FROM appointments a
                          JOIN patients p ON p.id=a.patient_id
                          JOIN doctors  d ON d.id=a.doctor_id
                          WHERE a.checkin_status IN ('Checked-In','Waiting',
                                'Consultation Started','Consulting','Completed')
                             OR a.status='Completed'
                          ORDER BY a.id DESC LIMIT 300""")
        appts = query("""SELECT a.id, a.queue_no, a.appt_date, a.appt_time,
                                a.predicted_time, a.department, a.reason,
                                a.priority, a.status, a.checkin_status,
                                a.created_at, p.name AS pname, d.name AS dname
                         FROM appointments a
                         JOIN patients p ON p.id=a.patient_id
                         JOIN doctors  d ON d.id=a.doctor_id
                         ORDER BY a.id DESC LIMIT 300""")
        try:
            scans = query("""SELECT c.*, p.name AS patient_name, a.queue_no,
                                    a.department
                             FROM checkins c
                             LEFT JOIN patients p ON p.id=c.patient_id
                             LEFT JOIN appointments a ON a.id=c.appointment_id
                             ORDER BY c.id DESC LIMIT 300""")
        except Exception:
            scans = []
        return render_template("admin_history.html", visits=visits,
                               appts=appts, scans=scans)

    @app.route("/admin/reports.csv")
    @role_required("admin")
    def admin_reports():
        rows = query("""SELECT a.queue_no,a.department,a.status,a.appt_date,a.predicted_time,
                               a.consult_minutes,a.delay_minutes,a.priority,
                               a.predicted_wait_time,a.actual_wait_minutes,
                               a.checkin_status,a.checkin_time,a.reason,
                               p.name AS patient, d.name AS doctor, d.fee AS fee
                        FROM appointments a JOIN patients p ON p.id=a.patient_id
                        JOIN doctors d ON d.id=a.doctor_id ORDER BY a.id DESC""")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Queue","Department","Priority","Status","Date","Predicted Time",
                    "Predicted Wait(min)","Actual Wait(min)","Consult(min)","Delay(min)",
                    "Check-in Status","Check-in Time","Reason","Patient","Doctor","Fee"])
        for r in rows:
            w.writerow([r["queue_no"],r["department"],r["priority"],r["status"],r["appt_date"],
                        to_ampm(r["predicted_time"]),r["predicted_wait_time"],
                        r["actual_wait_minutes"],r["consult_minutes"],r["delay_minutes"],
                        r["checkin_status"],to_ampm(r["checkin_time"]),r["reason"],
                        r["patient"],r["doctor"],r["fee"]])
        return Response(buf.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=mediqueue_report.csv"})

    @app.route("/admin/notifications")
    @role_required("admin")
    def admin_notifications():
        rows = query("SELECT * FROM notifications WHERE role='admin' ORDER BY id DESC LIMIT 200")
        execute("UPDATE notifications SET is_read=1 WHERE role='admin'")
        return render_template("notifications.html", rows=rows)
