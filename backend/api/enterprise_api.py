"""
Enterprise API / Routes
=======================
All NEW endpoints introduced by the enterprise upgrade. Nothing here
replaces an existing route - the original blueprint set is registered
first and keeps working exactly as before.

Added:
  GET  /admin/qr-checkin            Admin camera QR scanner page
  POST /api/admin/qr-scan           Verify QR + auto check-in
  POST /api/admin/barcode-scan      Barcode check-in
  GET  /admin/settings-history      Permanent settings change log
  GET  /admin/audit-logs            Admin activity audit trail
  GET  /admin/analytics             Enterprise analytics dashboard
  GET  /api/admin/enterprise-stats  Live KPI feed
  GET  /api/slots/live              Real-time AM/PM slot board
  GET  /api/realtime/stream         Server-Sent Events push channel
  GET  /api/realtime/state          Poll fallback for the same data
  GET  /patient/appointment/<id>/qr.png   Downloadable / printable QR
  GET  /patient/qr/<id>             Full-screen printable QR pass
"""
from __future__ import annotations

import json
from datetime import datetime

from flask import (Response, flash, jsonify, redirect, render_template, request,
                   session, url_for, abort)

from models import query
from config import Config
from time_utils import to_ampm
from services.qr_service import generator as qr_gen, scanner as qr_scanner, database as qr_db
from services.barcode_service import generator as bc_gen, scanner as bc_scanner
from services.scanner_service import checkin as checkin_service
from services.settings_service import settings as settings_service
from services.audit_service import audit
from services.appointment_service import slots as slot_service
from services.queue_service import queue as queue_service, waiting_time
from services.report_service import reports
from services.realtime_service import bus


def _require(role):
    if session.get("role") != role:
        flash("Please log in to continue.", "warning")
        return redirect(url_for("login"))
    return None


def _ip():
    return ((request.headers.get("X-Forwarded-For", "").split(",")[0].strip())
            or request.remote_addr or "-")


