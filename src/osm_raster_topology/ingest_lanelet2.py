from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import Counter

from osm_raster_topology.config import RunConfig
from osm_raster_topology.model import GraphNode, IngestedData, LineFeature, NodeRecord, PolygonFeature, TurnRestriction


EARTH_RADIUS_M = 6378137.0
DEFAULT_CENTERLINE_STEP_M = 1.0
MAX_CENTERLINE_POINTS = 2000


def ingest_lanelet2_xml(config: RunConfig) -> IngestedData:
    if config.input_path.suffix.lower() != ".osm":
        raise ValueError("Lanelet2 ingest expects .osm XML input.")
    if config.target_crs != "EPSG:3857":
        raise ValueError("Lanelet2 ingest currently supports EPSG:3857 only.")

    root = ET.parse(config.input_path).getroot()
    bounds = _parse_bounds(root)
    nodes = _parse_nodes(root)
    ways = _parse_ways(root)
    relations = _parse_relations(root)
    turn_restrictions = _extract_turn_restrictions(relations)

    line_features: list[LineFeature] = []
    polygon_features: list[PolygonFeature] = []
    lanelet_records: list[dict[str, object]] = []
    feature_id = 1
    synthesized_centerlines = 0
    lanelet_count = 0

    for relation in relations:
        tags = relation["tags"]
        if tags.get("type") != "lanelet":
            continue
        lanelet_count += 1
        left_refs = _collect_member_way_refs(relation, ways, "left")
        right_refs = _collect_member_way_refs(relation, ways, "right")
        centerline_refs = _collect_member_way_refs(relation, ways, "centerline")
        regulatory_refs = _collect_member_relation_refs(relation, "regulatory_element")
        polygon_outer = _build_lanelet_polygon(left_refs, right_refs, nodes)

        centerline_points: list[tuple[float, float]] = []
        centerline_node_refs: list[int] = []
        if centerline_refs:
            centerline_node_refs = centerline_refs[0]
            centerline_points = _coords_from_refs(centerline_node_refs, nodes)
        elif left_refs and right_refs:
            left_points = _coords_from_refs(left_refs[0], nodes)
            right_points = _coords_from_refs(right_refs[0], nodes)
            centerline_points, centerline_node_refs = _synthesize_centerline(left_points, right_points, left_refs[0])
            if centerline_points:
                synthesized_centerlines += 1

        if polygon_outer:
            polygon_features.append(
                PolygonFeature(
                    feature_id=feature_id,
                    osm_ref=f"relation/{relation['id']}",
                    tags=_build_lanelet_polygon_tags(tags),
                    outer=polygon_outer,
                    holes=[],
                )
            )
            feature_id += 1

        if len(centerline_points) < 2:
            lanelet_records.append(
                _build_lanelet_record(
                    relation,
                    left_refs,
                    right_refs,
                    centerline_refs,
                    regulatory_refs,
                    centerline_node_refs,
                )
            )
            continue

        lanelet_tags = _build_lanelet_line_tags(tags)
        line_features.append(
            LineFeature(
                feature_id=feature_id,
                osm_ref=f"relation/{relation['id']}",
                category="road",
                tags=lanelet_tags,
                node_refs=centerline_node_refs,
                points=centerline_points,
                z_group=_z_group(tags),
            )
        )
        feature_id += 1
        lanelet_records.append(
            _build_lanelet_record(
                relation,
                left_refs,
                right_refs,
                centerline_refs,
                regulatory_refs,
                centerline_node_refs,
            )
        )

    graph_nodes = _build_graph_nodes(line_features, nodes)
    stats = _build_stats(root, ways, relations, line_features, polygon_features, graph_nodes, turn_restrictions)
    stats["lanelet_relation_count"] = lanelet_count
    stats["centerline_synthesized_count"] = synthesized_centerlines

    lanelet_relations = _build_lanelet_relations(lanelet_records)

    return IngestedData(
        source_path=str(config.input_path),
        bounds_latlon=bounds,
        bounds_xy=_project_bounds(bounds),
        nodes=nodes,
        line_features=line_features,
        polygon_features=polygon_features,
        graph_nodes=graph_nodes,
        turn_restrictions=turn_restrictions,
        stats=stats,
        ingest_backend="lanelet2_xml",
        lanelet_relations=lanelet_relations,
        notes=[
            "Ingest backend: lanelet2_xml",
            "Lanelet2 relations are converted to road centerlines for rasterization.",
            "When centerline is missing, a synthesized centerline is derived from left/right bounds.",
            "Lanelet polygons are emitted as drivable areas (area=yes, drivable=yes).",
        ],
    )


