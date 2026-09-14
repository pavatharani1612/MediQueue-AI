"""Generates a synthetic dataset (~500 rows) used to train
a simple Random Forest Regressor that predicts waiting time.
"""
import os, random
import numpy as np
import pandas as pd
from config import Config

DEPTS = ["Cardiology","Neurology","Orthopedics","Dermatology","ENT",
         "General Medicine","Pediatrics","Gynecology","Ophthalmology","Dentistry"]
BASE = {"Cardiology":30,"Neurology":35,"Orthopedics":25,"Dermatology":20,
        "ENT":15,"General Medicine":10,"Pediatrics":15,"Gynecology":20,
        "Ophthalmology":15,"Dentistry":20}

def generate_dataset(n=None, path="dataset.csv"):
    n = n or Config.DATASET_SIZE
    rows = []
    for _ in range(n):
        dept = random.choice(DEPTS)
        avg = BASE[dept]
        pcount = random.randint(1, 25)
        late = random.randint(0, 5)
        emergency = random.randint(0, 3)
        delay = random.randint(0, 20)
        actual = avg + random.randint(-4, 8)
        wait = pcount * avg + late*5 + emergency*15 + delay + random.randint(-5, 10)
        rows.append([dept, pcount, avg, delay, late, emergency, actual, wait])
    df = pd.DataFrame(rows, columns=[
        "department","patient_count","avg_time","delay",
        "late","emergency","actual_time","wait_time"])
    df.to_csv(path, index=False)
    return df
