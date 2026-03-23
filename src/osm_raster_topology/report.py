from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from PIL import Image


def write_validation_report(bundle: dict[str, object], output_path: Path) -> None:
    _configure_fonts()

    metadata = bundle["metadata"]
    validation = bundle["validation"]
    preview_path = Path(bundle["artifacts"]["preview_png"])

    roads = validation["roads"]
    water = validation["water"]
    polygons = validation["polygons"]
    nodes = validation["nodes"]
    semantics = validation["semantics"]
    turn_restrictions = validation["turn_restrictions"]
    summary = validation["summary"]
    checks = validation["checks"]

    fig = plt.figure(figsize=(14, 8.4), dpi=220, constrained_layout=True)
    gs = fig.add_gridspec(2, 2, width_ratios=[1.05, 1.25], height_ratios=[1.0, 1.0])

    ax_preview = fig.add_subplot(gs[0, 0])
    ax_counts = fig.add_subplot(gs[0, 1])
    ax_ratios = fig.add_subplot(gs[1, 0])
    ax_diag = fig.add_subplot(gs[1, 1])

    _draw_preview(ax_preview, preview_path)
    _draw_feature_counts(ax_counts, roads, water, polygons)
    _draw_ratio_panel(ax_ratios, summary)
    _draw_diagnostics(ax_diag, checks, nodes, metadata, roads, semantics, turn_restrictions)

    fig.suptitle("OSM-to-Raster Conversion Validation", fontsize=18, fontweight="bold", y=1.02)
    fig.text(
        0.01,
        0.995,
        f"Input: {metadata['input_path']} | Pixel size: {metadata['pixel_size']} m | Oversample: {metadata['topology_oversample']}x",
        fontsize=9,
        color="#4f5b4f",
        va="top",
    )

    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _draw_preview(ax: plt.Axes, preview_path: Path) -> None:
    image = Image.open(preview_path).convert("RGB")
    ax.imshow(image)
    ax.set_title("(a) Raster Preview", loc="left", fontsize=12, fontweight="bold")
    ax.axis("off")


def _draw_feature_counts(ax: plt.Axes, roads: dict[str, object], water: dict[str, object], polygons: dict[str, object]) -> None:
    categories = ["Road", "Water", "Building", "Sports"]
    source = np.array(
        [
            roads["source_feature_count"],
            water["source_feature_count"],
            polygons["building"]["source_feature_count"],
            polygons["sports"]["source_feature_count"],
        ],
        dtype=float,
    )
    exported = np.array(
        [
            roads["exported_feature_count"],
            water["exported_feature_count"],
            polygons["building"]["exported_feature_count"],
            polygons["sports"]["exported_feature_count"],
        ],
        dtype=float,
    )
    x = np.arange(len(categories))
    width = 0.34

    ax.bar(x - width / 2, source, width=width, color="#cfd8cf", edgecolor="#7c897c", linewidth=0.8, label="Source")
    ax.bar(x + width / 2, exported, width=width, color="#2f7d32", edgecolor="#1f5b22", linewidth=0.8, label="Raster")

    for index, (s_val, e_val) in enumerate(zip(source, exported, strict=False)):
        ratio = 1.0 if s_val == 0 else e_val / s_val
        ax.text(index, max(s_val, e_val) + max(source) * 0.03, f"{ratio * 100:.0f}%", ha="center", va="bottom", fontsize=9)

    ax.set_title("(b) Feature Counts Before and After Conversion", loc="left", fontsize=12, fontweight="bold")
    ax.set_ylabel("Feature count")
    ax.set_xticks(x, categories)
    ax.grid(axis="y", color="#e6ebe6", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False, ncol=2, loc="upper right")