def register_enterprise_routes(app):

    # =============================================================== ADMIN QR
    @app.route("/admin/qr-checkin")
    def admin_qr_checkin():
        guard = _require("admin")
        if guard:
            return guard
        return render_template("admin/qr_checkin.html",
                               recent=checkin_service.today_checkins(25),
                               counts=checkin_service.counts_today())

    @app.route("/api/admin/qr-scan", methods=["POST"])
    def api_admin_qr_scan():
        if session.get("role") not in ("admin", "doctor", "staff"):
            return jsonify({"ok": False, "code": "auth",
                            "message": "Not authorised"}), 403
        body = (request.json or {}) if request.is_json else {}
        payload = body.get("payload") or request.form.get("payload") or ""
        stage = (body.get("stage") or request.form.get("stage")
                 or "checkin").strip().lower()
        if stage not in ("checkin", "consult"):
            stage = "checkin"
        result = qr_scanner.scan(payload,
                                 admin_name=session.get("name") or session.get("role"),
                                 method="qr", ip=_ip(), stage=stage)
        audit.log("QR Scan", "appointment", result.get("appointment_id", ""),
                  f"{result.get('code')}: {result.get('message')}")
        return jsonify(result)

    @app.route("/api/admin/qr-lookup", methods=["POST"])
    def api_admin_qr_lookup():
        """Validate an uploaded QR payload and return the patient / appointment
        details WITHOUT performing the check-in. The admin confirms first, then
        the existing /api/admin/qr-scan endpoint does the actual check-in."""
        if session.get("role") not in ("admin", "doctor", "staff"):
            return jsonify({"found": False, "code": "auth",
                            "message": "Not authorised"}), 403
        body = (request.json or {}) if request.is_json else {}
        payload = body.get("payload") or request.form.get("payload") or ""

        from services.qr_service import validator as qr_validator
        result = qr_validator.validate(payload, stage="checkin")
        appt = result.get("appointment")

        if not appt:
            return jsonify({"found": False, "code": result.get("code", "invalid"),
                            "message": "Invalid or expired QR code."})

        state = (appt["checkin_status"] if "checkin_status" in appt.keys() else None) or "Booked"
        detail = {
            "patient_name": appt["pname"],
            "patient_id": appt["patient_id"],
            "appointment_id": appt["id"],
            "department": appt["ddept"] or appt["department"],
            "doctor": appt["dname"],
            "appt_date": appt["appt_date"],
            "appt_time": to_ampm(appt["predicted_time"]) if appt["predicted_time"] else "—",
            "queue_no": appt["queue_no"],
            "status": appt["status"],
            "checkin_status": state,
        }

        code = result.get("code", "invalid")
        message = result.get("message", "")
        if not result.get("ok"):
            if code == "already":
                message = "Patient already checked in."
            elif code in ("invalid", "expired"):
                message = "Invalid or expired QR code."

        return jsonify({"found": True, "can_check_in": bool(result.get("ok")),
                        "code": code, "message": message, "appointment": detail})


    @app.route("/api/admin/barcode-scan", methods=["POST"])
    def api_admin_barcode_scan():
        if session.get("role") not in ("admin", "doctor", "staff"):
            return jsonify({"ok": False, "message": "Not authorised"}), 403
        code = ((request.json or {}).get("code") if request.is_json
                else request.form.get("code")) or ""
        result = bc_scanner.scan(code, admin_name=session.get("name") or "admin",
                                 ip=_ip())
        audit.log("Barcode Scan", "appointment", result.get("appointment_id", ""),
                  result.get("message", ""))
        return jsonify(result)

    @app.route("/api/admin/checkins")
    def api_admin_checkins():
        if session.get("role") not in ("admin", "doctor", "staff"):
            return jsonify({"rows": []}), 403
        rows = [dict(r) for r in checkin_service.today_checkins(50)]
        for r in rows:
            r["scan_time_label"] = to_ampm(r.get("scan_time"))
        return jsonify({"rows": rows, "counts": checkin_service.counts_today()})

    # ============================================================ PATIENT QR
    @app.route("/patient/appointment/<int:appt_id>/qr.png")
    def patient_qr_png(appt_id):
        appt = query("SELECT patient_id FROM appointments WHERE id=?",
                     (appt_id,), one=True)
        if not appt:
            abort(404)
        if session.get("role") == "patient" and appt["patient_id"] != session.get("uid"):
            abort(403)
        if session.get("role") not in ("patient", "admin", "doctor", "staff"):
            abort(401)
        qr_gen.issue_qr(appt_id)
        png = qr_gen.qr_png(qr_gen.payload_text(appt_id))
        if not png:
            abort(500)
        return Response(png, mimetype="image/png", headers={
            "Content-Disposition": f'attachment; filename="mediqueue-qr-{appt_id}.png"'})

    @app.route("/patient/qr/<int:appt_id>")
    def patient_qr_page(appt_id):
        guard = _require("patient")
        if guard:
            return guard
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
        payload = qr_gen.issue_qr(appt_id) or {}
        record = qr_db.get_by_appointment(appt_id)
        return render_template(
            "patient/qr_pass.html", a=a, payload=payload,
            qr_svg=qr_gen.qr_svg(qr_gen.payload_text(appt_id)),
            barcode_svg=bc_gen.barcode_svg(bc_gen.code_for(appt_id)),
            barcode=bc_gen.code_for(appt_id),
            qr_status=(record["status"] if record else "Active"))

    # ====================================================== SETTINGS + HISTORY
    @app.route("/admin/settings-history")
    def admin_settings_history():
        guard = _require("admin")
        if guard:
            return guard
        page = max(int(request.args.get("page", 1) or 1), 1)
        per_page = 50
        rows = settings_service.history(limit=per_page, page=page)
        return render_template("admin/settings_history.html", rows=rows, page=page,
                               per_page=per_page,
                               total=settings_service.history_total())

    @app.route("/admin/settings-plus", methods=["GET", "POST"])
    def admin_settings_plus():
        """Enterprise settings screen: every change is versioned + broadcast."""
        guard = _require("admin")
        if guard:
            return guard
        if request.method == "POST":
            data = {k: v for k, v in request.form.items() if k != "csrf_token"}
            changed = settings_service.set_many(data,
                                                admin_name=session.get("name", "admin"))
            flash(f"{changed} setting(s) updated - history recorded.", "success")
            return redirect(url_for("admin_settings_plus"))
        return render_template("admin/settings_plus.html",
                               vals=settings_service.all_settings(),
                               recent=settings_service.history(limit=10))

    @app.route("/api/settings/live")
    def api_settings_live():
        return jsonify(settings_service.all_settings())

    # ================================================================== AUDIT
    @app.route("/admin/audit-logs")
    def admin_audit_logs():
        guard = _require("admin")
        if guard:
            return guard
        page = max(int(request.args.get("page", 1) or 1), 1)
        action = request.args.get("action") or None
        per_page = 50
        return render_template("admin/audit_logs.html",
                               rows=audit.recent(page=page, per_page=per_page,
                                                 action=action),
                               logins=audit.login_history(25),
                               page=page, per_page=per_page,
                               total=audit.total(action), action=action or "")

    # ============================================================= ANALYTICS
    @app.route("/admin/analytics")
    def admin_analytics():
        guard = _require("admin")
        if guard:
            return guard
        return render_template("admin/analytics.html",
                               stats=_enterprise_stats(),
                               daily=reports.daily(),
                               monthly=reports.monthly(),
                               activities=audit.recent(limit=15))

    @app.route("/api/admin/enterprise-stats")
    def api_enterprise_stats():
        if session.get("role") != "admin":
            return jsonify({}), 403
        return jsonify(_enterprise_stats())

    # ============================================================ LIVE SLOTS
    @app.route("/api/slots/live")
    def api_slots_live():
        try:
            doctor_id = int(request.args.get("doctor_id", "0"))
        except ValueError:
            return jsonify({"error": "bad doctor_id", "slots": []}), 400
        if not doctor_id:
            return jsonify({"error": "doctor_id required", "slots": []}), 400
        on_date = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
        return jsonify(slot_service.available_slots(doctor_id, on_date))

    # ============================================================== REALTIME
    @app.route("/api/realtime/stream")
    def api_realtime_stream():
        return Response(bus.sse_stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no",
                                 "Connection": "keep-alive"})

    @app.route("/api/realtime/state")
    def api_realtime_state():
        """Polling fallback that mirrors the SSE payload."""
        role = session.get("role")
        state = {"role": role, "events": bus.snapshot(),
                 "server_time": datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
                 "settings": settings_service.all_settings()}
        if role == "admin":
            state["stats"] = _enterprise_stats()
        elif role == "doctor":
            state["queue"] = [dict(x) for x in queue_service.live_queue(session.get("uid"))]
        elif role == "patient":
            appt = query("""SELECT id FROM appointments
                            WHERE patient_id=? AND appt_date=?
                              AND status IN ('Waiting','Consulting','Emergency')
                            ORDER BY id DESC LIMIT 1""",
                         (session.get("uid"), datetime.now().strftime("%Y-%m-%d")),
                         one=True)
            if appt:
                state["queue_status"] = waiting_time.estimate_for(appt["id"])
        return jsonify(state)

    @app.route("/api/health")
    def api_health():
        try:
            query("SELECT 1", one=True)
            db_ok = True
        except Exception:
            db_ok = False
        return jsonify({"status": "ok" if db_ok else "degraded",
                        "database": db_ok,
                        "time": datetime.now().strftime("%I:%M %p").lstrip("0")})


