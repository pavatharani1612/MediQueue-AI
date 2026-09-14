"""
MediQueue AI - AI Engine
========================
Random Forest Regression engine that predicts patient waiting time.

This module *extends* the original ``prediction.py`` model instead of
replacing it:

*  the same Random Forest Regressor / dataset is reused,
*  richer evaluation metrics are added (MAE, RMSE, R2, feature importance),
*  a **context aware** predictor blends the ML output with the real, live
   state of the SQLite queue (patients ahead, doctor delay, emergency load,
   real average consultation duration, time of day ...).

Nothing here mutates the database schema - it only reads it.
"""
from __future__ import annotations

import os
from config import Config
import math
import joblib
import numpy as np
from datetime import datetime, timedelta

from models import query
from dataset_generator import DEPTS, BASE
import prediction as _base_model

METRICS_PATH = Config.METRICS_PATH if hasattr(Config, "METRICS_PATH") else "ai_metrics.pkl"

# Absolute safety rails so the UI never shows nonsense
MIN_WAIT = 0
MAX_WAIT = 8 * 60          # 8 hours
EMERGENCY_MAX_WAIT = 10    # minutes (kept identical to queue_manager)

OPEN_STATUSES = ("Waiting", "InProgress", "Emergency-PendingVerification")


# ---------------------------------------------------------------- helpers
def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _clamp(value, low=MIN_WAIT, high=MAX_WAIT):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if math.isnan(value) or math.isinf(value):
        value = 0.0
    return int(max(low, min(high, round(value))))


# ---------------------------------------------------------------- metrics
def compute_metrics(force: bool = False) -> dict:
    """Train / evaluate the Random Forest and cache MAE, RMSE, R2 ..."""
    if not force and os.path.exists(METRICS_PATH):
        try:
            cached = joblib.load(METRICS_PATH)
            if cached.get("rmse") is not None:
                return cached
        except Exception:
            pass

    import pandas as pd
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import (mean_absolute_error, mean_squared_error,
                                 r2_score)

    if os.path.exists(Config.DATASET_PATH):
        df = pd.read_csv(Config.DATASET_PATH)
    else:
        from dataset_generator import generate_dataset
        df = generate_dataset(path=Config.DATASET_PATH)

    features = ["dept_id", "patient_count", "avg_time", "delay", "late", "emergency"]
    df = df.copy()
    df["dept_id"] = df["department"].map({d: i for i, d in enumerate(DEPTS)}).fillna(0)
    X = df[features]
    y = df["wait_time"]

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42)
    model = RandomForestRegressor(n_estimators=200, random_state=42, n_jobs=-1)
    model.fit(Xtr, ytr)
    pred = model.predict(Xte)

    mse = float(mean_squared_error(yte, pred))
    metrics = {
        "model": "Random Forest Regression",
        "algorithm": "sklearn.ensemble.RandomForestRegressor (200 trees)",
        "mae": round(float(mean_absolute_error(yte, pred)), 2),
        "rmse": round(float(math.sqrt(mse)), 2),
        "mse": round(mse, 2),
        "r2": round(float(r2_score(yte, pred)), 4),
        "samples": int(len(df)),
        "train_samples": int(len(Xtr)),
        "test_samples": int(len(Xte)),
        "features": features,
        "importance": [round(float(v), 4) for v in model.feature_importances_],
        "actual": [round(float(v), 1) for v in list(yte)[:60]],
        "predicted": [round(float(v), 1) for v in list(pred)[:60]],
        "trained_at": datetime.now().isoformat(timespec="seconds"),
    }

    # Keep the shared model file in sync so predict_wait() benefits too.
    try:
        joblib.dump(model, _base_model.MODEL_PATH)
    except Exception:
        pass
    try:
        joblib.dump(metrics, METRICS_PATH)
    except Exception:
        pass
    return metrics