def _draw_ratio_panel(ax: plt.Axes, summary: dict[str, object]) -> None:
    labels = [
        "Road retention",
        "Building retention",
        "Sports retention",
        "Road semantic coverage",
        "Turn restriction coverage",
        "Anchor coverage",
    ]
    values = np.array(
        [
            summary["road_retention_ratio"],
            summary["building_retention_ratio"],
            summary["sports_retention_ratio"],
            summary["road_rule_coverage_ratio_average"],
            summary["turn_restriction_coverage_ratio"],
            summary["node_anchor_pixel_coverage_ratio"],
        ],
        dtype=float,
    )
    y = np.arange(len(labels))
    colors = ["#2f7d32" if value >= 0.999 else "#c07a12" for value in values]

    ax.barh(y, values, color=colors, edgecolor="#4f5b4f", linewidth=0.6)
    for index, value in enumerate(values):
        ax.text(min(value + 0.015, 1.02), index, f"{value * 100:.1f}%", va="center", fontsize=9)

    ax.set_title("(c) Preservation and Coverage Ratios", loc="left", fontsize=12, fontweight="bold")
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("Ratio")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.grid(axis="x", color="#e6ebe6", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _draw_diagnostics(
    ax: plt.Axes,
    checks: dict[str, object],
    nodes: dict[str, object],
    metadata: dict[str, object],
    roads: dict[str, object],
    semantics: dict[str, object],
    turn_restrictions: dict[str, object],
) -> None:
    metrics = [
        ("Road missing", checks["road_missing_feature_count"]),
        ("Road fragmented", checks["road_fragmented_feature_count"]),
        ("Planar component delta", checks["road_component_delta_planar"]),
        ("Z-aware component delta", checks["road_component_delta_z_aware"]),
        ("Object-stack overflow", checks["road_object_overflow_pixels"]),
        ("Node anchor missing", checks["node_anchor_missing_pixel_count"]),
        ("Node out-of-bounds", checks["node_anchor_out_of_bounds_count"]),
        ("Node collisions", checks["node_anchor_collision_count"]),
        ("Multi-object pixels", checks["road_multi_object_pixels"]),
    ]
    labels = [item[0] for item in metrics]
    signed_values = np.array([float(item[1]) for item in metrics], dtype=float)
    display_values = np.abs(signed_values)
    y = np.arange(len(metrics))
    colors = ["#2f7d32" if value == 0 else "#c07a12" for value in display_values]
    colors[-1] = "#6f7d6f"

    ax.barh(y, display_values, color=colors, edgecolor="#4f5b4f", linewidth=0.6)
    for index, value in enumerate(signed_values):
        ax.text(display_values[index] + max(display_values) * 0.02 + 0.5, index, f"{int(value)}", va="center", fontsize=9)

    ax.set_title("(d) Diagnostic Counts and Boundary Conditions", loc="left", fontsize=12, fontweight="bold")
    ax.set_xlabel("Absolute count")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.grid(axis="x", color="#e6ebe6", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    note_lines = [
        f"Raw nodes/ways/relations: {metadata['feature_stats']['raw_node_count']}/{metadata['feature_stats']['raw_way_count']}/{metadata['feature_stats']['raw_relation_count']}",
        f"Road components: source planar {roads['source_component_count_planar']} -> raster {roads['raster_component_count']}",
        f"Tagged road classes covered: {semantics['highway_class']['covered_feature_count']}/{semantics['highway_class']['source_tagged_feature_count']}",
        f"Turn restrictions covered: {turn_restrictions['covered_count']}/{turn_restrictions['source_count']}",
        "Out-of-bounds nodes are excluded from loss accounting.",
        "Non-zero z-aware delta indicates non-planar topology preserved in sidecar, not in 2D road mask.",
    ]
    ax.text(
        0.02,
        0.02,
        "\n".join(note_lines),
        transform=ax.transAxes,
        fontsize=8.5,
        color="#4f5b4f",
        va="bottom",
        ha="left",
        bbox={"facecolor": "white", "edgecolor": "#d8ded8", "boxstyle": "round,pad=0.35"},
    )


def _configure_fonts() -> None:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            font_manager.fontManager.addfont(path)
            family = font_manager.FontProperties(fname=path).get_name()
            plt.rcParams["font.family"] = family
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 10
