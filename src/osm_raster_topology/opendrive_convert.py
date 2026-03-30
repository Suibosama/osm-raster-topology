from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from osm_raster_topology.config import RunConfig


EARTH_RADIUS_M = 6378137.0
EPSG_4326 = "EPSG:4326"
EPSG_3857 = "EPSG:3857"


@dataclass(slots=True)
class ConversionResult:
    osm_path: Path
    report_path: Path
    warnings: list[str]
    stats: dict[str, int | float | str]


def convert_xodr_to_osm(config: RunConfig) -> ConversionResult:
    input_path = config.input_path
    if input_path.suffix.lower() != ".xodr":
        raise ValueError("OpenDRIVE converter expects .xodr input.")

    root = ET.parse(input_path).getroot()
    header = root.find("header")
    geo_reference = _read_geo_reference(header)
    offset = _read_offset(header)

    ds = _clamp(0.5 * config.pixel_size, 0.1, 1.0)
    roads = root.findall("road")
    road_records: list[dict[str, object]] = []
    warnings: list[str] = []

    for road in roads:
        road_record = _parse_road(road)
        if road_record is None:
            warnings.append(f"road {road.attrib.get('id', 'unknown')}: missing planView geometry")
            continue
        road_records.append(road_record)

    if not road_records:
        raise ValueError("No valid road records found in OpenDRIVE input.")

    lat0_lon0 = _parse_latlon0(geo_reference) if geo_reference else None
    if geo_reference:
        transformer = _build_transformer(geo_reference)
        if transformer is None:
            if lat0_lon0:
                warnings.append("geoReference incomplete: using lat_0/lon_0 local ENU fallback.")
            else:
                warnings.append("geoReference invalid or pyproj missing: interpreting x/y as EPSG:3857 meters.")
    else:
        transformer = None
        warnings.append("geoReference missing: interpreting x/y as EPSG:3857 meters.")

    converted_dir = config.outdir / "converted"
    converted_dir.mkdir(parents=True, exist_ok=True)
    osm_path = converted_dir / f"{input_path.stem}.osm"
    report_path = converted_dir / "conversion_report.json"

    node_id = -1
    way_id = -1
    nodes: list[dict[str, object]] = []
    ways: list[dict[str, object]] = []
    bounds = _Bounds()
    lane_marking_way_count = 0
    lane_area_way_count = 0

    for record in road_records:
        road_id = record["road_id"]
        road_length = record["length"]
        samples = _sample_reference_line(record["geometries"], road_length, ds)
        if not samples:
            warnings.append(f"road {road_id}: reference line sampling yielded no points")
            continue
        lane_sections = record["lane_sections"]
        road_types = record["road_types"]
        lane_sections_sorted = sorted(lane_sections, key=lambda section: section["s"])
        lane_offsets = record["lane_offsets"]
        for index, section in enumerate(lane_sections_sorted):
            s0 = section["s"]
            s1 = road_length
            if index + 1 < len(lane_sections_sorted):
                s1 = lane_sections_sorted[index + 1]["s"]
            if s1 <= s0:
                continue
            section_points = _extract_section_points(samples, s0, s1)
            if len(section_points) < 2:
                warnings.append(f"road {road_id} section {s0:.2f}: not enough points")
                continue
            latlon_nodes: list[int] = []
            for s_abs, x, y, _hdg in section_points:
                xw, yw = _apply_offset(x, y, offset)
                lon, lat = _to_wgs84(xw, yw, transformer, lat0_lon0)
                bounds.update(lat, lon)
                nodes.append({"id": node_id, "lat": lat, "lon": lon})
                latlon_nodes.append(node_id)
                node_id -= 1

            tags = _build_way_tags(
                section,
                road_types,
                s0,
                road_id=road_id,
                road_name=record.get("road_name", ""),
            )
            ways.append({"id": way_id, "nodes": latlon_nodes, "tags": tags})
            way_id -= 1

            lane_outputs = _build_lane_outputs(section, section_points, lane_offsets)
            for lane_mark in lane_outputs["markings"]:
                mark_node_refs: list[int] = []
                for x, y in lane_mark["points"]:
                    xw, yw = _apply_offset(x, y, offset)
                    lon, lat = _to_wgs84(xw, yw, transformer, lat0_lon0)
                    bounds.update(lat, lon)
                    nodes.append({"id": node_id, "lat": lat, "lon": lon})
                    mark_node_refs.append(node_id)
                    node_id -= 1
                ways.append({"id": way_id, "nodes": mark_node_refs, "tags": lane_mark["tags"]})
                way_id -= 1
                lane_marking_way_count += 1

            for lane_area in lane_outputs["areas"]:
                area_node_refs: list[int] = []
                for x, y in lane_area["points"]:
                    xw, yw = _apply_offset(x, y, offset)
                    lon, lat = _to_wgs84(xw, yw, transformer, lat0_lon0)
                    bounds.update(lat, lon)
                    nodes.append({"id": node_id, "lat": lat, "lon": lon})
                    area_node_refs.append(node_id)
                    node_id -= 1
                if area_node_refs and area_node_refs[0] != area_node_refs[-1]:
                    area_node_refs.append(area_node_refs[0])
                ways.append({"id": way_id, "nodes": area_node_refs, "tags": lane_area["tags"]})
                way_id -= 1
                lane_area_way_count += 1

    _write_osm(osm_path, nodes, ways, bounds)
    stats = {
        "road_count": len(road_records),
        "way_count": len(ways),
        "node_count": len(nodes),
        "lane_section_count": sum(len(record["lane_sections"]) for record in road_records),
        "lane_marking_way_count": lane_marking_way_count,
        "lane_area_way_count": lane_area_way_count,
        "geo_reference_used": bool(geo_reference),
        "geo_reference_fallback": "enu" if (geo_reference and transformer is None and lat0_lon0) else "webmercator",
        "sampling_step": ds,
    }
    report = {
        "input_path": str(input_path),
        "output_osm": str(osm_path),
        "stats": stats,
        "warnings": warnings,
        "notes": [
            "Road centerlines, lane markings, and lane areas are exported.",
            "laneSection splits are emitted as separate ways to preserve lane/speed changes.",
        ],
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return ConversionResult(
        osm_path=osm_path,
        report_path=report_path,
        warnings=warnings,
        stats=stats,
    )


def _parse_road(road: ET.Element) -> dict[str, object] | None:
    plan_view = road.find("planView")
    if plan_view is None:
        return None
    geometries: list[dict[str, object]] = []
    for geometry in plan_view.findall("geometry"):
        geom = _parse_geometry(geometry)
        if geom is not None:
            geometries.append(geom)
    if not geometries:
        return None
    lanes_node = road.find("lanes")
    lane_sections = _parse_lane_sections(lanes_node)
    lane_offsets = _parse_lane_offsets(lanes_node)
    road_types = _parse_road_types(road.findall("type"))
    length = float(road.attrib.get("length", geometries[-1]["s"] + geometries[-1]["length"]))
    return {
        "road_id": road.attrib.get("id", ""),
        "road_name": road.attrib.get("name", ""),
        "length": length,
        "geometries": geometries,
        "lane_sections": lane_sections,
        "lane_offsets": lane_offsets,
        "road_types": road_types,
    }


def _parse_geometry(geometry: ET.Element) -> dict[str, object] | None:
    s = float(geometry.attrib.get("s", "0"))
    x = float(geometry.attrib.get("x", "0"))
    y = float(geometry.attrib.get("y", "0"))
    hdg = float(geometry.attrib.get("hdg", "0"))
    length = float(geometry.attrib.get("length", "0"))
    if length <= 0:
        return None
    if geometry.find("line") is not None:
        return {"s": s, "x": x, "y": y, "hdg": hdg, "length": length, "kind": "line", "params": {}}
    arc = geometry.find("arc")
    if arc is not None:
        curvature = float(arc.attrib.get("curvature", "0"))
        return {
            "s": s,
            "x": x,
            "y": y,
            "hdg": hdg,
            "length": length,
            "kind": "arc",
            "params": {"curvature": curvature},
        }
    spiral = geometry.find("spiral")
    if spiral is not None:
        curv_start = float(spiral.attrib.get("curvStart", "0"))
        curv_end = float(spiral.attrib.get("curvEnd", "0"))
        return {
            "s": s,
            "x": x,
            "y": y,
            "hdg": hdg,
            "length": length,
            "kind": "spiral",
            "params": {"curvStart": curv_start, "curvEnd": curv_end},
        }
    param = geometry.find("paramPoly3")
    if param is not None:
        params = {k: float(param.attrib.get(k, "0")) for k in ("aU", "bU", "cU", "dU", "aV", "bV", "cV", "dV")}
        params["pRange"] = param.attrib.get("pRange", "arcLength")
        return {
            "s": s,
            "x": x,
            "y": y,
            "hdg": hdg,
            "length": length,
            "kind": "paramPoly3",
            "params": params,
        }
    return None


def _parse_lane_sections(lanes: ET.Element | None) -> list[dict[str, object]]:
    if lanes is None:
        return [{"s": 0.0, "left": [], "right": []}]
    sections: list[dict[str, object]] = []
    for section in lanes.findall("laneSection"):
        s = float(section.attrib.get("s", "0"))
        lanes_left = _collect_lanes(section.find("left"))
        lanes_right = _collect_lanes(section.find("right"))
        sections.append(
            {
                "s": s,
                "left": lanes_left,
                "right": lanes_right,
            }
        )
    return sections or [{"s": 0.0, "left": [], "right": []}]


def _parse_lane_offsets(lanes: ET.Element | None) -> list[dict[str, float]]:
    if lanes is None:
        return [{"s": 0.0, "a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}]
    offsets: list[dict[str, float]] = []
    for node in lanes.findall("laneOffset"):
        offsets.append(
            {
                "s": float(node.attrib.get("s", "0")),
                "a": float(node.attrib.get("a", "0")),
                "b": float(node.attrib.get("b", "0")),
                "c": float(node.attrib.get("c", "0")),
                "d": float(node.attrib.get("d", "0")),
            }
        )
    return sorted(offsets, key=lambda item: item["s"]) or [{"s": 0.0, "a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}]


def _collect_lanes(container: ET.Element | None) -> list[dict[str, object]]:
    if container is None:
        return []
    lanes: list[dict[str, object]] = []
    for lane in container.findall("lane"):
        lane_id = int(lane.attrib.get("id", "0"))
        lane_type = lane.attrib.get("type", "")
        speed_kph = _read_lane_speed(lane)
        widths = _read_lane_widths(lane)
        lanes.append({"id": lane_id, "type": lane_type, "speed_kph": speed_kph, "widths": widths})
    return lanes


def _read_lane_widths(lane: ET.Element) -> list[dict[str, float]]:
    widths: list[dict[str, float]] = []
    for width in lane.findall("width"):
        widths.append(
            {
                "sOffset": float(width.attrib.get("sOffset", "0")),
                "a": float(width.attrib.get("a", "0")),
                "b": float(width.attrib.get("b", "0")),
                "c": float(width.attrib.get("c", "0")),
                "d": float(width.attrib.get("d", "0")),
            }
        )
    return sorted(widths, key=lambda item: item["sOffset"])


def _read_lane_speed(lane: ET.Element) -> float | None:
    speed = lane.find("speed")
    if speed is None:
        return None
    max_value = speed.attrib.get("max")
    if not max_value:
        return None
    try:
        value = float(max_value)
    except ValueError:
        return None
    unit = speed.attrib.get("unit", "km/h")
    if unit in {"m/s", "ms"}:
        return value * 3.6
    return value


def _parse_road_types(types: list[ET.Element]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for node in types:
        s = float(node.attrib.get("s", "0"))
        road_type = node.attrib.get("type", "")
        speed_node = node.find("speed")
        speed_kph = None
        if speed_node is not None:
            max_value = speed_node.attrib.get("max")
            unit = speed_node.attrib.get("unit", "km/h")
            if max_value:
                try:
                    value = float(max_value)
                except ValueError:
                    value = None
                else:
                    speed_kph = value * 3.6 if unit in {"m/s", "ms"} else value
        records.append({"s": s, "road_type": road_type, "speed_kph": speed_kph})
    return sorted(records, key=lambda item: item["s"])


def _sample_reference_line(
    geometries: list[dict[str, object]],
    length: float,
    ds: float,
) -> list[tuple[float, float, float, float]]:
    points: list[tuple[float, float, float, float]] = []
    for geom in geometries:
        segment_points = _sample_geometry(geom, ds)
        if not segment_points:
            continue
        if points and math.isclose(points[-1][0], segment_points[0][0]):
            segment_points = segment_points[1:]
        points.extend(segment_points)
    if points and points[-1][0] < length:
        last_s, last_x, last_y, last_hdg = points[-1]
        points.append((length, last_x, last_y, last_hdg))
    return points


def _sample_geometry(geom: dict[str, object], ds: float) -> list[tuple[float, float, float, float]]:
    s0 = float(geom["s"])
    x0 = float(geom["x"])
    y0 = float(geom["y"])
    hdg0 = float(geom["hdg"])
    length = float(geom["length"])
    kind = geom["kind"]
    params = geom["params"]
    samples: list[tuple[float, float, float, float]] = []
    steps = max(1, int(math.ceil(length / ds)))
    step = length / steps

    if kind == "line":
        for i in range(steps + 1):
            s = i * step
            x = x0 + s * math.cos(hdg0)
            y = y0 + s * math.sin(hdg0)
            samples.append((s0 + s, x, y, hdg0))
        return samples

    if kind == "arc":
        curvature = float(params.get("curvature", 0.0))
        if abs(curvature) < 1e-9:
            for i in range(steps + 1):
                s = i * step
                x = x0 + s * math.cos(hdg0)
                y = y0 + s * math.sin(hdg0)
                samples.append((s0 + s, x, y, hdg0))
            return samples
        for i in range(steps + 1):
            s = i * step
            hdg = hdg0 + curvature * s
            x = x0 + (math.sin(hdg) - math.sin(hdg0)) / curvature
            y = y0 - (math.cos(hdg) - math.cos(hdg0)) / curvature
            samples.append((s0 + s, x, y, hdg))
        return samples

    if kind == "spiral":
        curv_start = float(params.get("curvStart", 0.0))
        curv_end = float(params.get("curvEnd", 0.0))
        dk = (curv_end - curv_start) / length if length > 0 else 0.0
        x, y = x0, y0
        heading = hdg0
        samples.append((s0, x, y, heading))
        s_acc = 0.0
        for _ in range(steps):
            curv_mid = curv_start + dk * (s_acc + step * 0.5)
            theta_mid = heading + curv_mid * step * 0.5
            x += step * math.cos(theta_mid)
            y += step * math.sin(theta_mid)
            heading += curv_mid * step
            s_acc += step
            samples.append((s0 + s_acc, x, y, heading))
        return samples

    if kind == "paramPoly3":
        a_u = float(params.get("aU", 0.0))
        b_u = float(params.get("bU", 0.0))
        c_u = float(params.get("cU", 0.0))
        d_u = float(params.get("dU", 0.0))
        a_v = float(params.get("aV", 0.0))
        b_v = float(params.get("bV", 0.0))
        c_v = float(params.get("cV", 0.0))
        d_v = float(params.get("dV", 0.0))
        p_range = params.get("pRange", "arcLength")
        for i in range(steps + 1):
            s = i * step
            p = s / length if p_range == "normalized" else s
            u = a_u + b_u * p + c_u * p * p + d_u * p * p * p
            v = a_v + b_v * p + c_v * p * p + d_v * p * p * p
            x = x0 + u * math.cos(hdg0) - v * math.sin(hdg0)
            y = y0 + u * math.sin(hdg0) + v * math.cos(hdg0)
            samples.append((s0 + s, x, y, hdg0))
        return samples

    return []


def _extract_section_points(
    samples: list[tuple[float, float, float, float]],
    s0: float,
    s1: float,
) -> list[tuple[float, float, float, float]]:
    if s1 <= s0:
        return []
    points = [p for p in samples if s0 <= p[0] <= s1]
    if not points:
        p0 = _interpolate_sample(samples, s0)
        p1 = _interpolate_sample(samples, s1)
        if p0 and p1:
            return [p0, p1]
        return []
    if points[0][0] > s0:
        p0 = _interpolate_sample(samples, s0)
        if p0:
            points.insert(0, p0)
    if points[-1][0] < s1:
        p1 = _interpolate_sample(samples, s1)
        if p1:
            points.append(p1)
    return points


def _interpolate_sample(
    samples: list[tuple[float, float, float, float]],
    target_s: float,
) -> tuple[float, float, float, float] | None:
    for (s0, x0, y0, h0), (s1, x1, y1, h1) in zip(samples[:-1], samples[1:]):
        if s0 <= target_s <= s1 and s1 > s0:
            t = (target_s - s0) / (s1 - s0)
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t
            hdg = h0 + (h1 - h0) * t
            return target_s, x, y, hdg
    return None


def _build_way_tags(
    section: dict[str, object],
    road_types: list[dict[str, object]],
    s0: float,
    road_id: str,
    road_name: str = "",
) -> dict[str, str]:
    driving_left = [lane for lane in section["left"] if lane["type"] == "driving"]
    driving_right = [lane for lane in section["right"] if lane["type"] == "driving"]
    lane_count = len(driving_left) + len(driving_right)
    speed_values = [lane["speed_kph"] for lane in driving_left + driving_right if lane["speed_kph"]]
    lane_speed = min(speed_values) if speed_values else None
    road_type = _road_type_at_s(road_types, s0)

    road_type = _road_type_at_s(road_types, s0)
    tags = {
        "source": "opendrive",
        "xodr:road_id": str(road_id),
        "xodr:lane_section_s": f"{s0:.3f}",
        "xodr:road_type": road_type or "unknown",
        "highway": _map_highway(road_type),
    }
    if road_name:
        tags["name"] = road_name
    if lane_count > 0:
        tags["lanes"] = str(lane_count)
        tags["xodr:lanes_left"] = str(len(driving_left))
        tags["xodr:lanes_right"] = str(len(driving_right))
    maxspeed = lane_speed
    if maxspeed is None:
        maxspeed = _road_speed_at_s(road_types, s0)
    if maxspeed is not None:
        tags["maxspeed"] = str(int(round(maxspeed)))
    if driving_left and not driving_right:
        tags["oneway"] = "yes"
        tags["xodr:traffic_direction"] = "same_as_reference"
    elif driving_right and not driving_left:
        tags["oneway"] = "yes"
        tags["xodr:traffic_direction"] = "opposite_reference"
    return tags


def _road_type_at_s(road_types: list[dict[str, object]], s0: float) -> str:
    if not road_types:
        return ""
    current = road_types[0]["road_type"]
    for record in road_types:
        if record["s"] <= s0:
            current = record["road_type"]
        else:
            break
    return current or ""


def _road_speed_at_s(road_types: list[dict[str, object]], s0: float) -> float | None:
    if not road_types:
        return None
    speed = road_types[0]["speed_kph"]
    for record in road_types:
        if record["s"] <= s0:
            speed = record["speed_kph"]
        else:
            break
    return speed


def _map_highway(road_type: str) -> str:
    mapping = {
        "motorway": "motorway",
        "rural": "trunk",
        "town": "primary",
        "urban": "primary",
        "pedestrian": "pedestrian",
        "bicycle": "cycleway",
        "lowSpeed": "service",
        "service": "service",
    }
    return mapping.get(road_type, "service")


def _build_lane_outputs(
    section: dict[str, object],
    section_points: list[tuple[float, float, float, float]],
    lane_offsets: list[dict[str, float]],
) -> dict[str, list[dict[str, object]]]:
    left_lanes = sorted(section["left"], key=lambda lane: lane["id"])
    right_lanes = sorted(section["right"], key=lambda lane: lane["id"], reverse=True)

    left_defs = _prepare_lane_defs(left_lanes, section["s"])
    right_defs = _prepare_lane_defs(right_lanes, section["s"])

    markings: list[dict[str, object]] = []
    areas: list[dict[str, object]] = []

    if not left_defs and not right_defs:
        return {"markings": markings, "areas": areas}

    left_boundaries = _sample_boundaries(section_points, lane_offsets, left_defs, side="left")
    right_boundaries = _sample_boundaries(section_points, lane_offsets, right_defs, side="right")

    markings.extend(_emit_boundary_markings(left_boundaries, side="left"))
    markings.extend(_emit_boundary_markings(right_boundaries, side="right"))
    areas.extend(_emit_lane_areas(left_boundaries, left_defs, side="left"))
    areas.extend(_emit_lane_areas(right_boundaries, right_defs, side="right"))
    return {"markings": markings, "areas": areas}


def _prepare_lane_defs(lanes: list[dict[str, object]], section_s: float) -> list[dict[str, object]]:
    lane_defs: list[dict[str, object]] = []
    for lane in lanes:
        if lane["id"] == 0:
            continue
        widths = lane.get("widths", [])
        if not widths:
            continue
        lane_defs.append(
            {
                "id": lane["id"],
                "type": lane.get("type", ""),
                "widths": widths,
                "section_s": section_s,
            }
        )
    return lane_defs


def _sample_boundaries(
    section_points: list[tuple[float, float, float, float]],
    lane_offsets: list[dict[str, float]],
    lane_defs: list[dict[str, object]],
    side: str,
) -> list[list[tuple[float, float]]]:
    if not lane_defs:
        return []
    boundaries: list[list[tuple[float, float]]] = [[] for _ in range(len(lane_defs) + 1)]
    sign = 1.0 if side == "left" else -1.0
    for s_abs, x, y, hdg in section_points:
        offset_base = _lane_offset_at(lane_offsets, s_abs)
        normal_x = -math.sin(hdg)
        normal_y = math.cos(hdg)
        cumulative = 0.0
        boundaries[0].append((x + normal_x * offset_base, y + normal_y * offset_base))
        for idx, lane in enumerate(lane_defs, start=1):
            width = _lane_width_at(lane, s_abs)
            if width is None:
                break
            cumulative += width
            t = offset_base + sign * cumulative
            boundaries[idx].append((x + normal_x * t, y + normal_y * t))
    return boundaries


def _emit_boundary_markings(boundaries: list[list[tuple[float, float]]], side: str) -> list[dict[str, object]]:
    markings: list[dict[str, object]] = []
    for index, points in enumerate(boundaries):
        if index == 0 or len(points) < 2:
            continue
        tags = {
            "xodr:feature": "lane_marking",
            "xodr:side": side,
            "xodr:boundary_index": str(index),
        }
        if index == len(boundaries) - 1:
            tags["xodr:boundary"] = "outer"
        else:
            tags["xodr:boundary"] = "inner"
        markings.append({"points": points, "tags": tags})
    return markings


def _emit_lane_areas(
    boundaries: list[list[tuple[float, float]]],
    lane_defs: list[dict[str, object]],
    side: str,
) -> list[dict[str, object]]:
    areas: list[dict[str, object]] = []
    if not boundaries:
        return areas
    for idx, lane in enumerate(lane_defs, start=1):
        if idx >= len(boundaries):
            break
        inner = boundaries[idx - 1]
        outer = boundaries[idx]
        if len(inner) < 2 or len(outer) < 2:
            continue
        polygon = inner + list(reversed(outer))
        tags = {
            "xodr:feature": "lane_area",
            "xodr:lane_id": str(lane["id"]),
            "xodr:lane_type": lane.get("type", ""),
            "xodr:side": side,
            "area": "yes",
        }
        areas.append({"points": polygon, "tags": tags})
    return areas


def _lane_offset_at(offsets: list[dict[str, float]], s_abs: float) -> float:
    current = offsets[0] if offsets else {"s": 0.0, "a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}
    for offset in offsets:
        if offset["s"] <= s_abs:
            current = offset
        else:
            break
    ds = s_abs - current["s"]
    return current["a"] + current["b"] * ds + current["c"] * ds * ds + current["d"] * ds * ds * ds


def _lane_width_at(lane: dict[str, object], s_abs: float) -> float | None:
    widths = lane.get("widths", [])
    if not widths:
        return None
    section_s = lane.get("section_s", 0.0)
    ds_section = s_abs - float(section_s)
    current = widths[0]
    for record in widths:
        if record["sOffset"] <= ds_section:
            current = record
        else:
            break
    ds = ds_section - current["sOffset"]
    return (
        current["a"]
        + current["b"] * ds
        + current["c"] * ds * ds
        + current["d"] * ds * ds * ds
    )


def _read_geo_reference(header: ET.Element | None) -> str | None:
    if header is None:
        return None
    geo_ref = header.find("geoReference")
    if geo_ref is None or geo_ref.text is None:
        return None
    return geo_ref.text.strip()


def _parse_latlon0(geo_reference: str | None) -> tuple[float, float] | None:
    if not geo_reference:
        return None
    lat0 = _parse_proj_param(geo_reference, "lat_0")
    lon0 = _parse_proj_param(geo_reference, "lon_0")
    if lat0 is None or lon0 is None:
        return None
    return lat0, lon0


def _parse_proj_param(text: str, key: str) -> float | None:
    tokens = text.replace("\n", " ").split()
    for token in tokens:
        if not token.startswith(f"+{key}="):
            continue
        value = token.split("=", 1)[1]
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _read_offset(header: ET.Element | None) -> dict[str, float] | None:
    if header is None:
        return None
    offset = header.find("offset")
    if offset is None:
        return None
    return {
        "x": float(offset.attrib.get("x", "0")),
        "y": float(offset.attrib.get("y", "0")),
        "z": float(offset.attrib.get("z", "0")),
        "hdg": float(offset.attrib.get("hdg", "0")),
    }


def _apply_offset(x: float, y: float, offset: dict[str, float] | None) -> tuple[float, float]:
    if offset is None:
        return x, y
    hdg = offset["hdg"]
    cos_h = math.cos(hdg)
    sin_h = math.sin(hdg)
    xw = x * cos_h - y * sin_h + offset["x"]
    yw = x * sin_h + y * cos_h + offset["y"]
    return xw, yw


def _build_transformer(geo_reference: str):
    try:
        from pyproj import CRS, Transformer
    except Exception:
        return None
    try:
        crs_src = CRS.from_user_input(geo_reference)
        crs_dst = CRS.from_user_input(EPSG_4326)
    except Exception:
        return None
    return Transformer.from_crs(crs_src, crs_dst, always_xy=True)


def _to_wgs84(
    x: float,
    y: float,
    transformer,
    lat0_lon0: tuple[float, float] | None,
) -> tuple[float, float]:
    if transformer is not None:
        lon, lat = transformer.transform(x, y)
        return lon, lat
    if lat0_lon0 is not None:
        return _local_enu_to_wgs84(x, y, lat0_lon0[0], lat0_lon0[1])
    return _webmercator_to_wgs84(x, y)


def _local_enu_to_wgs84(x: float, y: float, lat0: float, lon0: float) -> tuple[float, float]:
    lat0_rad = math.radians(lat0)
    dlat = (y / EARTH_RADIUS_M) * 180.0 / math.pi
    dlon = (x / (EARTH_RADIUS_M * math.cos(lat0_rad))) * 180.0 / math.pi
    return lon0 + dlon, lat0 + dlat


def _webmercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    lon = (x / EARTH_RADIUS_M) * 180.0 / math.pi
    lat = (2.0 * math.atan(math.exp(y / EARTH_RADIUS_M)) - math.pi / 2.0) * 180.0 / math.pi
    return lon, lat


def _write_osm(path: Path, nodes: list[dict[str, object]], ways: list[dict[str, object]], bounds: "_Bounds") -> None:
    osm = ET.Element("osm", attrib={"version": "0.6", "generator": "osm_raster_topology"})
    if bounds.valid:
        ET.SubElement(
            osm,
            "bounds",
            attrib={
                "minlat": f"{bounds.min_lat:.7f}",
                "minlon": f"{bounds.min_lon:.7f}",
                "maxlat": f"{bounds.max_lat:.7f}",
                "maxlon": f"{bounds.max_lon:.7f}",
            },
        )
    for node in nodes:
        ET.SubElement(
            osm,
            "node",
            attrib={
                "id": str(node["id"]),
                "lat": f"{node['lat']:.7f}",
                "lon": f"{node['lon']:.7f}",
                "version": "1",
            },
        )
    for way in ways:
        way_elem = ET.SubElement(
            osm,
            "way",
            attrib={
                "id": str(way["id"]),
                "version": "1",
            },
        )
        for node_ref in way["nodes"]:
            ET.SubElement(way_elem, "nd", attrib={"ref": str(node_ref)})
        for key, value in way["tags"].items():
            ET.SubElement(way_elem, "tag", attrib={"k": key, "v": str(value)})

    tree = ET.ElementTree(osm)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class _Bounds:
    def __init__(self) -> None:
        self.min_lat = float("inf")
        self.min_lon = float("inf")
        self.max_lat = float("-inf")
        self.max_lon = float("-inf")
        self.valid = False

    def update(self, lat: float, lon: float) -> None:
        self.min_lat = min(self.min_lat, lat)
        self.min_lon = min(self.min_lon, lon)
        self.max_lat = max(self.max_lat, lat)
        self.max_lon = max(self.max_lon, lon)
        self.valid = True
