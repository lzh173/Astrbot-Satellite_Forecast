"""Domain models used by the satellite forecast plugin.

The models deliberately have no AstrBot or database dependencies. This keeps
the forecast logic easy to test and lets the command and LLM layers share the
same data contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    """Return an aware UTC timestamp suitable for persistence."""

    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class UserLocation:
    """A user's observing location."""

    user_id: str
    latitude: float
    longitude: float
    name: str = ""
    altitude_m: float = 0.0
    maidenhead: str | None = None
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id must not be empty")
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")
        if self.altitude_m < -500:
            raise ValueError("altitude_m is below the supported range")
        _ensure_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class UserSettings:
    """Per-user forecast and notification preferences."""

    user_id: str
    min_elevation_deg: float = 10.0
    reminder_minutes: int = 6
    reminder_count: int = 1
    notifications_enabled: bool = True
    tle_storage_path: str | None = None
    timezone: str = "Asia/Shanghai"
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id must not be empty")
        if not 0 <= self.min_elevation_deg <= 90:
            raise ValueError("min_elevation_deg must be between 0 and 90")
        if self.reminder_minutes < 0:
            raise ValueError("reminder_minutes must not be negative")
        if self.reminder_count < 0:
            raise ValueError("reminder_count must not be negative")
        if not self.timezone.strip():
            raise ValueError("timezone must not be empty")
        _ensure_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class FollowedSatellite:
    """A satellite followed by a user, keyed by its NORAD catalog ID."""

    user_id: str
    norad_id: str
    display_name: str = ""
    source: str = ""
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id must not be empty")
        if not self.norad_id.strip():
            raise ValueError("norad_id must not be empty")
        _ensure_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    """A short-lived state waiting for a user's confirmation or next input."""

    user_id: str
    interaction_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id must not be empty")
        if not self.interaction_type.strip():
            raise ValueError("interaction_type must not be empty")
        _ensure_aware(self.created_at, "created_at")
        if self.expires_at is not None:
            _ensure_aware(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class SatelliteTransmitter:
    """A normalized SatNOGS transmitter entry."""

    description: str = ""
    mode: str = ""
    uplink: str = ""
    downlink: str = ""
    status: str = ""
    service: str = ""
    uplink_low_hz: float | None = None
    uplink_high_hz: float | None = None
    downlink_low_hz: float | None = None
    downlink_high_hz: float | None = None


@dataclass(frozen=True, slots=True)
class SatelliteRecord:
    """A normalized satellite catalog entry from CelesTrak or SatNOGS."""

    norad_id: str
    name: str
    aliases: tuple[str, ...] = ()
    source: str = ""
    tle_line1: str | None = None
    tle_line2: str | None = None
    transmitters: tuple[SatelliteTransmitter, ...] = ()
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.norad_id.strip():
            raise ValueError("norad_id must not be empty")
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if not self.source.strip():
            raise ValueError("source must not be empty")
        _ensure_aware(self.updated_at, "updated_at")

