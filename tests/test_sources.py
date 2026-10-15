import json

import httpx
import pytest

from satellite_forecast.sources import (
    CelesTrakSource,
    CustomSource,
    SatNOGSTransponderSource,
    parse_celestrak_gp_json,
    parse_celestrak_tle,
    parse_satnogs_transponders,
)


TLE = """ISS (ZARYA)
1 25544U 98067A   24001.00000000  .00010000  00000-0  18000-3 0  9999
2 25544  51.6400  10.0000 0005000  20.0000  30.0000 15.50000000123456
"""


def test_parse_celestrak_tle():
    records = parse_celestrak_tle(TLE)
    assert len(records) == 1
    assert records[0].name == "ISS (ZARYA)"
    assert records[0].norad_id == 25544
    assert records[0].line2.startswith("2 25544")


def test_parse_celestrak_gp_json_preserves_omm():
    records = parse_celestrak_gp_json(
        json.dumps(
            {
                "OBJECT_NAME": "METOP-C",
                "OBJECT_ID": "2018-087A",
                "NORAD_CAT_ID": 43689,
                "EPOCH": "2024-01-01T00:00:00.000Z",
                "MEAN_MOTION": 14.2,
            }
        )
    )
    assert records[0].name == "METOP-C"
    assert records[0].norad_id == 43689
    assert records[0].aliases == ("2018-087A",)
    assert records[0].omm["MEAN_MOTION"] == 14.2


def test_parse_satnogs_transponders_accepts_common_fields():
    result = parse_satnogs_transponders(
        [{"uuid": "abc", "description": "UHF", "downlink_low": 145800000, "mode": "FM"}]
    )
    assert result[0].uuid == "abc"
    assert result[0].downlink_low_hz == 145800000
    assert result[0].mode == "FM"


@pytest.mark.asyncio
async def test_source_adapters_allow_injected_http_client():
    def handler(request: httpx.Request) -> httpx.Response:
        if "transmitters" in request.url.path:
            return httpx.Response(200, json=[{"uuid": "t1", "mode": "FM"}])
        return httpx.Response(200, json=[{"OBJECT_NAME": "TEST", "NORAD_CAT_ID": 1}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        celestrak = await CelesTrakSource("https://test/gp", client=client).fetch()
        transmitters = await SatNOGSTransponderSource("https://test/transmitters", client=client).fetch(1)
    assert celestrak[0].name == "TEST"
    assert transmitters[0].uuid == "t1"


@pytest.mark.asyncio
async def test_custom_source_uses_normalized_parser():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"name": "custom"}))
    ) as client:
        source = CustomSource(
            "https://test/custom",
            lambda payload: [parse_celestrak_gp_json({"OBJECT_NAME": payload["name"]})[0]],
            client=client,
        )
        result = await source.fetch()
    assert result[0].name == "custom"


@pytest.mark.asyncio
async def test_satnogs_fetch_all_follows_next_page():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(200, json={"results": [{"uuid": "a"}], "next": "https://test/transmitters/?page=2"})
        return httpx.Response(200, json={"results": [{"uuid": "b"}], "next": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await SatNOGSTransponderSource("https://test/transmitters/", client=client).fetch_all()
    assert [item.uuid for item in result] == ["a", "b"]
    assert len(calls) == 2
