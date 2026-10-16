import importlib.util
from pathlib import Path

from satellite_forecast.storage import Storage


def _load_builder():
    path = Path(__file__).parents[1] / "scripts" / "download_ephemeris.py"
    spec = importlib.util.spec_from_file_location("offline_builder", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_builder_converts_omm_and_transmitters():
    builder = _load_builder()
    record = type("Record", (), {
        "norad_id": 43689,
        "name": "METOP-C",
        "aliases": ("2018-087A",),
        "source": "celestrak-gp",
        "line1": None,
        "line2": None,
        "omm": {"OBJECT_NAME": "METOP-C"},
    })()
    transmitter = type("Transmitter", (), {
        "description": "APT",
        "mode": "FM",
        "status": "active",
        "service": "amateur",
        "uplink_low_hz": None,
        "uplink_high_hz": None,
        "downlink_low_hz": 137100000,
        "downlink_high_hz": 137200000,
    })()
    stored = builder._stored_satellite(record, [transmitter])
    with Storage() as storage:
        storage.upsert_satellite(stored)
        loaded = storage.get_satellite("43689")
    assert loaded is not None
    assert loaded.omm == {"OBJECT_NAME": "METOP-C"}
    assert loaded.transmitters[0].downlink_low_hz == 137100000