def get_metrics(force: bool = False) -> dict:
    """Public accessor used by the Admin AI Performance dashboard."""
    metrics = compute_metrics(force=force)

    # ---- Live accuracy measured against real completed appointments ----
    try:
        rows = query("""SELECT predicted_wait_time, actual_start, created_at
                    FROM appointments
                    WHERE status='Completed' AND actual_start IS NOT NULL
                      AND predicted_wait_time IS NOT NULL
                    ORDER BY id DESC LIMIT 200""")
    except Exception:
        rows = []
    pairs = []
    for r in rows:
        try:
            start = datetime.fromisoformat(r["actual_start"])
            created = datetime.fromisoformat(r["created_at"])
            actual = (start - created).total_seconds() / 60.0
            if 0 <= actual <= MAX_WAIT:
                pairs.append((actual, float(r["predicted_wait_time"])))
        except Exception:
            continue

    live = {"samples": len(pairs), "mae": None, "rmse": None,
            "actual": [], "predicted": []}
    if pairs:
        errs = [abs(a - p) for a, p in pairs]
        live["mae"] = round(sum(errs) / len(errs), 2)
        live["rmse"] = round(math.sqrt(sum(e * e for e in errs) / len(errs)), 2)
        live["actual"] = [round(a, 1) for a, _ in pairs[:60]]
        live["predicted"] = [round(p, 1) for _, p in pairs[:60]]
    metrics = dict(metrics)
    metrics["live"] = live
    metrics["explanation"] = (
        "MediQueue AI uses Random Forest Regression to predict how long a "
        "patient will wait. The model learns from queue length, department "
        "average consultation time, accumulated doctor delay, late arrivals "
        "and emergency load, then the prediction is adjusted in real time "
        "using the live SQLite queue state."
    )
    return metrics


# ------------------------------------------------- context aware predict
def department_avg_consult(department: str) -> float:
    """Real average consultation duration from completed appointments."""
    rows = query("""SELECT actual_start, actual_end FROM appointments
                    WHERE department=? AND status='Completed'
                      AND actual_start IS NOT NULL AND actual_end IS NOT NULL
                    ORDER BY id DESC LIMIT 80""", (department,))
    durations = []
    for r in rows:
        try:
            d = (datetime.fromisoformat(r["actual_end"])
                 - datetime.fromisoformat(r["actual_start"])).total_seconds() / 60.0
            if 1 <= d <= 180:
                durations.append(d)
        except Exception:
            continue
    if durations:
        return round(sum(durations) / len(durations), 1)
    return float(BASE.get(department, 15))


def queue_context(doctor_id: int, appt_id: int | None = None) -> dict:
    """Collect everything the AI needs about the *live* queue."""
    doc = query("SELECT * FROM doctors WHERE id=?", (doctor_id,), one=True)
    department = doc["department"] if doc else "General Medicine"

    ordered = query("""SELECT * FROM appointments
                       WHERE doctor_id=? AND appt_date=?
                         AND status IN ('Waiting','InProgress','Emergency-PendingVerification')
                       ORDER BY
                         CASE WHEN status='InProgress' THEN 0 ELSE 1 END,
                         CASE WHEN priority='Emergency' AND emergency_verified=1 THEN 0
                              WHEN priority='Emergency' THEN 1
                              WHEN priority='Urgent' THEN 2
                              ELSE 3 END,
                         id""",
                    (doctor_id, _today()))

    ahead, position, target = 0, None, None
    for i, row in enumerate(ordered):
        if appt_id is not None and row["id"] == appt_id:
            position, ahead, target = i + 1, i, row
            break
    if position is None:
        ahead = len(ordered)
        position = len(ordered) + 1

    delay_row = query("""SELECT COALESCE(MAX(delay_minutes),0) d FROM appointments
                         WHERE doctor_id=? AND appt_date=?""",
                      (doctor_id, _today()), one=True)
    completed = query("""SELECT COUNT(*) c FROM appointments
                         WHERE doctor_id=? AND appt_date=? AND status='Completed'""",
                      (doctor_id, _today()), one=True)["c"]
    cancelled = query("""SELECT COUNT(*) c FROM appointments
                         WHERE doctor_id=? AND appt_date=? AND status IN('Cancelled','Missed')""",
                      (doctor_id, _today()), one=True)["c"]
    emergencies = sum(1 for r in ordered[:max(ahead, 0)]
                      if (r["priority"] or "") == "Emergency")
    in_progress = next((r for r in ordered if r["status"] == "InProgress"), None)

    return {
        "doctor": doc,
        "department": department,
        "queue": ordered,
        "queue_length": len(ordered),
        "patients_ahead": max(0, ahead),
        "position": position,
        "appointment": target,
        "delay_minutes": int(delay_row["d"] or 0),
        "completed_today": completed,
        "cancelled_today": cancelled,
        "emergencies_ahead": emergencies,
        "in_progress": in_progress,
        "avg_consult": department_avg_consult(department),
        "doctor_available": bool(doc["available"]) if doc else False,
    }


