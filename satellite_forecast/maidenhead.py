"""Encode and decode amateur-radio Maidenhead grid locators."""

from __future__ import annotations

from dataclasses import dataclass


FIELD_ALPHABET = "ABCDEFGHIJKLMNOPQR"
SUBSQUARE_ALPHABET = "abcdefghijklmnopqrstuvwx"


@dataclass(frozen=True, slots=True)
class MaidenheadBounds:
    """The center and bounds represented by a Maidenhead locator."""

    locator: str
    latitude: float
    longitude: float
    latitude_span: float
    longitude_span: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Return south, west, north, east coordinates."""

        return (
            self.latitude - self.latitude_span / 2,
            self.longitude - self.longitude_span / 2,
            self.latitude + self.latitude_span / 2,
            self.longitude + self.longitude_span / 2,
        )


def encode(latitude: float, longitude: float, precision: int = 3) -> str:
    """Return a Maidenhead locator with one to three field pairs."""

    if not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")
    if precision not in (1, 2, 3):
        raise ValueError("precision must be 1, 2, or 3")

    # The north/east edges belong to the last cell rather than a new cell.
    lat = min(latitude, 89.999999999)
    lon = min(longitude, 179.999999999)
    lat += 90.0
    lon += 180.0
    lat_span, lon_span = 20.0, 20.0
    result: list[str] = []

    result.append(FIELD_ALPHABET[int(lon // 20)])
    result.append(FIELD_ALPHABET[int(lat // 10)])
    lon %= 20
    lat %= 10
    if precision == 1:
        return "".join(result)

    result.append(str(int(lon // 2)))
    result.append(str(int(lat // 1)))
    lon %= 2
    lat %= 1
    lon_span, lat_span = 2.0, 1.0
    if precision == 2:
        return "".join(result)

    result.append(SUBSQUARE_ALPHABET[int(lon / (2.0 / 24.0))])
    result.append(SUBSQUARE_ALPHABET[int(lat / (1.0 / 24.0))])
    return "".join(result)


def decode(locator: str) -> MaidenheadBounds:
    """Decode a two, four, or six character locator to its cell center."""

    value = locator.strip()
    if len(value) not in (2, 4, 6):
        raise ValueError("locator must contain 2, 4, or 6 characters")
    if value[0].upper() not in FIELD_ALPHABET or value[1].upper() not in FIELD_ALPHABET:
        raise ValueError("invalid Maidenhead field")
    if len(value) >= 4 and (not value[2].isdigit() or not value[3].isdigit()):
        raise ValueError("invalid Maidenhead square")
    if len(value) == 6 and (
        value[4].lower() not in SUBSQUARE_ALPHABET
        or value[5].lower() not in SUBSQUARE_ALPHABET
    ):
        raise ValueError("invalid Maidenhead subsquare")

    longitude = FIELD_ALPHABET.index(value[0].upper()) * 20.0 - 180.0
    latitude = FIELD_ALPHABET.index(value[1].upper()) * 10.0 - 90.0
    longitude_span, latitude_span = 20.0, 10.0
    if len(value) >= 4:
        longitude += int(value[2]) * 2.0
        latitude += int(value[3])
        longitude_span, latitude_span = 2.0, 1.0
    if len(value) == 6:
        longitude += SUBSQUARE_ALPHABET.index(value[4].lower()) * (2.0 / 24.0)
        latitude += SUBSQUARE_ALPHABET.index(value[5].lower()) * (1.0 / 24.0)
        longitude_span, latitude_span = 2.0 / 24.0, 1.0 / 24.0
    return MaidenheadBounds(
        locator=value,
        latitude=latitude + latitude_span / 2,
        longitude=longitude + longitude_span / 2,
        latitude_span=latitude_span,
        longitude_span=longitude_span,
    )
