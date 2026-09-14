"""History Service - paginated appointment / queue history reads."""
from __future__ import annotations

from models import query


def patient_history(patient_id, page=1, per_page=10):
    offset = (max(page, 1) - 1) * per_page
    rows = query("""SELECT a.*, d.name AS dname, d.department AS ddept,
                           q.queue_token, q.status AS qr_status
                    FROM appointments a
                    JOIN doctors d ON d.id=a.doctor_id
                    LEFT JOIN qr_codes q ON q.appointment_id=a.id
                    WHERE a.patient_id=?
                    ORDER BY a.appt_date DESC, a.id DESC
                    LIMIT ? OFFSET ?""", (patient_id, per_page, offset))
    total = query("SELECT COUNT(*) n FROM appointments WHERE patient_id=?",
                  (patient_id,), one=True)["n"]
    return rows, total


def queue_history(doctor_id=None, limit=200):
    if doctor_id:
        return query("""SELECT * FROM queue_history WHERE doctor_id=?
                        ORDER BY id DESC LIMIT ?""", (doctor_id, limit))
    return query("SELECT * FROM queue_history ORDER BY id DESC LIMIT ?", (limit,))
