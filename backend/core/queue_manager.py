"""Queue manager - priority-aware tokens, emergency verification,
recalculates the queue after delays/cancellations.

Emergency rule: predicted waiting time is ALWAYS <= 10 minutes,
regardless of current queue length. Verified emergency appointments
jump to the top of the doctor's queue automatically."""
from datetime import datetime, timedelta
from models import query, execute
from prediction import predict_wait

PRIORITY_PREFIX = {"Emergency": "E", "Urgent": "U", "Normal": "N"}
PRIORITY_RANK = {"Emergency": 0, "Urgent": 1, "Normal": 2}

# Hard cap for emergency waiting time (minutes)
EMERGENCY_MAX_WAIT = 10


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def next_token(department, priority):
    """Per-department, per-priority token (E001/U001/N001)."""
    prefix = PRIORITY_PREFIX.get(priority, "N")
    row = query(
        "SELECT COUNT(*) c FROM appointments WHERE department=? AND priority=? AND appt_date=?",
        (department, priority, _today()), one=True)
    return f"{prefix}{(row['c']+1):03d}"


def priority_wait(priority, verified, dept, position):
    """Waiting-time strategy by priority.

    * Emergency (verified)     -> min(AI, 10) with a floor of 3
    * Emergency (pending)      -> min(AI, 10)
    * Urgent                   -> AI (typically 10-20)
    * Normal                   -> AI (queue-length driven)
    """
    if priority == "Emergency":
        base = predict_wait(dept, 1, emergency=1)
        wait = min(EMERGENCY_MAX_WAIT, max(3, base["wait_minutes"]))
        return {
            "wait_minutes": wait,
            "consult_minutes": 10,  # Emergency consultation is fixed at 10 minutes
            "confidence": 0.99 if verified else 0.9,
        }
    if priority == "Urgent":
        base = predict_wait(dept, position, emergency=0)
        # keep urgent inside the 10-20 min band
        wait = min(20, max(10, base["wait_minutes"]))
        return {
            "wait_minutes": wait,
            "consult_minutes": base["consult_minutes"],
            "confidence": base["confidence"],
        }
    # Normal -> full AI prediction using queue length
    return predict_wait(dept, position, emergency=0)


