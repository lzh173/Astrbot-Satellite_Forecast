from datetime import datetime, timezone

import pytest

from satellite_forecast.models import UserLocation, UserSettings


def test_location_and_settings_validate_ranges() -> None:
    location = UserLocation("user-1", 24.4798, 118.0894, name="Xiamen")
    settings = UserSettings("user-1", min_elevation_deg=10)

    assert location.name == "Xiamen"
    assert settings.notifications_enabled is True

    with pytest.raises(ValueError):
        UserLocation("user-1", 91, 0)
    with pytest.raises(ValueError):
        UserSettings("user-1", min_elevation_deg=-1)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError):
        UserLocation("user-1", 0, 0, updated_at=datetime.now())

    assert UserLocation(
        "user-1", 0, 0, updated_at=datetime.now(timezone.utc)
    ).updated_at.tzinfo is not None