def _model_wait(department, patient_count, delay, late, emergency):
    try:
        out = _base_model.predict_wait(department, max(1, patient_count),
                                       delay=delay, late=late, emergency=emergency)
        return float(out["wait_minutes"]), float(out["consult_minutes"]), float(out["confidence"])
    except Exception:
        avg = float(BASE.get(department, 15))
        return patient_count * avg + delay, avg, 0.6


def predict_wait_context(doctor_id: int, appt_id: int | None = None,
                         priority: str = "Normal", ctx: dict | None = None) -> dict:
    """Context aware waiting-time prediction (minutes) for one appointment.

    Blends the Random Forest output with a deterministic queue simulation
    based on the doctor's *real* average consultation time.
    """
    ctx = ctx or queue_context(doctor_id, appt_id)
    department = ctx["department"]
    ahead = ctx["patients_ahead"]
    avg_consult = ctx["avg_consult"]
    delay = ctx["delay_minutes"]

    ml_wait, ml_consult, confidence = _model_wait(
        department, ahead + 1, delay, ctx["cancelled_today"], ctx["emergencies_ahead"])

    # Deterministic simulation: remaining time of the current consultation
    # plus the expected duration of every patient ahead.
    remaining_current = 0.0
    cur = ctx["in_progress"]
    if cur is not None and cur["id"] != appt_id and cur["actual_start"]:
        try:
            elapsed = (datetime.now()
                       - datetime.fromisoformat(cur["actual_start"])).total_seconds() / 60.0
            remaining_current = max(0.0, avg_consult - elapsed)
        except Exception:
            remaining_current = avg_consult / 2

    sim_wait = remaining_current + max(0, ahead) * avg_consult + delay
    if not ctx["doctor_available"]:
        sim_wait += 10  # doctor currently unavailable -> small penalty

    # Blend: ML learns the patterns, the simulation keeps it grounded.
    wait = 0.45 * ml_wait + 0.55 * sim_wait

    if (priority or "Normal") == "Emergency":
        wait = min(wait, EMERGENCY_MAX_WAIT)
        avg_consult = min(avg_consult, 10)
    elif (priority or "Normal") == "Urgent":
        wait = min(wait, max(10.0, sim_wait * 0.6))

    if ahead == 0 and cur is None:
        wait = min(wait, max(2.0, avg_consult / 4))

    wait = _clamp(wait)
    predicted_at = datetime.now() + timedelta(minutes=wait)

    return {
        "wait_minutes": wait,
        "consult_minutes": int(round(avg_consult)),
        "confidence": round(float(confidence), 2),
        "patients_ahead": ahead,
        "position": ctx["position"],
        "queue_length": ctx["queue_length"],
        "delay_minutes": delay,
        "emergencies_ahead": ctx["emergencies_ahead"],
        "predicted_consult_at": predicted_at,
        "predicted_consult_time": predicted_at.strftime("%I:%M %p").lstrip("0"),
    }


def repredict_doctor_queue(doctor_id: int) -> int:
    """Recalculate predicted wait for every open appointment of a doctor.

    Called whenever the queue changes (join, check-in, cancel, missed,
    emergency, consultation start / complete, delay, reappointment).
    """
    from models import execute
    ctx = queue_context(doctor_id)
    updated = 0
    for row in ctx["queue"]:
        sub = dict(ctx)
        idx = next((i for i, r in enumerate(ctx["queue"]) if r["id"] == row["id"]), 0)
        sub["patients_ahead"] = idx
        sub["position"] = idx + 1
        sub["appointment"] = row
        pred = predict_wait_context(doctor_id, row["id"],
                                    priority=row["priority"] or "Normal", ctx=sub)
        execute("""UPDATE appointments
                   SET predicted_wait_time=?, predicted_time=?
                   WHERE id=?""",
                (pred["wait_minutes"],
                 pred["predicted_consult_at"].strftime("%H:%M"),
                 row["id"]))
        updated += 1
    return updated


# --------------------------------------------------------------- routes
def register_ai_routes(app):
    """AI Prediction pages/APIs were removed from the product.

    The internal time-estimation helpers stay in place because the queue
    engine uses them, but no AI Prediction route is exposed any more.
    """
    return app
