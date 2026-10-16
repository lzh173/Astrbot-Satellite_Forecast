"""Render a compact polar sky chart for a satellite pass."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .prediction import TrackPoint


def _font(size: int):
    try:
        return ImageFont.truetype("msyh.ttc", size)
    except OSError:
        return ImageFont.load_default()


def render_sky_chart(satellite_name: str, track: list[TrackPoint], *, timezone_label: str,
                     output_dir: str | Path | None = None) -> Path:
    """Render north-up, zenith-centred altitude/azimuth coordinates."""
    if not track:
        raise ValueError("track must not be empty")
    width = height = 720
    image = Image.new("RGB", (width, height), "#101820")
    draw = ImageDraw.Draw(image)
    center = (width // 2, 390)
    radius = 250
    for altitude in (0, 30, 60, 90):
        ring = radius * (1 - altitude / 90)
        box = (center[0] - ring, center[1] - ring, center[0] + ring, center[1] + ring)
        draw.ellipse(box, outline="#405466", width=2)
        if altitude < 90:
            draw.text((center[0] + 8, center[1] - ring - 18), f"{altitude}°", fill="#9fb3c8", font=_font(16))
    for azimuth, label in ((0, "北"), (90, "东"), (180, "南"), (270, "西")):
        angle = math.radians(azimuth - 90)
        end = (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))
        draw.line((center, end), fill="#304554", width=1)
        draw.text((center[0] + (radius + 24) * math.cos(angle) - 10,
                   center[1] + (radius + 24) * math.sin(angle) - 10), label,
                  fill="#dbe9f4", font=_font(20))
    points = []
    for item in track:
        distance = radius * (1 - max(0.0, min(90.0, item.elevation_degrees)) / 90)
        angle = math.radians(item.azimuth_degrees - 90)
        points.append((center[0] + distance * math.cos(angle), center[1] + distance * math.sin(angle)))
    if len(points) > 1:
        draw.line(points, fill="#43d9a3", width=5, joint="curve")
    for point, color in ((points[0], "#f4b860"), (points[-1], "#ed6a5a")):
        draw.ellipse((point[0] - 7, point[1] - 7, point[0] + 7, point[1] + 7), fill=color)
    draw.text((36, 28), f"{satellite_name} 过境天空轨迹", fill="#f4f7f9", font=_font(28))
    draw.text((36, 68), f"中心：天顶 ｜ 外圈：地平线 ｜ {timezone_label}", fill="#9fb3c8", font=_font(17))
    draw.text((36, 650), "黄色：开始  ·  红色：结束  ·  绿色：可见轨迹", fill="#dbe9f4", font=_font(17))
    target_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir())
    target_dir.mkdir(parents=True, exist_ok=True)
    output = target_dir / f"satellite-sky-{next(tempfile._get_candidate_names())}.png"
    image.save(output, format="PNG")
    return output
