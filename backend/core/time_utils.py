"""Central time formatting helpers.

The application stores predicted_time internally as "HH:MM" (24-hour) so
scheduler jobs can safely split on ':'. All USER-FACING output must go
through to_ampm() (or the Jinja `ampm` filter) so the entire UI, emails,
notifications and API responses show 12-hour AM/PM format.
"""
from datetime import datetime


def to_ampm(value):
    """Convert a value to a 12-hour clock string like '9:05 AM'.

    Accepts:
      * 'HH:MM' or 'HH:MM:SS' strings (24-hour)
      * 'YYYY-MM-DD HH:MM[:SS]' strings
      * ISO 8601 strings
      * datetime objects
      * None / '' -> ''
    """
    if value is None or value == "":
        return ""
    dt = None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        fmts = ("%H:%M", "%H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d %H:%M:%S", "%I:%M %p", "%I:%M%p")
        for f in fmts:
            try:
                dt = datetime.strptime(s, f)
                break
            except Exception:
                pass
        if dt is None:
            try:
                dt = datetime.fromisoformat(s)
            except Exception:
                return s  # give up, return original
    # 12-hour format, no leading zero on hour
    out = dt.strftime("%I:%M %p").lstrip("0")
    if out.startswith(":"):
        out = "12" + out
    return out


def to_ampm_datetime(value):
    """Full '2026-07-19 9:05 AM' style formatter."""
    if not value:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        dt = None
        for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(s, f); break
            except Exception:
                pass
        if dt is None:
            try:
                dt = datetime.fromisoformat(s)
            except Exception:
                return s
    time_part = dt.strftime("%I:%M %p").lstrip("0")
    if time_part.startswith(":"): time_part = "12" + time_part
    return dt.strftime("%Y-%m-%d ") + time_part
