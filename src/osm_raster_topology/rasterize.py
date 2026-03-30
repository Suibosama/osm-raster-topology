from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from osm_raster_topology.config import RunConfig
from osm_raster_topology.model import IngestedData, PolygonFeature, RasterResult


PADDING_PX = 2
DIRECTION_BITS = {
    (0, -1): 1 << 0,
    (1, -1): 1 << 1,
    (1, 0): 1 << 2,
    (1, 1): 1 << 3,
    (0, 1): 1 << 4,
    (-1, 1): 1 << 5,
    (-1, 0): 1 << 6,
    (-1, -1): 1 << 7,
}
HIGHWAY_CLASS_CODES = {
    "trunk": 1,
    "trunk_link": 2,
    "primary": 3,
    "secondary": 4,
    "tertiary": 5,
    "residential": 6,
    "service": 7,
    "pedestrian": 8,
    "footway": 9,
    "cycleway": 10,
    "path": 11,
    "steps": 12,
    "platform": 13,
    "construction": 14,
}
BUILDING_CLASS_CODES = {
    "yes": 1,
    "apartments": 2,
    "dormitory": 3,
    "college": 4,
    "university": 5,
    "house": 6,
    "residential": 7,
    "school": 8,
    "commercial": 9,
    "retail": 10,
    "roof": 11,
}
SPORT_CLASS_CODES = {
    "pitch": 1,
    "stadium": 2,
    "sports_centre": 3,
    "sports_hall": 4,
    "swimming_pool": 5,
    "track": 6,
}
ACCESS_CODES = {"no": 1, "private": 2, "destination": 3, "permissive": 4, "yes": 5}
PERMISSION_CODES = {"no": 1, "yes": 2, "designated": 3}
SURFACE_CODES = {"asphalt": 1, "paved": 2, "concrete": 3, "unpaved": 4, "gravel": 5}


