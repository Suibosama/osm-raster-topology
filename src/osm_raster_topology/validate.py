from __future__ import annotations

from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw

from osm_raster_topology.model import IngestedData, LineFeature, PolygonFeature, RasterResult
from osm_raster_topology.rasterize import PADDING_PX


SEMANTIC_LAYER_RULES = {
    "highway_class": lambda tags: "highway" in tags,
    "road_oneway": lambda tags: "oneway" in tags,
    "road_access": lambda tags: "access" in tags,
    "road_foot": lambda tags: "foot" in tags,
    "road_bicycle": lambda tags: "bicycle" in tags,
    "road_lanes": lambda tags: "lanes" in tags,
    "road_maxspeed_kph": lambda tags: "maxspeed" in tags,
    "road_surface_class": lambda tags: "surface" in tags,
}


def validate_preservation(data: IngestedData, raster: RasterResult) -> dict[str, object]:
    road_features = [feature for feature in data.line_features if feature.category == "road"]
    water_features = [feature for feature in data.line_features if feature.category == "water"]
    building_features = [feature for feature in data.polygon_features if _is_building(feature)]
    sports_features = [feature for feature in data.polygon_features if _is_sports(feature)]

    road_pixel_map = _collect_feature_pixels(raster.object_stacks.get("road", {}))
    water_pixel_map = _collect_feature_pixels(raster.object_stacks.get("water", {}))
    node_anchor_summary = _summarize_node_anchors(data, raster)

    road_source_components_planar = _count_source_components(road_features, z_aware=False)
    road_source_components_z_aware = _count_source_components(road_features, z_aware=True)
    water_source_components_planar = _count_source_components(water_features, z_aware=False)
    road_raster_components = _count_raster_components(raster.arrays["road_edges"])
    water_raster_components = _count_raster_components(raster.arrays["water_lines"])

    road_missing_ids = sorted(feature.feature_id for feature in road_features if feature.feature_id not in road_pixel_map)
    water_missing_ids = sorted(feature.feature_id for feature in water_features if feature.feature_id not in water_pixel_map)

    semantic_coverage = {
        layer_name: _summarize_semantic_coverage(layer_name, road_features, road_pixel_map, raster.arrays[layer_name])
        for layer_name in SEMANTIC_LAYER_RULES
    }
    building_summary = _summarize_polygon_layer(
        "building_class",
        building_features,
        raster.arrays["building_class"],
        data.bounds_xy,
        raster.pixel_size,
    )
    sports_summary = _summarize_polygon_layer(
        "sports_class",
        sports_features,
        raster.arrays["sports_class"],
        data.bounds_xy,
        raster.pixel_size,
    )
    turn_restrictions = _summarize_turn_restrictions(data, raster)
    lanelet_summary = _summarize_lanelet_relations(data)

    road_exported_count = len(road_pixel_map)
    water_exported_count = len(water_pixel_map)

    checks = {
        "road_component_delta_planar": road_raster_components - road_source_components_planar,
        "road_component_delta_z_aware": road_raster_components - road_source_components_z_aware,
        "water_component_delta_planar": water_raster_components - water_source_components_planar,
        "road_fragmented_feature_count": int(raster.metrics.get("road_feature_fragmented_count", 0)),
        "water_fragmented_feature_count": int(raster.metrics.get("water_feature_fragmented_count", 0)),
        "road_missing_feature_count": len(road_missing_ids),
        "water_missing_feature_count": len(water_missing_ids),
        "building_missing_feature_count": int(building_summary["missing_feature_count"]),
        "sports_missing_feature_count": int(sports_summary["missing_feature_count"]),
        "road_multi_object_pixels": int(raster.metrics.get("road_multi_object_pixels", 0)),
        "road_object_overflow_pixels": int(raster.metrics.get("road_object_overflow_pixels", 0)),
        "node_anchor_collision_count": node_anchor_summary["collision_count"],
        "node_anchor_missing_pixel_count": node_anchor_summary["missing_pixel_count"],
        "node_anchor_out_of_bounds_count": node_anchor_summary["out_of_bounds_count"],
        "turn_restriction_missing_count": turn_restrictions["missing_count"],
    }

    summary = {
        "road_retention_ratio": _safe_ratio(road_exported_count, len(road_features)),
        "water_retention_ratio": _safe_ratio(water_exported_count, len(water_features)),
        "building_retention_ratio": _safe_ratio(int(building_summary["exported_feature_count"]), len(building_features)),
        "sports_retention_ratio": _safe_ratio(int(sports_summary["exported_feature_count"]), len(sports_features)),
        "node_anchor_unique_ratio": node_anchor_summary["node_to_unique_anchor_ratio"],
        "node_anchor_pixel_coverage_ratio": _safe_ratio(
            node_anchor_summary["present_pixel_count"],
            node_anchor_summary["expected_anchor_pixel_count"],
        ),
        "road_highway_class_coverage_ratio": semantic_coverage["highway_class"]["coverage_ratio"],
        "road_rule_coverage_ratio_average": _average_ratio(
            [
                semantic_coverage["road_oneway"]["coverage_ratio"],
                semantic_coverage["road_access"]["coverage_ratio"],
                semantic_coverage["road_foot"]["coverage_ratio"],
                semantic_coverage["road_bicycle"]["coverage_ratio"],
                semantic_coverage["road_lanes"]["coverage_ratio"],
                semantic_coverage["road_maxspeed_kph"]["coverage_ratio"],
                semantic_coverage["road_surface_class"]["coverage_ratio"],
            ]
        ),
        "turn_restriction_coverage_ratio": turn_restrictions["coverage_ratio"],
        "lanelet_neighbor_ratio": lanelet_summary["neighbor_ratio"],
        "lanelet_successor_ratio": lanelet_summary["successor_ratio"],
    }

    return {
        "summary": summary,
        "checks": checks,
        "source_counts": {
            "raw_node_count": int(data.stats.get("raw_node_count", 0)),
            "raw_way_count": int(data.stats.get("raw_way_count", 0)),
            "raw_relation_count": int(data.stats.get("raw_relation_count", 0)),
            "road_feature_count": len(road_features),
            "water_feature_count": len(water_features),
            "graph_node_count": len(data.graph_nodes),
            "building_feature_count": len(building_features),
            "sports_feature_count": len(sports_features),
            "turn_restriction_count": len(data.turn_restrictions),
        },
        "roads": {
            "source_feature_count": len(road_features),
            "exported_feature_count": road_exported_count,
            "missing_feature_count": len(road_missing_ids),
            "missing_feature_ids": road_missing_ids,
            "missing_feature_refs": _feature_refs_from_ids(road_missing_ids, road_features),
            "source_component_count_planar": road_source_components_planar,
            "source_component_count_z_aware": road_source_components_z_aware,
            "raster_component_count": road_raster_components,
        },
        "water": {
            "source_feature_count": len(water_features),
            "exported_feature_count": water_exported_count,
            "missing_feature_count": len(water_missing_ids),
            "missing_feature_ids": water_missing_ids,
            "missing_feature_refs": _feature_refs_from_ids(water_missing_ids, water_features),
            "source_component_count_planar": water_source_components_planar,
            "raster_component_count": water_raster_components,
        },
        "nodes": node_anchor_summary,
        "polygons": {
            "building": building_summary,
            "sports": sports_summary,
        },
        "semantics": semantic_coverage,
        "turn_restrictions": turn_restrictions,
        "lanelet": lanelet_summary,
    }


