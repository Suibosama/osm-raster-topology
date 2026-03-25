from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from osm_raster_topology.pipeline import build_run_config, run_pipeline

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
except Exception:  # pragma: no cover
    DND_FILES = None
    TkBase = tk.Tk
else:
    TkBase = TkinterDnD.Tk


def launch_gui() -> int:
    app = RasterGui()
    app.mainloop()
    return 0


class RasterGui(TkBase):
    def __init__(self) -> None:
        super().__init__()
        self.title("矢量地图转栅格地图工具")
        self.geometry("860x620")
        self.minsize(760, 560)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.pixel_size_var = tk.StringVar(value="1.0")
        self.ingest_backend_var = tk.StringVar(value="auto")
        self.status_var = tk.StringVar(value="请选择矢量地图文件和输出目录。")

        self._build_widgets()
        self._enable_optional_drag_drop()

    def _build_widgets(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(6, weight=1)

        ttk.Label(root, text="矢量地图转栅格地图工具", font=("", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            root,
            text="选择或拖入 .osm 矢量地图文件，生成栅格地图与量化报告。",
        ).grid(row=1, column=0, sticky="w", pady=(6, 16))

        file_card = ttk.LabelFrame(root, text="1. 输入文件", padding=14)
        file_card.grid(row=2, column=0, sticky="nsew")
        file_card.columnconfigure(0, weight=1)
        ttk.Entry(file_card, textvariable=self.input_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(file_card, text="选择地图", command=self._pick_input).grid(row=0, column=1, padx=(10, 0))
        self.drop_hint = ttk.Label(file_card, text="", foreground="#5f6b5f")
        self.drop_hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        out_card = ttk.LabelFrame(root, text="2. 输出目录", padding=14)
        out_card.grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        out_card.columnconfigure(0, weight=1)
        ttk.Entry(out_card, textvariable=self.output_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(out_card, text="选择目录", command=self._pick_output).grid(row=0, column=1, padx=(10, 0))

        cfg_card = ttk.LabelFrame(root, text="3. 参数", padding=14)
        cfg_card.grid(row=4, column=0, sticky="nsew", pady=(14, 0))
        ttk.Label(cfg_card, text="像素分辨率（米）").grid(row=0, column=0, sticky="w")
        ttk.Entry(cfg_card, textvariable=self.pixel_size_var, width=12).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(cfg_card, text="Ingest 后端").grid(row=1, column=0, sticky="w", pady=(10, 0))
        backend = ttk.Combobox(
            cfg_card,
            textvariable=self.ingest_backend_var,
            values=["auto", "osm_xml", "lanelet2_xml"],
            state="readonly",
            width=14,
        )
        backend.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        ttk.Label(cfg_card, text="Lanelet2 选 lanelet2_xml，可输出专用量化报告").grid(row=1, column=2, sticky="w", padx=(20, 0), pady=(10, 0))
        ttk.Label(cfg_card, text="当前坐标系固定为 EPSG:3857").grid(row=0, column=2, sticky="w", padx=(20, 0))

        action_bar = ttk.Frame(root)
        action_bar.grid(row=5, column=0, sticky="ew", pady=(18, 0))
        ttk.Button(action_bar, text="开始转换", command=self._start_run).pack(side="left")
        ttk.Button(action_bar, text="打开输出目录", command=self._open_output_dir).pack(side="left", padx=(10, 0))

        status_card = ttk.LabelFrame(root, text="4. 运行状态", padding=14)
        status_card.grid(row=6, column=0, sticky="nsew", pady=(14, 0))
        status_card.columnconfigure(0, weight=1)
        status_card.rowconfigure(2, weight=1)
        ttk.Label(status_card, textvariable=self.status_var, foreground="#2c3a2c").grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(status_card, mode="determinate", maximum=100)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(12, 10))

        self.log = tk.Text(status_card, height=14, wrap="word")
        self.log.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(status_card, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.insert("end", "等待开始。\n")
        self.log.configure(state="disabled")

    def _enable_optional_drag_drop(self) -> None:
        if DND_FILES is None:
            self.drop_hint.configure(text="当前未安装 tkinterdnd2，拖拽不可用，请点击“选择地图”。")
            return
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._handle_drop)
        self.drop_hint.configure(text="可直接将 .osm 矢量地图文件拖入窗口。")

    def _handle_drop(self, event: tk.Event) -> None:
        raw = str(event.data).strip()
        path = _normalize_drop_path(raw)
        if path.suffix.lower() != ".osm":
            messagebox.showwarning("文件类型不支持", "请拖入 .osm 文件。")
            return
        self.input_var.set(str(path))
        if not self.output_var.get():
            self.output_var.set(str(path.with_name(f"{path.stem}_output")))

    def _pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="选择矢量地图文件",
            filetypes=[("OSM XML", "*.osm"), ("所有文件", "*.*")],
        )
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                src = Path(path)
                self.output_var.set(str(src.with_name(f"{src.stem}_output")))

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_var.set(path)

    def _start_run(self) -> None:
        input_path = Path(self.input_var.get().strip())
        output_path = Path(self.output_var.get().strip())
        try:
            pixel_size = float(self.pixel_size_var.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "像素分辨率必须是数字。")
            return

        if not input_path.exists():
            messagebox.showerror("输入不存在", "请选择有效的 .osm 矢量地图文件。")
            return
        if input_path.suffix.lower() != ".osm":
            messagebox.showerror("文件类型错误", "当前只支持 .osm 矢量地图文件。")
            return
        if pixel_size <= 0:
            messagebox.showerror("参数错误", "像素分辨率必须大于 0。")
            return

        output_path.mkdir(parents=True, exist_ok=True)
        self.status_var.set("转换进行中，请稍候...")
        self._append_log(f"输入文件: {input_path}")
        self._append_log(f"输出目录: {output_path}")
        self._append_log(f"像素分辨率: {pixel_size} m")
        self._append_log(f"Ingest 后端: {self.ingest_backend_var.get().strip()}")
        self.progress["value"] = 0

        worker = threading.Thread(
            target=self._run_pipeline_thread,
            args=(input_path, output_path, pixel_size),
            daemon=True,
        )
        worker.start()

    def _run_pipeline_thread(self, input_path: Path, output_path: Path, pixel_size: float) -> None:
        try:
            def progress_cb(stage: str, value: int) -> None:
                self.after(0, lambda: self._set_progress(stage, value))

            config = build_run_config(
                input_path=str(input_path),
                outdir=str(output_path),
                ingest_backend=self.ingest_backend_var.get().strip() or "auto",
                pixel_size=pixel_size,
                target_crs="EPSG:3857",
            )
            result = run_pipeline(config, progress_cb=progress_cb)
            self.after(0, lambda: self._on_success(result))
        except Exception as exc:  # pragma: no cover
            self.after(0, lambda: self._on_error(exc))

    def _on_success(self, result: dict[str, object]) -> None:
        self.progress.stop()
        self.status_var.set("转换完成。")
        self.progress["value"] = 100
        self._append_log(json.dumps(result, indent=2, ensure_ascii=False))
        messagebox.showinfo(
            "转换完成",
            "输出已生成。\n"
            f"结果包: {result['bundle']}\n"
            f"量化图: {result['validation_report']}",
        )

    def _on_error(self, exc: Exception) -> None:
        self.progress.stop()
        self.status_var.set("转换失败。")
        self.progress["value"] = 0
        self._append_log(f"转换失败: {exc}")
        messagebox.showerror("转换失败", str(exc))

    def _set_progress(self, stage: str, value: int) -> None:
        labels = {
            "ingest": "读取与解析中…",
            "rasterize": "栅格化处理中…",
            "sidecar": "拓扑与侧车生成中…",
            "validate": "量化验证中…",
            "report": "报告生成中…",
            "done": "转换完成。",
        }
        self.progress["value"] = value
        if stage in labels:
            self.status_var.set(labels[stage])

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_output_dir(self) -> None:
        output = self.output_var.get().strip()
        if not output:
            messagebox.showwarning("未设置输出目录", "请先选择输出目录。")
            return
        path = Path(output)
        if not path.exists():
            messagebox.showwarning("目录不存在", "输出目录还不存在。")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("打开失败", str(exc))


def _normalize_drop_path(raw: str) -> Path:
    text = raw.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return Path(text.strip('"'))
