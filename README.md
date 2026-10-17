# AstrBot Satellite Forecast

AstrBot 的卫星过境预报插件。它同步 CelesTrak 星历和 SatNOGS 转发器数据，按用户保存观测位置，计算卫星过境，并在查询结果中附带天空轨迹图。

## 功能概览

- 从 CelesTrak 同步 JSON/OMM 或 TLE 星历。
- 从 SatNOGS DB 同步卫星转发器信息。
- 支持地名、经纬度和 Maidenhead 网格定位，例如 `厦门`、`OL63`。
- 每个平台用户独立保存观测位置和关注卫星，私聊与群聊共用同一份用户数据。
- 查询 AOS、EOS、最高仰角、最高点时间和方位。
- 查询过境时生成天空轨迹图，并在图片中显示卫星名称、NORAD 编号、AOS、EOS、最高仰角和最高点时间。
- 支持 GEO 卫星的窗口边界可见性兜底处理。
- 支持 AstrBot LLM 工具，可以直接用自然语言查询。
- 支持联网同步，也支持在联网机器下载源文件后离线生成 SQLite 数据库。

## 安装

将整个仓库复制到 AstrBot 的插件目录：

```text
AstrBot/data/plugins/astrbot_plugin_satellite_forecast
```

进入插件目录安装依赖：

```powershell
python -m pip install -r requirements.txt
```

然后在 AstrBot WebUI 中重载插件。首次启动会按配置同步星历；如果服务器暂时无法联网，可以使用下面的离线数据库流程。

> 必须整体复制插件目录。天空轨迹图依赖 `main.py`、`satellite_forecast/prediction.py` 和 `satellite_forecast/sky_chart.py` 的接口一致，不能只替换入口文件。

## 三分钟上手

默认命令是 `/sat`。如在配置中修改了 `command_name`，请把下面的 `/sat` 替换为对应命令。

1. 打开菜单：

   ```text
   /sat
   ```

2. 绑定观测位置。可以使用城市名或 Maidenhead 网格：

   ```text
   /sat 1 厦门
   /sat 1 OL63
   ```

3. 查询卫星：

   ```text
   /sat 5 METOP
   ```

   如果有多个候选，直接回复结果前的数字即可。

4. 直接查询过境：

   ```text
   /sat pass METOP-C
   ```

   或查询关注列表中的卫星：

   ```text
   /sat 2 METOP-C
   ```

   机器人会返回文字预报，并尝试发送第一段过境的天空轨迹图。

## 命令参考

| 命令 | 用途 |
| --- | --- |
| `/sat` | 打开功能菜单。菜单没有退出项，发送其他消息即可结束当前操作。 |
| `/sat 1` | 查看当前观测位置。 |
| `/sat 1 厦门` | 设置观测位置。也支持 `OL63` 或经纬度可识别的地点。 |
| `/sat 2` | 查看关注卫星列表。 |
| `/sat 2 METOP-C` | 查询指定卫星的过境和天空图。 |
| `/sat 3` | 查看提醒状态、参数和可执行修改命令。 |
| `/sat 4` | 查看位置、关注卫星和下一次过境摘要。 |
| `/sat 5 METOP` | 模糊搜索卫星。 |
| `/sat 6` | 手动更新星历和转发器数据。 |
| `/sat add METOP-C` | 添加关注卫星。 |
| `/sat del METOP-C` | 移除关注卫星。也支持 `remove`。 |
| `/sat pass METOP-C` | 查询指定卫星未来 24 小时过境。 |
| `/sat set elevation 10` | 设置最低过境仰角，单位为度。 |
| `/sat set before 6` | 设置提醒提前分钟数。 |
| `/sat set repeat 1` | 设置提醒次数。 |
| `/sat set notify on` | 开启提醒参数；`off` 可关闭。 |

卫星搜索支持名称、常用缩写、国际设计代号和 NORAD 编号。默认展示北京时间（UTC+8）；代码内部使用 UTC 计算，用户时区显示以保存设置为准。

## 天空轨迹图

过境查询成功后，插件会将文字和图片放在同一条消息中。图片使用极坐标天空图：

- 中心是天顶，外圈是地平线。
- 北、东、南、西标在图上，绿色线是卫星轨迹。
- 黄色点表示 AOS，红色点表示 EOS。
- 图片顶部显示卫星名称、NORAD 编号、显示时区、AOS、EOS、最高仰角和最高点时间。

图片生成功能依赖 `requirements.txt` 中的 Pillow。若文字预报正常但没有图片，先检查 Pillow 是否安装，并确认部署目录中的三个核心模块来自同一版本。插件会对旧版天空图模块做兼容降级，但旧模块无法显示新版参数。

## GEO 卫星说明