def _collect_feature_pixels(payload: dict[str, object]) -> dict[int, set[tuple[int, int]]]:
    feature_pixels: dict[int, set[tuple[int, int]]] = defaultdict(set)
    if not isinstance(payload, dict):
        return feature_pixels
    overflow_lookup = {
        (int(item["row"]), int(item["col"])): [int(feature_id) for feature_id in item.get("object_ids", []) if int(feature_id) > 0]
        for item in payload.get("overflow_pixels", [])
    }
    for item in payload.get("pixels", []):
        row = int(item["row"])
        col = int(item["col"])
        count = int(item.get("count", 0))
        object_ids = overflow_lookup.get((row, col))
        if object_ids is None:
            padded_ids = [int(feature_id) for feature_id in item.get("object_ids", [])]
            object_ids = [feature_id for feature_id in padded_ids[:count] if feature_id > 0]
        for feature_id in object_ids:
            feature_pixels[feature_id].add((row, col))
    return feature_pixels


def _summarize_node_anchors(data: IngestedData, raster: RasterResult) -> dict[str, object]:
    anchor_map: dict[tuple[int, int], list[str]] = defaultdict(list)
    out_of_bounds_nodes: list[dict[str, object]] = []
    for node in data.graph_nodes:
        col, row = _to_super_pixel(node.point, data.bounds_xy, raster.pixel_size, raster.oversample)
        if 0 <= row < raster.arrays["node_anchor_super"].shape[0] and 0 <= col < raster.arrays["node_anchor_super"].shape[1]:
            anchor_map[(row, col)].append(node.node_key)
        else:
            out_of_bounds_nodes.append({"node_key": node.node_key, "row": row, "col": col})

    present_pixels = {tuple(coords.tolist()) for coords in np.argwhere(raster.arrays["node_anchor_super"] > 0)}
    expected_pixels = set(anchor_map)
    missing_pixels = sorted(expected_pixels - present_pixels)
    collision_pixels = sorted(pixel for pixel, node_keys in anchor_map.items() if len(node_keys) > 1)
    in_bounds_graph_node_count = len(data.graph_nodes) - len(out_of_bounds_nodes)

    return {
        "source_graph_node_count": len(data.graph_nodes),
        "in_bounds_graph_node_count": in_bounds_graph_node_count,
        "unique_anchor_pixel_count": len(expected_pixels),
        "expected_anchor_pixel_count": len(expected_pixels),
        "present_pixel_count": len(expected_pixels & present_pixels),
        "missing_pixel_count": len(missing_pixels),
        "missing_pixels_sample": [{"row": row, "col": col} for row, col in missing_pixels[:20]],
        "out_of_bounds_count": len(out_of_bounds_nodes),
        "out_of_bounds_sample": out_of_bounds_nodes[:20],
        "collision_count": len(collision_pixels),
        "collision_pixels_sample": [
            {"row": row, "col": col, "node_keys": anchor_map[(row, col)][:8]}
            for row, col in collision_pixels[:20]
        ],
        "node_to_unique_anchor_ratio": _safe_ratio(len(expected_pixels), in_bounds_graph_node_count),
        "anchor_pixel_coverage_ratio": _safe_ratio(len(expected_pixels & present_pixels), len(expected_pixels)),
    }


