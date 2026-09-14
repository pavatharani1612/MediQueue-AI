"""SMS Service - pluggable gateway (no-op unless SMS_ENABLED is configured).

Keeps the SMS interface stable so a provider (Twilio / MSG91) can be plugged
in later without touching any caller.
"""
from __future__ import annotations

import os
from datetime import datetime

ENABLED = os.environ.get("SMS_ENABLED", "0") in ("1", "true", "True")


def send_sms(phone: str, message: str) -> bool:
    if not ENABLED or not phone:
        print(f"[SMS:dry-run {datetime.now().strftime('%I:%M %p').lstrip('0')}] "
              f"{phone}: {message}")
        return False
    # Provider integration goes here.
    return True
