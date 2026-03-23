from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

from osm_raster_topology.config import RunConfig
from osm_raster_topology.ingest import ingest_osm
from osm_raster_topology.layers import default_layers
from osm_raster_topology.rasterize import (
    ACCESS_CODES,
    BUILDING_CLASS_CODES,
    HIGHWAY_CLASS_CODES,
    PERMISSION_CODES,
    SPORT_CLASS_CODES,
    SURFACE_CODES,
    rasterize_layers,
)
from osm_raster_topology.report import write_validation_report
from osm_raster_topology.sidecar import build_topology_sidecar
from osm_raster_topology.validate import validate_preservation


REQUIRED_MODULES = ["numpy", "PIL", "networkx"]
OPTIONAL_GIS_MODULES = ["pyosmium", "shapely", "rasterio"]


def build_run_config(input_path: str, outdir: str, pixel_size: float, target_crs: str) -> RunConfig:
    return RunConfig(
        input_path=Path(input_path),
        outdir=Path(outdir),
        pixel_size=pixel_size,
        target_crs=target_crs,
        layer_specs=default_layers(),
    )


def check_runtime_dependencies() -> dict[str, dict[str, bool]]:
    return {
        "required": {name: importlib.util.find_spec(name) is not None for name in REQUIRED_MODULES},
        "optional_gis": {name: importlib.util.find_spec(name) is not None for name in OPTIONAL_GIS_MODULES},
    }


