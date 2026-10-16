"""UTC-normalized display helpers for forecast timestamps."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def format_local_time(value: datetime, timezone_name: str) -> str:
    """Format an aware UTC timestamp in the requested display timezone."""

    try:
        return value.astimezone(ZoneInfo(timezone_name)).strftime("%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return value.astimezone(timezone.utc).strftime("%m-%d %H:%M:%S UTC")


def timezone_label(timezone_name: str) -> str:
    """Return a human-readable label so UTC/BJT output cannot be confused."""

    try:
        zone = ZoneInfo(timezone_name)
        offset = datetime.now(zone).utcoffset()
        if timezone_name == "Asia/Shanghai" or str(offset) == "8:00:00":
            return "北京时间（UTC+8）"
        if timezone_name == "UTC" or str(offset) == "0:00:00":
            return "UTC"
        return timezone_name
    except Exception:  # noqa: BLE001
        return "UTC"
