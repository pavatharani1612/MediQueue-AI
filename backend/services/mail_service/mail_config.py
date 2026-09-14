"""Mail Service - SMTP configuration (delegates to the central Config)."""
from __future__ import annotations

from config import Config


class MailConfig:
    ENABLED = Config.SMTP_ENABLED
    HOST = Config.SMTP_HOST
    PORT = Config.SMTP_PORT
    USER = Config.SMTP_USER
    PASSWORD = Config.SMTP_PASSWORD
    FROM = Config.SMTP_FROM
    USE_TLS = Config.SMTP_USE_TLS
    HOSPITAL = Config.HOSPITAL_NAME
