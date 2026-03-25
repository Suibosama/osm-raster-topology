from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class LayerSpec:
    name: str
    geometry_kind: str
    description: str
    band_index: int
    burn_strategy: str
    topology_role: str
    tags: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class RunConfig:
    input_path: Path
    outdir: Path
    ingest_backend: str = "auto"
    target_crs: str = "EPSG:3857"
    pixel_size: float = 1.0
    topology_oversample: int = 4
    object_stack_depth: int = 8
    tile_size: int = 4096
    all_touched_lines: bool = True
    emit_object_ids: bool = True
    emit_topology_sidecar: bool = True
    preserve_z_order: bool = True
    layer_specs: list[LayerSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["input_path"] = str(self.input_path)
        payload["outdir"] = str(self.outdir)
        return payload
