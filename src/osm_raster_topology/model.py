from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


PointXY = tuple[float, float]
LatLonBounds = tuple[float, float, float, float]
XYBounds = tuple[float, float, float, float]


@dataclass(slots=True)
class NodeRecord:
    osm_id: int
    lat: float
    lon: float
    x: float
    y: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LineFeature:
    feature_id: int
    osm_ref: str
    category: str
    tags: dict[str, str]
    node_refs: list[int]
    points: list[PointXY]
    z_group: str


@dataclass(slots=True)
class PolygonFeature:
    feature_id: int
    osm_ref: str
    tags: dict[str, str]
    outer: list[PointXY]
    holes: list[list[PointXY]] = field(default_factory=list)


@dataclass(slots=True)
class GraphNode:
    node_key: str
    osm_node_id: int
    category: str
    z_group: str
    degree: int
    point: PointXY
    role: str
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class TurnRestriction:
    relation_id: int
    restriction: str
    from_way_ref: int | None
    via_node_ref: int | None
    to_way_ref: int | None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class IngestedData:
    source_path: str
    bounds_latlon: LatLonBounds
    bounds_xy: XYBounds
    nodes: dict[int, NodeRecord]
    line_features: list[LineFeature]
    polygon_features: list[PolygonFeature]
    graph_nodes: list[GraphNode]
    turn_restrictions: list[TurnRestriction]
    stats: dict[str, int | float | str]
    ingest_backend: str = "osm_xml"
    lanelet_relations: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RasterResult:
    width: int
    height: int
    pixel_size: float
    bounds_xy: XYBounds
    oversample: int
    arrays: dict[str, np.ndarray]
    object_stacks: dict[str, object]
    files: dict[str, str]
    band_sums: dict[str, int]
    metrics: dict[str, int | float | str]
    preview_path: str