def rasterize_layers(data: IngestedData, config: RunConfig, raster_dir: Path) -> RasterResult:
    min_x, min_y, max_x, max_y = data.bounds_xy
    width = max(1, int(math.ceil((max_x - min_x) / config.pixel_size)) + 1 + PADDING_PX * 2)
    height = max(1, int(math.ceil((max_y - min_y) / config.pixel_size)) + 1 + PADDING_PX * 2)
    oversample = config.topology_oversample
    super_width = width * oversample
    super_height = height * oversample

    masks = {
        "area_fill": Image.new("L", (width, height), 0),
        "building_fill": Image.new("L", (width, height), 0),
        "building_boundary": Image.new("L", (width, height), 0),
        "sports_fill": Image.new("L", (width, height), 0),
        "sports_boundary": Image.new("L", (width, height), 0),
        "lane_area": Image.new("L", (width, height), 0),
        "node_mask": Image.new("L", (width, height), 0),
        "hole_mask": Image.new("L", (width, height), 0),
        "turn_restriction_via_mask": Image.new("L", (width, height), 0),
    }
    object_ids = {
        "area_object_ids": Image.new("I", (width, height), 0),
    }
    drawers = {name: ImageDraw.Draw(image) for name, image in masks.items()}
    id_drawers = {name: ImageDraw.Draw(image) for name, image in object_ids.items()}

    road_super = np.zeros((super_height, super_width), dtype=np.uint8)
    water_super = np.zeros((super_height, super_width), dtype=np.uint8)
    lane_marking_super = np.zeros((super_height, super_width), dtype=np.uint8)
    crossing_super = np.zeros((super_height, super_width), dtype=np.uint8)
    node_anchor_super = np.zeros((super_height, super_width), dtype=np.uint8)
    road_direction_super = np.zeros((super_height, super_width), dtype=np.uint8)
    water_direction_super = np.zeros((super_height, super_width), dtype=np.uint8)

    road_pixel_objects: dict[tuple[int, int], set[int]] = defaultdict(set)
    water_pixel_objects: dict[tuple[int, int], set[int]] = defaultdict(set)
    lane_marking_pixel_objects: dict[tuple[int, int], set[int]] = defaultdict(set)
    road_feature_pixels: dict[int, set[tuple[int, int]]] = defaultdict(set)
    water_feature_pixels: dict[int, set[tuple[int, int]]] = defaultdict(set)
    lane_marking_feature_pixels: dict[int, set[tuple[int, int]]] = defaultdict(set)
    road_feature_clipped: set[int] = set()
    water_feature_clipped: set[int] = set()
    lane_marking_feature_clipped: set[int] = set()

    road_priority = np.full((height, width), -1, dtype=np.int16)
    arrays: dict[str, np.ndarray] = {
        "highway_class": np.zeros((height, width), dtype=np.uint8),
        "road_oneway": np.zeros((height, width), dtype=np.uint8),
        "road_access": np.zeros((height, width), dtype=np.uint8),
        "road_foot": np.zeros((height, width), dtype=np.uint8),
        "road_bicycle": np.zeros((height, width), dtype=np.uint8),
        "road_lanes": np.zeros((height, width), dtype=np.uint8),
        "road_maxspeed_kph": np.zeros((height, width), dtype=np.uint16),
        "road_surface_class": np.zeros((height, width), dtype=np.uint8),
        "building_class": np.zeros((height, width), dtype=np.uint8),
        "building_levels": np.zeros((height, width), dtype=np.uint8),
        "building_min_level": np.zeros((height, width), dtype=np.uint8),
        "sports_class": np.zeros((height, width), dtype=np.uint8),
    }

    for polygon in data.polygon_features:
        outer = [_to_pixel(point, data.bounds_xy, config.pixel_size) for point in polygon.outer]
        if len(outer) < 3:
            continue
        drawers["area_fill"].polygon(outer, fill=1)
        id_drawers["area_object_ids"].polygon(outer, fill=polygon.feature_id)
        if _is_building(polygon.tags):
            drawers["building_fill"].polygon(outer, fill=1)
            _draw_polygon_boundary(drawers["building_boundary"], outer)
            _fill_polygon_semantics(arrays, polygon, outer)
        if _is_sports_feature(polygon.tags):
            drawers["sports_fill"].polygon(outer, fill=1)
            _draw_polygon_boundary(drawers["sports_boundary"], outer)
            _fill_sports_semantics(arrays, polygon, outer)
        if polygon.tags.get("xodr:feature") == "lane_area":
            drawers["lane_area"].polygon(outer, fill=1)
        for hole in polygon.holes:
            hole_points = [_to_pixel(point, data.bounds_xy, config.pixel_size) for point in hole]
            if len(hole_points) < 3:
                continue
            drawers["area_fill"].polygon(hole_points, fill=0)
            drawers["hole_mask"].polygon(hole_points, fill=1)
            id_drawers["area_object_ids"].polygon(hole_points, fill=0)
            if _is_building(polygon.tags):
                drawers["building_fill"].polygon(hole_points, fill=0)
                drawers["building_boundary"].polygon(hole_points, outline=1)
                _clear_polygon_semantics(arrays, hole_points)
            if _is_sports_feature(polygon.tags):
                drawers["sports_fill"].polygon(hole_points, fill=0)
                drawers["sports_boundary"].polygon(hole_points, outline=1)
                _clear_sports_semantics(arrays, hole_points)
            if polygon.tags.get("xodr:feature") == "lane_area":
                drawers["lane_area"].polygon(hole_points, fill=0)

    for feature in data.line_features:
        points_super = [_to_super_pixel(point, data.bounds_xy, config.pixel_size, oversample) for point in feature.points]
        if len(points_super) < 2:
            continue
        if feature.category == "road":
            occupancy = road_super
            direction = road_direction_super
            pixel_objects = road_pixel_objects
            feature_pixels = road_feature_pixels
            feature_clipped = road_feature_clipped
        elif feature.category == "water":
            occupancy = water_super
            direction = water_direction_super
            pixel_objects = water_pixel_objects
            feature_pixels = water_feature_pixels
            feature_clipped = water_feature_clipped
        elif feature.category == "lane_marking":
            occupancy = lane_marking_super
            direction = None
            pixel_objects = lane_marking_pixel_objects
            feature_pixels = lane_marking_feature_pixels
            feature_clipped = lane_marking_feature_clipped
        else:
            continue

        for start, end in zip(points_super[:-1], points_super[1:]):
            path = _iter_supercover_pixels(start, end)
            if len(path) == 1:
                path = [path[0], path[0]]
            for index, (x, y) in enumerate(path):
                if x < 0 or y < 0 or x >= super_width or y >= super_height:
                    feature_clipped.add(feature.feature_id)
                    continue
                occupancy[y, x] = 1
                feature_pixels[feature.feature_id].add((y, x))
                base_x = x // oversample
                base_y = y // oversample
                pixel_objects[(base_y, base_x)].add(feature.feature_id)
                if feature.category == "road":
                    _write_road_semantics(arrays, road_priority, base_y, base_x, feature.tags)
                if _is_z_aware(feature.tags):
                    crossing_super[y, x] = 1
                if direction is not None and index < len(path) - 1:
                    nx, ny = path[index + 1]
                    dx = _sign(nx - x)
                    dy = _sign(ny - y)
                    bit = DIRECTION_BITS.get((dx, dy))
                    if bit is not None:
                        direction[y, x] |= bit
                        reverse_bit = DIRECTION_BITS.get((-dx, -dy))
                        if reverse_bit is not None and 0 <= nx < super_width and 0 <= ny < super_height:
                            direction[ny, nx] |= reverse_bit

    for node in data.graph_nodes:
        sx, sy = _to_super_pixel(node.point, data.bounds_xy, config.pixel_size, oversample)
        if 0 <= sx < super_width and 0 <= sy < super_height:
            node_anchor_super[sy, sx] = 1
        x, y = _to_pixel(node.point, data.bounds_xy, config.pixel_size)
        radius = 1 if node.role == "endpoint" else 2
        drawers["node_mask"].ellipse((x - radius, y - radius, x + radius, y + radius), fill=1)

    for restriction in data.turn_restrictions:
        if restriction.via_node_ref is None:
            continue
        node = data.nodes.get(restriction.via_node_ref)
        if node is None:
            continue
        x, y = _to_pixel((node.x, node.y), data.bounds_xy, config.pixel_size)
        drawers["turn_restriction_via_mask"].ellipse((x - 2, y - 2, x + 2, y + 2), fill=1)

    arrays.update({name: np.array(image) for name, image in masks.items()})
    arrays["road_topology_super"] = road_super
    arrays["water_topology_super"] = water_super
    arrays["crossing_structure_super"] = crossing_super
    arrays["node_anchor_super"] = node_anchor_super
    arrays["road_direction_bits_super"] = road_direction_super
    arrays["water_direction_bits_super"] = water_direction_super
    arrays["lane_marking_super"] = lane_marking_super
    arrays["road_edges"] = _downsample_max(road_super, oversample)
    arrays["water_lines"] = _downsample_max(water_super, oversample)
    arrays["lane_markings"] = _downsample_max(lane_marking_super, oversample)
    arrays["crossing_structure"] = _downsample_max(crossing_super, oversample)
    arrays["road_direction_bits"] = _downsample_or(road_direction_super, oversample)
    arrays["water_direction_bits"] = _downsample_or(water_direction_super, oversample)
    arrays["line_object_ids"] = _build_primary_id_grid(height, width, road_pixel_objects)
    arrays["line_multi_object_count"] = _build_count_grid(height, width, road_pixel_objects)
    arrays["water_line_object_ids"] = _build_primary_id_grid(height, width, water_pixel_objects)
    arrays["water_line_multi_object_count"] = _build_count_grid(height, width, water_pixel_objects)
    arrays["area_object_ids"] = np.array(object_ids["area_object_ids"], dtype=np.int32)

    road_stack_payload, road_stack_summary = _build_object_stack_payload(road_pixel_objects, config.object_stack_depth)
    water_stack_payload, water_stack_summary = _build_object_stack_payload(water_pixel_objects, config.object_stack_depth)
    lane_stack_payload, lane_stack_summary = _build_object_stack_payload(lane_marking_pixel_objects, config.object_stack_depth)
    files: dict[str, str] = {}

    preview = _build_preview(arrays)
    preview_path = raster_dir / "preview.png"
    preview.save(preview_path)
    files["preview_png"] = str(preview_path)
    npz_path = raster_dir / "layers.npz"
    _write_npz(npz_path, arrays)
    files["layers_npz"] = str(npz_path)
    geotiff_path = _write_geotiff(raster_dir, arrays, data.bounds_xy, config.pixel_size)
    if geotiff_path is not None:
        files["layers_geotiff"] = str(geotiff_path)

    band_sums = {name: int((array > 0).sum()) for name, array in arrays.items()}
    metrics = {
        "road_feature_fragmented_count": _count_fragmented_features(road_feature_pixels, road_feature_clipped),
        "water_feature_fragmented_count": _count_fragmented_features(water_feature_pixels, water_feature_clipped),
        "road_multi_object_pixels": int(sum(1 for ids in road_pixel_objects.values() if len(ids) > 1)),
        "water_multi_object_pixels": int(sum(1 for ids in water_pixel_objects.values() if len(ids) > 1)),
        "road_object_overflow_pixels": int(road_stack_summary["overflow_pixel_count"]),
        "water_object_overflow_pixels": int(water_stack_summary["overflow_pixel_count"]),
        "lane_marking_object_overflow_pixels": int(lane_stack_summary["overflow_pixel_count"]),
        "building_boundary_pixels": int((arrays["building_boundary"] > 0).sum()),
        "building_fill_pixels": int((arrays["building_fill"] > 0).sum()),
        "sports_boundary_pixels": int((arrays["sports_boundary"] > 0).sum()),
        "sports_fill_pixels": int((arrays["sports_fill"] > 0).sum()),
        "semantic_road_pixels": int((arrays["highway_class"] > 0).sum()),
        "lane_marking_pixels": int((arrays["lane_markings"] > 0).sum()),
        "lane_area_pixels": int((arrays["lane_area"] > 0).sum()),
        "turn_restriction_pixels": int((arrays["turn_restriction_via_mask"] > 0).sum()),
        "topology_oversample": oversample,
    }

    return RasterResult(
        width=width,
        height=height,
        pixel_size=config.pixel_size,
        bounds_xy=data.bounds_xy,
        oversample=oversample,
        arrays=arrays,
        object_stacks={
            "road": road_stack_payload,
            "water": water_stack_payload,
            "lane_marking": lane_stack_payload,
        },
        files=files,
        band_sums=band_sums,
        metrics=metrics,
        preview_path=str(preview_path),
    )


