"""
Realtime Service - in-process event bus + Server-Sent Events fan-out.
=====================================================================
Dashboards subscribe once and receive push updates, so Admin / Doctor /
Patient / Reception screens refresh **without reloading the page**.

Socket.IO is used automatically when `flask-socketio` is installed;
otherwise the built-in SSE transport (zero extra dependencies) is used.
Both share the same `bus.publish()` API.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime

_socketio = None          # set by attach_socketio()


class EventBus:
    """Thread-safe pub/sub with bounded per-subscriber queues."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._last: dict[str, dict] = {}

    # ---------------------------------------------------------- publish
    def publish(self, topic: str, data: dict | None = None):
        event = {
            "topic": topic,
            "data": data or {},
            "at": datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
            "ts": time.time(),
        }
        with self._lock:
            self._last[topic] = event
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

        if _socketio is not None:
            try:
                _socketio.emit(topic, event)
                _socketio.emit("mq_event", event)
            except Exception:
                pass
        return event

    # ---------------------------------------------------------- subscribe
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._last)


bus = EventBus()


def attach_socketio(socketio):
    """Optional upgrade path: real WebSockets via flask-socketio."""
    global _socketio
    _socketio = socketio


def sse_stream():
    """Generator for `text/event-stream` responses."""
    q = bus.subscribe()
    try:
        yield "retry: 3000\n\n"
        yield f"data: {json.dumps({'topic': 'connected', 'data': {}})}\n\n"
        while True:
            try:
                event = q.get(timeout=20)
                yield f"event: {event['topic']}\ndata: {json.dumps(event)}\n\n"
            except queue.Empty:
                yield ": keep-alive\n\n"
    finally:
        bus.unsubscribe(q)


# --------------------------------------------------------------- module API
# Callers do `from services.realtime_service import bus` and then use
# bus.publish(...) / bus.snapshot() - these delegate to the shared instance.
def publish(topic: str, data: dict | None = None):
    return bus.publish(topic, data)


def subscribe():
    return bus.subscribe()


def unsubscribe(q):
    return bus.unsubscribe(q)


def snapshot() -> dict:
    return bus.snapshot()
