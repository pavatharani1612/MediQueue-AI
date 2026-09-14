"""QR Service - persistence layer (qr_codes table)."""
from __future__ import annotations

from datetime import datetime

from models import query, execute, get_conn


def init_tables():
    with get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS qr_codes(
            id INTEGER PRIMARY KEY,
            appointment_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL,
            queue_token TEXT UNIQUE,
            booking_date TEXT,
            hospital_id TEXT,
            signature TEXT,
            payload TEXT,
            status TEXT DEFAULT 'Active',      -- Active / Used / Expired
            scan_count INTEGER DEFAULT 0,
            last_scanned_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(appointment_id) REFERENCES appointments(id),
            FOREIGN KEY(patient_id)     REFERENCES patients(id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_qr_token ON qr_codes(queue_token)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_qr_appt  ON qr_codes(appointment_id)")


def save_qr(appointment_id, patient_id, queue_token, booking_date,
            hospital_id, signature, payload):
    now = datetime.now().isoformat(timespec="seconds")
    existing = query("SELECT id FROM qr_codes WHERE appointment_id=?",
                     (appointment_id,), one=True)
    if existing:
        execute("""UPDATE qr_codes SET queue_token=?, booking_date=?, hospital_id=?,
                          signature=?, payload=?, updated_at=? WHERE id=?""",
                (queue_token, booking_date, hospital_id, signature, payload,
                 now, existing["id"]))
        return existing["id"]
    return execute("""INSERT INTO qr_codes(appointment_id,patient_id,queue_token,
                        booking_date,hospital_id,signature,payload,status,
                        created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,'Active',?,?)""",
                   (appointment_id, patient_id, queue_token, booking_date,
                    hospital_id, signature, payload, now, now))


def get_by_token(token):
    return query("SELECT * FROM qr_codes WHERE queue_token=? COLLATE NOCASE",
                 (token,), one=True)


def get_by_appointment(appointment_id):
    return query("SELECT * FROM qr_codes WHERE appointment_id=?",
                 (appointment_id,), one=True)


def mark_scanned(token):
    execute("""UPDATE qr_codes SET scan_count=COALESCE(scan_count,0)+1,
                      last_scanned_at=?, status='Used' WHERE queue_token=?""",
            (datetime.now().isoformat(timespec="seconds"), token))


def expire(token, reason="completed"):
    """QR validity ends automatically once the appointment is finished."""
    execute("UPDATE qr_codes SET status='Expired', updated_at=? WHERE queue_token=?",
            (datetime.now().isoformat(timespec="seconds"), token))
    return reason


def expire_finished_appointments():
    """Background sweep: expire QRs of completed / cancelled / missed visits."""
    rows = query("""SELECT q.queue_token FROM qr_codes q
                    JOIN appointments a ON a.id=q.appointment_id
                    WHERE q.status != 'Expired'
                      AND a.status IN ('Completed','Cancelled','Missed')""")
    for r in rows:
        expire(r["queue_token"])
    return len(rows)


def scan_count_today():
    today = datetime.now().strftime("%Y-%m-%d")
    row = query("""SELECT COUNT(*) n FROM checkins WHERE date=? AND method='qr'""",
                (today,), one=True)
    return row["n"] if row else 0
