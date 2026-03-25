# OSM Raster Topology

面向自动驾驶场景的 `.osm -> 栅格地图` 转换工具。  
当前版本输出的不只是普通道路位图，还包含：

- 拓扑感知道路栅格
- 道路语义层
- 建筑/运动场语义层
- topology sidecar
- 量化验证结果与验证图

## 当前能力

- 支持输入：`.osm` XML
- 解析：`node` / `way` / `multipolygon relation` / `restriction relation`
- 输出：
  - `map_bundle.json`
  - `raster/preview.png`
  - `validation_report.png`（`matplotlib` 生成）
- 量化验证：
  - 道路缺失、道路断裂
  - 平面分量差值、分层分量差值
  - 节点锚点缺失/越界/碰撞
  - 道路语义覆盖率

## 推荐运行方式（虚拟环境）

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

## 一键启动脚本（Windows）

项目根目录提供了 [start.ps1](/B:/Codes/osm/start.ps1)。

### 1. 启动 GUI

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

GUI 名称已更新为“矢量地图转栅格地图工具”。若处理 Lanelet2，请在 GUI 中将 `Ingest 后端` 选择为 `lanelet2_xml`，会生成 Lanelet2 专用量化报告。

### 2. 命令行 `run`

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 `
  -Mode run `
  -InputPath .\tongji.osm `
  -OutputDir .\build\run_bundle `
  -PixelSize 1.0
```

### 3. 命令行 `check`

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 `
  -Mode check `
  -InputPath .\tongji.osm `
  -OutputDir .\build\check_bundle
```

### 脚本参数

- `-Mode gui|run|check|design`
- `-InputPath <osm 文件路径>`
- `-OutputDir <输出目录>`
- `-PixelSize <像素分辨率>`
- `-TargetCrs`（当前固定 `EPSG:3857`）
- `-SkipInstall`（跳过安装步骤）

## 直接用 CLI

### 启动 GUI

```bash
python -m osm_raster_topology gui
```

### 预检查

```bash
python -m osm_raster_topology check --input your_map.osm --outdir output_dir
```

### 正式运行

```bash
python -m osm_raster_topology run --input your_map.osm --outdir output_dir --pixel-size 1.0
```

Optional ingest backend override:

```bash
python -m osm_raster_topology run --input your_map.osm --outdir output_dir --pixel-size 1.0 --ingest-backend lanelet2_xml
```

## 输出文件说明

运行完成后，输出目录通常包含：

- `map_bundle.json`：主结果包（图层、语义、拓扑、验证）
- `raster/preview.png`：地图预览
- `validation_report.png`：量化图
- `topology/`
- `validation/`

## 量化图说明

`validation_report.png` 当前为中文论文风格 4 子图：

- `(a)` 栅格预览
- `(b)` 转换前后要素数量对比
- `(c)` 保留率与覆盖率
- `(d)` 诊断项与边界条件 + 指标定义说明

`(d)` 中定义了这些指标：

- 道路缺失
- 道路断裂
- 平面分量差值
- 分层分量差值
- 对象栈溢出
- 锚点缺失
- 节点越界
- 节点碰撞
- 多对象像素

## 项目结构

- [cli.py](/B:/Codes/osm/src/osm_raster_topology/cli.py)
- [gui.py](/B:/Codes/osm/src/osm_raster_topology/gui.py)
- [pipeline.py](/B:/Codes/osm/src/osm_raster_topology/pipeline.py)
- [ingest.py](/B:/Codes/osm/src/osm_raster_topology/ingest.py)
- [rasterize.py](/B:/Codes/osm/src/osm_raster_topology/rasterize.py)
- [sidecar.py](/B:/Codes/osm/src/osm_raster_topology/sidecar.py)
- [validate.py](/B:/Codes/osm/src/osm_raster_topology/validate.py)
- [report.py](/B:/Codes/osm/src/osm_raster_topology/report.py)

## 当前边界

- 仅支持 `.osm` XML（暂不支持 `.pbf`）
- `target_crs` 当前固定 `EPSG:3857`
- `relation` 主栅格仅处理 `multipolygon`
- 非平面拓扑（桥/隧/layer）不能仅靠单层二维道路栅格完整表达，当前通过 sidecar + crossing 层补充保留
