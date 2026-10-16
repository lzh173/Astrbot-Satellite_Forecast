"""Location resolution from Maidenhead locators and public geocoding APIs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class Location:
    latitude: float
    longitude: float
    display_name: str | None = None
    altitude_m: float = 0.0


_MAIDENHEAD = re.compile(r"^[A-Ra-r]{2}[0-9]{2}(?:[A-Xa-x]{2}(?:[0-9]{2})?)?$")


def maidenhead_to_location(locator: str) -> Location:
    """Convert a 4, 6, or 8 character Maidenhead locator to its center."""

    locator = locator.strip().upper()
    if not _MAIDENHEAD.fullmatch(locator):
        raise ValueError("Maidenhead locator must be 4, 6, or 8 characters")

    longitude = -180.0 + (ord(locator[0]) - ord("A")) * 20.0
    latitude = -90.0 + (ord(locator[1]) - ord("A")) * 10.0
    longitude += int(locator[2]) * 2.0
    latitude += int(locator[3])
    longitude_step, latitude_step = 2.0, 1.0

    if len(locator) >= 6:
        longitude_step, latitude_step = 5.0 / 60.0, 2.5 / 60.0
        longitude += (ord(locator[4]) - ord("A")) * longitude_step
        latitude += (ord(locator[5]) - ord("A")) * latitude_step
    if len(locator) == 8:
        longitude_step, latitude_step = 0.5 / 60.0, 0.25 / 60.0
        longitude += int(locator[6]) * longitude_step
        latitude += int(locator[7]) * latitude_step

    return Location(
        latitude=latitude + latitude_step / 2,
        longitude=longitude + longitude_step / 2,
        display_name=locator,
    )


class NominatimGeocoder:
    """Async Nominatim client with an injectable endpoint/client for tests."""

    def __init__(
        self,
        endpoint: str = "https://nominatim.openstreetmap.org/search",
        *,
        user_agent: str = "astrbot-satellite-forecast/0.1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.user_agent = user_agent
        self.client = client

    async def geocode(self, query: str) -> Location:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        params = {"q": query.strip(), "format": "jsonv2", "limit": 1}
        headers = {"User-Agent": self.user_agent}
        if self.client is None:
            async with httpx.AsyncClient(timeout=20, headers=headers) as client:
                response = await client.get(self.endpoint, params=params)
        else:
            response = await self.client.get(self.endpoint, params=params, headers=headers)
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list) or not results:
            raise LookupError(f"location not found: {query}")
        result: dict[str, Any] = results[0]
        try:
            return Location(
                latitude=float(result["lat"]),
                longitude=float(result["lon"]),
                display_name=result.get("display_name") or query.strip(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Nominatim returned an invalid location") from exc


class OpenMeteoGeocoder:
    """Keyless geocoder that is generally more reachable from mainland China."""

    def __init__(
        self,
        endpoint: str = "https://geocoding-api.open-meteo.com/v1/search",
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.client = client

    async def geocode(self, query: str) -> Location:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        params = {"name": query.strip(), "count": 1, "language": "zh", "format": "json"}
        if self.client is None:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(self.endpoint, params=params)
        else:
            response = await self.client.get(self.endpoint, params=params)
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            raise LookupError(f"location not found: {query}")
        result = results[0]
        try:
            name = str(result.get("name") or query.strip())
            admin = ", ".join(str(result[key]) for key in ("admin1", "country") if result.get(key))
            return Location(
                latitude=float(result["latitude"]),
                longitude=float(result["longitude"]),
                display_name=f"{name}（{admin}）" if admin else name,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Open-Meteo returned an invalid location") from exc


async def resolve_location(
    query: str,
    geocoder: NominatimGeocoder | OpenMeteoGeocoder | None = None,
) -> Location:
    """Resolve a Maidenhead locator locally, then use Open-Meteo and Nominatim."""

    normalized = query.strip()
    if not normalized:
        raise ValueError("location must not be empty")
    if _MAIDENHEAD.fullmatch(normalized):
        return maidenhead_to_location(normalized)
    if geocoder is not None:
        return await geocoder.geocode(normalized)

    errors: list[str] = []
    for provider in (OpenMeteoGeocoder(), NominatimGeocoder()):
        try:
            return await provider.geocode(normalized)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(provider).__name__}: {str(exc) or type(exc).__name__}")
    raise LookupError(f"无法解析位置“{normalized}”；" + "；".join(errors))
