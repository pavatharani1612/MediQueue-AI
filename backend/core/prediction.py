from config import Config
"""AI model - Random Forest Regressor to predict waiting time."""
import os, joblib
import numpy as np, pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_absolute_error
from dataset_generator import generate_dataset, DEPTS, BASE

MODEL_PATH = Config.MODEL_PATH
META_PATH = Config.MODEL_META_PATH


def _encode(df):
    df = df.copy()
    df["dept_id"] = df["department"].map({d:i for i,d in enumerate(DEPTS)})
    return df[["dept_id","patient_count","avg_time","delay","late","emergency"]]


def train():
    df = generate_dataset(path=Config.DATASET_PATH)
    X = _encode(df); y = df["wait_time"]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42)
    m = RandomForestRegressor(n_estimators=120, random_state=42)
    m.fit(Xtr, ytr)
    pred = m.predict(Xte)
    meta = {
        "r2": round(r2_score(yte, pred), 3),
        "mae": round(mean_absolute_error(yte, pred), 2),
        "samples": len(df),
        "actual": yte.tolist()[:60],
        "predicted": pred.tolist()[:60],
    }
    joblib.dump(m, MODEL_PATH); joblib.dump(meta, META_PATH)
    return meta


def train_model_if_needed():
    if not os.path.exists(MODEL_PATH):
        train()


def get_meta():
    if not os.path.exists(META_PATH):
        train()
    return joblib.load(META_PATH)


def predict_wait(dept, patient_count, delay=0, late=0, emergency=0):
    if not os.path.exists(MODEL_PATH):
        train()
    m = joblib.load(MODEL_PATH)
    dept_id = DEPTS.index(dept) if dept in DEPTS else 0
    avg = BASE.get(dept, 15)
    X = np.array([[dept_id, patient_count, avg, delay, late, emergency]])
    wait = float(m.predict(X)[0])
    confidence = round(max(0.6, min(0.99, 1 - abs(wait - (patient_count*avg))/max(wait,1)/5)), 2)
    return {
        "wait_minutes": max(1, round(wait)),
        "consult_minutes": avg,
        "confidence": confidence,
    }
