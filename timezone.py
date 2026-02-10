import os
from zoneinfo import ZoneInfo

def _tz():
    return ZoneInfo(os.getenv("REPORT_TIMEZONE", "America/Cuiaba"))
