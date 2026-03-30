# 矢量地图转栅格地图工具

将矢量地图（OSM / Lanelet2 OSM XML）转换为多层栅格、拓扑 sidecar 与量化报告。

## 功能概览

- 输入：`.osm` XML（标准 OSM 与 Lanelet2 OSM）
- 输入：`.xodr` OpenDRIVE（先转换为 OSM 再进入统一管线）
- 输出：
  - `map_bundle.json`
  - `raster/preview.png`
  - `raster/layers.npz`（栅格数组）
  - `raster/layers.tif`（可选：需安装 rasterio）
  - `validation_report.png`
  - `topology/` 与 `validation/`
- 量化报告：
  - OSM 模式：通用 OSM 量化报告
  - Lanelet2 模式：专用 Lanelet2 量化报告（邻接/后继覆盖率等）

## 运行环境

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
pip install -e .[gui]
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
pip install -e .[gui]
```

## 一键脚本（Windows）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

## GUI 用法

GUI 名称为“矢量地图转栅格地图工具”。

- 选择 `.osm` 文件
- 选择输出目录
- 选择 `Ingest 后端`
  - `auto`：自动识别 OSM 或 Lanelet2
  - `osm_xml`：强制 OSM 量化
  - `lanelet2_xml`：强制 Lanelet2 量化（会生成 Lanelet2 专用报告）

## CLI 用法

### 运行 GUI

```bash
python -m osm_raster_topology gui
```

### 预检

```bash
python -m osm_raster_topology check --input your_map.osm --outdir output_dir
```

### 正式运行

```bash
python -m osm_raster_topology run --input your_map.osm --outdir output_dir --pixel-size 1.0
```

### OpenDRIVE (.xodr)

```bash
python -m osm_raster_topology run \
  --input your_map.xodr \
  --outdir output_dir \
  --pixel-size 1.0
```

### 强制 Lanelet2 模式

```bash
python -m osm_raster_topology run \
  --input your_lanelet2.osm \
  --outdir output_dir \
  --pixel-size 1.0 \
  --ingest-backend lanelet2_xml
```

## 输出说明

运行后输出目录包含：

- `map_bundle.json`：主结果包（图层、语义、拓扑、验证）
- `raster/preview.png`：栅格预览
- `validation_report.png`：量化报告
- `topology/`：拓扑 sidecar
- `validation/`：校验中间产物

## 量化报告说明

### OSM 量化

- (a) 栅格预览
- (b) 要素数量对比
- (c) 保留率与覆盖率
- (d) 诊断项与边界条件

### Lanelet2 量化

在 OSM 量化基础上追加 Lanelet2 指标：

- 邻接覆盖率 = 有左右邻接的 lanelet / 总 lanelet
- 后继覆盖率 = 有后继的 lanelet / 总 lanelet
- lanelet 总数 / 孤立 lanelet / 规则引用数

## 目录结构

- `src/osm_raster_topology/cli.py`
- `src/osm_raster_topology/gui.py`
- `src/osm_raster_topology/pipeline.py`
- `src/osm_raster_topology/ingest.py`
- `src/osm_raster_topology/ingest_lanelet2.py`
- `src/osm_raster_topology/rasterize.py`
- `src/osm_raster_topology/sidecar.py`
- `src/osm_raster_topology/validate.py`
- `src/osm_raster_topology/report.py`

## 限制

- 目前仅支持 `.osm` XML（不支持 `.pbf`）
- OpenDRIVE 将先转换为 OSM，输出道路中心线 + 车道线 + 车道面
- `target_crs` 固定为 `EPSG:3857`