def _count_source_components(features: list[LineFeature], z_aware: bool) -> int:
    if not features:
        return 0
    adjacency: dict[int, set[int]] = {feature.feature_id: set() for feature in features}
    shared_nodes: dict[object, list[int]] = defaultdict(list)
    for feature in features:
        keys = set()
        for node_ref in feature.node_refs:
            key = (node_ref, feature.z_group) if z_aware else node_ref
            keys.add(key)
        for key in keys:
            shared_nodes[key].append(feature.feature_id)
    for feature_ids in shared_nodes.values():
        if len(feature_ids) < 2:
            continue
        root = feature_ids[0]
        for feature_id in feature_ids[1:]:
            adjacency[root].add(feature_id)
            adjacency[feature_id].add(root)

    remaining = set(adjacency)
    components = 0
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            feature_id = stack.pop()
            for neighbor in adjacency[feature_id]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
    return components


def _count_raster_components(array: np.ndarray) -> int:
    occupied = {tuple(coords.tolist()) for coords in np.argwhere(array > 0)}
    if not occupied:
        return 0
    components = 0
    while occupied:
        components += 1
        start = occupied.pop()
        stack = [start]
        while stack:
            row, col = stack.pop()
            for next_row in range(row - 1, row + 2):
                for next_col in range(col - 1, col + 2):
                    if (next_row, next_col) == (row, col):
                        continue
                    candidate = (next_row, next_col)
                    if candidate in occupied:
                        occupied.remove(candidate)
                        stack.append(candidate)
    return components


def _summarize_semantic_coverage(
    layer_name: str,
    road_features: list[LineFeature],
    road_pixel_map: dict[int, set[tuple[int, int]]],
    layer_array: np.ndarray,
) -> dict[str, object]:
    tagged_features = [feature for feature in road_features if SEMANTIC_LAYER_RULES[layer_name](feature.tags)]
    missing_ids: list[int] = []
    for feature in tagged_features:
        pixels = road_pixel_map.get(feature.feature_id, set())
        if not pixels:
            missing_ids.append(feature.feature_id)
            continue
        if not any(int(layer_array[row, col]) > 0 for row, col in pixels):
            missing_ids.append(feature.feature_id)
    covered_count = len(tagged_features) - len(missing_ids)
    return {
        "source_tagged_feature_count": len(tagged_features),
        "covered_feature_count": covered_count,
        "missing_feature_count": len(missing_ids),
        "coverage_ratio": _safe_ratio(covered_count, len(tagged_features)),
        "missing_feature_ids": missing_ids,
        "missing_feature_refs": _feature_refs_from_ids(missing_ids, tagged_features),
    }


