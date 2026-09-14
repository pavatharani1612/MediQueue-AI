"""Notification Service - one entry point for every in-app notification."""
from __future__ import annotations

from datetime import datetime

from models import query, execute
from services.realtime_service import bus


def push(role: str, user_id: int, title: str, message: str):
    """Insert a notification and broadcast it live to the matching dashboard."""
    execute("""INSERT INTO notifications(role,user_id,title,message,is_read,created_at)
               VALUES(?,?,?,?,0,?)""",
            (role, user_id, title, message, datetime.now().isoformat(timespec="seconds")))
    bus.publish("notification", {"role": role, "user_id": user_id,
                                 "title": title, "message": message})


def unread_count(role: str, user_id: int) -> int:
    row = query("""SELECT COUNT(*) n FROM notifications
                   WHERE role=? AND user_id=? AND is_read=0""",
                (role, user_id), one=True)
    return row["n"] if row else 0


def latest(role: str, user_id: int, limit: int = 20):
    return query("""SELECT * FROM notifications WHERE role=? AND user_id=?
                    ORDER BY id DESC LIMIT ?""", (role, user_id, limit))


def mark_all_read(role: str, user_id: int):
    execute("UPDATE notifications SET is_read=1 WHERE role=? AND user_id=?",
            (role, user_id))
