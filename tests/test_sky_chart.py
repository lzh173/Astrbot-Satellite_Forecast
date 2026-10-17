from satellite_forecast.models import SatelliteTransmitter
from satellite_forecast.sky_chart import _frequency, _transmitter_lines


def test_chart_prioritizes_useful_transmitter_modes_and_formats_frequencies():
    items = [
        SatelliteTransmitter(description="Experimental", mode="UHF", downlink_low_hz=435000000),
        SatelliteTransmitter(description="SSTV", mode="V", downlink_low_hz=145800000,
                             uplink_low_hz=435000000, status="active"),
        SatelliteTransmitter(description="LRPT", mode="AHRPT", downlink_low_hz=1_700_000_000),
    ]

    lines = _transmitter_lines(items)

    assert lines[0].startswith("SSTV | V")
    assert "145.8M" in lines[0]
    assert "435M" in lines[0]
    assert lines[1].startswith("LRPT | AHRPT")
    assert _frequency(137_100_000) == "137.1 MHz"