def add_appointment(patient_id, doctor_id, reason, priority,
                    emergency_reason=None, emergency_symptoms=None):
    doc = query("SELECT * FROM doctors WHERE id=?", (doctor_id,), one=True)
    waiting = query("""SELECT COUNT(*) c FROM appointments
                       WHERE doctor_id=? AND appt_date=? AND status IN('Waiting','InProgress')""",
                    (doctor_id, _today()), one=True)["c"]

    verified = 0
    emergency_status = None
    status = "Waiting"
    if priority == "Emergency":
        emergency_status = "Pending Verification"
        status = "Emergency-PendingVerification"

    # For an Emergency booking, waiting position is 1 (jumps to top after verification).
    position = 1 if priority == "Emergency" else waiting + 1
    pred = priority_wait(priority, verified, doc["department"], position)

    # Hard-cap emergency to 10 min regardless of what the model says
    if priority == "Emergency" and pred["wait_minutes"] > EMERGENCY_MAX_WAIT:
        pred["wait_minutes"] = EMERGENCY_MAX_WAIT

    predicted_dt = datetime.now() + timedelta(minutes=pred["wait_minutes"])
    q = next_token(doc["department"], priority)

    appt_id = execute("""INSERT INTO appointments
        (queue_no,patient_id,doctor_id,department,reason,priority,
         appt_date,predicted_time,predicted_wait_time,status,consult_minutes,created_at,
         emergency_reason,emergency_symptoms,emergency_verified,emergency_status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (q, patient_id, doctor_id, doc["department"], reason, priority,
         _today(), predicted_dt.strftime("%H:%M"), pred["wait_minutes"],
         status, pred["consult_minutes"], datetime.now().isoformat(),
         emergency_reason, emergency_symptoms, verified, emergency_status))

    execute("""INSERT INTO predictions(appointment_id,predicted_wait,predicted_consult,confidence,created_at)
               VALUES(?,?,?,?,?)""",
            (appt_id, pred["wait_minutes"], pred["consult_minutes"],
             pred["confidence"], datetime.now().isoformat()))
    return appt_id, q, predicted_dt


def recalculate_queue(doctor_id):
    """Recompute predicted_time for every Waiting patient of this doctor
    honouring priority order. Emergency (verified) always sits at the top
    with wait <= EMERGENCY_MAX_WAIT."""
    doc = query("SELECT * FROM doctors WHERE id=?", (doctor_id,), one=True)
    if not doc:
        return
    waiting = query("""SELECT * FROM appointments WHERE doctor_id=? AND appt_date=?
                       AND status IN('Waiting','Emergency-PendingVerification')
                       ORDER BY
                         CASE
                           WHEN priority='Emergency' AND emergency_verified=1 THEN 0
                           WHEN priority='Emergency' THEN 1
                           WHEN priority='Urgent' THEN 2
                           WHEN priority='Normal' THEN 3
                           ELSE 4
                         END, id""",
                    (doctor_id, _today()))
    base = datetime.now()
    for i, a in enumerate(waiting, start=1):
        pri = a["priority"] or "Normal"
        pred = priority_wait(pri, a["emergency_verified"] or 0,
                             doc["department"], i)
        # Hard cap emergency
        if pri == "Emergency" and pred["wait_minutes"] > EMERGENCY_MAX_WAIT:
            pred["wait_minutes"] = EMERGENCY_MAX_WAIT
        t = (base + timedelta(minutes=pred["wait_minutes"])).strftime("%H:%M")
        execute("UPDATE appointments SET predicted_time=?, predicted_wait_time=? WHERE id=?",
                (t, pred["wait_minutes"], a["id"]))


def apply_delay(doctor_id, minutes, reason=None, delayed_by=None):
    """Extend the doctor's availability, add delay to remaining appointments,
    log to doctor_delays, then recalculate the queue.

    Queue order is preserved (only wait times shift), unless a new
    Emergency arrives (which is handled by recalculate_queue's ordering)."""
    minutes = int(minutes)
    # Bump delay on all still-open appointments today for this doctor.
    execute("""UPDATE appointments SET delay_minutes = COALESCE(delay_minutes,0) + ?
               WHERE doctor_id=? AND appt_date=?
                 AND status IN('Waiting','InProgress','Emergency-PendingVerification')""",
            (minutes, doctor_id, _today()))
    # Log the delay event
    execute("""INSERT INTO doctor_delays(doctor_id,delay_minutes,reason,delayed_by,created_at)
               VALUES(?,?,?,?,?)""",
            (doctor_id, minutes, reason or "", delayed_by or "doctor",
             datetime.now().isoformat()))
    recalculate_queue(doctor_id)


def live_queue(doctor_id):
    """Priority-sorted queue: verified Emergency > Urgent > Normal > pending."""
    return query("""SELECT a.*, p.name AS patient_name, p.age, p.phone
                    FROM appointments a JOIN patients p ON p.id=a.patient_id
                    WHERE a.doctor_id=? AND a.appt_date=?
                    ORDER BY
                      CASE a.status
                        WHEN 'InProgress' THEN 0 ELSE 1 END,
                      CASE
                        WHEN a.priority='Emergency' AND a.emergency_verified=1 THEN 0
                        WHEN a.priority='Emergency' AND a.emergency_verified=0
                             AND a.status='Emergency-PendingVerification' THEN 1
                        WHEN a.priority='Urgent' THEN 2
                        WHEN a.priority='Normal' THEN 3
                        ELSE 4
                      END,
                      a.id""",
                 (doctor_id, _today()))


def approve_emergency(appt_id, verifier_role, verifier_id):
    execute("""UPDATE appointments
               SET emergency_verified=1, priority='Emergency',
                   emergency_status='Verified', status='Waiting',
                   verified_by=?, verified_at=?
               WHERE id=?""",
            (f"{verifier_role}:{verifier_id}", datetime.now().isoformat(), appt_id))
    a = query("SELECT doctor_id FROM appointments WHERE id=?", (appt_id,), one=True)
    if a:
        recalculate_queue(a["doctor_id"])


def reject_emergency(appt_id, new_priority, verifier_role, verifier_id):
    """new_priority must be 'Urgent' or 'Normal'."""
    if new_priority not in ("Urgent", "Normal"):
        new_priority = "Normal"
    a = query("SELECT * FROM appointments WHERE id=?", (appt_id,), one=True)
    if not a:
        return
    new_token = next_token(a["department"], new_priority)
    execute("""UPDATE appointments
               SET emergency_verified=0, priority=?, emergency_status='Rejected',
                   status='Waiting', queue_no=?, verified_by=?, verified_at=?
               WHERE id=?""",
            (new_priority, new_token, f"{verifier_role}:{verifier_id}",
             datetime.now().isoformat(), appt_id))
    recalculate_queue(a["doctor_id"])
