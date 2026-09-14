"""
Appointment Service - real-time slot engine
===========================================
Slots are computed live from:
  * current date & time (past times are never offered)
  * doctor working schedule / hospital timings
  * booked appointments  (slot disappears immediately)
  * cancelled appointments (slot frees up immediately)
  * completed appointments (slot stays blocked for the past)

All labels are 12-hour AM/PM - never railway time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, date as _date, time as dtime

from models import query
from services.settings_service import settings
from time_utils import to_ampm


def _parse_hhmm(text, fallback):
    try:
        h, m = str(text).split(":")[:2]
        return dtime(int(h), int(m))
    except Exception:
        return fallback


def _booked_times(doctor_id, on_date) -> set[str]:
    """Times that are unavailable: active bookings + completed consultations."""
    rows = query("""SELECT slot_time, predicted_time FROM appointments
                    WHERE doctor_id=? AND appt_date=?
                      AND status NOT IN ('Cancelled','Missed')""",
                 (doctor_id, on_date))
    return {(r["slot_time"] or r["predicted_time"])[:5]
            for r in rows if (r["slot_time"] or r["predicted_time"])}


def available_slots(doctor_id: int, on_date: str | None = None) -> dict:
    """Return the live slot board for a doctor on a given date."""
    on_date = on_date or datetime.now().strftime("%Y-%m-%d")
    interval = max(int(settings.get("slot_interval_min", "15") or 15), 5)
    lead = int(settings.get("min_lead_minutes", "10") or 10)
    start = _parse_hhmm(settings.get("working_start", "09:00"), dtime(9, 0))
    end = _parse_hhmm(settings.get("working_end", "21:00"), dtime(21, 0))

    doctor = query("SELECT id, name, available FROM doctors WHERE id=?",
                   (doctor_id,), one=True)
    booked = _booked_times(doctor_id, on_date)

    now = datetime.now()
    is_today = on_date == now.strftime("%Y-%m-%d")
    earliest = now + timedelta(minutes=lead)

    day = datetime.strptime(on_date, "%Y-%m-%d").date()
    cur = datetime.combine(day, start)
    end_dt = datetime.combine(day, end)

    slots, available = [], []
    while cur < end_dt:
        hhmm = cur.strftime("%H:%M")
        taken = hhmm in booked
        past = bool(is_today and cur <= earliest)
        item = {
            "time": hhmm,                 # internal 24h key
            "label": to_ampm(hhmm),       # user facing 12h AM/PM
            "taken": taken,
            "past": past,
            "available": (not taken) and (not past),
        }
        slots.append(item)
        if item["available"]:
            available.append(item)
        cur += timedelta(minutes=interval)

    return {
        "date": on_date,
        "doctor_id": doctor_id,
        "doctor": doctor["name"] if doctor else None,
        "doctor_available": bool(doctor["available"]) if doctor else False,
        "interval": interval,
        "now": now.strftime("%I:%M %p").lstrip("0"),
        "slots": slots,                       # full board (UI greys out past/taken)
        "available_slots": available,         # only bookable slots
        "available_count": len(available),
    }


def is_slot_free(doctor_id: int, on_date: str, hhmm: str) -> bool:
    board = available_slots(doctor_id, on_date)
    return any(s["time"] == (hhmm or "")[:5] and s["available"] for s in board["slots"])