def _fill_polygon_semantics(arrays: dict[str, np.ndarray], polygon: PolygonFeature, outer: list[tuple[int, int]]) -> None:
    mask = Image.new("L", (arrays["building_class"].shape[1], arrays["building_class"].shape[0]), 0)
    ImageDraw.Draw(mask).polygon(outer, fill=1)
    mask_array = np.array(mask) > 0
    arrays["building_class"][mask_array] = _encode_building_class(polygon.tags)
    arrays["building_levels"][mask_array] = _parse_small_int(polygon.tags.get("building:levels"))
    arrays["building_min_level"][mask_array] = _parse_small_int(polygon.tags.get("building:min_level"))


def _clear_polygon_semantics(arrays: dict[str, np.ndarray], hole: list[tuple[int, int]]) -> None:
    mask = Image.new("L", (arrays["building_class"].shape[1], arrays["building_class"].shape[0]), 0)
    ImageDraw.Draw(mask).polygon(hole, fill=1)
    mask_array = np.array(mask) > 0
    arrays["building_class"][mask_array] = 0
    arrays["building_levels"][mask_array] = 0
    arrays["building_min_level"][mask_array] = 0


def _fill_sports_semantics(arrays: dict[str, np.ndarray], polygon: PolygonFeature, outer: list[tuple[int, int]]) -> None:
    mask = Image.new("L", (arrays["sports_class"].shape[1], arrays["sports_class"].shape[0]), 0)
    ImageDraw.Draw(mask).polygon(outer, fill=1)
    mask_array = np.array(mask) > 0
    arrays["sports_class"][mask_array] = _encode_sport_class(polygon.tags)