def _summarize_polygon_layer(
    layer_name: str,
    polygon_features: list[PolygonFeature],
    layer_array: np.ndarray,
    bounds_xy: tuple[float, float, float, float],
    pixel_size: float,
) -> dict[str, object]:
    tagged_features = [feature for feature in polygon_features if _semantic_value_for_polygon(layer_name, feature.tags) > 0]
    semantic_hit_ids = {
        feature.feature_id
        for feature in tagged_features
        if _polygon_hits_layer(feature, layer_array, bounds_xy, pixel_size)
    }
    present_semantic_codes = sorted(int(code) for code in np.unique(layer_array) if int(code) > 0)
    missing_ids = [
        feature.feature_id
        for feature in tagged_features
        if feature.feature_id not in semantic_hit_ids
    ]
    covered_count = len(tagged_features) - len(missing_ids)
    semantic_coverage = {
        "source_tagged_feature_count": len(tagged_features),
        "covered_feature_count": covered_count,
        "missing_feature_count": len(missing_ids),
        "coverage_ratio": _safe_ratio(covered_count, len(tagged_features)),
        "present_semantic_codes": present_semantic_codes,
        "missing_feature_ids": missing_ids,
        "missing_feature_refs": _feature_refs_from_ids(missing_ids, tagged_features),
    }
    return {
        "source_feature_count": len(tagged_features),
        "exported_feature_count": covered_count,
        "missing_feature_count": len(missing_ids),
        "missing_feature_ids": missing_ids,
        "missing_feature_refs": _feature_refs_from_ids(missing_ids, tagged_features),
        "semantic_coverage": semantic_coverage,
    }


def _polygon_hits_layer(
    feature: PolygonFeature,
    layer_array: np.ndarray,
    bounds_xy: tuple[float, float, float, float],
    pixel_size: float,
) -> bool:
    outer = [_to_pixel(point, bounds_xy, pixel_size) for point in feature.outer]
    if len(outer) < 3:
        return False
    rows = [row for _, row in outer]
    cols = [col for col, _ in outer]
    for hole in feature.holes:
        hole_pixels = [_to_pixel(point, bounds_xy, pixel_size) for point in hole]
        rows.extend(row for _, row in hole_pixels)
        cols.extend(col for col, _ in hole_pixels)
    min_row = max(0, min(rows))
    max_row = min(layer_array.shape[0] - 1, max(rows))
    min_col = max(0, min(cols))
    max_col = min(layer_array.shape[1] - 1, max(cols))
    if min_row > max_row or min_col > max_col:
        return False
    mask = Image.new("L", (max_col - min_col + 1, max_row - min_row + 1), 0)
    drawer = ImageDraw.Draw(mask)
    shifted_outer = [(col - min_col, row - min_row) for col, row in outer]
    drawer.polygon(shifted_outer, fill=1)
    for hole in feature.holes:
        shifted_hole = [(col - min_col, row - min_row) for col, row in [_to_pixel(point, bounds_xy, pixel_size) for point in hole]]
        if len(shifted_hole) >= 3:
            drawer.polygon(shifted_hole, fill=0)
    mask_array = np.array(mask) > 0
    view = layer_array[min_row : max_row + 1, min_col : max_col + 1]
    return bool(np.any(view[mask_array] > 0))


def _semantic_value_for_polygon(layer_name: str, tags: dict[str, str]) -> int:
    if layer_name == "building_class":
        return 1 if "building" in tags or "building:part" in tags else 0
    if layer_name == "sports_class":
        return 1 if tags.get("leisure", "") in {"pitch", "stadium", "sports_centre", "sports_hall", "swimming_pool", "track"} or "sport" in tags else 0
    return 0