# ------------------------------------------------------------------ helpers
def _enterprise_stats() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    counts = checkin_service.counts_today()
    row = query("""SELECT COUNT(*) total,
                     SUM(CASE WHEN status IN ('Waiting','Consulting') THEN 1 ELSE 0 END) queue,
                     SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) completed,
                     SUM(CASE WHEN status='Cancelled' THEN 1 ELSE 0 END) cancelled,
                     SUM(CASE WHEN status LIKE 'Emergency%' THEN 1 ELSE 0 END) emergency
                   FROM appointments WHERE appt_date=?""", (today,), one=True)
    doctors_online = query("SELECT COUNT(*) n FROM doctors WHERE available=1",
                           one=True)["n"]
    departments = query("SELECT COUNT(*) n FROM departments", one=True)["n"]
    daily = reports.daily(today)
    return {
        "date": today,
        "server_time": datetime.now().strftime("%I:%M %p").lstrip("0"),
        "todays_appointments": row["total"] or 0,
        "todays_queue": row["queue"] or 0,
        "completed": row["completed"] or 0,
        "cancelled": row["cancelled"] or 0,
        "emergency": row["emergency"] or 0,
        "doctors_online": doctors_online,
        "departments": departments,
        "revenue": daily["revenue"],
        "live_checkins": counts["checked_in"],
        "qr_scans": counts["qr_scans"],
        "barcode_scans": counts["barcode_scans"],
        "hospital_name": settings_service.get("hospital_name", Config.HOSPITAL_NAME),
        "system_health": "Healthy",
    }