def _parse_bounds(root: ET.Element) -> tuple[float, float, float, float]:
    bounds = root.find("bounds")
    if bounds is None:
        lats = [float(node.attrib["lat"]) for node in root.findall("node")]
        lons = [float(node.attrib["lon"]) for node in root.findall("node")]
        return min(lats), min(lons), max(lats), max(lons)
    return (
        float(bounds.attrib["minlat"]),
        float(bounds.attrib["minlon"]),
        float(bounds.attrib["maxlat"]),
        float(bounds.attrib["maxlon"]),
    )


def _parse_nodes(root: ET.Element) -> dict[int, NodeRecord]:
    nodes: dict[int, NodeRecord] = {}
    for element in root.findall("node"):
        osm_id = int(element.attrib["id"])
        lat = float(element.attrib["lat"])
        lon = float(element.attrib["lon"])
        x, y = _project(lat, lon)
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}
        nodes[osm_id] = NodeRecord(osm_id=osm_id, lat=lat, lon=lon, x=x, y=y, tags=tags)
    return nodes


def _parse_ways(root: ET.Element) -> dict[int, dict[str, object]]:
    ways: dict[int, dict[str, object]] = {}
    for element in root.findall("way"):
        way_id = int(element.attrib["id"])
        node_refs = [int(nd.attrib["ref"]) for nd in element.findall("nd")]
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}
        ways[way_id] = {"nodes": node_refs, "tags": tags}
    return ways


def _parse_relations(root: ET.Element) -> list[dict[str, object]]:
    relations: list[dict[str, object]] = []
    for element in root.findall("relation"):
        members = [
            {
                "type": member.attrib.get("type", ""),
                "ref": int(member.attrib.get("ref", "0")),
                "role": member.attrib.get("role", ""),
            }
            for member in element.findall("member")
        ]
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}
        relations.append({"id": int(element.attrib["id"]), "members": members, "tags": tags})
    return relations


def _extract_turn_restrictions(relations: list[dict[str, object]]) -> list[TurnRestriction]:
    restrictions: list[TurnRestriction] = []
    for relation in relations:
        tags = relation["tags"]
        if tags.get("type") != "restriction":
            continue
        from_way_ref = None
        via_node_ref = None
        to_way_ref = None
        for member in relation["members"]:
            role = member["role"]
            if member["type"] == "way" and role == "from":
                from_way_ref = member["ref"]
            elif member["type"] == "node" and role == "via":
                via_node_ref = member["ref"]
            elif member["type"] == "way" and role == "to":
                to_way_ref = member["ref"]
        restrictions.append(
            TurnRestriction(
                relation_id=relation["id"],
                restriction=tags.get("restriction", "unknown"),
                from_way_ref=from_way_ref,
                via_node_ref=via_node_ref,
                to_way_ref=to_way_ref,
                tags=tags,
            )
        )
    return restrictions


def _collect_member_way_refs(relation: dict[str, object], ways: dict[int, dict[str, object]], role: str) -> list[list[int]]:
    refs: list[list[int]] = []
    for member in relation["members"]:
        if member["type"] != "way":
            continue
        if member["role"] != role:
            continue
        way = ways.get(member["ref"])
        if way is None:
            continue
        node_refs = way["nodes"]
        if len(node_refs) < 2:
            continue
        refs.append(node_refs)
    return refs


def _collect_member_relation_refs(relation: dict[str, object], role: str) -> list[int]:
    refs: list[int] = []
    for member in relation["members"]:
        if member["type"] != "relation":
            continue
        if member["role"] != role:
            continue
        refs.append(member["ref"])
    return refs


def _coords_from_refs(node_refs: list[int], nodes: dict[int, NodeRecord]) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for node_ref in node_refs:
        node = nodes.get(node_ref)
        if node is None:
            continue
        coords.append((node.x, node.y))
    return coords


def _build_lanelet_polygon(
    left_refs: list[list[int]],
    right_refs: list[list[int]],
    nodes: dict[int, NodeRecord],
) -> list[tuple[float, float]]:
    if not left_refs or not right_refs:
        return []
    left_points = _coords_from_refs(left_refs[0], nodes)
    right_points = _coords_from_refs(right_refs[0], nodes)
    if len(left_points) < 2 or len(right_points) < 2:
        return []
    if _point_distance(left_points[0], right_points[0]) > _point_distance(left_points[0], right_points[-1]):
        right_points = list(reversed(right_points))
    outer = left_points + list(reversed(right_points))
    if not outer:
        return []
    if _point_distance(outer[0], outer[-1]) > 1e-6:
        outer.append(outer[0])
    if len(outer) < 4:
        return []
    return outer


