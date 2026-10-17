from datetime import datetime, timedelta, timezone

from PIL import Image

from satellite_forecast.models import SatelliteTransmitter
from satellite_forecast.prediction import PassPrediction, TrackPoint
from satellite_forecast.sky_chart import render_sky_chart


def test_render_loop_keeps_table_inside_canvas(tmp_path):
    start = datetime(2026, 10, 18, tzinfo=timezone.utc)
    track = [TrackPoint(start + timedelta(minutes=i), 10 + i * 2, 180 + i) for i in range(20)]
    prediction = PassPrediction("METEOR-M2 4", track[0].time, track[-1].time, 72, track[10].time, 180, "南")
    transmitters = tuple(
        SatelliteTransmitter(description=f"LRPT S-E RHCP D{i} K", mode="LRPT", downlink_low_hz=137100000 + i * 800000,
                              status="active")
        for i in range(12)
    )

    output = render_sky_chart("METEOR-M2 4", track, timezone_label="北京时间（UTC+8）",
                              norad_id="59051", prediction=prediction, transmitters=transmitters,
                              output_dir=tmp_path)
    with Image.open(output) as image:
        assert image.size == (1080, 760)
        assert image.getbbox() == (0, 0, 1080, 760)
