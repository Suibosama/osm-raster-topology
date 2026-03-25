from __future__ import annotations

from osm_raster_topology.model import IngestedData


def build_topology_sidecar(data: IngestedData) -> dict[str, object]:
    incident_features: dict[str, list[int]] = {}
    for node in data.graph_nodes:
        features = sorted(
            feature.feature_id
            for feature in data.line_features
            if node.osm_node_id in feature.node_refs and feature.category == node.category and feature.z_group == node.z_group
        )
        incident_features[node.node_key] = features

    graph_nodes = [
        {
            "node_key": node.node_key,
            "osm_node_id": node.osm_node_id,
            "category": node.category,
            "z_group": node.z_group,
            "degree": node.degree,
            "role": node.role,
            "point": {"x": node.point[0], "y": node.point[1]},
            "incident_feature_ids": incident_features.get(node.node_key, []),
            "tags": node.tags,
        }
        for node in data.graph_nodes
    ]
    graph_edges = [
        {
            "feature_id": feature.feature_id,
            "osm_ref": feature.osm_ref,
            "category": feature.category,
            "z_group": feature.z_group,
            "node_refs": feature.node_refs,
            "point_count": len(feature.points),
            "tags": feature.tags,
        }
        for feature in data.line_features
    ]
    polygon_faces = [
        {
            "feature_id": feature.feature_id,
            "osm_ref": feature.osm_ref,
            "outer_point_count": len(feature.outer),
            "hole_count": len(feature.holes),
            "tags": feature.tags,
        }
        for feature in data.polygon_features
    ]
    polygon_holes = [
        {
            "parent_feature_id": feature.feature_id,
            "hole_index": hole_index,
            "point_count": len(hole),
        }
        for feature in data.polygon_features
        for hole_index, hole in enumerate(feature.holes, start=1)
    ]
    overlap_groups = [
        {
            "feature_id": feature.feature_id,
            "osm_ref": feature.osm_ref,
            "z_group": feature.z_group,
        }
        for feature in data.line_features
        if feature.z_group != "ground:0"
    ]
    turn_restrictions = [
        {
            "relation_id": restriction.relation_id,
            "restriction": restriction.restriction,
            "from_way_ref": restriction.from_way_ref,
            "via_node_ref": restriction.via_node_ref,
            "to_way_ref": restriction.to_way_ref,
            "tags": restriction.tags,
        }
        for restriction in data.turn_restrictions
    ]
    return {
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "polygon_faces": polygon_faces,
        "polygon_holes": polygon_holes,
        "overlap_groups": overlap_groups,
        "turn_restrictions": turn_restrictions,
        "lanelet_relations": data.lanelet_relations,
        "notes": data.notes,
    }