def _build_lanelet_record(
    relation: dict[str, object],
    left_refs: list[list[int]],
    right_refs: list[list[int]],
    centerline_refs: list[list[int]],
    regulatory_refs: list[int],
    centerline_node_refs: list[int],
) -> dict[str, object]:
    left_way_ids = _extract_way_ids(relation, "left")
    right_way_ids = _extract_way_ids(relation, "right")
    centerline_way_ids = _extract_way_ids(relation, "centerline")
    return {
        "lanelet_id": relation["id"],
        "tags": relation["tags"],
        "left_way_ids": left_way_ids,
        "right_way_ids": right_way_ids,
        "centerline_way_ids": centerline_way_ids,
        "left_node_refs": left_refs[0] if left_refs else [],
        "right_node_refs": right_refs[0] if right_refs else [],
        "centerline_node_refs": centerline_node_refs,
        "regulatory_element_refs": regulatory_refs,
    }


def _extract_way_ids(relation: dict[str, object], role: str) -> list[int]:
    ids: list[int] = []
    for member in relation["members"]:
        if member["type"] == "way" and member["role"] == role:
            ids.append(member["ref"])
    return ids


def _build_lanelet_relations(lanelet_records: list[dict[str, object]]) -> list[dict[str, object]]:
    if not lanelet_records:
        return []
    by_id = {int(record["lanelet_id"]): record for record in lanelet_records}
    left_way_map: dict[int, set[int]] = {}
    right_way_map: dict[int, set[int]] = {}
    start_node_map: dict[int, set[int]] = {}
    end_node_map: dict[int, set[int]] = {}

    for record in lanelet_records:
        lanelet_id = int(record["lanelet_id"])
        left_way_map[lanelet_id] = set(record.get("left_way_ids", []))
        right_way_map[lanelet_id] = set(record.get("right_way_ids", []))
        centerline_nodes = record.get("centerline_node_refs", [])
        if centerline_nodes:
            start_node_map.setdefault(int(centerline_nodes[0]), set()).add(lanelet_id)
            end_node_map.setdefault(int(centerline_nodes[-1]), set()).add(lanelet_id)

    relations: list[dict[str, object]] = []
    for lanelet_id, record in by_id.items():
        left_neighbors = sorted(
            other_id
            for other_id, other_right in right_way_map.items()
            if other_id != lanelet_id and left_way_map.get(lanelet_id, set()) & other_right
        )
        right_neighbors = sorted(
            other_id
            for other_id, other_left in left_way_map.items()
            if other_id != lanelet_id and right_way_map.get(lanelet_id, set()) & other_left
        )
        successors = sorted(
            other_id
            for node_id in record.get("centerline_node_refs", [])[ -1 : ]
            for other_id in start_node_map.get(int(node_id), set())
            if other_id != lanelet_id
        )
        predecessors = sorted(
            other_id
            for node_id in record.get("centerline_node_refs", [])[ :1 ]
            for other_id in end_node_map.get(int(node_id), set())
            if other_id != lanelet_id
        )
        relations.append(
            {
                "lanelet_id": lanelet_id,
                "predecessor_lanelets": predecessors,
                "successor_lanelets": successors,
                "left_neighbors": left_neighbors,
                "right_neighbors": right_neighbors,
                "regulatory_element_refs": record.get("regulatory_element_refs", []),
                "tags": record.get("tags", {}),
            }
        )
    return relations


def _synthesize_centerline(
    left: list[tuple[float, float]],
    right: list[tuple[float, float]],
    left_node_refs: list[int],
) -> tuple[list[tuple[float, float]], list[int]]:
    if len(left) < 2 or len(right) < 2:
        return [], []
    if _point_distance(left[0], right[0]) > _point_distance(left[0], right[-1]):
        right = list(reversed(right))

    left_len = _polyline_length(left)
    right_len = _polyline_length(right)
    if left_len <= 0 or right_len <= 0:
        return [], []

    max_len = max(left_len, right_len)
    step = max(DEFAULT_CENTERLINE_STEP_M, max_len / MAX_CENTERLINE_POINTS)
    sample_count = max(2, int(max_len / step) + 1)

    left_samples = _resample_polyline(left, sample_count)
    right_samples = _resample_polyline(right, sample_count)
    if not left_samples or not right_samples:
        return [], []

    centerline = [((lx + rx) * 0.5, (ly + ry) * 0.5) for (lx, ly), (rx, ry) in zip(left_samples, right_samples)]
    node_refs: list[int] = []
    if left_node_refs:
        node_refs = [left_node_refs[0], left_node_refs[-1]]
    return centerline, node_refs


def _build_lanelet_line_tags(tags: dict[str, str]) -> dict[str, str]:
    lanelet_tags = dict(tags)
    if "highway" not in lanelet_tags:
        lanelet_tags["highway"] = "service"
    if "one_way" in lanelet_tags and "oneway" not in lanelet_tags:
        lanelet_tags["oneway"] = lanelet_tags["one_way"]
    if "speed_limit" in lanelet_tags and "maxspeed" not in lanelet_tags:
        lanelet_tags["maxspeed"] = lanelet_tags["speed_limit"]
    return lanelet_tags