def ensure_output_dirs(config: RunConfig) -> dict[str, Path]:
    paths = {
        "root": config.outdir,
        "raster": config.outdir / "raster",
        "topology": config.outdir / "topology",
        "validation": config.outdir / "validation",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def write_design_bundle(config: RunConfig) -> dict[str, Path]:
    paths = ensure_output_dirs(config)
    manifest = {
        "input_path": str(config.input_path),
        "target_crs": config.target_crs,
        "pixel_size": config.pixel_size,
        "topology_oversample": config.topology_oversample,
        "object_stack_depth": config.object_stack_depth,
        "tile_size": config.tile_size,
        "emit_object_ids": config.emit_object_ids,
        "emit_topology_sidecar": config.emit_topology_sidecar,
        "preserve_z_order": config.preserve_z_order,
        "layers": [layer.to_dict() for layer in config.layer_specs],
        "semantic_legends": _semantic_legends(),
        "runtime_dependencies": check_runtime_dependencies(),
        "current_runtime": "pure_python_xml_runner",
    }
    check_report = {
        "runtime_dependencies": check_runtime_dependencies(),
        "notes": [
            "Current implementation exports topology-aware PNG, NPZ, and JSON from .osm XML.",
            "Semantic road and building layers are emitted alongside topology layers and sidecars.",
        ],
    }

    _write_json(paths["root"] / "manifest.json", manifest)
    _write_json(paths["root"] / "layers.json", {"layers": manifest["layers"]})
    _write_json(paths["root"] / "topology_policy.json", _topology_policy())
    _write_json(paths["validation"] / "check_report.json", check_report)
    _write_json(paths["topology"] / "topology_sidecar.template.json", {"graph_nodes": [], "graph_edges": [], "polygon_faces": [], "turn_restrictions": []})
    return paths


def run_pipeline(config: RunConfig) -> dict[str, object]:
    paths = ensure_output_dirs(config)
    data = ingest_osm(config)
    raster = rasterize_layers(data, config, paths["raster"])
    sidecar = build_topology_sidecar(data)
    validation = validate_preservation(data, raster)
    bundle = {
        "metadata": {
            "input_path": str(config.input_path),
            "target_crs": config.target_crs,
            "pixel_size": config.pixel_size,
            "topology_oversample": config.topology_oversample,
            "object_stack_depth": config.object_stack_depth,
            "bounds_latlon": {
                "min_lat": data.bounds_latlon[0],
                "min_lon": data.bounds_latlon[1],
                "max_lat": data.bounds_latlon[2],
                "max_lon": data.bounds_latlon[3],
            },
            "bounds_xy": {
                "min_x": data.bounds_xy[0],
                "min_y": data.bounds_xy[1],
                "max_x": data.bounds_xy[2],
                "max_y": data.bounds_xy[3],
            },
            "image_size": {"width": raster.width, "height": raster.height},
            "feature_stats": data.stats,
            "layer_summaries": raster.band_sums,
            "raster_metrics": raster.metrics,
            "runtime_dependencies": check_runtime_dependencies(),
            "notes": data.notes,
        },
        "layers": [layer.to_dict() for layer in config.layer_specs],
        "semantic_legends": _semantic_legends(),
        "topology_policy": _topology_policy(),
        "validation": validation,
        "topology": sidecar,
        "object_stacks": raster.object_stacks,
        "raster_layers": {name: _encode_array_to_json(name, array) for name, array in raster.arrays.items()},
        "artifacts": raster.files,
    }

    bundle_path = paths["root"] / "map_bundle.json"
    _write_json(bundle_path, bundle)
    validation_report_path = paths["root"] / "validation_report.png"
    write_validation_report(bundle, validation_report_path)
    legacy_html = paths["root"] / "validation_report.html"
    if legacy_html.exists():
        legacy_html.unlink()
    bundle["artifacts"]["validation_report_png"] = str(validation_report_path)
    _write_json(bundle_path, bundle)

    return {
        "status": "ok",
        "outdir": str(paths["root"]),
        "bundle": str(bundle_path),
        "validation_report": str(validation_report_path),
        "preview": raster.preview_path,
        "image_size": {"width": raster.width, "height": raster.height},
        "topology_oversample": raster.oversample,
        "feature_stats": data.stats,
        "raster_metrics": raster.metrics,
        "validation": validation["checks"],
    }


def _semantic_legends() -> dict[str, dict[str, int]]:
    return {
        "highway_class": HIGHWAY_CLASS_CODES,
        "building_class": BUILDING_CLASS_CODES,
        "sports_class": SPORT_CLASS_CODES,
        "road_access": ACCESS_CODES,
        "road_foot_or_bicycle": PERMISSION_CODES,
        "road_surface_class": SURFACE_CODES,
    }


def _encode_array_to_json(name: str, array: np.ndarray) -> dict[str, object]:
    return {
        "name": name,
        "encoding": "row_rle",
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "rows": _encode_rows(array),
    }


def _encode_rows(array: np.ndarray) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if array.ndim != 2:
        raise ValueError("Only 2D arrays are supported for JSON export.")
    height, width = array.shape
    for row_index in range(height):
        row = array[row_index]
        runs: list[list[int]] = []
        start = None
        current_value = 0
        for col in range(width):
            value = int(row[col])
            if value == 0:
                if start is not None:
                    runs.append([start, col - 1, current_value])
                    start = None
                continue
            if start is None:
                start = col
                current_value = value
                continue
            if value != current_value:
                runs.append([start, col - 1, current_value])
                start = col
                current_value = value
        if start is not None:
            runs.append([start, width - 1, current_value])
        if runs:
            rows.append({"row": row_index, "runs": runs})
    return rows


def _topology_policy() -> dict[str, object]:
    return {
        "foreground_connectivity": 8,
        "background_connectivity": 4,
        "line_policy": "Road topology is preserved on an oversampled grid; preview layers are visual summaries.",
        "anti_merge_policy": "Nearby non-connected roads are separated by oversampled occupancy and direction bits.",
        "thin_line_policy": "Thin roads are burned with supercover traversal plus explicit node anchors.",
        "junction_policy": "Complex junctions use node anchors, direction-bit rasters, and graph sidecars.",
        "identity_policy": "Pixels with multiple objects are serialized into object stacks and overflow sidecars.",
        "semantic_policy": "Road class, oneway, access, foot, bicycle, lanes, maxspeed, surface, and building semantics are emitted as raster bands.",
        "crossing_policy": "Bridge, tunnel, and layer information is preserved in crossing_structure and z_group sidecars.",
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
