"""Prediction Service - facade over the existing AI/ML engine.

The trained model files live in ml_models/. This wrapper keeps callers
independent from the underlying implementation and records every
prediction into prediction_history for accuracy reporting.
"""
from __future__ import annotations

from datetime import datetime

from models import execute, query, get_conn


def init_tables():
    with get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS prediction_history(
            id INTEGER PRIMARY KEY,
            appointment_id INTEGER,
            doctor_id INTEGER,
            predicted_minutes INTEGER,
            actual_minutes INTEGER,
            error_minutes INTEGER,
            created_at TEXT,
            FOREIGN KEY(appointment_id) REFERENCES appointments(id))""")


def record(appointment_id, doctor_id, predicted_minutes, actual_minutes=None):
    error = (abs(actual_minutes - predicted_minutes)
             if actual_minutes is not None and predicted_minutes is not None else None)
    return execute("""INSERT INTO prediction_history(appointment_id,doctor_id,
                        predicted_minutes,actual_minutes,error_minutes,created_at)
                      VALUES(?,?,?,?,?,?)""",
                   (appointment_id, doctor_id, predicted_minutes, actual_minutes,
                    error, datetime.now().isoformat(timespec="seconds")))


def accuracy():
    """Percentage of predictions within a 5 minute tolerance."""
    row = query("""SELECT COUNT(*) n,
                          SUM(CASE WHEN error_minutes<=5 THEN 1 ELSE 0 END) good
                   FROM prediction_history WHERE error_minutes IS NOT NULL""", one=True)
    if not row or not row["n"]:
        try:
            import ai_engine
            metrics = ai_engine.compute_metrics() or {}
            r2 = metrics.get("r2")
            if r2 is not None:
                return round(max(min(float(r2), 1.0), 0) * 100, 1)
        except Exception:
            pass
        return None
    return round((row["good"] or 0) * 100.0 / row["n"], 1)
