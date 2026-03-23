from __future__ import annotations


def normalize_topology() -> None:
    """
    Planned normalization boundary.

    Expected behavior:
    - repair invalid polygons with linework-preserving logic
    - split or mark grade-separated crossings
    - project geometries into a stable metric CRS
    - snap to precision only under explicit guardrails
    """
    raise NotImplementedError(
        "Topology normalization is not implemented yet. This stage should prefer geometry fixes "
        "that preserve original linework and should never silently collapse narrow structures."
    )