def _clear_sports_semantics(arrays: dict[str, np.ndarray], hole: list[tuple[int, int]]) -> None:
    mask = Image.new("L", (arrays["sports_class"].shape[1], arrays["sports_class"].shape[0]), 0)
    ImageDraw.Draw(mask).polygon(hole, fill=1)
    mask_array = np.array(mask) > 0
    arrays["sports_class"][mask_array] = 0


def _write_road_semantics(
    arrays: dict[str, np.ndarray],
    priority_grid: np.ndarray,
    row: int,
    col: int,
    tags: dict[str, str],
) -> None:
    if row < 0 or col < 0 or row >= priority_grid.shape[0] or col >= priority_grid.shape[1]:
        return
    priority = _road_priority(tags)
    if priority < priority_grid[row, col]:
        return
    priority_grid[row, col] = priority
    arrays["highway_class"][row, col] = _encode_highway_class(tags)
    arrays["road_oneway"][row, col] = 1 if tags.get("oneway") == "yes" else 2 if tags.get("oneway") == "no" else 0
    arrays["road_access"][row, col] = ACCESS_CODES.get(tags.get("access", ""), 0)
    arrays["road_foot"][row, col] = PERMISSION_CODES.get(tags.get("foot", ""), 0)
    arrays["road_bicycle"][row, col] = PERMISSION_CODES.get(tags.get("bicycle", ""), 0)
    arrays["road_lanes"][row, col] = _parse_small_int(tags.get("lanes"))
    arrays["road_maxspeed_kph"][row, col] = _parse_maxspeed(tags.get("maxspeed"))
    arrays["road_surface_class"][row, col] = SURFACE_CODES.get(tags.get("surface", ""), 0)


