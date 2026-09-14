# MediQueue AI

**Intelligent Hospital Queue Prediction and Appointment Management System**
BCA Final Year Project · Python · Flask · SQLite · scikit-learn

---

## ✨ Features

- 🏥 10 Departments · 40 Doctors · 30 seed Patients (auto-generated on first run)
- 🤖 AI-powered wait-time prediction (Random Forest on 500 auto samples)
- ⏱ Live queue with automatic re-prediction on delays/cancellations
- 📨 Full SMTP email flow: registration, booking, 10-min reminder, delay, cancel, complete
- 👤 Three role-based dashboards: **Admin · Doctor · Patient**
- 🚨 Missed / late appointment handling with re-appointment workflow
- 📊 Admin analytics: revenue, department chart, AI accuracy graph, CSV export
- 💎 Premium UI — glassmorphism, neumorphism, gradient cards, 3D hover, animated orbs, typing hero, counter animations, doctor card flip, queue rings
- 🔐 Hashed passwords, session role-checks, parameterised SQL, CSRF-safe form pattern

---

## 🚀 Run locally (one command)

```bash
unzip mediqueue-ai.zip
cd mediqueue
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>

### Demo logins
| Role    | Username              | Password    |
| ------- | --------------------- | ----------- |
| Admin   | `admin`               | `admin123`  |
| Doctor  | `d001` … `d040`       | `doctor123` |
| Patient | `patient1@demo.com` … | `patient123`|

---

## 📧 Enable real emails
Edit `config.py`:
```python
SMTP_ENABLED = True
SMTP_USER    = "you@gmail.com"
SMTP_PASSWORD= "app_password"
```
Until then, every email is stored under **Admin → Email Logs**.

---

## 🗂 Project Structure
```
mediqueue/
├── app.py                    # entry point
├── config.py                 # editable SMTP + settings (no .env)
├── database.py               # tables + seed data
├── dataset_generator.py      # 500-row synthetic dataset
├── prediction.py             # Random Forest train/predict
├── queue_manager.py          # queue logic + recalculation
├── email_service.py          # SMTP + HTML templates
├── scheduler.py              # APScheduler background jobs
├── routes.py                 # every Flask route
├── models.py                 # sqlite helpers
├── requirements.txt
├── templates/                # 20+ Jinja pages
└── static/
    ├── css/style.css
    └── js/app.js
```

---

## 🧠 Architecture
```
Browser ─► Flask routes ─► SQLite (parameterised)
                │
                ├─ prediction.py  (scikit-learn model)
                ├─ queue_manager  (adds appointment, recomputes ETA)
                ├─ email_service  (SMTP, HTML templates, logged)
                └─ scheduler      (10-min reminders, missed sweep)
```

---

## 🖼 Screenshots (placeholders)
- `docs/screenshot-home.png`
- `docs/screenshot-ai.png`
- `docs/screenshot-admin.png`
- `docs/screenshot-doctor.png`

---

Built with ❤️ for a BCA Final Year Project.
