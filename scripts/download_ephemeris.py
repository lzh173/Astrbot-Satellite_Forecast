#!/usr/bin/env python3
"""Download satellite data and build a database for offline plugin use.

Run this script on a machine that can reach CelesTrak and SatNOGS, then copy
the generated SQLite file to AstrBot's configured ``data_dir`` as
``satellite.db``. The script intentionally has no AstrBot dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import traceback
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from satellite_forecast.models import SatelliteRecord as StoredSatellite, SatelliteTransmitter
from satellite_forecast.sources import CelesTrakSource, SatNOGSTransponderSource
from satellite_forecast.storage import Storage


DEFAULT_CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
DEFAULT_SATNOGS = "https://db.satnogs.org/api/transmitters/"


def _system_proxy(url: str) -> str | None:
    """Return the system proxy for ``url`` without exposing credentials."""
    parsed = urlsplit(url)
    if not parsed.hostname or proxy_bypass(parsed.hostname):
        return None
    proxies = getproxies()
    return proxies.get(parsed.scheme) or proxies.get("all")


def _proxy_label(proxy: str | None) -> str:
    if not proxy:
        return "direct"
    parsed = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    return f"{parsed.hostname or '<proxy>'}:{parsed.port or 80}"


def _stored_satellite(record, transmitters) -> StoredSatellite:
    return StoredSatellite(
        norad_id=str(record.norad_id),
        name=record.name,
        aliases=tuple(record.aliases),
        source=record.source or "celestrak",
        tle_line1=record.line1,
        tle_line2=record.line2,
        omm=dict(record.omm) if record.omm else None,
        transmitters=tuple(
            SatelliteTransmitter(
                description=item.description or "",
                mode=item.mode or "",
                status=str(item.status or ""),
                service=str(item.service or ""),
                uplink_low_hz=item.uplink_low_hz,
                uplink_high_hz=item.uplink_high_hz,
                downlink_low_hz=item.downlink_low_hz,
                downlink_high_hz=item.downlink_high_hz,
            )
            for item in transmitters
        ),
    )


async def build_database(
    output: Path,
    *,
    celestrak_endpoint: str = DEFAULT_CELESTRAK,
    group: str = "active",
    data_format: str = "json",
    satnogs_endpoint: str = DEFAULT_SATNOGS,
    skip_transmitters: bool = False,
    timeout: float = 60.0,
    proxy: str | None = None,
    use_system_proxy: bool = True,
) -> tuple[int, int]:
    """Download sources and atomically create ``output``."""

    headers = {
        "User-Agent": "AstrBot-Satellite-Forecast-offline-builder/0.1",
        "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
    }
    if proxy is None and use_system_proxy:
        proxy = _system_proxy(celestrak_endpoint) or _system_proxy(satnogs_endpoint)
    async with httpx.AsyncClient(
        timeout=timeout, headers=headers, follow_redirects=True, proxy=proxy
    ) as client:
        try:
            records = await CelesTrakSource(
                endpoint=celestrak_endpoint,
                group=group,
                format=data_format,
                client=client,
            ).fetch()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"CelesTrak download failed: {type(exc).__name__}: {exc!r}"
            ) from exc
        transmitters = []
        if not skip_transmitters:
            try:
                transmitters = await SatNOGSTransponderSource(
                    satnogs_endpoint, client=client
                ).fetch_all()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"SatNOGS download failed: {type(exc).__name__}: {exc!r}"
                ) from exc

    by_norad = {}
    for item in transmitters:
        raw = item.raw
        satellite = raw.get("satellite")
        nested_norad = satellite.get("norad_cat_id") if isinstance(satellite, dict) else None
        norad = (
            raw.get("norad_cat_id")
            or raw.get("satellite__norad_cat_id")
            or raw.get("norad_id")
            or nested_norad
        )
        if norad is not None:
            by_norad.setdefault(str(norad), []).append(item)

    stored = [
        _stored_satellite(record, by_norad.get(str(record.norad_id), []))
        for record in records
        if record.norad_id is not None
    ]
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="satellite-db-", dir=output.parent) as temp_dir:
        temporary_output = Path(temp_dir) / output.name
        with Storage(temporary_output) as storage:
            storage.save_satellites(stored)
        temporary_output.replace(output)
    return len(stored), len(transmitters)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="下载星历和转发器信息并生成 satellite.db")
    parser.add_argument("-o", "--output", type=Path, default=Path("satellite.db"), help="输出 SQLite 文件路径")
    parser.add_argument("--celestrak-endpoint", default=DEFAULT_CELESTRAK, help="CelesTrak 或兼容源 URL")
    parser.add_argument("--group", default="active", help="CelesTrak GROUP 参数")
    parser.add_argument("--format", dest="data_format", choices=("json", "tle"), default="json", help="CelesTrak 数据格式，默认 json 以保留 OMM")
    parser.add_argument("--satnogs-endpoint", default=DEFAULT_SATNOGS, help="SatNOGS 或兼容源 URL")
    parser.add_argument("--skip-transmitters", action="store_true", help="只下载星历，不下载转发器")
    parser.add_argument("--timeout", type=float, default=60.0, help="单次 HTTP 请求超时秒数")
    parser.add_argument("--debug", action="store_true", help="失败时输出完整 Python traceback")
    parser.add_argument("--proxy", help="手动指定代理 URL；默认自动使用系统代理")
    parser.add_argument("--no-proxy", action="store_true", help="强制直连，忽略系统代理")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        satellites, transmitters = asyncio.run(build_database(
            args.output,
            celestrak_endpoint=args.celestrak_endpoint,
            group=args.group,
            data_format=args.data_format,
            satnogs_endpoint=args.satnogs_endpoint,
            skip_transmitters=args.skip_transmitters,
            timeout=args.timeout,
            proxy=args.proxy,
            use_system_proxy=not args.no_proxy,
        ))
    except Exception as exc:  # noqa: BLE001
        message = f"generation failed [{type(exc).__name__}]: {exc!r}"
        print(message, file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1
    selected_proxy = None if args.no_proxy else (args.proxy or _system_proxy(DEFAULT_CELESTRAK))
    print(f"生成完成：{args.output.resolve()}")
    print(f"星历：{satellites} 条；转发器：{transmitters} 条")
    print(f"网络：{_proxy_label(selected_proxy)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
