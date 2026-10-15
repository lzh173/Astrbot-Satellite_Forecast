import httpx
import pytest

from satellite_forecast.geocoding import (
    Location,
    NominatimGeocoder,
    maidenhead_to_location,
    resolve_location,
)


def test_maidenhead_locator_returns_center():
    location = maidenhead_to_location("OK03")
    assert location.latitude == pytest.approx(13.5)
    assert location.longitude == pytest.approx(101.0)


@pytest.mark.asyncio
async def test_nominatim_endpoint_is_injectable():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "Xiamen"
        assert request.headers["user-agent"] == "test-agent"
        return httpx.Response(200, json=[{"lat": "24.4798", "lon": "118.0894", "display_name": "Xiamen"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        geocoder = NominatimGeocoder("https://test/search", user_agent="test-agent", client=client)
        result = await resolve_location("Xiamen", geocoder)
    assert result == Location(24.4798, 118.0894, "Xiamen")


@pytest.mark.asyncio
async def test_resolve_location_does_not_call_nominatim_for_locator():
    class FailingGeocoder:
        async def geocode(self, query):
            raise AssertionError("should resolve locally")

    result = await resolve_location("PM53", FailingGeocoder())
    assert result.display_name == "PM53"
