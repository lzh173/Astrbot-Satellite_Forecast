from satellite_forecast.models import SatelliteRecord
from satellite_forecast.satellite_search import SatelliteSearchIndex


def test_search_matches_name_alias_and_norad_id() -> None:
    index = SatelliteSearchIndex(
        [
            SatelliteRecord("40069", "METOP-C", aliases=("metop c",), source="celestrak"),
            SatelliteRecord("28654", "FENGYUN 2G", aliases=("FY2G",), source="satnogs"),
            SatelliteRecord("25544", "ISS", aliases=("ZARYA",), source="celestrak"),
        ]
    )

    assert index.search("metop-c")[0].satellite.norad_id == "40069"
    assert index.search("fy2g")[0].satellite.norad_id == "28654"
    assert index.search("25544")[0].satellite.name == "ISS"


def test_search_is_ranked_and_handles_empty_queries() -> None:
    index = SatelliteSearchIndex(
        [SatelliteRecord("1", "NOAA 1", source="celestrak"), SatelliteRecord("2", "NOAA 2", source="celestrak")]
    )

    results = index.search("noaa", limit=1)
    assert len(results) == 1
    assert results[0].score >= 90
    assert index.search("   ") == []
    assert index.search("noaa", limit=0) == []
