"""Mail Service - small helpers shared by mail templates."""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def is_valid(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def mask(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    keep = name[:2]
    return f"{keep}{'*' * max(len(name) - 2, 0)}@{domain}"
