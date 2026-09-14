"""
Enterprise bootstrap
====================
Creates the new tables, back-fills data for existing records and wires the
new services into the running Flask app - without altering any existing
behaviour.
"""
from __future__ import annotations

from services.qr_service import database as qr_db, generator as qr_gen
from services.scanner_service import checkin as checkin_service
from services.barcode_service import generator as bc_gen
from services.settings_service import settings as settings_service
from services.audit_service import audit
from services.queue_service import queue as queue_service
from services.report_service import reports
from services.prediction_service import predictor


def init_enterprise_tables():
    """Idempotent: safe to run on every start, never drops data."""
    settings_service.init_tables()
    qr_db.init_tables()
    checkin_service.init_tables()
    bc_gen.init_tables()
    audit.init_tables()
    queue_service.init_tables()
    reports.init_tables()
    predictor.init_tables()
    print("[ENTERPRISE] tables ready")


def backfill():
    """Ensure every existing appointment has a QR + barcode record."""
    qr_gen.backfill_all()
    qr_db.expire_finished_appointments()


def migrate_settings_from_legacy():
    """Copy legacy hospital name / timings into the new settings store once."""
    from config import Config
    if not settings_service.get("hospital_name", ""):
        settings_service.set("hospital_name", Config.HOSPITAL_NAME,
                             admin_name="system")
