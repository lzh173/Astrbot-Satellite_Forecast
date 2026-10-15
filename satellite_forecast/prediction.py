"""Skyfield-based satellite pass prediction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from skyfield.api import EarthSatellite, load, wgs84

from .geocoding import Location
from .sources import SatelliteRecord


@dataclass(frozen=True, slots=True)
class PassPrediction:
    satellite_name: str
    aos: datetime
    eos: datetime
    max_elevation_degrees: float
    max_elevation_time: datetime
    max_azimuth_degrees: float
    max_direction: str


def _utc_datetime(value: Any) -> datetime:
    result = value.utc_datetime()
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def azimuth_to_direction(azimuth_degrees: float) -> str:
    """Return an eight-point Chinese compass direction."""

    directions = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
    return directions[int((azimuth_degrees % 360.0 + 22.5) // 45.0) % 8]


def _make_satellite(record: SatelliteRecord, timescale: Any) -> EarthSatellite:
    if record.line1 and record.line2:
        return EarthSatellite(record.line1, record.line2, record.name, timescale)
    if record.omm:
        satellite = EarthSatellite.from_omm(timescale, record.omm)
        if isinstance(satellite, (list, tuple)):
            satellite = satellite[0]
        satellite.name = record.name
        return satellite
    raise ValueError("satellite record needs either TLE lines or OMM/GP JSON data")


def _max_point(satellite: EarthSatellite, observer: Any, time: Any) -> tuple[float, float]:
    altitude, azimuth, _ = (satellite - observer).at(time).altaz()
    return float(altitude.degrees), float(azimuth.degrees % 360.0)


def predict_passes(
    satellite: SatelliteRecord,
    location: Location,
    *,
    start: datetime | None = None,
    duration: timedelta = timedelta(hours=24),
    minimum_elevation_degrees: float = 10.0,
    timescale: Any | None = None,
) -> list[PassPrediction]:
    """Calculate visible passes in ``[start, start + duration]``.

    ``start`` is interpreted as UTC when naive.  Skyfield's event order is
    rise, culmination, set; incomplete event groups at a search boundary are
    discarded because they do not describe a complete visible pass.
    """

    if not -90.0 <= minimum_elevation_degrees <= 90.0:
        raise ValueError("minimum elevation must be between -90 and 90 degrees")
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")
    start = start or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)
    end = start + duration

    ts = timescale or load.timescale()
    sat = _make_satellite(satellite, ts)
    observer = wgs84.latlon(
        latitude_degrees=location.latitude,
        longitude_degrees=location.longitude,
        elevation_m=location.altitude_m,
    )
    t0, t1 = ts.from_datetime(start), ts.from_datetime(end)
    times, events = sat.find_events(
        observer, t0, t1, altitude_degrees=minimum_elevation_degrees
    )

    predictions: list[PassPrediction] = []
    index = 0
    while index + 2 < len(events):
        if tuple(int(events[index + offset]) for offset in range(3)) != (0, 1, 2):
            index += 1
            continue
        aos_time, peak_time, eos_time = (times[index], times[index + 1], times[index + 2])
        elevation, azimuth = _max_point(sat, observer, peak_time)
        predictions.append(
            PassPrediction(
                satellite_name=satellite.name,
                aos=_utc_datetime(aos_time),
                eos=_utc_datetime(eos_time),
                max_elevation_degrees=elevation,
                max_elevation_time=_utc_datetime(peak_time),
                max_azimuth_degrees=azimuth,
                max_direction=azimuth_to_direction(azimuth),
            )
        )
        index += 3
    return predictions
