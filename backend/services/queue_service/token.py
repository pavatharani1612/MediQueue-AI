"""Queue Service - daily token generator (e.g. CARD-07)."""
from __future__ import annotations

from datetime import datetime

from models import query


def next_token(department: str, on_date: str | None = None) -> str:
    on_date = on_date or datetime.now().strftime("%Y-%m-%d")
    prefix = "".join(word[0] for word in (department or "GEN").split())[:4].upper()
    row = query("""SELECT COUNT(*) n FROM appointments
                   WHERE department=? AND appt_date=?""", (department, on_date), one=True)
    return f"{prefix}-{(row['n'] if row else 0) + 1:02d}"
