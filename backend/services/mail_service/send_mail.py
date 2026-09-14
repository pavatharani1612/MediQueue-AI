"""Mail Service - sending helpers (wrapper around the existing email_service)."""
from __future__ import annotations

from services.mail_service import mail_template


def send(to: str, subject: str, title: str, body_html: str) -> bool:
    from email_service import send_email
    try:
        send_email(to, subject, mail_template.wrap(title, body_html))
        return True
    except Exception as exc:
        print("[MAIL] send failed:", exc)
        return False


def send_checkin_mail(appointment) -> bool:
    if not appointment or not appointment["email"]:
        return False
    return send(appointment["email"], "MediQueue AI - Check-in confirmed",
                "Check-in confirmed", mail_template.checkin_body(appointment))


def send_qr_mail(appointment, checkin_url: str) -> bool:
    if not appointment or not appointment["email"]:
        return False
    return send(appointment["email"], "MediQueue AI - Your appointment QR pass",
                "Appointment QR pass",
                mail_template.qr_body(appointment, checkin_url))
