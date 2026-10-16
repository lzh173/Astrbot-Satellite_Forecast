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


def test_source_files_can_be_loaded_and_synthesized(tmp_path):
    builder = _load_builder()
    record = type("Record", (), {
        "norad_id": 43689,
        "name": "METOP-C",
        "aliases": ("2018-087A",),
        "source": "celestrak-gp",
        "line1": None,
        "line2": None,
        "omm": {"OBJECT_NAME": "METOP-C", "NORAD_CAT_ID": 43689},
        "raw": {"OBJECT_NAME": "METOP-C", "NORAD_CAT_ID": 43689},
    })()
    builder.save_source_files(tmp_path / "sources", [record], [], "json")
    records, transmitters = builder.load_source_files(tmp_path / "sources", "json")
    assert records[0].norad_id == 43689
    assert transmitters == []


def test_source_files_preserve_satnogs_mapping_and_build_database(tmp_path):
    builder = _load_builder()
    record = type("Record", (), {
        "norad_id": 43689,
        "name": "METOP-C",
        "aliases": (),
        "source": "celestrak-gp",
        "line1": None,
        "line2": None,
        "omm": {"OBJECT_NAME": "METOP-C", "NORAD_CAT_ID": 43689},
        "raw": {"OBJECT_NAME": "METOP-C", "NORAD_CAT_ID": 43689},
    })()
    transmitter = type("Transmitter", (), {
        "raw": {
            "uuid": "tx-1",
            "norad_cat_id": 43689,
            "description": "APT",
            "downlink_low": 137100000,
            "downlink_high": 137200000,
        },
        "description": "APT",
        "mode": "",
        "status": "active",
        "service": "",
        "uplink_low_hz": None,
        "uplink_high_hz": None,
        "downlink_low_hz": 137100000,
        "downlink_high_hz": 137200000,
    })()
    source_dir = tmp_path / "sources"
    builder.save_source_files(source_dir, [record], [transmitter], "json")

    records, transmitters = builder.load_source_files(source_dir, "json")
    assert records[0].norad_id == 43689
    assert transmitters[0].raw["norad_cat_id"] == 43689

    output = tmp_path / "satellite.db"
    count, transmitter_count = __import__("asyncio").run(
        builder.build_database(output, from_files=source_dir)
    )
    assert (count, transmitter_count) == (1, 1)
    with Storage(output) as storage:
        loaded = storage.get_satellite("43689")
    assert loaded is not None
    assert loaded.transmitters[0].description == "APT"


def test_tle_source_files_round_trip(tmp_path):
    builder = _load_builder()
    record = type("Record", (), {
        "name": "ISS (ZARYA)",
        "line1": "1 25544U 98067A   24001.00000000  .00010000  00000-0  18000-3 0  9999",
        "line2": "2 25544  51.6400  10.0000 0005000  20.0000  30.0000 15.50000000123456",
    })()
    source_dir = tmp_path / "tle"
    builder.save_source_files(source_dir, [record], [], "tle")

    assert (source_dir / "ephemeris.tle").read_text(encoding="utf-8").splitlines() == [
        record.name,
        record.line1,
        record.line2,
    ]
    records, transmitters = builder.load_source_files(source_dir, "tle")
    assert records[0].norad_id == 25544
    assert records[0].line1 == record.line1
    assert transmitters == []
