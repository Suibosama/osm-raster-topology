# OSM Raster Topology

这个仓库提供一版面向自动驾驶场景的 `.osm -> 栅格地图` 实现。目标不是只生成一张道路二值图，而是输出统一的 `map_bundle.json`，把拓扑层、语义层、规则和对象栈收在一个 JSON 包里，同时保留一张预览图。

## 当前能力

- 读取 `.osm` XML
- 解析 `node`、`way`、`multipolygon relation`
- 解析 `restriction` relation 并导出转向限制
- 输出道路超采样拓扑栅格
- 输出建筑填充和建筑边界
- 输出运动场与体育设施填充和边界
- 输出道路语义层：道路类型、单行、通行属性、车道数、限速、路面材质
- 输出 topology sidecar，保存图结构、多对象重叠和转向限制

## 主要输出

`build/.../map_bundle.json` 中统一包含这些关键层：

- `road_topology_super`
- `road_direction_bits_super`
- `node_anchor_super`
- `road_edges`
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
- `sports_fill`
- `sports_boundary`
- `sports_class`
- `turn_restriction_via_mask`
- `line_object_ids`
- `road_object_stack`

同时会生成：

- `map_bundle.json`
- `raster/preview.png`

## 设计思路

1. 道路主拓扑不直接落单层 mask，而是落在 4x 超采样栅格上。
2. 节点、方向位掩码和对象栈共同用于避免误连、断裂和身份丢失。
3. 建筑不只输出填充区域，还输出边界。
4. 交通规则和语义属性统一写进 `map_bundle.json`。

## 命令

预检：

```bash
python -m osm_raster_topology check --input tongji.osm --outdir build/check
```

运行：

```bash
python -m osm_raster_topology run --input tongji.osm --outdir build/run --pixel-size 1.0
```

## 当前边界

- 只支持 `.osm` XML，不支持 `.pbf`
- `relation` 中主栅格只处理 `multipolygon`
- 坐标系固定为 `EPSG:3857`
- 输出格式为 `PNG + NPZ + JSON`

## 代码入口

- [cli.py](B:\Codes\osm\src\osm_raster_topology\cli.py)
- [pipeline.py](B:\Codes\osm\src\osm_raster_topology\pipeline.py)
- [ingest.py](B:\Codes\osm\src\osm_raster_topology\ingest.py)
- [rasterize.py](B:\Codes\osm\src\osm_raster_topology\rasterize.py)
- [sidecar.py](B:\Codes\osm\src\osm_raster_topology\sidecar.py)
- [validate.py](B:\Codes\osm\src\osm_raster_topology\validate.py)
