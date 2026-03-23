# OSM Raster Topology

这是一个面向自动驾驶地图表达的 `.osm -> 栅格地图` 工具。它不只是输出一张普通道路位图，而是同时导出：

- 道路拓扑栅格
- 道路语义层
- 建筑和运动场语义
- topology sidecar
- 量化验证结果

当前版本支持命令行运行，也支持桌面 GUI 端到端运行。

## 功能

- 读取 `.osm` XML
- 解析 `node`、`way`、`multipolygon relation`
- 解析 `restriction relation`
- 生成道路拓扑栅格
- 生成建筑填充、边界和语义
- 生成运动场填充、边界和语义
- 生成道路语义层：
  - `highway_class`
  - `oneway`
  - `access`
  - `foot`
  - `bicycle`
  - `lanes`
  - `maxspeed`
  - `surface`
- 生成统一结果包 `map_bundle.json`
- 生成栅格预览图 `preview.png`
- 生成 `matplotlib` 量化验证图 `validation_report.png`

## 安装

建议使用 Python 3.11 及以上版本。

基础安装：

```bash
pip install -e .
```

如果需要拖拽 GUI，可安装 GUI 附加依赖：

```bash
pip install -e .[gui]
```

如果需要 GIS 相关附加依赖：

```bash
pip install -e .[runtime]
```

## 端到端使用

### 方式 1：桌面 GUI

启动 GUI：

```bash
python -m osm_raster_topology gui
```

或者安装后直接运行：

```bash
osm-topology-ui
```

GUI 使用流程：

1. 选择或拖入 `.osm` 文件
2. 选择输出目录
3. 设置像素分辨率
4. 点击“开始转换”

运行完成后，会在你选择的输出目录下生成全部结果文件。

说明：

- 如果没有安装 `tkinterdnd2`，拖拽不可用，但点击选择文件仍然可用
- 当前 GUI 不会写死输入路径和输出路径，全部由用户选择

### 方式 2：命令行

预检查：

```bash
python -m osm_raster_topology check --input your_map.osm --outdir output_dir
```

正式运行：

```bash
python -m osm_raster_topology run --input your_map.osm --outdir output_dir --pixel-size 1.0
```

参数说明：

- `--input`：输入 `.osm` 文件路径
- `--outdir`：输出目录
- `--pixel-size`：像素大小，单位米
- `--target-crs`：当前固定为 `EPSG:3857`

## 输出文件

运行完成后，输出目录下通常会生成：

- `map_bundle.json`
- `validation_report.png`
- `raster/preview.png`
- `topology/`
- `validation/`

### `map_bundle.json`

这是主结果文件，包含：

- 元数据
- 图层定义
- 语义编码表
- topology policy
- 验证结果
- topology sidecar
- 对象栈
- 栅格层数据

### `validation_report.png`

这是论文风格的量化验证图，由 `matplotlib` 生成。当前包含：

- `(a)` 栅格预览
- `(b)` 转换前后要素数量对比
- `(c)` 保留率与覆盖率
- `(d)` 诊断项与边界条件

### `raster/preview.png`

用于快速查看转换结果的预览图。

## 当前输出的主要图层

当前 `map_bundle.json` 中包含这些关键层：

- `road_topology_super`
- `road_direction_bits_super`
- `node_anchor_super`
- `road_edges`
- `water_lines`
- `crossing_structure`
- `highway_class`
- `road_oneway`
- `road_access`
- `road_foot`
- `road_bicycle`
- `road_lanes`
- `road_maxspeed_kph`
- `road_surface_class`
- `building_fill`
- `building_boundary`
- `building_class`
- `building_levels`
- `building_min_level`
- `sports_fill`
- `sports_boundary`
- `sports_class`
- `turn_restriction_via_mask`
- `line_object_ids`
- `line_multi_object_count`
- `area_object_ids`

## 量化验证口径

验证逻辑位于 [validate.py](/B:/Codes/osm/src/osm_raster_topology/validate.py)。

主要指标包括：

- `road_missing_feature_count`
  - 源 OSM 中的道路要素在输出对象栈中完全找不到
- `road_fragmented_feature_count`
  - 单条道路栅格化后裂成多个 8 邻接连通分量
- `road_component_delta_planar`
  - 栅格道路连通分量数减去源路网平面连通分量数
- `road_component_delta_z_aware`
  - 栅格道路连通分量数减去源路网分层连通分量数
- `node_anchor_missing_pixel_count`
  - 范围内图节点对应的锚点像素未写入
- `node_anchor_out_of_bounds_count`
  - 图节点投影后落在当前栅格范围外
- 道路语义覆盖率
  - 对带标签道路逐项检查 `oneway/access/foot/bicycle/lanes/maxspeed/surface` 是否保留

## 项目结构

- [src/osm_raster_topology/cli.py](/B:/Codes/osm/src/osm_raster_topology/cli.py)
  - 命令行入口
- [src/osm_raster_topology/gui.py](/B:/Codes/osm/src/osm_raster_topology/gui.py)
  - 桌面 GUI 入口
- [src/osm_raster_topology/pipeline.py](/B:/Codes/osm/src/osm_raster_topology/pipeline.py)
  - 主流程编排
- [src/osm_raster_topology/ingest.py](/B:/Codes/osm/src/osm_raster_topology/ingest.py)
  - OSM XML 解析
- [src/osm_raster_topology/rasterize.py](/B:/Codes/osm/src/osm_raster_topology/rasterize.py)
  - 栅格化与语义写入
- [src/osm_raster_topology/sidecar.py](/B:/Codes/osm/src/osm_raster_topology/sidecar.py)
  - topology sidecar 构建
- [src/osm_raster_topology/validate.py](/B:/Codes/osm/src/osm_raster_topology/validate.py)
  - 量化验证
- [src/osm_raster_topology/report.py](/B:/Codes/osm/src/osm_raster_topology/report.py)
  - `matplotlib` 验证图生成

## 当前边界

- 当前只支持 `.osm` XML，不支持 `.pbf`
- 当前坐标系固定为 `EPSG:3857`
- `relation` 主栅格仅处理 `multipolygon`
- 桥、隧道、`layer` 这类非平面拓扑不能只靠单层二维道路栅格完整表达，当前通过 sidecar 和 crossing 相关层补充保留

## 最小示例

```bash
python -m osm_raster_topology run --input tongji.osm --outdir build/run_bundle --pixel-size 1.0
```

运行后重点查看：

- `build/run_bundle/map_bundle.json`
- `build/run_bundle/validation_report.png`
- `build/run_bundle/raster/preview.png`
