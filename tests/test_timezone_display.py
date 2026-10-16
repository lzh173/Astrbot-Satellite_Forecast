from datetime import datetime, timezone

from satellite_forecast.time_utils import format_local_time, timezone_label


def test_timezone_label_distinguishes_bjt_and_utc():
    instant = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    assert format_local_time(instant, "Asia/Shanghai") == "10-16 20:00:00"
    assert format_local_time(instant, "UTC") == "10-16 12:00:00"
    assert timezone_label("Asia/Shanghai") == "北京时间（UTC+8）"
    assert timezone_label("UTC") == "UTC"


def test_invalid_timezone_falls_back_to_utc():
    instant = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    assert format_local_time(instant, "invalid/zone") == "10-16 12:00:00 UTC"
    assert timezone_label("invalid/zone") == "UTC"
