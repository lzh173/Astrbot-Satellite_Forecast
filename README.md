# AstrBot Satellite Forecast

AstrBot satellite forecast plugin. It downloads TLE data from CelesTrak, reads transmitter data from SatNOGS, stores the local catalog in SQLite, and calculates satellite passes for each user's observing location.

## Install

Clone this repository into AstrBot's plugin directory:

```text
AstrBot/data/plugins/astrbot_plugin_satellite_forecast
```

Install the plugin dependencies from `requirements.txt`, then reload the plugin in AstrBot WebUI. The runtime database is stored under AstrBot's data directory and is not stored in the plugin source tree.

## Commands

默认菜单命令为 `/sat`。可以在插件配置中将 `command_name` 改为 `X`，之后使用 `/X` 打开菜单。配置值可以填写 `X` 或 `/X`，建议选择不会与其他机器人冲突的名称；插件不会注册固定的 `/q` 命令。

```text
/sat
/sat 1 Xiamen
/sat 1 PM53
/sat 2 metop-c
/sat 5 fy2g
/sat add metop-c
/sat del metop-c
/sat pass metop-c
/sat set elevation 10
/sat 4
/sat 6
```

`/sat 1` binds a location to the current user. Locations support place names and Maidenhead locators. A user location is shared across that user's private and group conversations on the same platform.

Satellite search accepts names, aliases, descriptions, international designators, and NORAD catalog IDs. Inputs such as `metop-c` and `fy2g` are supported when they exist in the synchronized catalog.

`/sat 2` lists followed satellites; `/sat 2 metop-c` calculates visible passes directly. `/sat 4` shows the bound location, followed satellites, and a next-pass summary. `/sat 5 fy2g` performs a fuzzy catalog search. When a menu command needs more information, the reply includes an example and the next action to take. After a search returns numbered candidates, simply reply with the number.

When a search or `/sat add` command returns multiple candidates, reply with its number to select one. `/sat 5` without a query shows usage instead of listing followed satellites. Pass times default to `Asia/Shanghai`; an explicitly saved user timezone is respected.

## LLM tools

The plugin registers tools for resolving a place name or Maidenhead locator, binding and reading the current user's location, fuzzy satellite search, managing followed satellites, and predicting passes using the current user's location.

The plugin's tools remain available to AstrBot's normal LLM conversation. For example, try asking with the robot wake word: `Metop-C明天在OL63有什么过境`.

## Configuration

The included `_conf_schema.json` exposes startup synchronization, the daily synchronization interval, a data directory, and CelesTrak/SatNOGS-compatible endpoints. CelesTrak supports `tle` and `json` formats. A compatible mirror can be supplied by changing the endpoint while keeping the corresponding format. CelesTrak requests include a descriptive `User-Agent`. The Python source adapter also accepts an optional `headers` mapping for programmatic/custom integrations; it is intentionally not exposed as an arbitrary object in the AstrBot 4.28 schema because that version requires every object to declare fixed `items`.

`debug_logging` is disabled by default. Enable it in the plugin configuration when diagnosing synchronization, command parsing, fuzzy search, location resolution, pass prediction, or LLM tool calls. The detailed logs intentionally record lengths, counts, identifiers, and sanitized endpoint paths instead of complete prompts, credentials, or exact personal coordinates.

## Data sources

- CelesTrak: active satellite TLE data.
- SatNOGS DB: transmitter records associated with the NORAD catalog ID.
- Open-Meteo Geocoding: primary keyless place-name geocoding, including Chinese place names.
- OpenStreetMap Nominatim: fallback place-name geocoding when Open-Meteo is unavailable.

The source parser interfaces are kept separate so a configured mirror can provide CelesTrak-compatible or SatNOGS-compatible payloads later.

## Offline database builder

如果运行 AstrBot 的服务器无法访问 CelesTrak 或 SatNOGS，可以在另一台能联网的机器上生成数据库：

```powershell
python scripts/download_ephemeris.py --output satellite.db
```

脚本默认自动读取系统代理（Windows 系统代理、macOS/Linux 系统代理及代理环境变量），无需额外配置。成功时会输出实际使用的代理地址（不会输出用户名和密码）。如果需要手动指定代理，可使用 `--proxy http://127.0.0.1:7890`；排障时可用 `--no-proxy` 强制直连。

如果失败信息不完整，可增加 `--debug` 查看异常类型和完整 traceback；例如 `ReadTimeout` 表示网络超时，`HTTPStatusError` 后会显示 HTTP 状态码。可以先用 `--skip-transmitters` 判断是 CelesTrak 还是 SatNOGS 无法访问。

脚本默认下载 CelesTrak 的 JSON/OMM 星历并保存 SatNOGS 转发器信息。生成成功后，将 `satellite.db` 复制到插件配置的 `data_dir` 目录中，文件名保持为 `satellite.db`，然后重新加载插件。默认 `data_dir` 为：

```text
data/plugin_data/astrbot_plugin_satellite_forecast/satellite.db
```

下载和合成也可以分两步进行。先在可联网机器上只保存源文件：

```powershell
python scripts/download_ephemeris.py --download-only --raw-dir source_data
```

这会生成 `source_data/ephemeris.json`（或 `ephemeris.tle`）和 `source_data/transmitters.json`。将整个 `source_data` 目录复制到服务器后，在服务器上完全离线合成数据库：

```powershell
python scripts/download_ephemeris.py --from-files source_data --output satellite.db
```

`--from-files` 不会访问网络。使用 TLE 文件时，下载和合成两步都要加 `--format tle`。如果没有转发器文件，脚本仍可合成数据库，但卫星不会显示转发器信息。

也可以只生成星历，或指定 CelesTrak/SatNOGS 兼容镜像：

```powershell
python scripts/download_ephemeris.py --skip-transmitters
python scripts/download_ephemeris.py --celestrak-endpoint https://example.invalid/gp.php --satnogs-endpoint https://example.invalid/transmitters/
```

脚本会先写入临时文件，所有下载和数据库写入成功后才替换目标文件；目标数据库不会因下载失败而被覆盖。

## Development

```text
pip install -r requirements.txt pytest pytest-asyncio
pytest -q
```
