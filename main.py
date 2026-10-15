"""AstrBot entrypoint for satellite forecast and transponder lookup."""

from __future__ import annotations

import re
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register
try:
    from astrbot.api import AstrBotConfig
except ImportError:  # pragma: no cover - older AstrBot versions
    AstrBotConfig = dict

from satellite_forecast.geocoding import Location, resolve_location
from satellite_forecast.models import FollowedSatellite, SatelliteRecord as StoredSatellite, SatelliteTransmitter, UserLocation, UserSettings
from satellite_forecast.prediction import predict_passes
from satellite_forecast.satellite_search import SatelliteSearchIndex
from satellite_forecast.sources import CelesTrakSource, SatNOGSTransponderSource, SatelliteRecord as SourceSatellite
from satellite_forecast.storage import Storage


PLUGIN_NAME = "astrbot_plugin_satellite_forecast"


@register(PLUGIN_NAME, "lzh173", "查询卫星星历、转发器信息和可见过境，支持用户位置绑定。", "0.1.0")
class SatelliteForecastPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        data_dir = self.config.get("data_dir", "data/plugin_data/astrbot_plugin_satellite_forecast")
        self.storage = Storage(Path(data_dir) / "satellite.db")
        self.index = SatelliteSearchIndex(self._to_source(s) for s in self.storage.list_satellites())
        self._sync_task: asyncio.Task | None = None

    async def initialize(self):
        logger.info("Satellite Forecast plugin initialized")
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def terminate(self):
        if self._sync_task is not None:
            self._sync_task.cancel()
            await asyncio.gather(self._sync_task, return_exceptions=True)
        self.storage.close()

    async def _sync_loop(self):
        interval = max(3600, int(self.config.get("sync_interval_hours", 24) * 3600))
        sync_on_start = bool(self.config.get("sync_on_start", True))
        if sync_on_start:
            await self._sync()
        while True:
            await asyncio.sleep(interval)
            await self._sync()

    def _user_key(self, event: AstrMessageEvent) -> str:
        sender = getattr(event.message_obj, "sender", None)
        user_id = getattr(sender, "user_id", None) or getattr(sender, "id", None) or event.get_sender_name()
        return f"{event.get_platform_name() or 'unknown'}:{user_id}"

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
                                tle_line1=item.line1, tle_line2=item.line2, transmitters=transmitters)

    async def _sync(self) -> str:
        try:
            source_config = self.config.get("celestrak", {})
            records = await CelesTrakSource(
                endpoint=source_config.get("endpoint", "https://celestrak.org/NORAD/elements/gp.php"),
                group=source_config.get("group", "active"),
                format=source_config.get("format", "tle"),
            ).fetch()
            tx_config = self.config.get("satnogs", {})
            tx_source = SatNOGSTransponderSource(tx_config.get("endpoint", "https://db.satnogs.org/api/transmitters/"))
            all_transmitters = await tx_source.fetch_all()
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
            self.index.replace(self._to_source(item) for item in stored)
            return f"更新完成：CelesTrak {len(stored)} 条。"
        except Exception as exc:  # noqa: BLE001
            logger.exception("satellite sync failed")
            return f"更新失败，已保留旧数据：{exc}"

    def _find(self, query: str):
        matches = self.index.search(query, 10)
        if not matches:
            return None, "没有找到匹配的卫星，可输入名称、缩写或 NORAD 编号。"
        if len(matches) > 1 and matches[0].score < 100 and matches[1].score >= matches[0].score - 3:
            return None, "找到多个候选，请缩小范围：\n" + "\n".join(
                f"{i}. {m.satellite.name}（NORAD {m.satellite.norad_id}）" for i, m in enumerate(matches[:5], 1))
        return matches[0].satellite, None

    async def _user_location(self, event: AstrMessageEvent, query: str | None = None) -> Location | None:
        key = self._user_key(event)
        if query is not None:
            result = await resolve_location(query)
            self.storage.set_location(UserLocation(key, result.latitude, result.longitude,
                                                   result.display_name or query, result.altitude_m))
            return result
        saved = self.storage.get_location(key)
        return Location(saved.latitude, saved.longitude, saved.name, saved.altitude_m) if saved else None

    @staticmethod
    def _time(value: datetime, timezone_name: str) -> str:
        try:
            return value.astimezone(ZoneInfo(timezone_name)).strftime("%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            return value.astimezone(timezone.utc).strftime("%m-%d %H:%M:%S UTC")

    async def _passes(self, event: AstrMessageEvent, query: str, hours: int = 24) -> str:
        satellite, error = self._find(query)
        if error:
            return error
        location = await self._user_location(event)
        if location is None:
            return "还没有绑定观测位置，请先发送 /q 1 厦门 或 /q 1 JO62QM。"
        settings = self.storage.get_settings(self._user_key(event))
        stored = self.storage.get_satellite(str(satellite.norad_id))
        if stored is None:
            return "本地没有完整星历，请先发送 /q 6 更新数据。"
        result = predict_passes(self._to_source(stored), location, duration=timedelta(hours=hours),
                                minimum_elevation_degrees=settings.min_elevation_deg)
        if not result:
            return f"未来 {hours} 小时内没有达到 {settings.min_elevation_deg:g}° 的过境。"
        lines = [f"{stored.name}（NORAD {stored.norad_id}）过境："]
        for item in result[:5]:
            lines.append(f"AOS {self._time(item.aos, settings.timezone)} | 最大仰角 {item.max_elevation_degrees:.1f}° "
                         f"{self._time(item.max_elevation_time, settings.timezone)} "
                         f"{item.max_azimuth_degrees:.1f}°（{item.max_direction}） | EOS {self._time(item.eos, settings.timezone)}")
        return "\n".join(lines)

    def _menu(self) -> str:
        return ("卫星预报：\n1. 定观测位置\n2. 挑卫星算过境\n3. 改提醒设置\n4. 现在的状态\n"
                "5. 搜索卫星\n6. 更新星历和转发器\n0. 收工\n\n"
                "直接问：/q ai 明天厦门有啥值得收的过境")

    @filter.command("q")
    async def q(self, event: AstrMessageEvent):
        """卫星预报菜单入口。"""
        argument = re.sub(r"^/q\b", "", event.message_str.strip(), flags=re.I).strip()
        if not argument:
            yield event.plain_result(self._menu()); return
        if argument == "0":
            yield event.plain_result("收工。\n"); return
        if argument == "1":
            saved = self.storage.get_location(self._user_key(event))
            yield event.plain_result("当前没有绑定位置，请使用 /q 1 地名或网格。" if not saved else
                                     f"当前位置：{saved.name}（{saved.latitude:.4f}, {saved.longitude:.4f}）"); return
        if argument.startswith("1 "):
            try:
                location = await self._user_location(event, argument[2:].strip())
                yield event.plain_result(f"位置已绑定：{location.display_name or argument[2:].strip()}（{location.latitude:.4f}, {location.longitude:.4f}）")
            except Exception as exc:  # noqa: BLE001
                yield event.plain_result(f"位置解析失败：{exc}")
            return
        if argument == "6":
            yield event.plain_result(await self._sync()); return
        if argument.startswith("add "):
            satellite, error = self._find(argument[4:].strip())
            if error:
                yield event.plain_result(error); return
            self.storage.add_followed_satellite(FollowedSatellite(self._user_key(event), str(satellite.norad_id), satellite.name, satellite.source or ""))
            yield event.plain_result(f"已关注：{satellite.name}（NORAD {satellite.norad_id}）"); return
        if argument.startswith("del ") or argument.startswith("remove "):
            query = argument.split(" ", 1)[1].strip()
            satellite, error = self._find(query)
            if error:
                yield event.plain_result(error); return
            removed = self.storage.remove_followed_satellite(self._user_key(event), str(satellite.norad_id))
            yield event.plain_result("已从关注列表移除。" if removed else "这颗卫星不在你的关注列表中。"); return
        if argument == "3":
            settings = self.storage.get_settings(self._user_key(event))
            yield event.plain_result(f"提醒设置：最低仰角 {settings.min_elevation_deg:g}°，提前 {settings.reminder_minutes} 分钟，提醒 {settings.reminder_count} 次。\n"
                                     "修改示例：/q set elevation 10"); return
        if argument.startswith("set "):
            parts = argument.split()
            if len(parts) != 3 or parts[1] not in {"elevation", "before", "repeat"}:
                yield event.plain_result("用法：/q set elevation 10、/q set before 6、/q set repeat 1"); return
            try:
                key = self._user_key(event)
                old = self.storage.get_settings(key)
                value = float(parts[2]) if parts[1] == "elevation" else int(parts[2])
                settings = UserSettings(key, value if parts[1] == "elevation" else old.min_elevation_deg,
                                        value if parts[1] == "before" else old.reminder_minutes,
                                        value if parts[1] == "repeat" else old.reminder_count,
                                        old.notifications_enabled, old.tle_storage_path, old.timezone)
                self.storage.set_settings(settings)
                yield event.plain_result("提醒设置已更新。"); return
            except (TypeError, ValueError) as exc:
                yield event.plain_result(f"设置无效：{exc}"); return
        if argument.startswith("pass "):
            yield event.plain_result(await self._passes(event, argument[5:].strip())); return
        if argument.startswith("5 "):
            results = self.index.search(argument[2:].strip(), 10)
            yield event.plain_result("没有找到匹配的卫星。" if not results else
                "\n".join(f"{i}. {x.satellite.name}（NORAD {x.satellite.norad_id}）" for i, x in enumerate(results, 1)))
            return
        if argument.startswith("2 "):
            yield event.plain_result(await self._passes(event, argument[2:].strip()))
            return
        if argument in {"2", "4", "5"}:
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
                    "\n发送 /q 2 卫星名称 可直接计算。") if followed else "还没有关注卫星。用 /q add 卫星名称 添加。"
                yield event.plain_result(text)
            return
        if argument.lower().startswith("ai "):
            prompt = argument[3:].strip()
            try:
                provider_id = await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)
                if not provider_id:
                    yield event.plain_result("当前没有可用的 LLM 提供商。"); return
                from astrbot.core.agent.tool import ToolSet
                manager = self.context.get_llm_tool_manager()
                tools = ToolSet()
                for tool in getattr(manager, "func_list", []):
                    tools.add_tool(tool)
                response = await self.context.tool_loop_agent(
                    event=event,
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    tools=tools,
                    max_steps=10,
                    tool_call_timeout=60,
                )
                text = getattr(response, "completion_text", None) or str(response)
                yield event.plain_result(text)
            except Exception as exc:  # noqa: BLE001
                logger.exception("/q ai failed")
                yield event.plain_result(f"AI 查询失败：{exc}")
            return
        yield event.plain_result("无法识别这个选项。\n\n" + self._menu())

    @filter.llm_tool(name="resolve_observer_location")
    async def resolve_observer_location(self, event: AstrMessageEvent, location: str) -> MessageEventResult:
        """将地名或 Maidenhead 网格转换为经纬度。

        Args:
            location(string): 地名或 4/6 位网格。
        """
        try:
            result = await resolve_location(location)
            return event.plain_result(f"{result.display_name or location}：纬度 {result.latitude:.6f}，经度 {result.longitude:.6f}")
        except Exception as exc:  # noqa: BLE001
            return event.plain_result(f"位置解析失败：{exc}")

    @filter.llm_tool(name="set_my_location")
    async def set_my_location(self, event: AstrMessageEvent, location: str, altitude_m: float = 0) -> MessageEventResult:
        """绑定当前用户观测位置。

        Args:
            location(string): 地名或网格。
            altitude_m(number): 海拔米数。
        """
        try:
            result = await resolve_location(location)
            self.storage.set_location(UserLocation(self._user_key(event), result.latitude, result.longitude, result.display_name or location, altitude_m))
            return event.plain_result(f"已绑定：{result.display_name or location}（{result.latitude:.5f}, {result.longitude:.5f}）")
        except Exception as exc:  # noqa: BLE001
            return event.plain_result(f"位置绑定失败：{exc}")

    @filter.llm_tool(name="get_my_location")
    async def get_my_location(self, event: AstrMessageEvent) -> MessageEventResult:
        """查询当前用户绑定的观测位置。"""
        item = self.storage.get_location(self._user_key(event))
        return event.plain_result("尚未绑定观测位置。" if item is None else f"{item.name}：{item.latitude:.5f}, {item.longitude:.5f}，海拔 {item.altitude_m:g} 米")

    @filter.llm_tool(name="list_my_satellites")
    async def list_my_satellites(self, event: AstrMessageEvent) -> MessageEventResult:
        """列出当前用户关注的卫星。"""
        items = self.storage.list_followed_satellites(self._user_key(event))
        return event.plain_result("暂无关注卫星。" if not items else "\n".join(f"{i}. {x.display_name}（NORAD {x.norad_id}）" for i, x in enumerate(items, 1)))

    @filter.llm_tool(name="add_my_satellite")
    async def add_my_satellite(self, event: AstrMessageEvent, satellite: str) -> MessageEventResult:
        """按名称、缩写或 NORAD ID 关注卫星。

        Args:
            satellite(string): 卫星描述。
        """
        item, error = self._find(satellite)
        if error:
            return event.plain_result(error)
        self.storage.add_followed_satellite(FollowedSatellite(self._user_key(event), str(item.norad_id), item.name, item.source or ""))
        return event.plain_result(f"已关注：{item.name}（NORAD {item.norad_id}）")

    @filter.llm_tool(name="remove_my_satellite")
    async def remove_my_satellite(self, event: AstrMessageEvent, satellite: str) -> MessageEventResult:
        """从当前用户关注列表移除卫星。

        Args:
            satellite(string): 卫星描述。
        """
        item, error = self._find(satellite)
        if error:
            return event.plain_result(error)
        removed = self.storage.remove_followed_satellite(self._user_key(event), str(item.norad_id))
        return event.plain_result("已移除。" if removed else "这颗卫星不在你的关注列表中。")

    @filter.llm_tool(name="search_satellites")
    async def search_satellites(self, event: AstrMessageEvent, query: str) -> MessageEventResult:
        """按名称、缩写、NORAD ID 或描述模糊搜索卫星。

        Args:
            query(string): 卫星搜索词。
        """
        results = self.index.search(query, 10)
        return event.plain_result("没有找到匹配的卫星。" if not results else
                                  "\n".join(f"{i}. {x.satellite.name}（NORAD {x.satellite.norad_id}）" for i, x in enumerate(results, 1)))

    @filter.llm_tool(name="predict_my_satellite_passes")
    async def predict_my_satellite_passes(self, event: AstrMessageEvent, satellite: str, hours: int = 24) -> MessageEventResult:
        """使用当前用户位置预测卫星过境。

        Args:
            satellite(string): 卫星名称、缩写或 NORAD ID。
            hours(number): 预测小时数。
        """
        if not 1 <= hours <= 168:
            return event.plain_result("hours 必须在 1 到 168 之间。")
        return event.plain_result(await self._passes(event, satellite, hours))

    @filter.llm_tool(name="predict_satellite_passes")
    async def predict_satellite_passes(self, event: AstrMessageEvent, satellite: str, latitude: float,
                                       longitude: float, altitude_m: float = 0, hours: int = 24) -> MessageEventResult:
        """根据经纬度预测指定卫星的过境。

        Args:
            satellite(string): 卫星名称、缩写或 NORAD ID。
            latitude(number): 观测点纬度，范围 -90 到 90。
            longitude(number): 观测点经度，范围 -180 到 180。
            altitude_m(number): 观测点海拔，单位为米。
            hours(number): 预测未来小时数，范围 1 到 168。
        """
        if not 1 <= hours <= 168:
            return event.plain_result("hours 必须在 1 到 168 之间。")
        try:
            location = Location(latitude, longitude, altitude_m=altitude_m)
        except ValueError as exc:
            return event.plain_result(f"观测点参数无效：{exc}")
        item, error = self._find(satellite)
        if error:
            return event.plain_result(error)
        stored = self.storage.get_satellite(str(item.norad_id))
        if stored is None:
            return event.plain_result("本地没有完整星历，请先更新数据。")
        settings = self.storage.get_settings(self._user_key(event))
        result = predict_passes(self._to_source(stored), location, duration=timedelta(hours=hours),
                                minimum_elevation_degrees=settings.min_elevation_deg)
        if not result:
            return event.plain_result(f"未来 {hours} 小时内没有达到 {settings.min_elevation_deg:g}° 的过境。")
        return event.plain_result("\n".join(
            [f"{stored.name}（NORAD {stored.norad_id}）过境："] +
            [f"AOS {self._time(p.aos, settings.timezone)} | 最大仰角 {p.max_elevation_degrees:.1f}° "
             f"{self._time(p.max_elevation_time, settings.timezone)} {p.max_azimuth_degrees:.1f}°（{p.max_direction}） | "
             f"EOS {self._time(p.eos, settings.timezone)}" for p in result[:5]]
        ))

    @filter.llm_tool(name="get_satellite_transmitters")
    async def get_satellite_transmitters(self, event: AstrMessageEvent, satellite: str) -> MessageEventResult:
        """查询指定卫星的 SatNOGS 转发器信息。

        Args:
            satellite(string): 卫星名称、缩写或 NORAD ID。
        """
        item, error = self._find(satellite)
        if error:
            return event.plain_result(error)
        stored = self.storage.get_satellite(str(item.norad_id))
        if stored is None or not stored.transmitters:
            return event.plain_result(f"{item.name} 暂无已同步的转发器信息。")
        lines = [f"{stored.name}（NORAD {stored.norad_id}）转发器："]
        for index, transmitter in enumerate(stored.transmitters, 1):
            lines.append(f"{index}. {transmitter.description or '未命名'} | 模式 {transmitter.mode or '未知'} | "
                         f"上行 {transmitter.uplink or '未知'} | 下行 {transmitter.downlink or '未知'} | "
                         f"状态 {transmitter.status or '未知'}")
        return event.plain_result("\n".join(lines))