GEO 卫星相对地面移动很慢，常常不会像低轨卫星一样在预测窗口内完整触发一次“升起-最高点-落下”事件。插件会在没有完整事件组时，按预测窗口采样可见区间，并用窗口边界生成过境摘要和轨迹图。

因此，GEO 结果中的 AOS/EOS 可能是未来 24 小时查询窗口的边界，而不是卫星真正从地平线升起或落下的瞬间。要获得更有意义的结果，请结合最低仰角设置和较长预测窗口理解。

## 配置

配置文件由 `_conf_schema.json` 提供，主要选项如下：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `command_name` | `sat` | 命令名称，可填写 `sat` 或 `/sat`；不要使用 `q`。 |
| `sync_on_start` | `true` | 启动时立即同步数据。 |
| `sync_interval_hours` | `24` | 自动同步间隔，单位为小时。 |
| `data_dir` | `data/plugin_data/astrbot_plugin_satellite_forecast` | SQLite、图表和运行数据目录。相对路径按 AstrBot 工作目录解析。 |
| `debug_logging` | `false` | 输出同步、搜索、定位和轨迹图诊断日志。 |
| `celestrak.endpoint` | CelesTrak GP 接口 | CelesTrak 或兼容镜像地址。 |
| `celestrak.group` | `active` | CelesTrak 数据分组。 |
| `celestrak.format` | `tle` | 在线同步格式，可选 `tle` 或 `json`。 |
| `satnogs.endpoint` | SatNOGS DB 接口 | 转发器数据源或兼容镜像地址。 |

默认数据库文件名为 `satellite_forecast.db`，位于 `data_dir` 中。插件更新不会把用户位置和关注列表写入仓库目录。

## 离线数据库

在能联网的机器上直接生成数据库：

```powershell
python scripts/download_ephemeris.py --output satellite.db
```

脚本默认下载 JSON/OMM 星历和 SatNOGS 转发器。它会自动读取系统代理，也可以手动指定代理：

```powershell
python scripts/download_ephemeris.py --proxy http://127.0.0.1:7890
python scripts/download_ephemeris.py --no-proxy
```

推荐将下载和合成分开，这样服务器无需访问网络：

```powershell
# 联网机器
python scripts/download_ephemeris.py --download-only --raw-dir source_data

# 将 source_data 整个目录复制到离线服务器后
python scripts/download_ephemeris.py --from-files source_data --output satellite.db
```

TLE 模式的两步都要加 `--format tle`：

```powershell
python scripts/download_ephemeris.py --format tle --download-only --raw-dir source_data
python scripts/download_ephemeris.py --format tle --from-files source_data --output satellite.db
```

生成的 `satellite.db` 复制到插件 `data_dir` 目录，并重新加载插件。`--from-files` 不访问网络；`--skip-transmitters` 可只处理星历。下载失败时，脚本不会用半成品覆盖已有数据库。

## LLM 使用

插件注册了位置解析、位置绑定、卫星搜索、关注卫星管理、过境预测和转发器查询工具。启用 AstrBot 的正常 LLM 对话后，可以直接询问：

```text
Metop-C 明天在 OL63 有哪些过境？
帮我关注 METOP-C
我当前绑定的观测位置是什么？
```

自然语言预测使用当前用户已保存的位置；首次使用前仍建议先执行 `/sat 1 地名或网格`。

## 常见问题

### 文字有了，但没有图片

确认已安装 Pillow：

```powershell
python -c "from PIL import Image; print(Image.__version__)"
```

然后确认插件目录整体更新并重载。日志中若出现 `sample_pass_track` 或 `render_sky_chart` 导入错误，通常是 `main.py` 与 `satellite_forecast` 子模块版本混用。

### 提示没有设置观测位置

先发送 `/sat 1 厦门`、`/sat 1 OL63` 或其他可识别地点，再查询过境。

### 搜索到多个卫星后怎么办

直接回复候选列表中的数字。选择状态短期有效，超时后重新执行搜索即可。

### CelesTrak 返回 403 或超时

检查服务器出口、系统代理或 `--proxy` 参数。可先使用 `--skip-transmitters` 判断问题来自星历源还是 SatNOGS；需要完整错误堆栈时增加 `--debug`。

### GEO 结果看起来不像普通过境

这是预期行为。GEO 卫星可能在整个预测窗口内都处于可见区间，结果会使用窗口边界表达这段可见性。

## 开发与测试

安装开发依赖后，在仓库根目录执行：

```powershell
python -m pip install -r requirements.txt pytest pytest-asyncio
python -m pytest -q
python -m compileall -q .
git diff --check
```

## 版本与许可证

当前正式版：`v1.0.1`。

作者：lzh173
仓库：[github.com/lzh173/Astrbot-Satellite_Forecast](https://github.com/lzh173/Astrbot-Satellite_Forecast)
