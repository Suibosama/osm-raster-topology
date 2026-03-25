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
    summary = validation["summary"]
    checks = validation["checks"]

    fig = plt.figure(figsize=(14, 8.6), dpi=220)
    gs = fig.add_gridspec(
        2,
        2,
        width_ratios=[1.0, 1.3],
        height_ratios=[1.0, 1.0],
        left=0.07,
        right=0.985,
        top=0.89,
        bottom=0.10,
        wspace=0.24,
        hspace=0.16,
    )

    ax_preview = fig.add_subplot(gs[0, 0])
    ax_counts = fig.add_subplot(gs[0, 1])
    ax_ratios = fig.add_subplot(gs[1, 0])
    diag_gs = gs[1, 1].subgridspec(1, 2, width_ratios=[0.74, 0.26], wspace=0.04)
    ax_diag = fig.add_subplot(diag_gs[0, 0])
    ax_diag_note = fig.add_subplot(diag_gs[0, 1])

    _draw_preview(ax_preview, preview_path)
    _draw_feature_counts(ax_counts, roads, water, polygons)
    _draw_ratio_panel(ax_ratios, summary)
    _draw_diagnostics(ax_diag, ax_diag_note, checks, validation.get("lanelet", {}))

    fig.suptitle("OSM 转栅格量化验证", fontsize=18, fontweight="bold", y=0.975)
    fig.text(
        0.015,
        0.948,
        f"输入文件: {metadata['input_path']} | 像素分辨率: {metadata['pixel_size']} m | 拓扑超采样: {metadata['topology_oversample']}x",
        fontsize=8.5,
        color="#4f5b4f",
        va="top",
    )

    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_lanelet2_report(bundle: dict[str, object], output_path: Path) -> None:
    _configure_fonts()

    metadata = bundle["metadata"]
    validation = bundle["validation"]
    preview_path = Path(bundle["artifacts"]["preview_png"])

    roads = validation["roads"]
    water = validation["water"]
    polygons = validation["polygons"]
    checks = validation["checks"]
    lanelet = validation.get("lanelet", {})

    fig = plt.figure(figsize=(14, 8.6), dpi=220)
    gs = fig.add_gridspec(
        2,
        2,
        width_ratios=[1.0, 1.3],
        height_ratios=[1.0, 1.0],
        left=0.07,
        right=0.985,
        top=0.89,
        bottom=0.14,
        wspace=0.24,
        hspace=0.16,
    )

    ax_preview = fig.add_subplot(gs[0, 0])
    ax_counts = fig.add_subplot(gs[0, 1])
    ax_lanelet = fig.add_subplot(gs[1, 0])
    ax_diag = fig.add_subplot(gs[1, 1])

    _draw_preview(ax_preview, preview_path)
    _draw_feature_counts(ax_counts, roads, water, polygons)
    _draw_lanelet_panel(ax_lanelet, lanelet)
    _draw_diagnostics(ax_diag, fig.add_axes([0, 0, 0, 0]), checks, lanelet, hide_note=True)

    fig.suptitle("Lanelet2 转栅格量化报告", fontsize=18, fontweight="bold", y=0.975)
    fig.text(
        0.015,
        0.948,
        f"输入文件: {metadata['input_path']} | 像素分辨率 {metadata['pixel_size']} m | 拓扑超采样 {metadata['topology_oversample']}x",
        fontsize=8.5,
        color="#4f5b4f",
        va="top",
    )

    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _draw_preview(ax: plt.Axes, preview_path: Path) -> None:
    image = Image.open(preview_path).convert("RGB")
    ax.imshow(image)
    ax.set_title("(a) 栅格预览", loc="left", fontsize=11.5, fontweight="bold", pad=6)
    ax.axis("off")


def _draw_feature_counts(ax: plt.Axes, roads: dict[str, object], water: dict[str, object], polygons: dict[str, object]) -> None:
    categories = ["道路", "水系", "建筑", "运动场"]
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

    ax.bar(x - width / 2, source, width=width, color="#cfd8cf", edgecolor="#7c897c", linewidth=0.8, label="转换前")
    ax.bar(x + width / 2, exported, width=width, color="#2f7d32", edgecolor="#1f5b22", linewidth=0.8, label="转换后")

    for index, (s_val, e_val) in enumerate(zip(source, exported, strict=False)):
        ratio = 1.0 if s_val == 0 else e_val / s_val
        ax.text(index, max(s_val, e_val) + max(source) * 0.03, f"{ratio * 100:.0f}%", ha="center", va="bottom", fontsize=9)

    ax.set_title("(b) 转换前后要素数量对比", loc="left", fontsize=11.5, fontweight="bold", pad=6)
    ax.set_ylabel("要素数量")
    ax.set_xticks(x, categories)
    ax.grid(axis="y", color="#e6ebe6", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False, ncol=2, loc="upper right", fontsize=9, handlelength=1.8, columnspacing=1.4)


