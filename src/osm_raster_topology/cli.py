from __future__ import annotations

import argparse
import json
from pathlib import Path

from osm_raster_topology.pipeline import build_run_config, check_runtime_dependencies, run_pipeline, write_design_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="osm-topology",
        description="Topology-aware OSM to raster converter for multi-layer map products.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", required=True, help="Input .osm XML file path.")
    common.add_argument("--outdir", required=True, help="Output directory.")
    common.add_argument("--pixel-size", type=float, default=1.0, help="Pixel size in projected meters.")
    common.add_argument("--target-crs", default="EPSG:3857", help="Target CRS. The current runner supports EPSG:3857 only.")

    design = subparsers.add_parser("design", parents=[common], help="Emit the design bundle and output contract.")
    design.set_defaults(handler=handle_design)

    check = subparsers.add_parser("check", parents=[common], help="Run input and dependency preflight checks.")
    check.set_defaults(handler=handle_check)

    run = subparsers.add_parser("run", parents=[common], help="Run the pure Python .osm to raster pipeline.")
    run.set_defaults(handler=handle_run)
    return parser


def handle_design(args: argparse.Namespace) -> int:
    config = build_run_config(
        input_path=args.input,
        outdir=args.outdir,
        pixel_size=args.pixel_size,
        target_crs=args.target_crs,
    )
    paths = write_design_bundle(config)
    print(json.dumps({"status": "ok", "outdir": str(paths["root"])}, indent=2, ensure_ascii=False))
    return 0


def handle_check(args: argparse.Namespace) -> int:
    config = build_run_config(
        input_path=args.input,
        outdir=args.outdir,
        pixel_size=args.pixel_size,
        target_crs=args.target_crs,
    )
    result = {
        "input_exists": Path(config.input_path).exists(),
        "input_suffix": config.input_path.suffix.lower(),
        "supports_direct_run": config.input_path.suffix.lower() == ".osm",
        "dependency_status": check_runtime_dependencies(),
        "layer_count": len(config.layer_specs),
        "target_crs": config.target_crs,
        "pixel_size": config.pixel_size,
        "topology_oversample": config.topology_oversample,
        "object_stack_depth": config.object_stack_depth,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["input_exists"] else 2


def handle_run(args: argparse.Namespace) -> int:
    config = build_run_config(
        input_path=args.input,
        outdir=args.outdir,
        pixel_size=args.pixel_size,
        target_crs=args.target_crs,
    )
    result = run_pipeline(config)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.handler(args)
