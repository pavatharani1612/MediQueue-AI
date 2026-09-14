"""Database initialisation + seeding.
Creates tables, inserts admin, 10 departments, 40 doctors, ~30 patients.
"""
import os, random, sqlite3
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
from config import Config
from models import get_conn, execute, query

DEPARTMENTS = [
    ("Cardiology", "Heart & vascular care", "❤️"),
    ("Neurology", "Brain & nervous system", "🧠"),
    ("Orthopedics", "Bones & joints", "🦴"),
    ("Dermatology", "Skin, hair & nails", "🌿"),
    ("ENT", "Ear, nose & throat", "👂"),
    ("General Medicine", "Everyday healthcare", "🩺"),
    ("Pediatrics", "Children's health", "🧒"),
    ("Gynecology", "Women's health", "🌸"),
    ("Ophthalmology", "Eye care", "👁️"),
    ("Dentistry", "Dental care", "🦷"),
]

CONSULT_TIME = {
    "Cardiology": 30, "Neurology": 35, "Orthopedics": 25,
    "Dermatology": 20, "ENT": 15, "General Medicine": 10,
    "Pediatrics": 15, "Gynecology": 20, "Ophthalmology": 15,
    "Dentistry": 20,
}

FIRST = ["Arun","Priya","Rahul","Kavya","Vikram","Sneha","Rohit","Ananya",
        "Karthik","Meera","Sanjay","Divya","Aditya","Isha","Nikhil","Pooja",
        "Manoj","Ritika","Suresh","Neha","Rajeev","Shreya","Varun","Anjali",
        "Deepak","Lakshmi","Amit","Swati","Harish","Bhavana","Ravi","Radha",
        "Naveen","Aparna","Mahesh","Sushma","Ganesh","Preeti","Rakesh","Tanvi"]
LAST = ["Sharma","Verma","Iyer","Reddy","Menon","Nair","Rao","Patel","Kumar",
        "Singh","Gupta","Joshi","Mehta","Pillai","Kapoor","Shah","Bhatt","Das"]
QUAL = ["MBBS, MD","MBBS, MS","MBBS, DM","MBBS, DNB","MBBS, MCh"]


def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS admin(
            id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT);
        CREATE TABLE IF NOT EXISTS departments(
            id INTEGER PRIMARY KEY, name TEXT UNIQUE, description TEXT, icon TEXT);
        CREATE TABLE IF NOT EXISTS doctors(
            id INTEGER PRIMARY KEY,
            doc_code TEXT UNIQUE,
            name TEXT, department TEXT, qualification TEXT,
            experience INTEGER, specialization TEXT,
            fee INTEGER, avg_time INTEGER,
            available INTEGER DEFAULT 1,
            photo TEXT,
            username TEXT UNIQUE, password TEXT);
        CREATE TABLE IF NOT EXISTS patients(
            id INTEGER PRIMARY KEY,
            name TEXT, age INTEGER, gender TEXT,
            phone TEXT, email TEXT UNIQUE,
            address TEXT, blood_group TEXT, password TEXT,
            created_at TEXT);
        CREATE TABLE IF NOT EXISTS appointments(
            id INTEGER PRIMARY KEY,
            queue_no TEXT,
            patient_id INTEGER, doctor_id INTEGER,
            department TEXT, reason TEXT, priority TEXT,
            appt_date TEXT, predicted_time TEXT,
            actual_start TEXT, actual_end TEXT,
            status TEXT DEFAULT 'Waiting',
            consult_minutes INTEGER,
            delay_minutes INTEGER DEFAULT 0,
            prescription TEXT, notes TEXT,
            reminder_sent INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id),
            FOREIGN KEY(doctor_id)  REFERENCES doctors(id));
        CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY,
            role TEXT, user_id INTEGER,
            title TEXT, message TEXT,
            is_read INTEGER DEFAULT 0, created_at TEXT);
        CREATE TABLE IF NOT EXISTS email_logs(
            id INTEGER PRIMARY KEY,
            recipient TEXT, subject TEXT, body TEXT,
            status TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY,
            actor_role TEXT, actor_id INTEGER,
            action TEXT, details TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS predictions(
            id INTEGER PRIMARY KEY,
            appointment_id INTEGER, predicted_wait REAL,
            predicted_consult REAL, confidence REAL, created_at TEXT);
        """)

        # ---- Migration: emergency / priority verification / delay columns ----
        _extra_cols = [
            ("emergency_reason",    "TEXT"),
            ("emergency_symptoms",  "TEXT"),
            ("emergency_verified",  "INTEGER DEFAULT 0"),
            ("verified_by",         "TEXT"),
            ("verified_at",         "TEXT"),
            ("emergency_status",    "TEXT"),
            ("predicted_wait_time", "INTEGER"),
            ("delay_reason",        "TEXT"),
            ("delayed_by",          "TEXT"),
            ("delayed_at",          "TEXT"),
        ]
        existing = {r[1] for r in c.execute("PRAGMA table_info(appointments)").fetchall()}
        for name, ddl in _extra_cols:
            if name not in existing:
                c.execute(f"ALTER TABLE appointments ADD COLUMN {name} {ddl}")

        # ---- DoctorDelay log table ----
        c.execute("""CREATE TABLE IF NOT EXISTS doctor_delays(
            id INTEGER PRIMARY KEY,
            doctor_id INTEGER NOT NULL,
            delay_minutes INTEGER NOT NULL,
            reason TEXT,
            delayed_by TEXT,
            created_at TEXT,
            FOREIGN KEY(doctor_id) REFERENCES doctors(id))""")


def seed_all():
    # Admin
    if not query("SELECT 1 FROM admin", one=True):
        execute("INSERT INTO admin(username,password) VALUES(?,?)",
                (Config.ADMIN_USERNAME,
                 generate_password_hash(Config.ADMIN_PASSWORD)))

    # Departments
    if not query("SELECT 1 FROM departments", one=True):
        for n, d, i in DEPARTMENTS:
            execute("INSERT INTO departments(name,description,icon) VALUES(?,?,?)",
                    (n, d, i))

    # Doctors: 4 per department -> 40 total
    if not query("SELECT 1 FROM doctors", one=True):
        idx = 1
        for dept, _, _ in DEPARTMENTS:
            for k in range(4):
                nm = f"Dr. {random.choice(FIRST)} {random.choice(LAST)}"
                code = f"D{idx:03d}"
                execute("""INSERT INTO doctors
                    (doc_code,name,department,qualification,experience,
                     specialization,fee,avg_time,available,photo,username,password)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, nm, dept, random.choice(QUAL),
                     random.randint(5, 30), dept + " Specialist",
                     random.choice([300, 500, 700, 1000, 1500]),
                     CONSULT_TIME[dept], 1, "",
                     code.lower(), generate_password_hash("doctor123")))
                idx += 1

    # Patients
    if not query("SELECT 1 FROM patients", one=True):
        for k in range(30):
            nm = f"{random.choice(FIRST)} {random.choice(LAST)}"
            em = f"patient{k+1}@demo.com"
            execute("""INSERT INTO patients
                (name,age,gender,phone,email,address,blood_group,password,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (nm, random.randint(18, 70),
                 random.choice(["Male","Female","Other"]),
                 f"98{random.randint(10000000,99999999)}",
                 em, "Demo City", random.choice(["A+","B+","O+","AB+","O-"]),
                 generate_password_hash("patient123"),
                 datetime.now().isoformat()))
