"""CME equity-index-futures session state in America/Chicago."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

CME_TZ = ZoneInfo("America/Chicago")


def session_status(now: datetime) -> str:
    local = now.astimezone(CME_TZ)
    weekday = local.weekday()  # Monday=0, Sunday=6
    clock = local.timetz().replace(tzinfo=None)
    if weekday == 5:
        return "WEEKEND"
    if weekday == 6:
        return "OPEN" if clock >= time(17, 0) else "WEEKEND"
    if weekday == 4 and clock >= time(16, 0):
        return "WEEKEND"
    if time(16, 0) <= clock < time(17, 0):
        return "DAILY_BREAK"
    return "OPEN"
