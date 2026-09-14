"""APScheduler background jobs.
- 10-min pre-consultation reminders
- Missed-appointment sweep
- (Phase 2) Auto-cancel re-appointments & no-response cancellations
"""
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from models import query, execute
from email_service import mail_reminder, mail_missed
from config import Config

_scheduler = None


def _reminder_job(app):
    with app.app_context():
        now = datetime.now()
        rows = query("""SELECT a.*, p.name AS pname, p.email,
                               d.name AS dname
                        FROM appointments a
                        JOIN patients p ON p.id=a.patient_id
                        JOIN doctors  d ON d.id=a.doctor_id
                        WHERE a.status='Waiting' AND a.reminder_sent=0
                          AND a.appt_date=?""", (now.strftime("%Y-%m-%d"),))
        for a in rows:
            try:
                hh, mm = a["predicted_time"].split(":")
                pred_dt = now.replace(hour=int(hh), minute=int(mm),
                                      second=0, microsecond=0)
                if 0 <= (pred_dt - now).total_seconds() <= Config.REMINDER_MINUTES_BEFORE*60:
                    mail_reminder({"name":a["pname"],"email":a["email"]},
                                  {"name":a["dname"]}, a["predicted_time"], a["id"])
                    execute("UPDATE appointments SET reminder_sent=1 WHERE id=?",(a["id"],))
            except Exception:
                pass


def _missed_job(app):
    with app.app_context():
        now = datetime.now()
        rows = query("""SELECT a.*, p.email, p.name AS pname
                        FROM appointments a JOIN patients p ON p.id=a.patient_id
                        WHERE a.status='Waiting' AND a.appt_date=?""",
                     (now.strftime("%Y-%m-%d"),))
        for a in rows:
            try:
                hh,mm = a["predicted_time"].split(":")
                pred_dt = now.replace(hour=int(hh), minute=int(mm),
                                      second=0, microsecond=0)
                if (now - pred_dt).total_seconds() > Config.MISSED_GRACE_MINUTES*60:
                    execute("UPDATE appointments SET status='Missed' WHERE id=?",(a["id"],))
                    execute("""INSERT INTO notifications(role,user_id,title,message,created_at)
                               VALUES('admin',0,'Missed Appointment',?,?)""",
                            (f"{a['pname']} missed appointment {a['queue_no']}", now.isoformat()))
                    mail_missed({"name":a["pname"],"email":a["email"]}, a["id"])
            except Exception:
                pass


def start_scheduler(app, extra_jobs=None):
    """extra_jobs: list of (job_id, callable, interval_minutes)."""
    global _scheduler
    if _scheduler: return
    s = BackgroundScheduler(daemon=True)
    s.add_job(lambda: _reminder_job(app), "interval", minutes=1, id="reminders")
    s.add_job(lambda: _missed_job(app),   "interval", minutes=2, id="missed")
    for jid, fn, mins in (extra_jobs or []):
        s.add_job(fn, "interval", minutes=mins, id=jid)
    s.start()
    _scheduler = s
