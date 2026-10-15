import pytest

from satellite_forecast.maidenhead import decode, encode


def test_known_locator_round_trip() -> None:
    locator = encode(24.4798, 118.0894)
    decoded = decode(locator)

    assert locator == "OL94bl"
    assert abs(decoded.latitude - 24.4798) < 0.03
    assert abs(decoded.longitude - 118.0894) < 0.05
    assert decoded.bounds[0] < 24.4798 < decoded.bounds[2]


def test_locator_precision_and_validation() -> None:
    assert len(encode(0, 0, precision=1)) == 2
    assert len(encode(0, 0, precision=2)) == 4
    assert len(encode(0, 0, precision=3)) == 6
    with pytest.raises(ValueError):
        decode("OL6")
    with pytest.raises(ValueError):
        encode(0, 0, precision=4)
