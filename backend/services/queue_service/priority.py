"""Queue Service - priority rules (emergency first, then seniors)."""
from __future__ import annotations

ORDER = {"Emergency": 0, "Senior": 1, "Child": 1, "Normal": 2}


def rank(priority: str | None) -> int:
    return ORDER.get((priority or "Normal").title(), 2)


def sort_key(appointment: dict):
    return (rank(appointment.get("priority")),
            appointment.get("predicted_time") or "99:99",
            appointment.get("id") or 0)


def sort_queue(items: list[dict]) -> list[dict]:
    return sorted(items, key=sort_key)
