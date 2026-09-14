# MEDIQUEUE AI — Enterprise Edition

## Run
```bash
pip install -r requirements.txt
python run.py            # http://127.0.0.1:5000   (admin / admin123)
python -m pytest tests   # smoke tests
```

## Structure
```
MEDIQUEUE_AI/
  run.py                     single entry point (registers backend on sys.path)
  backend/
    core/                    existing app modules (app_factory, routes, models, db, AI…)
    api/enterprise_api.py    all new endpoints
    middleware/security.py   headers, rate limiting, session expiry, audit hooks
    services/
      qr_service/            generator.py scanner.py validator.py database.py
      barcode_service/       generator.py scanner.py validator.py
      queue_service/         queue.py waiting_time.py priority.py token.py
      appointment_service/   slots.py  (real-time AM/PM slot engine)
      scanner_service/       checkin.py (shared check-in engine)
      settings_service/      settings.py (+ permanent settings_history)
      audit_service/         audit.py (audit_logs + login_history)
      notification_service/  notifications.py
      realtime_service/      bus.py (Socket.IO if installed, else SSE)
      mail_service/ sms_service/ prediction_service/ history_service/ report_service/
  frontend/templates/{admin,doctor,patient}/ + static/{css,js,images,fonts}
  config/ database/ datasets/ ml_models/ uploads/ exports/ reports/ logs/ tests/
```

## New in this upgrade
* **QR fixed & complete** — signed payload (patient_id, appointment_id, queue_token,
  booking_date, hospital_id + HMAC), stored in `qr_codes`, issued automatically on every
  booking and back-filled for old appointments, downloadable PNG, printable pass,
  visible in appointment history, auto-expired after completion/cancellation.
* **Admin QR Check-In** — `/admin/qr-checkin` camera scanner (jsQR, offline bundled) with
  manual token fallback; stores patient, appointment, scan time, date, admin name, method,
  status and IP in `checkins`; returns *Already Checked In*, *Invalid QR Code*,
  *Appointment Expired*.
* **Full check-in flow** — scan → verify → mark Checked-In → enters live queue →
  AI re-prediction → notifications to patient/doctor/admin → live push to all dashboards.
* **Real-time slots** — `/api/slots/live` filters past times, booked, cancelled and
  completed slots; 12-hour AM/PM labels everywhere (no railway time).
* **Settings history** — every admin change stores old value, new value, admin, date,
  time and IP permanently (`/admin/settings-history`, editor at `/admin/settings-plus`).
* **Audit logs** — `/admin/audit-logs` with action, admin, date, time, browser, device, IP
  plus login history.
* **Live dashboards** — `/api/realtime/stream` (SSE) upgrades to Socket.IO when installed;
  `enterprise_live.js` refreshes KPIs, queue, settings and theme without page reload.
* **Analytics** — `/admin/analytics`: today's queue/appointments, doctors online,
  departments, revenue, live check-ins, QR/barcode scan counts, prediction accuracy,
  daily/monthly reports, recent activity, system health, charts.
* **Database** — added `qr_codes`, `checkins`, `barcodes`, `queue_history`,
  `settings_history`, `audit_logs`, `login_history`, `reports`, `payments`,
  `prediction_history` with foreign keys. All migrations are idempotent — no existing
  data is dropped or renamed.
* **Security** — hashed passwords (existing), role guards, rate limiting, session expiry,
  security headers, upload size limit, parameterised SQL, activity logging.
* **UI** — same design language, upgraded with glassmorphism cards, animated stats,
  better tables/forms, print styles and a dark mode driven by the Theme setting.

Everything previously in the app (AI prediction, queue manager, booking, barcode, QR pass,
email actions, reception/staff desk, feedback, reports, notifications, all four panels)
is untouched and still registered first.