def _summarize_turn_restrictions(data: IngestedData, raster: RasterResult) -> dict[str, object]:
    via_mask = raster.arrays["turn_restriction_via_mask"]
    missing_relation_ids: list[int] = []
    for restriction in data.turn_restrictions:
        if restriction.via_node_ref is None:
            missing_relation_ids.append(restriction.relation_id)
            continue
        node = data.nodes.get(restriction.via_node_ref)
        if node is None:
            missing_relation_ids.append(restriction.relation_id)
            continue
        col, row = _to_pixel((node.x, node.y), data.bounds_xy, raster.pixel_size)
        if row < 0 or col < 0 or row >= via_mask.shape[0] or col >= via_mask.shape[1] or int(via_mask[row, col]) == 0:
            missing_relation_ids.append(restriction.relation_id)
    covered_count = len(data.turn_restrictions) - len(missing_relation_ids)
    return {
        "source_count": len(data.turn_restrictions),
        "covered_count": covered_count,
        "missing_count": len(missing_relation_ids),
        "coverage_ratio": _safe_ratio(covered_count, len(data.turn_restrictions)),
        "missing_relation_ids": missing_relation_ids,
    }


def _summarize_lanelet_relations(data: IngestedData) -> dict[str, object]:
    lanelets = data.lanelet_relations
    if not lanelets:
        return {
            "lanelet_count": 0,
            "with_predecessor": 0,
            "with_successor": 0,
            "with_left_neighbor": 0,
            "with_right_neighbor": 0,
            "with_any_neighbor": 0,
            "isolated_lanelets": 0,
            "regulatory_ref_count": 0,
            "neighbor_ratio": 0.0,
            "successor_ratio": 0.0,
        }
    with_predecessor = sum(1 for l in lanelets if l.get("predecessor_lanelets"))
    with_successor = sum(1 for l in lanelets if l.get("successor_lanelets"))
    with_left = sum(1 for l in lanelets if l.get("left_neighbors"))
    with_right = sum(1 for l in lanelets if l.get("right_neighbors"))
    with_any = sum(1 for l in lanelets if l.get("left_neighbors") or l.get("right_neighbors"))
    isolated = sum(
        1
        for l in lanelets
        if not l.get("predecessor_lanelets")
        and not l.get("successor_lanelets")
        and not l.get("left_neighbors")
        and not l.get("right_neighbors")
    )
    regulatory_count = sum(len(l.get("regulatory_element_refs", [])) for l in lanelets)
    lanelet_count = len(lanelets)
    return {
        "lanelet_count": lanelet_count,
        "with_predecessor": with_predecessor,
        "with_successor": with_successor,
        "with_left_neighbor": with_left,
        "with_right_neighbor": with_right,
        "with_any_neighbor": with_any,
        "isolated_lanelets": isolated,
        "regulatory_ref_count": regulatory_count,
        "neighbor_ratio": _safe_ratio(with_any, lanelet_count),
        "successor_ratio": _safe_ratio(with_successor, lanelet_count),
    }


def _feature_refs_from_ids(feature_ids: list[int], features: list[LineFeature | PolygonFeature]) -> list[str]:
    ref_map = {feature.feature_id: feature.osm_ref for feature in features}
    return [ref_map[feature_id] for feature_id in feature_ids if feature_id in ref_map]


def _is_building(feature: PolygonFeature) -> bool:
    return "building" in feature.tags or "building:part" in feature.tags


def _is_sports(feature: PolygonFeature) -> bool:
    leisure = feature.tags.get("leisure", "")
    return leisure in {"pitch", "stadium", "sports_centre", "sports_hall", "swimming_pool", "track"} or "sport" in feature.tags


def _to_pixel(point: tuple[float, float], bounds_xy: tuple[float, float, float, float], pixel_size: float) -> tuple[int, int]:
    min_x, _, _, max_y = bounds_xy
    col = int(round((point[0] - min_x) / pixel_size)) + PADDING_PX
    row = int(round((max_y - point[1]) / pixel_size)) + PADDING_PX
    return col, row


def _to_super_pixel(
    point: tuple[float, float],
    bounds_xy: tuple[float, float, float, float],
    pixel_size: float,
    oversample: int,
) -> tuple[int, int]:
    min_x, _, _, max_y = bounds_xy
    col = int(round((point[0] - min_x) / pixel_size * oversample)) + PADDING_PX * oversample
    row = int(round((max_y - point[1]) / pixel_size * oversample)) + PADDING_PX * oversample
    return col, row


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 6)


def _average_ratio(values: list[float]) -> float:
    if not values:
        return 1.0
    return round(sum(values) / len(values), 6)
