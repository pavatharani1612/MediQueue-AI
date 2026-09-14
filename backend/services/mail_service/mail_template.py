"""Mail Service - HTML templates (12-hour AM/PM times everywhere)."""
from __future__ import annotations

from time_utils import to_ampm


def wrap(title: str, body_html: str) -> str:
    from email_service import _wrap
    return _wrap(title, body_html)


def checkin_body(appointment) -> str:
    return (f"<p>Hello {appointment['pname']},</p>"
            f"<p>You are checked in for <b>Dr {appointment['dname']}</b>.</p>"
            f"<p>Token: <b>{appointment['queue_no']}</b><br>"
            f"Appointment time: <b>{to_ampm(appointment['appt_time'] or appointment['predicted_time'])}</b></p>"
            f"<p>Your live queue position is available in your dashboard.</p>")


def qr_body(appointment, checkin_url: str) -> str:
    return (f"<p>Hello {appointment['pname']},</p>"
            f"<p>Your appointment QR pass is ready.</p>"
            f"<p>Token: <b>{appointment['qr_token']}</b><br>"
            f"Date: <b>{appointment['appt_date']}</b><br>"
            f"Time: <b>{to_ampm(appointment['appt_time'] or appointment['predicted_time'])}</b></p>"
            f"<p>Show the QR at the reception desk: "
            f"<a href='{checkin_url}'>{checkin_url}</a></p>")
