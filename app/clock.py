"""One place that decides what "today" is.

The server may run in UTC (Render does), but the students using StudyPilot are in
California, so "today", "due Friday" and seeded task dates use APP_TIMEZONE instead.
"""

from __future__ import annotations

import os
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "America/Los_Angeles"))
except Exception:  # missing tz database: fall back to the server's clock
    _TZ = None


def today() -> date:
    return datetime.now(_TZ).date() if _TZ else date.today()
