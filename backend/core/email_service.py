"""SMTP email service. Logs every mail to email_logs table.
Set Config.SMTP_ENABLED=True and fill credentials to actually send.
"""
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from config import Config
from models import execute


def _log(to, subject, body, status):
    execute("""INSERT INTO email_logs(recipient,subject,body,status,created_at)
               VALUES(?,?,?,?,?)""",
            (to, subject, body, status, datetime.now().isoformat()))


def _build_msg(to, subject, body):
    msg = MIMEMultipart()
    msg["From"] = Config.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html", "utf-8"))
    return msg


def _smtp_send(to, msg):
    ctx = ssl.create_default_context()
    port = int(Config.SMTP_PORT)
    # SSL (port 465) vs STARTTLS (587/25)
    if port == 465:
        with smtplib.SMTP_SSL(Config.SMTP_HOST, port, context=ctx, timeout=20) as s:
            s.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            s.sendmail(Config.SMTP_USER, to, msg.as_string())
    else:
        with smtplib.SMTP(Config.SMTP_HOST, port, timeout=20) as s:
            s.ehlo()
            if Config.SMTP_USE_TLS:
                s.starttls(context=ctx)
                s.ehlo()
            s.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            s.sendmail(Config.SMTP_USER, to, msg.as_string())


def send_email(to, subject, body):
    if not to:
        _log(to or "-", subject, body, "SKIPPED (no recipient)")
        return False
    if not Config.SMTP_ENABLED:
        _log(to, subject, body, "LOGGED (SMTP disabled)")
        return True
    if not Config.SMTP_USER or not Config.SMTP_PASSWORD:
        _log(to, subject, body, "FAIL: SMTP_USER/SMTP_PASSWORD not configured")
        return False
    # Gmail app-passwords are 16 chars w/o spaces; strip user typos
    pw = (Config.SMTP_PASSWORD or "").replace(" ", "")
    Config.SMTP_PASSWORD = pw
    try:
        msg = _build_msg(to, subject, body)
        _smtp_send(to, msg)
        _log(to, subject, body, "SENT")
        return True
    except smtplib.SMTPAuthenticationError as e:
        hint = ("Gmail rejected the login. Use a Gmail App Password "
                "(https://myaccount.google.com/apppasswords), not your regular password. "
                "Set SMTP_USER and SMTP_PASSWORD in config.py or environment variables.")
        _log(to, subject, body, f"FAIL: AUTH ({e.smtp_code}): {hint}")
        return False
    except (smtplib.SMTPException, OSError) as e:
        _log(to, subject, body, f"FAIL: {type(e).__name__}: {e}")
        return False
    except Exception as e:
        _log(to, subject, body, f"FAIL: {type(e).__name__}: {e}")
        return False


