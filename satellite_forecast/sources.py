"""Satellite data source models, parsers, and HTTP source adapters.

The parsers intentionally accept both decoded JSON and response text so that
they can be used by the plugin and by offline tests without a network client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Protocol

import httpx


@dataclass(slots=True)
class Transponder:
    """A SatNOGS transmitter/receiver entry."""

    uuid: str | None = None
    description: str | None = None
    mode: str | None = None
    uplink_low_hz: float | None = None
    uplink_high_hz: float | None = None
    downlink_low_hz: float | None = None
    downlink_high_hz: float | None = None
    baud: float | None = None
    status: str | None = None
    service: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SatelliteRecord:
    """Normalized satellite data shared by all source adapters.

    CelesTrak TLE records have ``line1`` and ``line2``.  CelesTrak GP JSON
    records retain their original orbital-element mapping in ``omm`` and can
    be converted to a Skyfield satellite by :mod:`satellite_forecast.prediction`.
    """

    name: str
    aliases: tuple[str, ...] = ()
    norad_id: int | None = None
    object_id: str | None = None
    line1: str | None = None
    line2: str | None = None
    omm: Mapping[str, Any] | None = None
    transponders: list[Transponder] = field(default_factory=list)
    source: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


class SourceParser(Protocol):
    def __call__(self, payload: Any) -> list[SatelliteRecord]: ...


def _decode_json(payload: Any) -> Any:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def _first(data: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in data and data[name] not in (None, ""):
            return data[name]
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _mapping_items(data: Any) -> list[Mapping[str, Any]]:
    data = _decode_json(data)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    if isinstance(data, Mapping):
        for key in ("data", "results", "items", "satellites", "transmitters"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        return [data]
    raise ValueError("JSON payload must be an object or an array")


def parse_celestrak_tle(payload: str | bytes) -> list[SatelliteRecord]:
    """Parse the classic CelesTrak three-line TLE format."""

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    lines = [line.strip() for line in payload.splitlines() if line.strip()]
    records: list[SatelliteRecord] = []
    index = 0
    while index < len(lines):
        if lines[index].startswith("1 "):
            name = f"NORAD {_int_or_none(lines[index][2:7]) or ''}".strip()
            line1 = lines[index]
            if index + 1 >= len(lines) or not lines[index + 1].startswith("2 "):
                raise ValueError(f"TLE line 2 is missing after line {index + 1}")
            line2 = lines[index + 1]
            index += 2
        else:
            if index + 2 >= len(lines):
                raise ValueError("incomplete TLE record")
            name, line1, line2 = lines[index : index + 3]
            if not line1.startswith("1 ") or not line2.startswith("2 "):
                raise ValueError(f"invalid TLE record near line {index + 1}")
            index += 3
        records.append(
            SatelliteRecord(
                name=name,
                norad_id=_int_or_none(line1[2:7]),
                line1=line1,
                line2=line2,
                source="celestrak-tle",
                raw={"name": name, "line1": line1, "line2": line2},
            )
        )
    return records


def parse_celestrak_gp_json(payload: Any) -> list[SatelliteRecord]:
    """Parse CelesTrak ``FORMAT=json`` GP/OMM records."""

    records: list[SatelliteRecord] = []
    for item in _mapping_items(payload):
        name = str(_first(item, "OBJECT_NAME", "object_name", "name") or "Unknown")
        object_id = _first(item, "OBJECT_ID", "object_id")
        records.append(
            SatelliteRecord(
                name=name,
                aliases=(str(object_id),) if object_id else (),
                norad_id=_int_or_none(_first(item, "NORAD_CAT_ID", "norad_cat_id", "norad_id")),
                object_id=object_id,
                omm=dict(item),
                source="celestrak-gp",
                raw=dict(item),
            )
        )
    return records


def parse_celestrak(payload: Any, format: str | None = None) -> list[SatelliteRecord]:
    """Parse either CelesTrak TLE text or GP JSON."""

    if format == "tle":
        return parse_celestrak_tle(payload)
    if format in {"json", "gp", "omm"}:
        return parse_celestrak_gp_json(payload)
    if isinstance(payload, (bytes, bytearray)):
        try:
            return parse_celestrak_gp_json(payload)
        except (json.JSONDecodeError, ValueError):
            return parse_celestrak_tle(payload)
    if isinstance(payload, str):
        try:
            return parse_celestrak_gp_json(payload)
        except (json.JSONDecodeError, ValueError):
            return parse_celestrak_tle(payload)
    return parse_celestrak_gp_json(payload)


def parse_satnogs_transponders(payload: Any) -> list[Transponder]:
    """Parse the list/object variants returned by the SatNOGS DB API."""

    result: list[Transponder] = []
    for item in _mapping_items(payload):
        result.append(
            Transponder(
                uuid=str(_first(item, "uuid", "id")) if _first(item, "uuid", "id") is not None else None,
                description=_first(item, "description", "name"),
                mode=_first(item, "mode", "modulation"),
                uplink_low_hz=_float_or_none(_first(item, "uplink_low", "uplink_low_hz")),
                uplink_high_hz=_float_or_none(_first(item, "uplink_high", "uplink_high_hz")),
                downlink_low_hz=_float_or_none(_first(item, "downlink_low", "downlink_low_hz")),
                downlink_high_hz=_float_or_none(_first(item, "downlink_high", "downlink_high_hz")),
                baud=_float_or_none(_first(item, "baud", "symbol_rate")),
                status=_first(item, "status", "alive"),
                service=_first(item, "service", "type"),
                raw=dict(item),
            )
        )
    return result


class SatelliteSource(Protocol):
    async def fetch(self) -> list[SatelliteRecord]: ...


class CelesTrakSource:
    """Download and parse a CelesTrak TLE or GP JSON endpoint."""

    def __init__(
        self,
        endpoint: str = "https://celestrak.org/NORAD/elements/gp.php",
        *,
        group: str = "active",
        format: str = "json",
        headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.group = group
        self.format = format
        self.headers = {
            "User-Agent": "AstrBot-Satellite-Forecast/0.1 (+https://github.com/lzh173/Astrbot-Satellite_Forecast)",
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
        }
        if headers:
            self.headers.update(headers)
        self.client = client

    async def fetch(self) -> list[SatelliteRecord]:
        params = {"GROUP": self.group, "FORMAT": self.format}
        if self.client is None:
            async with httpx.AsyncClient(timeout=30, headers=self.headers) as client:
                response = await client.get(self.endpoint, params=params)
        else:
            # Explicitly merge client defaults so httpx's default User-Agent
            # cannot replace the source adapter's descriptive identity.
            request_headers = httpx.Headers(self.client.headers)
            for name, value in self.headers.items():
                request_headers[name] = value
            response = await self.client.get(self.endpoint, params=params, headers=request_headers)
        response.raise_for_status()
        payload: Any = response.json() if self.format in {"json", "gp", "omm"} else response.text
        return parse_celestrak(payload, self.format)


class SatNOGSTransponderSource:
    """Download transmitter information from SatNOGS DB."""

    def __init__(
        self,
        endpoint: str = "https://db.satnogs.org/api/transmitters/",
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.client = client

    async def fetch(self, norad_id: int | str | None = None) -> list[Transponder]:
        params = {"satellite__norad_cat_id": str(norad_id)} if norad_id is not None else None
        if self.client is None:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(self.endpoint, params=params)
        else:
            response = await self.client.get(self.endpoint, params=params)
        response.raise_for_status()
        return parse_satnogs_transponders(response.json())

    async def fetch_all(self) -> list[Transponder]:
        """Fetch all pages when the API returns a paginated response."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=30) as client:
                return await self._fetch_all_with_client(client)
        return await self._fetch_all_with_client(self.client)

    async def _fetch_all_with_client(self, client: httpx.AsyncClient) -> list[Transponder]:
        url: str | None = self.endpoint
        params: Mapping[str, Any] | None = None
        result: list[Transponder] = []
        while url:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            result.extend(parse_satnogs_transponders(payload))
            next_url = payload.get("next") if isinstance(payload, Mapping) else None
            url = str(next_url) if next_url else None
            params = None
        return result


class CustomSource:
    """Adapter for a user-provided endpoint and parser.

    ``parser`` receives the decoded response body.  A custom parser can return
    any normalized ``SatelliteRecord`` objects, which keeps custom source
    formats isolated from the rest of the plugin.
    """

    def __init__(
        self,
        endpoint: str,
        parser: SourceParser,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.parser = parser
        self.params = params
        self.headers = headers
        self.client = client

    async def fetch(self) -> list[SatelliteRecord]:
        if self.client is None:
            async with httpx.AsyncClient(timeout=30, headers=self.headers) as client:
                response = await client.get(self.endpoint, params=self.params)
        else:
            response = await self.client.get(
                self.endpoint, params=self.params, headers=self.headers
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        return self.parser(payload)


async def merge_transponders(
    records: list[SatelliteRecord], source: SatNOGSTransponderSource
) -> list[SatelliteRecord]:
    """Attach SatNOGS entries to records, preserving records without NORAD IDs."""

    for record in records:
        if record.norad_id is not None:
            record.transponders = await source.fetch(record.norad_id)
    return records
