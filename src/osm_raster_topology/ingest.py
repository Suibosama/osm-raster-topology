from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import Counter

from osm_raster_topology.config import RunConfig
from osm_raster_topology.model import GraphNode, IngestedData, LineFeature, NodeRecord, PolygonFeature, TurnRestriction


EARTH_RADIUS_M = 6378137.0
POINT_NODE_TAGS = {
    ("highway", "crossing"),
    ("highway", "traffic_signals"),
    ("highway", "bus_stop"),
    ("public_transport", "platform"),
}
AREA_TAG_KEYS = {
    "building",
    "building:part",
    "landuse",
    "amenity",
    "leisure",
    "natural",
    "water",
    "aeroway",
    "boundary",
}
LINE_TAG_KEYS = {"highway", "waterway", "railway"}


def ingest_osm(config: RunConfig) -> IngestedData:
    if config.input_path.suffix.lower() != ".osm":
        raise ValueError("Current pure Python runner only supports .osm XML input.")
    if config.target_crs != "EPSG:3857":
        raise ValueError("Current runner only supports EPSG:3857.")

    root = ET.parse(config.input_path).getroot()
    bounds = _parse_bounds(root)
    nodes = _parse_nodes(root)
    ways = _parse_ways(root)
    relations = _parse_relations(root)
    turn_restrictions = _extract_turn_restrictions(relations)

    relation_polygon_way_ids: set[int] = set()
    polygon_features: list[PolygonFeature] = []
    feature_id = 1

    for relation in relations:
        if relation["tags"].get("type") != "multipolygon":
            continue
        polygon = _build_relation_polygon(relation, ways, nodes, feature_id)
        if polygon is None:
            continue
        polygon_features.append(polygon)
        feature_id += 1
        for member in relation["members"]:
            if member["type"] == "way":
                relation_polygon_way_ids.add(member["ref"])

    line_features: list[LineFeature] = []
    for way_id, way in ways.items():
        tags = way["tags"]
        node_refs = way["nodes"]
        if len(node_refs) < 2:
            continue
        if _is_polygon_candidate(tags, node_refs) and way_id not in relation_polygon_way_ids:
            outer = _coords_from_refs(node_refs, nodes)
            if len(outer) >= 4:
                polygon_features.append(
                    PolygonFeature(
                        feature_id=feature_id,
                        osm_ref=f"way/{way_id}",
                        tags=tags,
                        outer=outer,
                        holes=[],
                    )
                )
                feature_id += 1
            continue
        category = _classify_line(tags)
        if category is None:
            continue
        points = _coords_from_refs(node_refs, nodes)
        if len(points) < 2:
            continue
        line_features.append(
            LineFeature(
                feature_id=feature_id,
                osm_ref=f"way/{way_id}",
                category=category,
                tags=tags,
                node_refs=node_refs,
                points=points,
                z_group=_z_group(tags),
            )
        )
        feature_id += 1

    graph_nodes = _build_graph_nodes(line_features, nodes)
    stats = _build_stats(root, ways, relations, line_features, polygon_features, graph_nodes, turn_restrictions)

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
        notes=[
            "Current build supports .osm XML only and does not handle .pbf yet.",
            "Only multipolygon relations participate in raster export; route relations stay out of the main raster.",
            "Turn restrictions are exported in the topology sidecar.",
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


def _build_relation_polygon(
    relation: dict[str, object],
    ways: dict[int, dict[str, object]],
    nodes: dict[int, NodeRecord],
    feature_id: int,
) -> PolygonFeature | None:
    outer_paths: list[list[int]] = []
    inner_paths: list[list[int]] = []
    for member in relation["members"]:
        if member["type"] != "way":
            continue
        way = ways.get(member["ref"])
        if way is None:
            continue
        refs = way["nodes"]
        if len(refs) < 2:
            continue
        role = member["role"] or "outer"
        if role == "inner":
            inner_paths.append(refs)
        else:
            outer_paths.append(refs)

    outer_rings = _assemble_rings(outer_paths)
    if not outer_rings:
        return None
    inner_rings = _assemble_rings(inner_paths)
    outer = _coords_from_refs(outer_rings[0], nodes)
    holes = [_coords_from_refs(ring, nodes) for ring in inner_rings if len(ring) >= 4]
    if len(outer) < 4:
        return None
    return PolygonFeature(
        feature_id=feature_id,
        osm_ref=f"relation/{relation['id']}",
        tags=relation["tags"],
        outer=outer,
        holes=holes,
    )


def _assemble_rings(paths: list[list[int]]) -> list[list[int]]:
    pending = [path[:] for path in paths if len(path) >= 2]
    rings: list[list[int]] = []
    while pending:
        ring = pending.pop(0)
        merged = True
        while merged and ring[0] != ring[-1]:
            merged = False
            for index, candidate in enumerate(pending):
                if ring[-1] == candidate[0]:
                    ring.extend(candidate[1:])
                elif ring[-1] == candidate[-1]:
                    ring.extend(reversed(candidate[:-1]))
                elif ring[0] == candidate[-1]:
                    ring = candidate[:-1] + ring
                elif ring[0] == candidate[0]:
                    ring = list(reversed(candidate[1:])) + ring
                else:
                    continue
                pending.pop(index)
                merged = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


def _coords_from_refs(node_refs: list[int], nodes: dict[int, NodeRecord]) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for node_ref in node_refs:
        node = nodes.get(node_ref)
        if node is None:
            continue
        coords.append((node.x, node.y))
    return coords


def _is_polygon_candidate(tags: dict[str, str], node_refs: list[int]) -> bool:
    if len(node_refs) < 4 or node_refs[0] != node_refs[-1]:
        return False
    if tags.get("area") == "no":
        return False
    if tags.get("area") == "yes":
        return True
    if any(key in tags for key in AREA_TAG_KEYS):
        return True
    if any(key in tags for key in LINE_TAG_KEYS):
        return False
    return False


def _classify_line(tags: dict[str, str]) -> str | None:
    if "highway" in tags:
        return "road"
    if "waterway" in tags:
        return "water"
    return None


def _z_group(tags: dict[str, str]) -> str:
    layer = tags.get("layer", "0")
    if tags.get("bridge") == "yes":
        return f"bridge:{layer}"
    if tags.get("tunnel") == "yes":
        return f"tunnel:{layer}"
    return f"ground:{layer}"


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
        if any((k, v) in POINT_NODE_TAGS for k, v in node.tags.items()) and role == "intermediate":
            role = "tagged_point"
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
    building_polygon_count = sum(1 for feature in polygon_features if _is_building(feature.tags))
    road_line_count = sum(1 for feature in line_features if feature.category == "road")
    return {
        "raw_node_count": len(root.findall("node")),
        "raw_way_count": len(ways),
        "raw_relation_count": len(relations),
        "line_feature_count": len(line_features),
        "road_line_count": road_line_count,
        "polygon_feature_count": len(polygon_features),
        "building_polygon_count": building_polygon_count,
        "graph_node_count": len(graph_nodes),
        "multipolygon_relation_count": relation_types.get("multipolygon", 0),
        "turn_restriction_count": len(turn_restrictions),
    }


def _is_building(tags: dict[str, str]) -> bool:
    return "building" in tags or "building:part" in tags


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