def _build_lanelet_polygon_tags(tags: dict[str, str]) -> dict[str, str]:
    polygon_tags = dict(tags)
    polygon_tags.setdefault("area", "yes")
    polygon_tags.setdefault("drivable", "yes")
    polygon_tags.setdefault("source", "lanelet2")
    return polygon_tags


def _build_graph_nodes(line_features: list[LineFeature], nodes: dict[int, NodeRecord]) -> list[GraphNode]:
    degree_map: Counter[tuple[int, str, str]] = Counter()
    for feature in line_features:
        refs = feature.node_refs
        if len(refs) < 2:
            continue
        for start, end in zip(refs[:-1], refs[1:]):
            if start == end:
                continue
            degree_map[(start, feature.category, feature.z_group)] += 1
            degree_map[(end, feature.category, feature.z_group)] += 1

    graph_nodes: list[GraphNode] = []
    for key, degree in degree_map.items():
        node_id, category, z_group = key
        node = nodes.get(node_id)
        if node is None:
            continue
        role = "intermediate"
        if degree == 1:
            role = "endpoint"
        elif degree >= 3:
            role = "junction"
        if role == "intermediate":
            continue
        graph_nodes.append(
            GraphNode(
                node_key=f"{category}:{z_group}:{node_id}",
                osm_node_id=node_id,
                category=category,
                z_group=z_group,
                degree=degree,
                point=(node.x, node.y),
                role=role,
                tags=node.tags,
            )
        )
    return graph_nodes


def _build_stats(
    root: ET.Element,
    ways: dict[int, dict[str, object]],
    relations: list[dict[str, object]],
    line_features: list[LineFeature],
    polygon_features: list[PolygonFeature],
    graph_nodes: list[GraphNode],
    turn_restrictions: list[TurnRestriction],
) -> dict[str, int | float | str]:
    relation_types = Counter(rel["tags"].get("type", "unknown") for rel in relations)
    road_line_count = sum(1 for feature in line_features if feature.category == "road")
    return {
        "raw_node_count": len(root.findall("node")),
        "raw_way_count": len(ways),
        "raw_relation_count": len(relations),
        "line_feature_count": len(line_features),
        "road_line_count": road_line_count,
        "polygon_feature_count": len(polygon_features),
        "building_polygon_count": 0,
        "graph_node_count": len(graph_nodes),
        "multipolygon_relation_count": relation_types.get("multipolygon", 0),
        "turn_restriction_count": len(turn_restrictions),
    }


def _z_group(tags: dict[str, str]) -> str:
    layer = tags.get("layer", "0")
    if tags.get("bridge") == "yes":
        return f"bridge:{layer}"
    if tags.get("tunnel") == "yes":
        return f"tunnel:{layer}"
    return f"ground:{layer}"


def _project(lat: float, lon: float) -> tuple[float, float]:
    lon_rad = math.radians(lon)
    lat_rad = math.radians(max(min(lat, 85.05112878), -85.05112878))
    x = EARTH_RADIUS_M * lon_rad
    y = EARTH_RADIUS_M * math.log(math.tan(math.pi / 4.0 + lat_rad / 2.0))
    return x, y


def _project_bounds(bounds: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    min_lat, min_lon, max_lat, max_lon = bounds
    min_x, min_y = _project(min_lat, min_lon)
    max_x, max_y = _project(max_lat, max_lon)
    return min(min_x, max_x), min(min_y, max_y), max(min_x, max_x), max(min_y, max_y)


def _point_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _polyline_length(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(_point_distance(a, b) for a, b in zip(points[:-1], points[1:]))


def _resample_polyline(points: list[tuple[float, float]], sample_count: int) -> list[tuple[float, float]]:
    if len(points) < 2:
        return []
    if sample_count <= 2:
        return [points[0], points[-1]]

    cumulative = [0.0]
    for a, b in zip(points[:-1], points[1:]):
        cumulative.append(cumulative[-1] + _point_distance(a, b))
    total_length = cumulative[-1]
    if total_length <= 0:
        return []

    step = total_length / (sample_count - 1)
    samples = [points[0]]
    target = step
    seg_index = 0
    while len(samples) < sample_count - 1:
        while seg_index < len(cumulative) - 1 and cumulative[seg_index + 1] < target:
            seg_index += 1
        if seg_index >= len(points) - 1:
            break
        p0 = points[seg_index]
        p1 = points[seg_index + 1]
        seg_start = cumulative[seg_index]
        seg_len = cumulative[seg_index + 1] - seg_start
        if seg_len <= 0:
            samples.append(p1)
            target += step
            continue
        t = (target - seg_start) / seg_len
        samples.append((p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t))
        target += step
    samples.append(points[-1])
    return samples
