"""AstrBot entrypoint for satellite forecast and transponder lookup."""

from __future__ import annotations

import re
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

# AstrBot 4.28 may import a plugin through its dotted module path without
# adding the plugin directory itself to sys.path. Make sibling plugin modules
# importable in both that environment and direct local execution.
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
try:
    from astrbot.api import AstrBotConfig
except ImportError:  # pragma: no cover - older AstrBot versions
    AstrBotConfig = dict

from satellite_forecast.geocoding import Location, resolve_location
from satellite_forecast.models import FollowedSatellite, PendingInteraction, SatelliteRecord as StoredSatellite, SatelliteTransmitter, UserLocation, UserSettings, utc_now
from satellite_forecast.prediction import is_geostationary, predict_passes
from satellite_forecast.satellite_search import SatelliteSearchIndex
from satellite_forecast.sources import CelesTrakSource, SatNOGSTransponderSource, SatelliteRecord as SourceSatellite
from satellite_forecast.storage import Storage
from satellite_forecast.time_utils import format_local_time, timezone_label


PLUGIN_NAME = "astrbot_plugin_satellite_forecast"


def _command_argument(message: str | None, command_name: str) -> str:
    """Extract arguments from the configured command entrypoint."""
    text = (message or "").strip()
    text = re.sub(r"^/?" + re.escape(command_name) + r"(?:\s+|$)", "", text, count=1, flags=re.IGNORECASE)
    return text.strip()


def _endpoint_label(endpoint: object) -> str:
    """Return a log-safe endpoint label without query strings or credentials."""
    try:
        parsed = urlsplit(str(endpoint))
        return parsed.netloc + parsed.path if parsed.netloc else parsed.path
    except ValueError:
        return "<invalid-endpoint>"


