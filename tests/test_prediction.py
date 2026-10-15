from datetime import datetime, timedelta, timezone

import pytest

from satellite_forecast.geocoding import Location
from satellite_forecast.prediction import azimuth_to_direction, predict_passes
from satellite_forecast.sources import SatelliteRecord


TLE = (
    "1 25544U 98067A   24001.00000000  .00010000  00000-0  18000-3 0  9999",
    "2 25544  51.6400  10.0000 0005000  20.0000  30.0000 15.50000000123456",
)


@pytest.mark.parametrize(
    ("azimuth", "direction"),
    [(0, "北"), (45, "东北"), (90, "东"), (180, "南"), (270, "西")],
)
def test_azimuth_to_direction(azimuth, direction):
    assert azimuth_to_direction(azimuth) == direction


def test_predict_passes_returns_complete_skyfield_events():
    pytest.importorskip("skyfield")
    result = predict_passes(
        SatelliteRecord(name="ISS", norad_id=25544, line1=TLE[0], line2=TLE[1]),
        Location(24.4798, 118.0894, altitude_m=20),
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        duration=timedelta(days=1),
        minimum_elevation_degrees=0,
    )
    assert result
    first = result[0]
    assert first.aos < first.max_elevation_time < first.eos
    assert 0 <= first.max_azimuth_degrees < 360
    assert -90 <= first.max_elevation_degrees <= 90