def _draw_ratio_panel(ax: plt.Axes, summary: dict[str, object]) -> None:
    labels = [
        "道路保留率",
        "建筑保留率",
        "运动场保留率",
        "道路语义覆盖率",
        "转向限制覆盖率",
        "锚点覆盖率",
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

    ax.set_title("(c) 保留率与覆盖率", loc="left", fontsize=11.5, fontweight="bold", pad=6)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("比例")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.grid(axis="x", color="#e6ebe6", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _draw_diagnostics(
    ax: plt.Axes,
    ax_note: plt.Axes,
    checks: dict[str, object],
    lanelet: dict[str, object],
    hide_note: bool = False,
) -> None:
    metrics = [
        ("道路缺失", checks["road_missing_feature_count"]),
        ("道路断裂", checks["road_fragmented_feature_count"]),
        ("平面分量差值", checks["road_component_delta_planar"]),
        ("分层分量差值", checks["road_component_delta_z_aware"]),
        ("对象栈溢出", checks["road_object_overflow_pixels"]),
        ("锚点缺失", checks["node_anchor_missing_pixel_count"]),
        ("节点越界", checks["node_anchor_out_of_bounds_count"]),
        ("节点碰撞", checks["node_anchor_collision_count"]),
        ("多对象像素", checks["road_multi_object_pixels"]),
    ]
    labels = [item[0] for item in metrics]
    signed_values = np.array([float(item[1]) for item in metrics], dtype=float)
    display_values = np.abs(signed_values)
    y = np.arange(len(metrics))
    colors = ["#2f7d32" if value == 0 else "#c07a12" for value in display_values]
    colors[-1] = "#6f7d6f"

    ax.barh(y, display_values, color=colors, edgecolor="#4f5b4f", linewidth=0.6)
    max_display = max(float(display_values.max()), 1.0)
    for index, value in enumerate(signed_values):
        ax.text(display_values[index] + max_display * 0.02 + 0.5, index, f"{int(value)}", va="center", fontsize=9)

    ax.set_title("(d) 诊断项与边界条件", loc="left", fontsize=11.5, fontweight="bold", pad=6)
    ax.set_xlabel("绝对数量")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlim(0, max_display * 1.08)
    ax.grid(axis="x", color="#e6ebe6", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    note_lines = [
        "指标定义:",
        "道路缺失: 源 OSM 道路要素在栅格对象栈中完全找不到。",
        "道路断裂: 单条道路被栅格化后裂成多个 8 邻接连通分量。",
        "平面分量差值: 栅格道路连通分量数 - 源路网平面连通分量数。",
        "分层分量差值: 栅格道路连通分量数 - 源路网分层连通分量数。",
        "对象栈溢出: 单像素内对象数超过 object stack 深度上限。",
        "锚点缺失: 范围内图节点对应的 node anchor 像素未写入。",
        "节点越界: 图节点投影后落在当前栅格范围外，不计入缺失。",
        "节点碰撞: 多个图节点投影到同一锚点像素，常见于桥/隧/分层重合。",
        "多对象像素: 同一像素同时属于多条道路或线对象。",
    ]
    if not hide_note:
        lanelet_count = int(lanelet.get("lanelet_count", 0))
        if lanelet_count > 0:
            note_lines.extend(
                [
                    "",
                    "Lanelet2 指标:",
                    f"lanelet 总数: {lanelet_count}",
                    f"有前驱: {lanelet.get('with_predecessor', 0)}",
                    f"有后继: {lanelet.get('with_successor', 0)}",
                    f"左邻接: {lanelet.get('with_left_neighbor', 0)}",
                    f"右邻接: {lanelet.get('with_right_neighbor', 0)}",
                    f"任一邻接: {lanelet.get('with_any_neighbor', 0)}",
                    f"孤立 lanelet: {lanelet.get('isolated_lanelets', 0)}",
                    f"规则引用数: {lanelet.get('regulatory_ref_count', 0)}",
                ]
            )
        ax_note.axis("off")
        ax_note.text(
            0.98,
            0.5,
            "\n".join(note_lines),
            transform=ax_note.transAxes,
            fontsize=7.8,
            color="#4f5b4f",
            va="center",
            ha="right",
            bbox={"facecolor": "white", "edgecolor": "#d8ded8", "boxstyle": "round,pad=0.30"},
        )


def _draw_lanelet_panel(ax: plt.Axes, lanelet: dict[str, object]) -> None:
    lanelet_count = int(lanelet.get("lanelet_count", 0))
    labels = ["邻接覆盖率", "后继覆盖率"]
    values = np.array(
        [
            float(lanelet.get("neighbor_ratio", 0.0)),
            float(lanelet.get("successor_ratio", 0.0)),
        ],
        dtype=float,
    )
    y = np.arange(len(labels))
    colors = ["#2f7d32" if value >= 0.999 else "#c07a12" for value in values]
    ax.barh(y, values, color=colors, edgecolor="#4f5b4f", linewidth=0.6)
    for index, value in enumerate(values):
        ax.text(min(value + 0.02, 1.02), index, f"{value * 100:.1f}%", va="center", fontsize=9)

    ax.set_title("(c) Lanelet2 量化指标", loc="left", fontsize=11.5, fontweight="bold", pad=6)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("比例")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.grid(axis="x", color="#e6ebe6", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax.text(
        0.02,
        -0.55,
        f"lanelet 总数: {lanelet_count} | 孤立: {lanelet.get('isolated_lanelets', 0)} | 规则引用数: {lanelet.get('regulatory_ref_count', 0)}",
        transform=ax.transAxes,
        fontsize=9,
        color="#4f5b4f",
        ha="left",
    )
    ax.text(
        0.02,
        -0.82,
        "邻接覆盖率 = 有左右邻接的 lanelet / 总 lanelet",
        transform=ax.transAxes,
        fontsize=8,
        color="#4f5b4f",
        ha="left",
    )
    ax.text(
        0.02,
        -1.02,
        "后继覆盖率 = 有后继的 lanelet / 总 lanelet",
        transform=ax.transAxes,
        fontsize=8,
        color="#4f5b4f",
        ha="left",
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