@register(PLUGIN_NAME, "lzh173", "查询卫星星历、转发器信息和可见过境，支持用户位置绑定。", "1.0.2")
class SatelliteForecastPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        configured_command = str(self.config.get("command_name", "sat")).strip().lstrip("/")
        if configured_command.lower() == "q":
            logger.warning("command_name=q is reserved and disabled; using /sat instead")
            configured_command = "sat"
        self.command_name = configured_command if re.fullmatch(r"[A-Za-z0-9_]+", configured_command) else "sat"
        self.debug_enabled = bool(self.config.get("debug_logging", False))
        data_dir = self.config.get("data_dir", "data/plugin_data/astrbot_plugin_satellite_forecast")
        self.storage = Storage(Path(data_dir) / "satellite.db")
        # Search indexes the storage model. Convert to the source model only
        # at the prediction boundary; their transmitter field names differ.
        self.index = SatelliteSearchIndex(self.storage.list_satellites())
        self._sync_task: asyncio.Task | None = None
        self._debug("constructed: data_dir=%s catalog_size=%d", data_dir, len(self.index.all()))

    def _command(self) -> str:
        return "/" + self.command_name

    def _debug(self, message: str, *args: object) -> None:
        """Emit detailed diagnostics only when explicitly enabled in config."""
        if self.debug_enabled:
            logger.debug("[satellite-forecast] " + message, *args)

    async def initialize(self):
        self._debug("initialize: sync_on_start=%s interval_hours=%s", self.config.get("sync_on_start", True),
                    self.config.get("sync_interval_hours", 24))
        logger.info("Satellite Forecast plugin initialized (debug_logging=%s)", self.debug_enabled)
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def terminate(self):
        self._debug("terminate: sync_task_exists=%s", self._sync_task is not None)
        if self._sync_task is not None:
            self._sync_task.cancel()
            await asyncio.gather(self._sync_task, return_exceptions=True)
        self.storage.close()
        self._debug("terminate: storage closed")

    async def _sync_loop(self):
        interval = max(3600, int(self.config.get("sync_interval_hours", 24) * 3600))
        sync_on_start = bool(self.config.get("sync_on_start", True))
        self._debug("sync loop started: interval_seconds=%d sync_on_start=%s", interval, sync_on_start)
        if sync_on_start:
            self._debug("sync loop: starting initial sync")
            await self._sync()
        while True:
            await asyncio.sleep(interval)
            self._debug("sync loop: starting scheduled sync")
            await self._sync()

    def _user_key(self, event: AstrMessageEvent) -> str:
        sender = getattr(event.message_obj, "sender", None)
        user_id = getattr(sender, "user_id", None) or getattr(sender, "id", None) or event.get_sender_name()
        key = f"{event.get_platform_name() or 'unknown'}:{user_id}"
        self._debug("resolved user key: platform=%s user_id_present=%s", event.get_platform_name() or "unknown", bool(user_id))
        return key

    @staticmethod
    def _to_source(item: StoredSatellite) -> SourceSatellite:
        from satellite_forecast.sources import Transponder

        transponders = [
            Transponder(description=transmitter.description, mode=transmitter.mode,
                        status=transmitter.status, service=transmitter.service,
                        uplink_low_hz=transmitter.uplink_low_hz,
                        uplink_high_hz=transmitter.uplink_high_hz,
                        downlink_low_hz=transmitter.downlink_low_hz,
                        downlink_high_hz=transmitter.downlink_high_hz)
            for transmitter in item.transmitters
        ]
        return SourceSatellite(name=item.name, aliases=item.aliases,
                               norad_id=int(item.norad_id) if item.norad_id.isdigit() else None,
                               line1=item.tle_line1, line2=item.tle_line2, source=item.source,
                               omm=item.omm,
                               transponders=transponders)

    @staticmethod
    def _to_stored(item: SourceSatellite) -> StoredSatellite:
        transmitters = tuple(
            SatelliteTransmitter(
                description=t.description or "",
                mode=t.mode or "",
                uplink=str(t.uplink_low_hz or ""),
                downlink=str(t.downlink_low_hz or ""),
                status=str(t.status or ""),
                service=str(t.service or ""),
                uplink_low_hz=t.uplink_low_hz,
                uplink_high_hz=t.uplink_high_hz,
                downlink_low_hz=t.downlink_low_hz,
                downlink_high_hz=t.downlink_high_hz,
            )
            for t in item.transponders
        )
        return StoredSatellite(norad_id=str(item.norad_id), name=item.name, aliases=tuple(item.aliases), source=item.source or "celestrak",
                                tle_line1=item.line1, tle_line2=item.line2, omm=dict(item.omm) if item.omm else None,
                                transmitters=transmitters)

    async def _sync(self) -> str:
        started = time.monotonic()
        try:
            source_config = self.config.get("celestrak", {})
            celestrak_endpoint = source_config.get("endpoint", "https://celestrak.org/NORAD/elements/gp.php")
            self._debug("sync: celestrak endpoint=%s group=%s format=%s", _endpoint_label(celestrak_endpoint),
                        source_config.get("group", "active"), source_config.get("format", "tle"))
            records = await CelesTrakSource(
                endpoint=celestrak_endpoint,
                group=source_config.get("group", "active"),
                format=source_config.get("format", "tle"),
            ).fetch()
            self._debug("sync: celestrak parsed records=%d", len(records))
            tx_config = self.config.get("satnogs", {})
            tx_endpoint = tx_config.get("endpoint", "https://db.satnogs.org/api/transmitters/")
            self._debug("sync: satnogs endpoint=%s", _endpoint_label(tx_endpoint))
            tx_source = SatNOGSTransponderSource(tx_endpoint)
            all_transmitters = await tx_source.fetch_all()
            self._debug("sync: satnogs parsed transmitters=%d", len(all_transmitters))
            by_norad = {}
            for transmitter in all_transmitters:
                raw = transmitter.raw
                satellite = raw.get("satellite")
                satellite_norad = satellite.get("norad_cat_id") if isinstance(satellite, dict) else None
                norad = (raw.get("norad_cat_id") or raw.get("satellite__norad_cat_id") or
                         raw.get("norad_id") or satellite_norad)
                if norad is not None:
                    by_norad.setdefault(str(norad), []).append(transmitter)
            stored = []
            for record in records:
                record.transponders = by_norad.get(str(record.norad_id), [])
                stored.append(self._to_stored(record))
            self.storage.save_satellites(stored)
            self.index.replace(stored)
            self._debug("sync completed: satellites=%d mapped_norad=%d elapsed=%.2fs", len(stored), len(by_norad), time.monotonic() - started)
            return f"更新完成：CelesTrak {len(stored)} 条。"
        except Exception as exc:  # noqa: BLE001
            logger.exception("satellite sync failed")
            self._debug("sync failed after %.2fs: %s", time.monotonic() - started, type(exc).__name__)
            return "更新失败，已保留原有数据。请稍后重试；如持续失败，请检查网络或代理设置。"

    def _find(self, query: str):
        matches = self.index.search(query, 10)
        self._debug("search: query_length=%d result_count=%d top=%s", len(query), len(matches),
                    [(m.satellite.name, round(m.score, 1)) for m in matches[:3]])
        if not matches:
            return None, "没有找到匹配的卫星，可输入名称、缩写或 NORAD 编号。"
        if len(matches) > 1 and matches[0].score < 100 and matches[1].score >= matches[0].score - 3:
            return None, "匹配到多颗卫星，请缩小搜索范围：\n" + "\n".join(
                f"{i}. {m.satellite.name}（NORAD {m.satellite.norad_id}）" for i, m in enumerate(matches[:5], 1))
        return matches[0].satellite, None

    def _save_selection(self, event: AstrMessageEvent, action: str, matches) -> None:
        """Keep a short-lived candidate list so the next message can be 1/2/3."""
        self.storage.set_pending_interaction(PendingInteraction(
            self._user_key(event),
            "choose_satellite",
            {"action": action, "norad_ids": [str(item.satellite.norad_id) for item in matches[:10]]},
            expires_at=utc_now() + timedelta(minutes=10),
        ))

    async def _consume_selection(self, event: AstrMessageEvent, argument: str) -> str | None:
        if not argument.isdigit():
            return None
        pending = self.storage.get_pending_interaction(self._user_key(event), "choose_satellite")
        if pending is None:
            return None
        index = int(argument) - 1
        candidates = pending.payload.get("norad_ids", [])
        if index < 0 or index >= len(candidates):
            return f"这个编号无效，请回复 1 到 {len(candidates)} 之间的数字。"
        item = self.storage.get_satellite(str(candidates[index]))
        self.storage.clear_pending_interaction(self._user_key(event), "choose_satellite")
        if item is None:
            return "候选卫星已不在本地目录，请重新搜索。"
        action = pending.payload.get("action")
        if action == "add":
            self.storage.add_followed_satellite(FollowedSatellite(
                self._user_key(event), item.norad_id, item.name, item.source
            ))
            return f"已关注：{item.name}（NORAD {item.norad_id}）"
        if action == "remove":
            removed = self.storage.remove_followed_satellite(self._user_key(event), item.norad_id)
            return "已从关注列表移除。" if removed else "这颗卫星不在你的关注列表中。"
        if action == "search":
            return f"已选择：{item.name}（NORAD {item.norad_id}）。可发送 {self._command()} add {item.name} 关注，或 {self._command()} 2 {item.name} 计算过境。"
        return f"已选择：{item.name}（NORAD {item.norad_id}）。"

    async def _user_location(self, event: AstrMessageEvent, query: str | None = None) -> Location | None:
        key = self._user_key(event)
        if query is not None:
            self._debug("location resolve requested: query_length=%d", len(query))
            result = await resolve_location(query)
            self.storage.set_location(UserLocation(key, result.latitude, result.longitude,
                                                   result.display_name or query, result.altitude_m))
            return result
        saved = self.storage.get_location(key)
        self._debug("location loaded: found=%s", saved is not None)
        return Location(saved.latitude, saved.longitude, saved.name, saved.altitude_m) if saved else None

    @staticmethod
    def _time(value: datetime, timezone_name: str) -> str:
        return format_local_time(value, timezone_name)

    @staticmethod
    def _timezone_label(timezone_name: str) -> str:
        return timezone_label(timezone_name)

    def _format_passes(self, satellite_name: str, norad_id: str, passes, timezone_name: str,
                       hours: int | None = None, *, is_geo: bool = False) -> str:
        """Format a pass list with each orbital milestone clearly labeled."""
        title = f"{satellite_name}（NORAD {norad_id}）过境预报"
        title += f"\n时间：{self._timezone_label(timezone_name)}"
        if hours is not None:
            title += f"\n预测范围：未来 {hours} 小时"
        if is_geo:
            title += "\n类型：GEO 地球静止轨道（AOS/EOS 可能是预测窗口边界）"
        lines = [title]
        for index, item in enumerate(passes[:5], 1):
            lines.extend((
                "",
                f"{index}. 过境时间：{self._time(item.aos, timezone_name)} 至 {self._time(item.eos, timezone_name)}",
                f"   最高仰角：{item.max_elevation_degrees:.1f}°（{self._time(item.max_elevation_time, timezone_name)}）",
                f"   最高点方向：{item.max_direction}（方位角 {item.max_azimuth_degrees:.1f}°）",
            ))
        return "\n".join(lines)

    async def _passes(self, event: AstrMessageEvent, query: str, hours: int = 24) -> str:
        self._debug("pass prediction requested: query=%s hours=%d", query[:80], hours)
        satellite, error = self._find(query)
        if error:
            return error
        location = await self._user_location(event)
        if location is None:
            return f"还没有绑定观测位置，请先发送 {self._command()} 1 厦门 或 {self._command()} 1 JO62QM。"
        settings = self.storage.get_settings(self._user_key(event))
        stored = self.storage.get_satellite(str(satellite.norad_id))
        if stored is None:
            return f"本地没有完整星历，请先发送 {self._command()} 6 更新数据。"
        result = predict_passes(self._to_source(stored), location, duration=timedelta(hours=hours),
                                minimum_elevation_degrees=settings.min_elevation_deg)
        self._debug("pass prediction completed: satellite=%s count=%d minimum_elevation=%s", stored.norad_id,
                    len(result), settings.min_elevation_deg)
        if not result:
            return f"未来 {hours} 小时内没有达到 {settings.min_elevation_deg:g}° 的过境。"
        return self._format_passes(stored.name, stored.norad_id, result, settings.timezone, hours,
                                   is_geo=is_geostationary(self._to_source(stored)))

    async def _passes_with_chart(self, event: AstrMessageEvent, query: str, hours: int = 24):
        """Return the text forecast and a best-effort chart for command replies."""
        text = await self._passes(event, query, hours)
        if text.startswith(("没有找到", "还没有绑定", "本地没有", "未来 ", "更新")):
            return text, None
        try:
            # Older or partially copied plugin directories can still provide
            # text forecasts while the optional chart files are being updated.
            from satellite_forecast.prediction import sample_pass_track
            from satellite_forecast.sky_chart import render_sky_chart

            satellite, error = self._find(query)
            location = await self._user_location(event)
            stored = self.storage.get_satellite(str(satellite.norad_id)) if satellite and location else None
            settings = self.storage.get_settings(self._user_key(event)) if location else None
            if not satellite or not location or not stored or not settings:
                return text, None
            passes = predict_passes(self._to_source(stored), location, duration=timedelta(hours=hours),
                                    minimum_elevation_degrees=settings.min_elevation_deg)
            if not passes:
                return text, None
            track = sample_pass_track(self._to_source(stored), location, passes[0])
            chart_dir = Path(self.config.get("data_dir", "data/plugin_data/astrbot_plugin_satellite_forecast")) / "charts"
            chart_kwargs = {
                "timezone_label": self._timezone_label(settings.timezone),
                "output_dir": chart_dir,
                "norad_id": stored.norad_id,
                "prediction": passes[0],
                "transmitters": stored.transmitters,
                "format_time": lambda value: self._time(value, settings.timezone),
            }
            try:
                chart = render_sky_chart(stored.name, track, **chart_kwargs)
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                # A partially updated deployment may still have the old chart
                # module. Keep image delivery working until it is synchronized.
                logger.warning("old sky_chart module detected; sync satellite_forecast/sky_chart.py")
                chart = render_sky_chart(
                    stored.name,
                    track,
                    timezone_label=chart_kwargs["timezone_label"],
                    output_dir=chart_dir,
                )
            self._debug("sky chart generated: path=%s size=%d", chart, chart.stat().st_size)
            return text, chart
        except Exception as exc:  # noqa: BLE001
            logger.warning("satellite sky chart unavailable: %s: %s", type(exc).__name__, exc)
            self._debug("sky chart generation failed: %s: %s", type(exc).__name__, exc)
            return text, None

    def _image_result(self, path):
        try:
            from astrbot.api.message_components import Image
            return Image.fromFileSystem(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("satellite sky chart image component unavailable: %s: %s", type(exc).__name__, exc)
            return None

    def _pass_result(self, text: str, chart):
        """Build one message chain so adapters receive text and image together."""
        image = self._image_result(chart) if chart else None
        if image is None:
            return None
        try:
            from astrbot.api.message_components import Plain
            return [Plain(text), image]
        except Exception as exc:  # noqa: BLE001
            logger.warning("satellite sky chart message chain unavailable: %s: %s", type(exc).__name__, exc)
            return None

    @staticmethod
    def _parse_pass_query(value: str) -> tuple[str, int]:
        """Parse a satellite query with an optional forecast duration."""
        parts = value.split()
        hours = 24
        if parts and re.fullmatch(r"\d+", parts[-1]):
            hours = int(parts.pop())
        if not parts:
            raise ValueError("请填写卫星名称")
        if not 1 <= hours <= 168:
            raise ValueError("预测小时数必须在 1 到 168 之间")
        return " ".join(parts), hours

    def _menu(self) -> str:
        command = self._command()
        return ("卫星过境助手\n"
                "回复数字选择功能：\n"
                "1. 观测位置（设置或查看）\n"
                "2. 关注卫星与过境预报\n"
                "3. 提醒设置\n"
                "4. 查看我的状态\n"
                "5. 搜索卫星\n"
                "6. 更新星历数据\n"
                f"常用命令：{command} 1 厦门｜{command} add METOP-C｜{command} 2 METOP-C\n"
                "也可以直接问：Metop-C 明天在 OL63 有哪些过境？\n"
                f"预测时长可写在末尾，例如：{command} pass METOP-C 48")

    @filter.event_message_type(EventMessageType.ALL)
    async def configured_command(self, event: AstrMessageEvent):
        """Dispatch the configured command name and pending numeric choices."""
        text = (event.message_str or "").strip()
        pattern = rf"^/?{re.escape(self.command_name)}(?:\s+|$)"
        is_command = re.match(pattern, text, flags=re.IGNORECASE) is not None
        is_pending_choice = text.isdigit() and self.storage.get_pending_interaction(
            self._user_key(event), "choose_satellite"
        ) is not None
        if not is_command and not is_pending_choice:
            return
        self._debug("command event received: configured_name=%s pending_choice=%s", self.command_name, is_pending_choice)
        async for result in self._command_handler(event):
            yield result

    async def _command_handler(self, event: AstrMessageEvent):
        """卫星预报菜单入口。"""
        argument = _command_argument(event.message_str, self.command_name)
        self._debug("command received: argument=%s", argument[:120] if argument else "<menu>")
        if not argument:
            yield event.plain_result(self._menu()); return
        selection = await self._consume_selection(event, argument)
        if selection is not None:
            yield event.plain_result(selection); return
        if argument == "1":
            saved = self.storage.get_location(self._user_key(event))
            yield event.plain_result(
                f"还没有设置观测位置。\n请发送：{self._command()} 1 地名或 Maidenhead 网格\n例如：{self._command()} 1 厦门 或 {self._command()} 1 OL63。"
                if not saved else
                f"当前观测位置：{saved.name}\n纬度：{saved.latitude:.4f}｜经度：{saved.longitude:.4f}\n"
                f"需要更换位置时，发送：{self._command()} 1 地名或网格。"
            ); return
        if argument.startswith("1 "):
            try:
                location = await self._user_location(event, argument[2:].strip())
                yield event.plain_result(f"观测位置已更新：{location.display_name or argument[2:].strip()}\n"
                                         f"坐标：{location.latitude:.4f}，{location.longitude:.4f}\n"
                                         f"现在可以发送 {self._command()} 2 卫星名称查询过境。")
            except Exception as exc:  # noqa: BLE001
                self._debug("location command failed: %s", type(exc).__name__)
                yield event.plain_result("暂时无法识别这个位置。\n请改用城市名称或 Maidenhead 网格，例如：厦门、OL63。")
            return
        if argument == "6":
            self._debug("command branch: manual sync")
            yield event.plain_result(await self._sync()); return
        if argument == "add" or argument == "remove" or argument == "del":
            yield event.plain_result(f"请补充卫星名称，例如：{self._command()} add METOP-C，或 {self._command()} del METOP-C。"); return
        if argument.startswith("add "):
            query = argument[4:].strip()
            matches = self.index.search(query, 10)
            satellite, error = self._find(query)
            if len(matches) > 1 and matches[0].score < 100 and matches[1].score >= matches[0].score - 3:
                self._save_selection(event, "add", matches)
                yield event.plain_result("匹配到多颗卫星，请回复编号选择：\n" + "\n".join(
                    f"{i}. {m.satellite.name}（NORAD {m.satellite.norad_id}）" for i, m in enumerate(matches[:5], 1)))
                return
            if error:
                yield event.plain_result(error); return
            self.storage.add_followed_satellite(FollowedSatellite(self._user_key(event), str(satellite.norad_id), satellite.name, satellite.source or ""))
            yield event.plain_result(f"已关注：{satellite.name}（NORAD {satellite.norad_id}）"); return
        if argument.startswith("del ") or argument.startswith("remove "):
            query = argument.split(" ", 1)[1].strip()
            matches = self.index.search(query, 10)
            if len(matches) > 1 and matches[0].score < 100 and matches[1].score >= matches[0].score - 3:
                self._save_selection(event, "remove", matches)
                yield event.plain_result("匹配到多颗卫星，请回复编号选择：\n" + "\n".join(
                    f"{i}. {m.satellite.name}（NORAD {m.satellite.norad_id}）" for i, m in enumerate(matches[:5], 1)))
                return
            satellite, error = self._find(query)
            if error:
                yield event.plain_result(error); return
            removed = self.storage.remove_followed_satellite(self._user_key(event), str(satellite.norad_id))
            yield event.plain_result("已从关注列表移除。" if removed else "这颗卫星不在你的关注列表中。"); return
        if argument == "3":
            settings = self.storage.get_settings(self._user_key(event))
            enabled = "已开启" if settings.notifications_enabled else "已关闭"
            yield event.plain_result(
                "提醒设置\n"
                f"状态：{enabled}\n"
                f"最低仰角：{settings.min_elevation_deg:g}°（低于此角度不提醒）\n"
                f"提前时间：{settings.reminder_minutes} 分钟\n"
                f"提醒次数：{settings.reminder_count} 次\n\n"
                "可直接发送以下命令修改：\n"
                f"{self._command()} set elevation 10  设置最低仰角\n"
                f"{self._command()} set before 6      设置提前分钟数\n"
                f"{self._command()} set repeat 1      设置提醒次数\n"
                f"{self._command()} set notify on     开启提醒参数\n"
                f"{self._command()} set notify off    关闭提醒参数\n\n"
                "说明：当前版本保存提醒参数，主动推送调度仍在接入中。"
            ); return
        if argument.startswith("set "):
            parts = argument.split()
            if len(parts) != 3 or parts[1] not in {"elevation", "before", "repeat", "notify"}:
                yield event.plain_result(f"用法示例：\n{self._command()} set elevation 10\n{self._command()} set before 6\n{self._command()} set repeat 1\n{self._command()} set notify on"); return
            try:
                key = self._user_key(event)
                old = self.storage.get_settings(key)
                if parts[1] == "notify":
                    if parts[2].lower() not in {"on", "off"}:
                        raise ValueError("notify must be on or off")
                    settings = UserSettings(key, old.min_elevation_deg, old.reminder_minutes,
                                            old.reminder_count, parts[2].lower() == "on",
                                            old.tle_storage_path, old.timezone)
                    self.storage.set_settings(settings)
                    yield event.plain_result(f"提醒参数已{'开启' if settings.notifications_enabled else '关闭'}。发送 {self._command()} 3 查看。")
                    return
                value = float(parts[2]) if parts[1] == "elevation" else int(parts[2])
                settings = UserSettings(key, value if parts[1] == "elevation" else old.min_elevation_deg,
                                        value if parts[1] == "before" else old.reminder_minutes,
                                        value if parts[1] == "repeat" else old.reminder_count,
                                        old.notifications_enabled, old.tle_storage_path, old.timezone)
                self.storage.set_settings(settings)
                yield event.plain_result("提醒参数已更新。发送 3 可查看当前设置。"); return
            except (TypeError, ValueError) as exc:
                self._debug("settings command failed: %s", type(exc).__name__)
                yield event.plain_result("设置无效，请填写数字。\n例如：" + f"{self._command()} set elevation 10"); return
        if argument.startswith("pass "):
            try:
                query, hours = self._parse_pass_query(argument[5:].strip())
            except ValueError as exc:
                yield event.plain_result(str(exc)); return
            text, chart = await self._passes_with_chart(event, query, hours)
            chain = self._pass_result(text, chart)
            yield event.chain_result(chain) if chain else event.plain_result(text)
            return
        if argument == "5":
            yield event.plain_result(f"请输入卫星名称、缩写或 NORAD 编号。\n例如：{self._command()} 5 metop")
            return
        if argument.startswith("5 "):
            results = self.index.search(argument[2:].strip(), 10)
            if not results:
                yield event.plain_result("没有找到匹配的卫星。")
                return
            self._save_selection(event, "search", results)
            yield event.plain_result("搜索结果如下，回复编号即可选择：\n" +
                "\n".join(f"{i}. {x.satellite.name}（NORAD {x.satellite.norad_id}）" for i, x in enumerate(results, 1)))
            return
        if argument.startswith("2 "):
            try:
                query, hours = self._parse_pass_query(argument[2:].strip())
            except ValueError as exc:
                yield event.plain_result(str(exc)); return
            text, chart = await self._passes_with_chart(event, query, hours)
            chain = self._pass_result(text, chart)
            yield event.chain_result(chain) if chain else event.plain_result(text)
            return
        if argument == "status":
            satellites, transmitters, updated = self.storage.catalog_stats()
            updated_text = self._time(updated, "Asia/Shanghai") if updated else "暂无"
            yield event.plain_result(f"数据状态：\n星历卫星：{satellites} 条\n转发器记录：{transmitters} 条\n最近更新：{updated_text}（北京时间）")
            return
        if argument in {"2", "4"}:
            followed = self.storage.list_followed_satellites(self._user_key(event))
            if argument == "4":
                location = self.storage.get_location(self._user_key(event))
                lines = [f"当前状态：\n位置：{location.name if location else '未设置'}",
                         f"关注卫星：{', '.join(x.display_name for x in followed) or '暂无'}"]
                if followed and location:
                    next_pass = await self._passes(event, followed[0].display_name, 24)
                    lines.append("\n" + next_pass)
                yield event.plain_result("\n".join(lines))
            else:
                text = ("关注卫星：\n" + "\n".join(
                    f"{i}. {x.display_name}（{x.norad_id}）" for i, x in enumerate(followed, 1)) +
                    f"\n发送 {self._command()} 2 卫星名称即可计算。") if followed else f"关注列表还是空的。发送 {self._command()} add 卫星名称添加一颗卫星。"
                yield event.plain_result(text)
            return
        yield event.plain_result("没有找到对应的菜单操作，请查看下面的选项：\n\n" + self._menu())

    @filter.llm_tool(name="resolve_observer_location")
    async def resolve_observer_location(self, event: AstrMessageEvent, location: str) -> str:
        """将地名或 Maidenhead 网格转换为经纬度。

        Args:
            location(string): 地名或 4/6 位网格。
        """
        try:
            self._debug("tool resolve_observer_location: input_length=%d", len(location))
            result = await resolve_location(location)
            return f"{result.display_name or location}：纬度 {result.latitude:.6f}，经度 {result.longitude:.6f}"
        except Exception as exc:  # noqa: BLE001
            self._debug("tool resolve_observer_location failed: %s", type(exc).__name__)
            return f"位置解析失败：{exc}"

    @filter.llm_tool(name="set_my_location")
    async def set_my_location(self, event: AstrMessageEvent, location: str, altitude_m: float = 0) -> str:
        """绑定当前用户观测位置。

        Args:
            location(string): 地名或网格。
            altitude_m(number): 海拔米数。
        """
        try:
            self._debug("tool set_my_location: input_length=%d altitude=%s", len(location), altitude_m)
            result = await resolve_location(location)
            self.storage.set_location(UserLocation(self._user_key(event), result.latitude, result.longitude, result.display_name or location, altitude_m))
            return f"已绑定：{result.display_name or location}（{result.latitude:.5f}, {result.longitude:.5f}）"
        except Exception as exc:  # noqa: BLE001
            self._debug("tool set_my_location failed: %s", type(exc).__name__)
            return f"位置绑定失败：{exc}"

    @filter.llm_tool(name="get_my_location")
    async def get_my_location(self, event: AstrMessageEvent) -> str:
        """查询当前用户绑定的观测位置。"""
        item = self.storage.get_location(self._user_key(event))
        self._debug("tool get_my_location: found=%s", item is not None)
        return "尚未绑定观测位置。" if item is None else f"{item.name}：{item.latitude:.5f}, {item.longitude:.5f}，海拔 {item.altitude_m:g} 米"

    @filter.llm_tool(name="list_my_satellites")
    async def list_my_satellites(self, event: AstrMessageEvent) -> str:
        """列出当前用户关注的卫星。"""
        items = self.storage.list_followed_satellites(self._user_key(event))
        self._debug("tool list_my_satellites: count=%d", len(items))
        return "暂无关注卫星。" if not items else "\n".join(f"{i}. {x.display_name}（NORAD {x.norad_id}）" for i, x in enumerate(items, 1))

    @filter.llm_tool(name="add_my_satellite")
    async def add_my_satellite(self, event: AstrMessageEvent, satellite: str) -> str:
        """按名称、缩写或 NORAD ID 关注卫星。

        Args:
            satellite(string): 卫星描述。
        """
        self._debug("tool add_my_satellite: query=%s", satellite[:80])
        item, error = self._find(satellite)
        if error:
            return error
        self.storage.add_followed_satellite(FollowedSatellite(self._user_key(event), str(item.norad_id), item.name, item.source or ""))
        return f"已关注：{item.name}（NORAD {item.norad_id}）"

    @filter.llm_tool(name="remove_my_satellite")
    async def remove_my_satellite(self, event: AstrMessageEvent, satellite: str) -> str:
        """从当前用户关注列表移除卫星。

        Args:
            satellite(string): 卫星描述。
        """
        self._debug("tool remove_my_satellite: query=%s", satellite[:80])
        item, error = self._find(satellite)
        if error:
            return error
        removed = self.storage.remove_followed_satellite(self._user_key(event), str(item.norad_id))
        return "已移除。" if removed else "这颗卫星不在你的关注列表中。"

    @filter.llm_tool(name="search_satellites")
    async def search_satellites(self, event: AstrMessageEvent, query: str) -> str:
        """按名称、缩写、NORAD ID 或描述模糊搜索卫星。

        Args:
            query(string): 卫星搜索词。
        """
        results = self.index.search(query, 10)
        self._debug("tool search_satellites: query=%s results=%d", query[:80], len(results))
        return "没有找到匹配的卫星。" if not results else \
            "\n".join(f"{i}. {x.satellite.name}（NORAD {x.satellite.norad_id}）" for i, x in enumerate(results, 1))

    @filter.llm_tool(name="predict_my_satellite_passes")
    async def predict_my_satellite_passes(self, event: AstrMessageEvent, satellite: str, hours: int = 24) -> str:
        """使用当前用户位置预测卫星过境。

        Args:
            satellite(string): 卫星名称、缩写或 NORAD ID。
            hours(number): 预测小时数。
        """
        self._debug("tool predict_my_satellite_passes: query=%s hours=%s", satellite[:80], hours)
        if not 1 <= hours <= 168:
            return "hours 必须在 1 到 168 之间。"
        return await self._passes(event, satellite, hours)

    @filter.llm_tool(name="predict_satellite_passes")
    async def predict_satellite_passes(self, event: AstrMessageEvent, satellite: str, latitude: float,
                                       longitude: float, altitude_m: float = 0, hours: int = 24) -> str:
        """根据经纬度预测指定卫星的过境。

        Args:
            satellite(string): 卫星名称、缩写或 NORAD ID。
            latitude(number): 观测点纬度，范围 -90 到 90。
            longitude(number): 观测点经度，范围 -180 到 180。
            altitude_m(number): 观测点海拔，单位为米。
            hours(number): 预测未来小时数，范围 1 到 168。
        """
        self._debug("tool predict_satellite_passes: query=%s lat=%s lon=%s hours=%s", satellite[:80], latitude, longitude, hours)
        if not 1 <= hours <= 168:
            return "hours 必须在 1 到 168 之间。"
        try:
            location = Location(latitude, longitude, altitude_m=altitude_m)
        except ValueError as exc:
            return f"观测点参数无效：{exc}"
        item, error = self._find(satellite)
        if error:
            return error
        stored = self.storage.get_satellite(str(item.norad_id))
        if stored is None:
            return "本地没有完整星历，请先更新数据。"
        settings = self.storage.get_settings(self._user_key(event))
        result = predict_passes(self._to_source(stored), location, duration=timedelta(hours=hours),
                                minimum_elevation_degrees=settings.min_elevation_deg)
        if not result:
            return f"未来 {hours} 小时内没有达到 {settings.min_elevation_deg:g}° 的过境。"
        return self._format_passes(stored.name, stored.norad_id, result, settings.timezone, hours)

    @filter.llm_tool(name="get_satellite_transmitters")
    async def get_satellite_transmitters(self, event: AstrMessageEvent, satellite: str) -> str:
        """查询指定卫星的 SatNOGS 转发器信息。

        Args:
            satellite(string): 卫星名称、缩写或 NORAD ID。
        """
        self._debug("tool get_satellite_transmitters: query=%s", satellite[:80])
        item, error = self._find(satellite)
        if error:
            return error
        stored = self.storage.get_satellite(str(item.norad_id))
        if stored is None or not stored.transmitters:
            return f"{item.name} 暂无已同步的转发器信息。"
        lines = [f"{stored.name}（NORAD {stored.norad_id}）转发器："]
        for index, transmitter in enumerate(stored.transmitters, 1):
            lines.append(f"{index}. {transmitter.description or '未命名'} | 模式 {transmitter.mode or '未知'} | "
                         f"上行 {transmitter.uplink or '未知'} | 下行 {transmitter.downlink or '未知'} | "
                         f"状态 {transmitter.status or '未知'}")
        return "\n".join(lines)
