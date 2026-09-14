"""Queue Service - estimated waiting time (AI first, deterministic fallback)."""
from __future__ import annotations

from models import query
from services.queue_service import queue as queue_mod


def average_consult_minutes(doctor_id: int) -> int:
    d = query("SELECT avg_time FROM doctors WHERE id=?", (doctor_id,), one=True)
    return int((d["avg_time"] if d and d["avg_time"] else 15))


def estimate_for(appt_id: int) -> dict:
    """Minutes until this patient is called, plus a 12-hour AM/PM label."""
    from datetime import datetime, timedelta

    position = queue_mod.position_of(appt_id)
    if position is None:
        return {"position": None, "wait_minutes": 0, "eta": "-"}

    a = query("SELECT doctor_id FROM appointments WHERE id=?", (appt_id,), one=True)
    per_patient = average_consult_minutes(a["doctor_id"])

    # AI prediction is preferred when the model is trained and available.
    wait = max((position - 1), 0) * per_patient
    try:
        import ai_engine
        predicted = ai_engine.predict_wait(a["doctor_id"], position)  # type: ignore[attr-defined]
        if predicted:
            wait = int(predicted)
    except Exception:
        pass

    eta = datetime.now() + timedelta(minutes=wait)
    return {
        "position": position,
        "wait_minutes": wait,
        "eta": eta.strftime("%I:%M %p").lstrip("0"),
    }
