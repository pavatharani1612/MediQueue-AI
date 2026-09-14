# =========================================================
# MediQueue AI - Configuration
# SMTP values can be overridden via environment variables.
# =========================================================
import os


def _envbool(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# Project root = two levels up from backend/core
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATABASE_DIR = os.path.join(BASE_DIR, "database")
DATASET_DIR = os.path.join(BASE_DIR, "datasets")
ML_DIR = os.path.join(BASE_DIR, "ml_models")
for _d in (DATABASE_DIR, DATASET_DIR, ML_DIR,
           os.path.join(BASE_DIR, "logs"), os.path.join(BASE_DIR, "exports"),
           os.path.join(BASE_DIR, "reports"), os.path.join(BASE_DIR, "uploads")):
    os.makedirs(_d, exist_ok=True)


class Config:
    # ---- Enterprise paths ----
    BASE_DIR = BASE_DIR
    TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend", "templates")
    STATIC_DIR = os.path.join(BASE_DIR, "frontend", "static")
    UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
    EXPORT_DIR = os.path.join(BASE_DIR, "exports")
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    DATASET_PATH = os.path.join(DATASET_DIR, "dataset.csv")
    MODEL_PATH = os.path.join(ML_DIR, "queue_model.pkl")
    MODEL_META_PATH = os.path.join(ML_DIR, "queue_model_meta.pkl")
    METRICS_PATH = os.path.join(ML_DIR, "ai_metrics.pkl")
    HOSPITAL_ID = os.environ.get("HOSPITAL_ID", "MQ-HOSP-001")
    SECRET_KEY = os.environ.get("SECRET_KEY", "mediqueue2026@bca#project$secure987")
    DATABASE = os.environ.get("DATABASE_PATH", os.path.join(DATABASE_DIR, "mediqueue.db"))

    # ---- Admin (predefined) ----
    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD = "admin123"

    # ---- SMTP ----
    # To send REAL emails via Gmail:
    #   1. Enable 2-Step Verification on your Google account.
    #   2. Create a Gmail App Password: https://myaccount.google.com/apppasswords
    #   3. Set env vars OR edit the defaults below:
    #        SMTP_USER      = your gmail address
    #        SMTP_PASSWORD  = 16-char app password (no spaces)
    #        SMTP_FROM      = "Your Name <your@gmail.com>"
    SMTP_ENABLED  = _envbool("SMTP_ENABLED", True)
    SMTP_HOST     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER     = os.environ.get("SMTP_USER", "mediqueue.ai@gmail.com")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "obzosrixvwdkunhn")
    SMTP_FROM     = os.environ.get("SMTP_FROM", "MediQueue AI <mediqueue.ai@gmail.com>")
    SMTP_USE_TLS  = _envbool("SMTP_USE_TLS", True)

    # ---- App ----
    HOSPITAL_NAME = "MediQueue AI Hospital"
    HOSPITAL_EMAIL = "info@mediqueue.ai"
    HOSPITAL_ADDRESS = "MediQueue AI Hospital, Chennai, India"
    EMERGENCY_NUMBER = "+91-9092983363"
    WORKING_HOURS = "Mon-Sat 9:00 AM - 9:00 PM"

    # ---- AI ----
    DATASET_SIZE = 500
    REMINDER_MINUTES_BEFORE = 10
    MISSED_GRACE_MINUTES = 20
