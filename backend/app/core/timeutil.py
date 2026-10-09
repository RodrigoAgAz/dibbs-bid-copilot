from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def today_et() -> date:
    """DIBBS runs on Eastern time; 'today' for solicitations means today in New York."""
    return datetime.now(ET).date()
