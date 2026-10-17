from datetime import timedelta

from satellite_forecast.models import (
    FollowedSatellite,
    PendingInteraction,
    SatelliteRecord,
    SatelliteTransmitter,
    UserLocation,
    UserSettings,
    utc_now,
)
from satellite_forecast.storage import Storage


def test_user_location_settings_and_followed_satellite_round_trip() -> None:
    with Storage() as storage:
        storage.set_location(UserLocation("u1", 24.4798, 118.0894, "Xiamen", 20))
        storage.set_settings(UserSettings("u1", reminder_minutes=8, reminder_count=2))
        storage.add_followed_satellite(
            FollowedSatellite("u1", "40069", "METOP-C", "celestrak")
        )

        assert storage.get_location("u1").longitude == 118.0894
        assert storage.get_settings("u1").reminder_count == 2
        assert storage.list_followed_satellites("u1")[0].norad_id == "40069"
        assert storage.remove_followed_satellite("u1", "40069") is True
        assert storage.list_followed_satellites("u1") == []


def test_pending_interaction_expires_and_satellite_catalog_round_trips() -> None:
    satellite = SatelliteRecord(
        norad_id="40069",
        name="METOP-C",
        aliases=("metop c",),
        source="satnogs",
        transmitters=(SatelliteTransmitter(
            description="APT 137.1 MHz",
            uplink_low_hz=145800000,
            uplink_high_hz=145900000,
            downlink_low_hz=137100000,
            downlink_high_hz=137200000,
        ),),
    )
    with Storage() as storage:
        storage.set_pending_interaction(
            PendingInteraction(
                "u1",
                "choose_satellite",
                {"query": "metop-c"},
                expires_at=utc_now() - timedelta(seconds=1),
            )
        )
        assert storage.get_pending_interaction("u1", "choose_satellite") is None

        storage.upsert_satellite(satellite)
        loaded = storage.get_satellite("40069")
        assert loaded is not None
        assert loaded.aliases == ("metop c",)
        assert loaded.transmitters[0].description == "APT 137.1 MHz"
        assert loaded.transmitters[0].uplink_high_hz == 145900000
        assert loaded.transmitters[0].downlink_low_hz == 137100000


def test_satellite_catalog_preserves_omm_ephemeris() -> None:
    satellite = SatelliteRecord(
        norad_id="43689",
        name="METOP-C",
        source="celestrak-gp",
        omm={
            "OBJECT_NAME": "METOP-C",
            "NORAD_CAT_ID": 43689,
            "EPOCH": "2024-01-01T00:00:00.000Z",
        },
    )
    with Storage() as storage:
        storage.upsert_satellite(satellite)
        loaded = storage.get_satellite("43689")
    assert loaded is not None
    assert loaded.omm == satellite.omm


def test_notification_target_round_trip() -> None:
    with Storage() as storage:
        storage.set_notification_target("qq:123", "GroupMessage:456")
        assert storage.list_notification_targets() == [("qq:123", "GroupMessage:456")]