# ---- Templated helpers ----
def _wrap(title, html):
    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;background:#f2f7ff;padding:24px">
      <div style="max-width:560px;margin:auto;background:#fff;border-radius:14px;
                  box-shadow:0 8px 24px rgba(30,80,200,.1);overflow:hidden">
        <div style="background:linear-gradient(135deg,#2563eb,#38bdf8);
                    color:#fff;padding:20px 24px">
          <h2 style="margin:0">🏥 MediQueue AI</h2>
          <div style="opacity:.9;font-size:13px">{title}</div>
        </div>
        <div style="padding:22px 24px;color:#1e293b;line-height:1.55">{html}</div>
        <div style="padding:14px 24px;background:#f8fafc;color:#64748b;
                    font-size:12px;text-align:center">
          Thank you — MediQueue AI Hospital Management System
        </div>
      </div></div>"""


def mail_registration(p):
    send_email(p["email"], "Welcome to MediQueue AI",
        _wrap("Registration Successful",
              f"<p>Hello <b>{p['name']}</b>,</p>"
              "<p>Your MediQueue AI account has been created successfully. "
              "You can now book appointments and receive smart queue predictions.</p>"))

def _rget(obj, key, default=None):
    """Safely read a key from either a dict or a sqlite3.Row (or None)."""
    if obj is None:
        return default
    try:
        if isinstance(obj, dict):
            return obj.get(key, default)
        # sqlite3.Row supports item access + keys()
        return obj[key] if key in obj.keys() else default
    except Exception:
        return default


def mail_appointment(p, d, appt, pred_time):
    appt_id = appt.get("id") if isinstance(appt, dict) else None
    if appt_id is None:
        pid = _rget(p, "id"); did = _rget(d, "id")
        if pid and did:
            from models import query as _q
            row = _q("SELECT id FROM appointments WHERE patient_id=? AND doctor_id=? ORDER BY id DESC LIMIT 1",
                     (pid, did), one=True)
            appt_id = row["id"] if row else ""
        else:
            appt_id = ""
    import hmac, hashlib
    base = "http://127.0.0.1:5000"
    sig = hmac.new(Config.SECRET_KEY.encode(), f"wait5:{appt_id}".encode(),
                   hashlib.sha256).hexdigest()[:24]
    # Silent endpoint: no page, no login, no redirect. Returns HTTP 204.
    wait5_url = f"{base}/api/wait5/{appt_id}?t={sig}"
    reappt_url = f"{base}/patient/reappoint-request/{appt_id}"
    btn = (
        f"<div style='margin-top:22px;text-align:center'>"
        f"<a href='{wait5_url}' "
        f"style='display:inline-block;padding:12px 22px;margin:6px;"
        f"background:linear-gradient(135deg,#2563eb,#38bdf8);color:#fff;"
        f"border-radius:8px;text-decoration:none;font-weight:600'>"
        f"⏱️ Waiting 5 Minutes</a>"
        f"<a href='{reappt_url}' "
        f"style='display:inline-block;padding:12px 22px;margin:6px;"
        f"background:#f59e0b;color:#fff;border-radius:8px;text-decoration:none;"
        f"font-weight:600'>🔁 Reappointment</a></div>"
    )
    # Secure one-click links + digital QR pass (added in the AI upgrade)
    action_token = _rget(appt, "action_token")
    extra = ""
    if action_token:
        def _lnk(url, label, bg):
            return (f"<a href='{url}' style='display:inline-block;padding:11px 20px;margin:5px;"
                    f"background:{bg};color:#fff;border-radius:8px;text-decoration:none;"
                    f"font-weight:600'>{label}</a>")
        extra = (
            "<div style='margin-top:10px;text-align:center'>"
            + _lnk(f"{base}/appointment/action/{action_token}/confirm", "✅ Confirm", "#16a34a")
            + _lnk(f"{base}/appointment/action/{action_token}/reschedule", "🔄 Reschedule", "#0ea5e9")
            + _lnk(f"{base}/appointment/action/{action_token}/cancel", "✖ Cancel", "#ef4444")
            + "</div>"
        )
    if appt_id:
        extra += (f"<p style='text-align:center;margin-top:14px'>"
                  f"<a href='{base}/patient/appointment/{appt_id}/pass' "
                  f"style='color:#2563eb;font-weight:600'>📲 Open your QR check-in pass</a></p>")

    send_email(p["email"], "Appointment Confirmed - MediQueue AI",
        _wrap("Appointment Confirmed",
              f"<p>Hello <b>{p['name']}</b>,</p>"
              f"<p>Your appointment with <b>{d['name']}</b> ({d['department']}) is confirmed.</p>"
              f"<ul><li>Queue No: <b>{appt['queue_no']}</b></li>"
              f"<li>Predicted time: <b>{pred_time}</b></li>"
              f"<li>Reason: {appt['reason']}</li></ul>"
              + btn + extra))

def mail_reminder(p, d, pred_time, appt_id):
    send_email(p["email"], "Your consultation is in 10 minutes",
        _wrap("Pre-consultation Reminder",
              f"<p>Hello <b>{p['name']}</b>,</p>"
              f"<p>Your consultation with <b>{d['name']}</b> is expected at "
              f"<b>{pred_time}</b>.</p>"
              "<p>Please choose:</p>"
              f"<p><a href='http://127.0.0.1:5000/patient/response/{appt_id}/continue'>Continue Waiting</a> · "
              f"<a href='http://127.0.0.1:5000/patient/request/{appt_id}/reappoint'>Request Re-appointment</a> · "
              f"<a href='http://127.0.0.1:5000/patient/request/{appt_id}/cancel'>Cancel</a></p>"))

def mail_missed(p, appt_id):
    send_email(p["email"], "Missed Appointment - Action Required",
        _wrap("Missed Appointment",
              f"<p>Hello <b>{p['name']}</b>,</p>"
              "<p>Your consultation time has expired. Please choose:</p>"
              f"<p><a href='http://127.0.0.1:5000/patient/request/{appt_id}/reappoint'>Re-appointment</a> · "
              f"<a href='http://127.0.0.1:5000/patient/request/{appt_id}/cancel'>Cancel</a></p>"))

HOSPITAL_NAME = "MediQueue AI Hospital"

def mail_delay(p, d, new_time, delay_minutes=None, reason=None,
               predicted_wait=None):
    """Notify a patient that the doctor added a delay to the queue."""
    dur = f"{delay_minutes} min" if delay_minutes is not None else "extended"
    wait_html = (f"<li>Updated Predicted Waiting Time: <b>{predicted_wait} min</b></li>"
                 if predicted_wait is not None else "")
    reason_html = f"<li>Delay Reason: <b>{reason}</b></li>" if reason else ""
    send_email(p["email"], "Queue Updated - New Predicted Time",
        _wrap("Queue Updated",
              f"<p>Hello <b>{p['name']}</b>,</p>"
              f"<p>Your doctor has added a delay to the queue. Here are the updated details:</p>"
              f"<ul>"
              f"<li>Doctor: <b>{d['name']}</b></li>"
              f"<li>Department: <b>{d['department']}</b></li>"
              f"<li>Delay Duration: <b>{dur}</b></li>"
              f"<li>Updated Appointment Time: <b>{new_time}</b></li>"
              f"{wait_html}"
              f"{reason_html}"
              f"<li>Hospital: <b>{HOSPITAL_NAME}</b></li>"
              f"</ul>"
              f"<p>Your appointment status remains unchanged — thank you for your patience.</p>"))

def mail_cancel(p, d):
    send_email(p["email"], "Appointment Cancelled",
        _wrap("Cancelled",
              f"<p>Hello <b>{p['name']}</b>, your appointment with {d['name']} "
              "has been cancelled.</p>"))

def mail_complete(p, d):
    send_email(p["email"], "Consultation Completed",
        _wrap("Consultation Completed",
              f"<p>Hello <b>{p['name']}</b>,</p>"
              f"<p>Your consultation with <b>{d['name']}</b> has been completed. "
              "Please check your dashboard for prescription and notes.</p>"))
