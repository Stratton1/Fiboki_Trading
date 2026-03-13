"""Market session definitions and utilities.

Sessions are defined in UTC:
  Asian:            00:00 – 08:00 UTC
  London:           08:00 – 12:00 UTC
  London-NY Overlap: 12:00 – 16:00 UTC
  New York:         16:00 – 21:00 UTC
  Off-Hours:        21:00 – 00:00 UTC
"""

from datetime import datetime, time

SESSIONS = [
    {"name": "Asian", "start": time(0, 0), "end": time(8, 0), "color": "#E8F5E9"},
    {"name": "London", "start": time(8, 0), "end": time(12, 0), "color": "#E3F2FD"},
    {"name": "London-NY Overlap", "start": time(12, 0), "end": time(16, 0), "color": "#FFF3E0"},
    {"name": "New York", "start": time(16, 0), "end": time(21, 0), "color": "#FCE4EC"},
    {"name": "Off-Hours", "start": time(21, 0), "end": time(0, 0), "color": "#F5F5F5"},
]


def get_session_for_timestamp(ts: datetime) -> str:
    """Return the market session name for a given UTC timestamp."""
    t = ts.time()
    if time(0, 0) <= t < time(8, 0):
        return "Asian"
    elif time(8, 0) <= t < time(12, 0):
        return "London"
    elif time(12, 0) <= t < time(16, 0):
        return "London-NY Overlap"
    elif time(16, 0) <= t < time(21, 0):
        return "New York"
    else:
        return "Off-Hours"


def get_sessions_metadata() -> list[dict]:
    """Return session definitions for frontend rendering."""
    return [
        {"name": s["name"], "start_utc": s["start"].strftime("%H:%M"), "end_utc": s["end"].strftime("%H:%M"), "color": s["color"]}
        for s in SESSIONS
    ]
