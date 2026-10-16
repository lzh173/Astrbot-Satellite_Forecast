# AstrBot Satellite Forecast Agent Handoff

本文档用于帮助后续智能体快速接手本仓库。项目是 AstrBot 的卫星过境预报插件，主要面向中文用户和中国大陆网络环境。

## 项目状态

- 远端：`git@github.com:lzh173/Astrbot-Satellite_Forecast.git`
- 主分支：`master`
- 最新远端提交：本次正式版提交，发布标签为 `v1.0.0`
- 当前插件版本：`v1.0.0`
- GPG 签名 key：`C8922CEE7E3B6239E76380AC444F3D4763360C7B`
- Python 测试当前结果：`37 passed`

## 目录结构

- `main.py`：AstrBot 插件入口、指令、用户菜单、LLM 工具和定时任务。
- `satellite_forecast/models.py`：用户位置、设置、关注卫星、卫星及转发器模型。
- `satellite_forecast/storage.py`：SQLite 持久化。
- `satellite_forecast/sources.py`：CelesTrak、SatNOGS 和兼容自定义源的下载与解析。
- `satellite_forecast/prediction.py`：基于 Skyfield 的过境计算。
- `satellite_forecast/sky_chart.py`：使用 Pillow 绘制天空轨迹图。
- `satellite_forecast/geocoding.py`：地名和梅登海德网格解析。
- `satellite_forecast/maidenhead.py`：梅登海德网格转换。
- `satellite_forecast/time_utils.py`：UTC、北京时间和用户时区显示。
- `satellite_forecast/satellite_search.py`：卫星模糊搜索。
- `scripts/download_ephemeris.py`：独立于 AstrBot 的星历/转发器下载和数据库构建脚本。
- `tests/`：单元测试和离线构建测试。
- `_conf_schema.json`：AstrBot 插件配置 schema。修改配置结构前必须先查 AstrBot 当前版本文档和已有 schema 格式。
- `README.md`：用户安装、配置和离线数据库说明。
- `metadata.yaml`：插件元数据和版本号。

## 当前主要功能

- 固定支持 CelesTrak 星历和 SatNOGS 转发器源。
- 支持 CelesTrak/SatNOGS 格式兼容的自定义源入口。
- 星历格式支持 CelesTrak JSON/OMM 和 TLE。
- 支持系统代理自动发现，也支持 `--proxy` 和 `--no-proxy`。
- 用户可绑定地名、经纬度或梅登海德网格，并按用户保存位置。
- 支持卫星模糊搜索，例如 `metop-c`、`fy2g`。
- 支持用户关注卫星和未来过境计算：AOS、EOS、最高仰角、最高仰角时间和方位。
- 查询过境时生成 Pillow 天空轨迹图，图片显示卫星名称、NORAD 编号、AOS、EOS、最高仰角和最高点时间。
- GEO 卫星在没有完整升起/落下事件时使用预测窗口边界采样，避免常见 GEO 查询为空。
- 时间内部统一按 UTC 计算，展示层按用户设置的时区显示，默认 `Asia/Shanghai`。
- 提供菜单指令和 LLM 工具。菜单命令前缀必须从配置读取，不能硬编码 `/q`，因为可能与其他机器人冲突。
- LLM 工具包括位置解析、位置绑定、关注卫星管理、卫星搜索、过境预测和转发器查询。
- 调试日志受配置控制；需要排查问题时优先开启插件 debug 配置。

## 离线数据库流程

联网机器只下载并保存源文件：

```powershell
python scripts/download_ephemeris.py --download-only --raw-dir source_data
```

生成：

- `source_data/ephemeris.json`，JSON/OMM 模式
- `source_data/ephemeris.tle`，TLE 模式
- `source_data/transmitters.json`，SatNOGS 转发器

将整个目录复制到服务器后离线合成：

```powershell
python scripts/download_ephemeris.py --from-files source_data --output satellite.db
```

TLE 模式下载和合成两步都要加 `--format tle`。`--from-files` 不访问网络。脚本会先写入独立源文件，再合成 SQLite；数据库写入采用临时文件替换，不应覆盖旧数据库为半成品。

## 开发和验证

在仓库根目录执行：

```powershell
python -m pytest -q
python -m compileall -q .
git diff --check
```

修改下载脚本时，至少验证：

- JSON/OMM 源文件保存和重新读取。
- TLE 源文件保存和重新读取。
- `transmitters.json` 的常见 SatNOGS 结构，尤其是 `norad_cat_id` 和嵌套 `satellite.norad_cat_id`。
- 合成数据库后卫星能关联到转发器。
- `--download-only` 不创建或覆盖数据库。
- `--from-files` 不创建 HTTP 请求。
- 网络失败时不会覆盖已有数据库。

## 修改约定

1. 先阅读相关代码、测试和 AstrBot 开发文档，再编辑。
2. 手工编辑使用 `apply_patch`，不要用 shell 重定向写文件。
3. 不要回滚其他人的工作区改动。
4. 用户要求每次修改都要更新 `metadata.yaml` 版本号。
5. 完成后运行测试、编译检查和 `git diff --check`。
6. 使用 GPG 签名提交，并推送到 `origin master`。
7. 提交前检查 `git status --short`，不要提交运行产物或缓存。

## 当前工作区注意事项

以下文件是本地运行离线脚本产生的未跟踪产物，通常不应提交：

- `scripts/satellite.db`
- `scripts/source_data/`

图片轨迹功能要求部署目录同时包含 `satellite_forecast/prediction.py` 中的 `sample_pass_track` 和新增的 `satellite_forecast/sky_chart.py`，并安装 `requirements.txt` 中的 Pillow。升级时应整体覆盖插件目录后重新加载；若只覆盖 `main.py`，旧版子模块会导致功能缺失。

继续工作前先执行：

```powershell
git status --short
```

不要擅自删除这些文件；如需清理，先确认它们不是用户准备复制到服务器的离线数据。

## 常见问题排查

- CelesTrak 返回 `403`：检查系统代理、`--proxy`、User-Agent 和网络出口；不要把 403 当成数据库解析错误。
- AstrBot 报 schema `KeyError: 'items'`：检查 `_conf_schema.json` 的数组字段是否使用 AstrBot 要求的 `items` 结构，并参考当前 AstrBot wiki。
- AstrBot 导入 API 失败：不要凭经验导入不存在的符号，例如 `EventMessageType`；以当前安装的 AstrBot 版本 API 为准。
- `SatelliteRecord` 没有 `transmitters`：源层使用的是 `transponders`，数据库层模型使用 `transmitters`，转换时必须经过 `_stored_satellite` 或等价的显式映射。
- 地名解析失败：优先检查中国大陆可用的地理编码服务、代理和回退逻辑；梅登海德网格不依赖在线 API。
- 过境时间异常：检查 Skyfield 使用的时间对象、UTC/BJT 转换和显示时区，不要在预测核心中直接使用本地无时区时间。
- LLM 工具结果为空：检查工具函数是否返回字符串/结构化结果，而不是只发送消息后返回 `None`；同时检查 AstrBot 当前工具注册约定。

## 提交示例

```powershell
git add <相关文件>
git commit -S -m "简短描述修改"
git push origin master
git log -1 --show-signature
git ls-remote origin refs/heads/master
git status --short
```