def _build_object_stack_payload(
    pixel_objects: dict[tuple[int, int], set[int]],
    depth: int,
) -> tuple[dict[str, object], dict[str, int]]:
    rows: list[int] = []
    cols: list[int] = []
    counts: list[int] = []
    ids_rows: list[list[int]] = []
    overflows: list[dict[str, object]] = []

    for (row, col), object_ids in sorted(pixel_objects.items()):
        ids_sorted = sorted(object_ids)
        rows.append(row)
        cols.append(col)
        counts.append(len(ids_sorted))
        ids_rows.append(ids_sorted[:depth] + [0] * max(0, depth - len(ids_sorted[:depth])))
        if len(ids_sorted) > depth:
            overflows.append({"row": row, "col": col, "object_ids": ids_sorted})
    payload = {
        "encoding": "sparse_object_stack",
        "depth": depth,
        "pixels": [
            {"row": row, "col": col, "count": count, "object_ids": object_ids}
            for row, col, count, object_ids in zip(rows, cols, counts, ids_rows, strict=False)
        ],
        "overflow_pixels": overflows,
    }
    return payload, {"occupied_pixel_count": len(rows), "overflow_pixel_count": len(overflows)}


def _count_fragmented_features(feature_pixels: dict[int, set[tuple[int, int]]], clipped_features: set[int]) -> int:
    fragmented = 0
    for feature_id, pixels in feature_pixels.items():
        if not pixels or feature_id in clipped_features:
            continue
        if _sparse_component_count(pixels) > 1:
            fragmented += 1
    return fragmented


