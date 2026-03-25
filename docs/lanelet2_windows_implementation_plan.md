# Lanelet2 on Windows: Implementation Plan

## Conclusion

On Windows, the feasible path is **not** installing full ROS Lanelet2 and calling `lanelet2_python` directly. The practical approach for this repository is:

1. Parse Lanelet2-style OSM XML with pure Python (`xml.etree.ElementTree`).
2. Convert lanelet relations into existing internal primitives (`LineFeature`, `PolygonFeature`, `GraphNode`).
3. Reuse current raster pipeline unchanged as much as possible.

This is aligned with current project architecture (`ingest -> rasterize -> sidecar -> report`) and avoids WSL.

## Why this path

- Current codebase already has a pure-Python OSM XML parser in `src/osm_raster_topology/ingest.py`.
- Runtime in this repo is Python `3.14.2`; ROS/Lanelet2 bindings for Windows are not a reliable dependency target.
- Existing rasterization stack already supports both line and polygon channels in `src/osm_raster_topology/rasterize.py`.

## Data Contract to Preserve

Minimum must-preserve fields when ingesting Lanelet2:

- geometry:
  - `lanelet_id`
  - left/right bound node sequences
  - synthesized or explicit centerline
- semantics:
  - `subtype`, `location`, `one_way` (or mapped equivalent)
  - participant access (`vehicle`, `bicycle`, `pedestrian`)
- topology:
  - predecessor/successor adjacency
  - left/right neighbors (if present)
- projection metadata:
  - keep original lat/lon in nodes and continue using existing EPSG:3857 projection pipeline

## Proposed Backend Design

Add a backend switch in ingest stage:

- `osm_xml` (existing behavior)
- `lanelet2_xml` (new)
- `auto` (detect by relation tags)

Detection heuristic for `lanelet2_xml`:

- relation with `type=lanelet` exists, or
- relation with `type=regulatory_element` exists.

### New module

Create `src/osm_raster_topology/ingest_lanelet2.py`:

- parse `node`, `way`, `relation`
- collect lanelet relations
- read `left/right/centerline` members
- if centerline missing: synthesize from left/right bounds by resampling and midpoint pairing
- output:
  - road centerlines -> `LineFeature(category="road")`
  - drivable lanelet polygons -> `PolygonFeature(tags include area=yes, drivable=yes, source=lanelet2)`
  - graph nodes from centerline refs (reuse existing graph builder logic)
  - lanelet stats (`lanelet_relation_count`, `centerline_synthesized_count`)

### Keep rasterizer changes minimal

Current rasterizer already consumes:

- lines for road topology and semantics
- polygons for area fill

So first iteration only requires tag mapping in ingest:

- set centerline tags to include `highway=service` fallback
- map lanelet access tags to existing semantic slots:
  - `road_access`
  - `road_foot`
  - `road_bicycle`

No immediate change to raster logic is required for MVP.

## Implementation Milestones

### M1: MVP (recommended first PR)

- Add ingest backend selection in `src/osm_raster_topology/pipeline.py`.
- Implement `ingest_lanelet2.py` with centerline extraction/synthesis.
- Add CLI flag in `src/osm_raster_topology/cli.py`:
  - `--ingest-backend auto|osm_xml|lanelet2_xml`.
- Emit lanelet notes/stats in `map_bundle.json` metadata.

Acceptance:

- `python -m osm_raster_topology run --input <lanelet2.osm> ...` completes.
- Preview PNG has connected road skeleton.
- `map_bundle.json` shows lanelet counters.

### M2: Drivable-area enhancement

- Burn lanelet polygon into `area_fill` path via existing polygon channel.
- Add a dedicated drivable mask band (optional new layer spec).

Acceptance:

- Drivable surface appears as contiguous region.
- Junction geometry is less fragmented than centerline-only output.

### M3: Topology/Rules enrichment

- Parse lanelet adjacency (predecessor/successor/left/right) into sidecar.
- Parse minimal regulatory elements and attach references in sidecar.

Acceptance:

- Sidecar graph contains lanelet-level connectivity.
- Turn and rule checks can reference lanelet ids.

## Windows Environment Recommendation

Use pure Python only for Lanelet2 ingestion:

- Python: prefer 3.11 or 3.12 for widest wheel compatibility.
- Required packages remain current project deps (`numpy`, `Pillow`, `networkx`).
- Optional geometry quality check: add `shapely>=2.0` for polygon validity diagnostics.

Avoid depending on:

- ROS distro installation on Windows
- `lanelet2_python` binary bindings for production path

## Risks and Mitigations

1. Missing or malformed lanelet members
- Mitigation: strict validation and skip-with-warning counters.

2. Left/right boundary direction inconsistency
- Mitigation: normalize orientation before centerline/polygon generation.

3. Centerline synthesis artifacts in wide/curved lanes
- Mitigation: arc-length resampling with configurable step (e.g., 1.0m).

4. Semantic mismatch from lanelet tags to OSM highway tags
- Mitigation: keep original lanelet tags in `tags` payload and add fallback `highway` only for raster compatibility.

## Concrete File Touch Plan

- `src/osm_raster_topology/cli.py`
  - add `--ingest-backend` argument
- `src/osm_raster_topology/config.py`
  - add `ingest_backend` in `RunConfig`
- `src/osm_raster_topology/pipeline.py`
  - dispatch ingest backend (`ingest_osm` vs `ingest_lanelet2`)
- `src/osm_raster_topology/ingest_lanelet2.py` (new)
  - lanelet2 relation parser and mapper
- `README.md`
  - add Windows lanelet2 ingestion notes and example command

## Suggested first command contract

```powershell
python -m osm_raster_topology run `
  --input .\mapping_example.osm `
  --outdir .\build\run_lanelet2_mvp `
  --pixel-size 1.0 `
  --target-crs EPSG:3857 `
  --ingest-backend lanelet2_xml
```

## Scope Decision

Do not implement a "Lanelet2 -> standard OSM" exporter first. In this repository, direct ingest to internal model is lower risk and keeps fidelity higher.
