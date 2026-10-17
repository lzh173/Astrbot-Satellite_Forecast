"""Render a compact polar sky chart for a satellite pass."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .prediction import PassPrediction, TrackPoint


_USEFUL_TRANSMITTER_TERMS = (
    "sstv", "ahrpt", "lrpt", "hrpt", "transponder", "转发器",
    "apt", "fm", "ssb", "cw", "beacon", "信标",
)


def _transmitter_priority(item) -> tuple[int, str]:
    text = " ".join(str(getattr(item, field, "") or "") for field in ("description", "mode", "service")).lower()
    rank = next((index for index, term in enumerate(_USEFUL_TRANSMITTER_TERMS) if term in text), len(_USEFUL_TRANSMITTER_TERMS))
    return rank, text


def _frequency(value: float | None) -> str:
    if value is None:
        return "-"
    value = float(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:g} MHz"
    if value >= 1_000:
        return f"{value / 1_000:g} kHz"
    return f"{value:g} Hz"


def _transmitter_lines(transmitters) -> list[str]:
    selected = sorted(transmitters or (), key=_transmitter_priority)
    lines = []
    for item in selected[:5]:
        label = (getattr(item, "description", "") or getattr(item, "mode", "") or "未命名").strip()
        mode = (getattr(item, "mode", "") or "未知").strip()
        downlink = _frequency(getattr(item, "downlink_low_hz", None))
        if downlink == "-":
            downlink = (getattr(item, "downlink", "") or "-").strip()
        uplink = _frequency(getattr(item, "uplink_low_hz", None))
        if uplink == "-":
            uplink = (getattr(item, "uplink", "") or "-").strip()
        status = (getattr(item, "status", "") or "未知").strip()
        lines.append(f"{label[:22]} | {mode[:10]} | 下行 {downlink} | 上行 {uplink} | {status[:8]}")
    return lines


def _font(size: int):
    try:
        return ImageFont.truetype("msyh.ttc", size)
    except OSError:
        return ImageFont.load_default()


def render_sky_chart(satellite_name: str, track: list[TrackPoint], *, timezone_label: str,
                     output_dir: str | Path | None = None,
                     norad_id: str | None = None,
                     prediction: PassPrediction | None = None,
                     transmitters=(),
                     format_time=None) -> Path:
    """Render north-up, zenith-centred altitude/azimuth coordinates."""
    if not track:
        raise ValueError("track must not be empty")
    width, height = 1080, 760
    image = Image.new("RGB", (width, height), "#101820")
    draw = ImageDraw.Draw(image)
    center = (300, 390)
    radius = 230
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
    draw.text((36, 20), f"{satellite_name} 过境天空轨迹", fill="#f4f7f9", font=_font(26))
    identifier = f"NORAD {norad_id}" if norad_id else ""
    draw.text((36, 54), f"{identifier} ｜ {timezone_label}", fill="#9fb3c8", font=_font(17))
    if prediction is not None:
        formatter = format_time or (lambda value: value.strftime("%m-%d %H:%M:%S"))
        details = (
            f"AOS {formatter(prediction.aos)}   EOS {formatter(prediction.eos)}\n"
            f"最高仰角 {prediction.max_elevation_degrees:.1f}°   "
            f"最高点 {formatter(prediction.max_elevation_time)}"
        )
        draw.multiline_text((36, 82), details, fill="#dbe9f4", font=_font(16), spacing=4)
    draw.text((36, 710), "黄色：开始  ·  红色：结束  ·  绿色：可见轨迹",
              fill="#dbe9f4", font=_font(17))

    # Keep communication details in a separate right-hand table so long
    # frequency and status values never compete with the sky plot.
    panel_left, panel_top, panel_right, panel_bottom = 570, 24, 1050, 720
    draw.rounded_rectangle((panel_left, panel_top, panel_right, panel_bottom), radius=8,
                           fill="#172632", outline="#405466", width=2)
    draw.text((panel_left + 24, panel_top + 22), "收发器 / 转发器",
              fill="#f4f7f9", font=_font(24))
    draw.text((panel_left + 24, panel_top + 58), "优先显示 SSTV、AHRPT、LRPT、HRPT 和转发器",
              fill="#9fb3c8", font=_font(14))
    columns = (("类型", 24, 18), ("模式", 170, 12), ("下行", 250, 16), ("上行", 350, 16), ("状态", 430, 12))
    header_y = panel_top + 106
    draw.line((panel_left + 20, header_y + 28, panel_right - 20, header_y + 28), fill="#405466", width=1)
    for label, offset, size in columns:
        draw.text((panel_left + offset, header_y), label, fill="#f4b860", font=_font(size))
    tx_lines = _transmitter_lines(transmitters)
    for index, line in enumerate(tx_lines):
        fields = [field.strip() for field in line.split("|")]
        row_y = header_y + 44 + index * 42
        draw.line((panel_left + 20, row_y + 28, panel_right - 20, row_y + 28), fill="#2b414f", width=1)
        for column_index, (_, offset, size) in enumerate(columns):
            value = fields[column_index] if column_index < len(fields) else "-"
            draw.text((panel_left + offset, row_y), value[:20], fill="#dbe9f4", font=_font(size))
    if not tx_lines:
        draw.text((panel_left + 24, header_y + 48), "暂无可用收发器数据",
                  fill="#9fb3c8", font=_font(17))
    target_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir())
    target_dir.mkdir(parents=True, exist_ok=True)
    output = target_dir / f"satellite-sky-{next(tempfile._get_candidate_names())}.png"
    image.save(output, format="PNG")
    return output