def _sparse_component_count(pixels: set[tuple[int, int]]) -> int:
    remaining = set(pixels)
    components = 0
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            y, x = stack.pop()
            for ny in range(y - 1, y + 2):
                for nx in range(x - 1, x + 2):
                    if (ny, nx) == (y, x):
                        continue
                    if (ny, nx) in remaining:
                        remaining.remove((ny, nx))
                        stack.append((ny, nx))
    return components


def _build_primary_id_grid(height: int, width: int, pixel_objects: dict[tuple[int, int], set[int]]) -> np.ndarray:
    grid = np.zeros((height, width), dtype=np.int32)
    for (row, col), object_ids in pixel_objects.items():
        if 0 <= row < height and 0 <= col < width and object_ids:
            grid[row, col] = min(object_ids)
    return grid


def _build_count_grid(height: int, width: int, pixel_objects: dict[tuple[int, int], set[int]]) -> np.ndarray:
    grid = np.zeros((height, width), dtype=np.uint8)
    for (row, col), object_ids in pixel_objects.items():
        if 0 <= row < height and 0 <= col < width:
            grid[row, col] = min(255, len(object_ids))
    return grid


def _downsample_max(array: np.ndarray, oversample: int) -> np.ndarray:
    height, width = array.shape
    return array.reshape(height // oversample, oversample, width // oversample, oversample).max(axis=(1, 3))


def _downsample_or(array: np.ndarray, oversample: int) -> np.ndarray:
    height, width = array.shape
    blocks = array.reshape(height // oversample, oversample, width // oversample, oversample)
    return np.bitwise_or.reduce(np.bitwise_or.reduce(blocks, axis=3), axis=1)


def _draw_polygon_boundary(drawer: ImageDraw.ImageDraw, polygon: list[tuple[int, int]]) -> None:
    drawer.line(polygon + [polygon[0]], fill=1, width=1)


def _to_pixel(point: tuple[float, float], bounds_xy: tuple[float, float, float, float], pixel_size: float) -> tuple[int, int]:
    min_x, _, _, max_y = bounds_xy
    x = int(round((point[0] - min_x) / pixel_size)) + PADDING_PX
    y = int(round((max_y - point[1]) / pixel_size)) + PADDING_PX
    return x, y


def _to_super_pixel(
    point: tuple[float, float],
    bounds_xy: tuple[float, float, float, float],
    pixel_size: float,
    oversample: int,
) -> tuple[int, int]:
    min_x, _, _, max_y = bounds_xy
    x = int(round((point[0] - min_x) / pixel_size * oversample)) + PADDING_PX * oversample
    y = int(round((max_y - point[1]) / pixel_size * oversample)) + PADDING_PX * oversample
    return x, y


def _iter_supercover_pixels(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    x0, y0 = start
    x1, y1 = end
    dx = x1 - x0
    dy = y1 - y0
    nx = abs(dx)
    ny = abs(dy)
    sign_x = _sign(dx)
    sign_y = _sign(dy)
    x = x0
    y = y0
    points = [(x, y)]
    ix = 0
    iy = 0
    while ix < nx or iy < ny:
        decision_x = (1 + 2 * ix) * ny
        decision_y = (1 + 2 * iy) * nx
        if decision_x == decision_y:
            x += sign_x
            y += sign_y
            ix += 1
            iy += 1
        elif decision_x < decision_y:
            x += sign_x
            ix += 1
        else:
            y += sign_y
            iy += 1
        points.append((x, y))
    deduped: list[tuple[int, int]] = []
    for point in points:
        if not deduped or deduped[-1] != point:
            deduped.append(point)
    return deduped


def _sign(value: int) -> int:
    if value < 0:
        return -1
    if value > 0:
        return 1
    return 0


def _is_z_aware(tags: dict[str, str]) -> bool:
    return tags.get("bridge") == "yes" or tags.get("tunnel") == "yes" or tags.get("layer", "0") != "0"


def _is_building(tags: dict[str, str]) -> bool:
    return "building" in tags or "building:part" in tags


def _is_sports_feature(tags: dict[str, str]) -> bool:
    leisure = tags.get("leisure", "")
    return leisure in SPORT_CLASS_CODES or "sport" in tags


def _encode_highway_class(tags: dict[str, str]) -> int:
    return HIGHWAY_CLASS_CODES.get(tags.get("highway", ""), 255 if "highway" in tags else 0)


def _road_priority(tags: dict[str, str]) -> int:
    highway = tags.get("highway", "")
    code = HIGHWAY_CLASS_CODES.get(highway, 200)
    return 300 - code


def _encode_building_class(tags: dict[str, str]) -> int:
    value = tags.get("building", tags.get("building:part", ""))
    return BUILDING_CLASS_CODES.get(value, 255 if value else 0)


def _encode_sport_class(tags: dict[str, str]) -> int:
    leisure = tags.get("leisure", "")
    if leisure in SPORT_CLASS_CODES:
        return SPORT_CLASS_CODES[leisure]
    return 255 if "sport" in tags else 0


def _parse_small_int(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(0, min(255, int(float(value))))
    except ValueError:
        return 0


def _parse_maxspeed(value: str | None) -> int:
    if not value:
        return 0
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return 0
    return max(0, min(65535, int(digits)))
def _build_preview(arrays: dict[str, np.ndarray]) -> Image.Image:
    height, width = arrays["road_edges"].shape
    rgb = np.full((height, width, 3), 245, dtype=np.uint8)
    rgb[arrays["area_fill"] > 0] = np.array([228, 232, 221], dtype=np.uint8)
    rgb[arrays["building_fill"] > 0] = np.array([186, 186, 186], dtype=np.uint8)
    rgb[arrays["building_boundary"] > 0] = np.array([95, 95, 95], dtype=np.uint8)
    rgb[arrays["sports_fill"] > 0] = np.array([149, 191, 95], dtype=np.uint8)
    rgb[arrays["sports_boundary"] > 0] = np.array([71, 122, 34], dtype=np.uint8)
    rgb[arrays["lane_area"] > 0] = np.array([210, 210, 250], dtype=np.uint8)
    rgb[arrays["hole_mask"] > 0] = np.array([250, 250, 250], dtype=np.uint8)
    rgb[arrays["water_lines"] > 0] = np.array([70, 140, 220], dtype=np.uint8)
    rgb[arrays["road_edges"] > 0] = np.array([233, 148, 52], dtype=np.uint8)
    rgb[arrays["lane_markings"] > 0] = np.array([245, 205, 84], dtype=np.uint8)
    rgb[arrays["crossing_structure"] > 0] = np.array([178, 55, 34], dtype=np.uint8)
    rgb[arrays["turn_restriction_via_mask"] > 0] = np.array([156, 33, 33], dtype=np.uint8)
    rgb[arrays["node_mask"] > 0] = np.array([20, 20, 20], dtype=np.uint8)
    return Image.fromarray(rgb, mode="RGB")


def _write_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    np.savez_compressed(path, **arrays)


def _write_geotiff(
    raster_dir: Path,
    arrays: dict[str, np.ndarray],
    bounds_xy: tuple[float, float, float, float],
    pixel_size: float,
) -> Path | None:
    try:
        import rasterio
        from rasterio.transform import from_origin
    except Exception:
        return None

    height, width = arrays["road_edges"].shape
    band_items = [(name, array) for name, array in sorted(arrays.items()) if array.shape == (height, width)]
    if not band_items:
        return None
    transform = from_origin(bounds_xy[0], bounds_xy[3], pixel_size, pixel_size)
    path = raster_dir / "layers.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=len(band_items),
        dtype=band_items[0][1].dtype,
        crs="EPSG:3857",
        transform=transform,
        nodata=0,
        compress="deflate",
        tiled=True,
    ) as dst:
        for index, (name, array) in enumerate(band_items, start=1):
            dst.write(array, index)
            dst.set_band_description(index, name)
    return path
