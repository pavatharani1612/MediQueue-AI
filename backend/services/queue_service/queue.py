"""Queue Service - live queue reads (thin, cached-friendly wrappers)."""
from __future__ import annotations

from datetime import datetime

from models import query, get_conn
from services.realtime_service import bus

ACTIVE = ("Waiting", "Consulting", "Emergency", "Emergency-PendingVerification")


def init_tables():
    with get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS queue_history(
            id INTEGER PRIMARY KEY,
            appointment_id INTEGER,
            doctor_id INTEGER,
            patient_id INTEGER,
            position INTEGER,
            status TEXT,
            waiting_minutes INTEGER,
            recorded_at TEXT,
            FOREIGN KEY(appointment_id) REFERENCES appointments(id))""")


def live_queue(doctor_id: int, on_date: str | None = None):
    on_date = on_date or datetime.now().strftime("%Y-%m-%d")
    placeholders = ",".join("?" for _ in ACTIVE)
    rows = query(f"""SELECT a.*, p.name AS patient_name
                     FROM appointments a JOIN patients p ON p.id=a.patient_id
                     WHERE a.doctor_id=? AND a.appt_date=?
                       AND a.status IN ({placeholders})
                     ORDER BY CASE WHEN a.priority='Emergency' THEN 0
                                   WHEN a.priority='Senior' THEN 1 ELSE 2 END,
                              a.predicted_time, a.id""",
                 (doctor_id, on_date, *ACTIVE))
    out = []
    for i, r in enumerate(rows, start=1):
        item = dict(r)
        item["position"] = i
        out.append(item)
    return out


def position_of(appt_id: int):
    a = query("SELECT doctor_id, appt_date FROM appointments WHERE id=?",
              (appt_id,), one=True)
    if not a:
        return None
    for item in live_queue(a["doctor_id"], a["appt_date"]):
        if item["id"] == appt_id:
            return item["position"]
    return None


def snapshot_history(doctor_id: int):
    """Persist the current queue shape into queue_history (audit / analytics)."""
    from models import execute
    now = datetime.now().isoformat(timespec="seconds")
    for item in live_queue(doctor_id):
        execute("""INSERT INTO queue_history(appointment_id,doctor_id,patient_id,
                     position,status,waiting_minutes,recorded_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (item["id"], doctor_id, item["patient_id"], item["position"],
                 item["status"], item.get("delay_minutes") or 0, now))


def broadcast(doctor_id: int):
    bus.publish("queue", {"doctor_id": doctor_id,
                          "size": len(live_queue(doctor_id))})
