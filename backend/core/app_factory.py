"""MediQueue AI - main entry point.
Run:  python app.py
"""
from flask import Flask, request, session
from config import Config
from database import init_db, seed_all
from routes import register_routes
from extra_features import register_extra_routes, init_extra_tables
from enhancements import (
    register_enhancement_routes, init_enhancement_tables,
    auto_cancel_reappointments, auto_cancel_no_response,
)
from scheduler import start_scheduler
from prediction import train_model_if_needed
from upgrades import init_upgrade_tables, register_upgrade_routes
from queue_live import register_queue_live_routes
from ai_engine import register_ai_routes, compute_metrics

# ---- Enterprise layer (new, additive only) ----
from enterprise_bootstrap import (init_enterprise_tables, backfill,
                                  migrate_settings_from_legacy)
from api.enterprise_api import register_enterprise_routes
from middleware.security import register_security
from services.qr_service import generator as qr_generator
from services.qr_service import database as qr_database
from services.settings_service import settings as settings_service
from services.audit_service import audit as audit_service
from services.realtime_service import bus as realtime_bus


def create_app():
    app = Flask(__name__,
                template_folder=Config.TEMPLATE_DIR,
                static_folder=Config.STATIC_DIR)
    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY

    from time_utils import to_ampm, to_ampm_datetime
    app.jinja_env.filters['ampm'] = to_ampm
    app.jinja_env.filters['ampm_dt'] = to_ampm_datetime

    @app.context_processor
    def inject_globals():
        from datetime import datetime as _dt
        # SETTINGS is live: any admin change is reflected on every page instantly.
        try:
            live_settings = settings_service.all_settings()
        except Exception:
            live_settings = {}
        return {"CFG": Config, "now": _dt.now(), "SETTINGS": live_settings,
                "HOSPITAL_NAME": live_settings.get("hospital_name",
                                                   Config.HOSPITAL_NAME)}

    # ---------- Role-based route guard ----------
    @app.before_request
    def _role_guard():
        from flask import request, session, redirect
        path = request.path or "/"
        role = session.get("role")
        if not role:
            return None
        if (path.startswith("/static") or path.startswith("/logout")
                or path == "/login" or path.startswith("/api/")
                or path.startswith("/staff/")
                or path.startswith("/appointment/action/")):
            return None
        if role == "admin" and (path.startswith("/patient") or path.startswith("/doctor")):
            return redirect("/admin")
        if role == "doctor" and (path.startswith("/admin") or path.startswith("/patient")):
            return redirect("/doctor")
        if role == "patient" and (path.startswith("/admin") or path.startswith("/doctor")):
            return redirect("/patient")
        return None

    with app.app_context():
        init_db()
        init_extra_tables()
        init_enhancement_tables()   # <-- new Phase 2 tables & migrations
        init_upgrade_tables()       # <-- QR check-in / email actions (Phase 3)
        init_enterprise_tables()    # <-- Phase 4: QR codes, check-ins, audit, settings history
        seed_all()
        migrate_settings_from_legacy()
        backfill()                  # every appointment gets a valid QR + barcode
        train_model_if_needed()
        try:
            compute_metrics()       # cache MAE / RMSE / R2 for the AI dashboard
        except Exception as exc:
            print("[AI] metrics warm-up skipped:", exc)

    register_routes(app)
    register_extra_routes(app)
    register_enhancement_routes(app)  # <-- new Phase 2 routes
    register_upgrade_routes(app)      # QR pass, staff check-in, email actions
    register_queue_live_routes(app)   # /api/patient/queue-live, /patient/live
    register_ai_routes(app)           # /admin/ai-performance, /api/admin/ai-metrics
    register_security(app)            # headers, rate limiting, session expiry, audit
    register_enterprise_routes(app)   # QR check-in, audit logs, settings history, live slots

    # ---------- Auto-issue a QR for every new booking ----------
    @app.after_request
    def _auto_issue_qr(response):
        try:
            if (request.method == "POST" and request.path.startswith("/patient/book")
                    and session.get("role") == "patient" and response.status_code < 400):
                latest = _q("""SELECT id FROM appointments WHERE patient_id=?
                               ORDER BY id DESC LIMIT 1""",
                            (session.get("uid"),), one=True)
                if latest:
                    qr_generator.issue_qr(latest["id"])
        except Exception as exc:
            print("[QR] auto-issue skipped:", exc)
        return response
    # Pass Phase-2 background jobs into the shared scheduler
    from queue_manager import recalculate_queue
    from models import query as _q
    def _requeue_all():
        with app.app_context():
            for d in _q("SELECT id FROM doctors WHERE available=1"):
                try: recalculate_queue(d['id'])
                except Exception: pass
    def _expire_qr_codes():
        with app.app_context():
            try:
                qr_database.expire_finished_appointments()
            except Exception:
                pass

    start_scheduler(app, extra_jobs=[
        ("requeue_periodic", _requeue_all, 1),
        ("qr_expiry_sweep", _expire_qr_codes, 5),
        ("reappt_autocancel", lambda: auto_cancel_reappointments(app), 2),
        ("no_response_cancel", lambda: auto_cancel_no_response(app), 3),
    ])
    return app


app = create_app()

if __name__ == "__main__":
    print("=" * 60)
    print(" MediQueue AI ready  ->  http://127.0.0.1:5000")
    print(" Admin login  ->  admin / admin123")
    print("=" * 60)
    app.run(debug=True, use_reloader=False, host="0.0.0.0", port=5000)
