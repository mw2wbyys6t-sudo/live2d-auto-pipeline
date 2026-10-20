#!/usr/bin/env python3
"""
绑定前导出：图层 -> 关节部件 + 像素网格 + 覆盖率报告。

网格与拆分逻辑分别住在 ``live2d_builder/mesh/pixel_grid.py`` 与
``core/segment_engine/rig_split.py``；这里只负责跑一遍并打印表格。

Run:
    python tools/rig_mesh_export.py --image docs/assets/demo_input.png --out out/rig
    python tools/rig_mesh_export.py --image X.png --layers 已有分层目录 --out out/rig \
        --spacing 16 --min-cell 4

Exit codes: 0 覆盖率达标, 1 有部件未被完整覆盖, 2 输入不足。
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live2d_builder.rig_layout import (  # noqa: E402
    build_rig_layout,
    coverage_table,
    write_rig_layout,
)


def _load_layers(layers_dir: Path, canvas) -> dict:
    """读部件 PNG。

    不能直接 glob 整个目录：合成器会把 ``preview.png`` / ``composite_preview.png``
    一起写进去，它们是一整张不透明画布，当成部件会让覆盖率假性跑到 100%。
    优先用 ``layer_guide.json`` 里登记的真实部件清单。
    """
    guide = layers_dir / "layer_guide.json"
    names = None
    if guide.is_file():
        try:
            data = json.loads(guide.read_text(encoding="utf-8"))
            entries = data.get("layers") if isinstance(data, dict) else data
            if isinstance(entries, list):
                names = [str(e.get("name", "")).strip() for e in entries if isinstance(e, dict)]
                names = [n for n in names if n]
        except (ValueError, OSError):
            names = None

    skip = {"preview", "composite_preview", "layer_guide", "guide"}
    layers = {}
    if names:
        for name in names:
            png = layers_dir / f"{name}.png"
            if png.is_file():
                layer = Image.open(png).convert("RGBA")
                if canvas is None or layer.size == canvas:
                    layers[name] = layer
        return layers

    for png in sorted(layers_dir.glob("*.png")):
        if png.stem in skip:
            continue
        layer = Image.open(png).convert("RGBA")
        if canvas is not None and layer.size != canvas:
            continue
        layers[png.stem] = layer
    return layers


def _silhouette(image: Image.Image, explicit: str | None) -> tuple:
    """角色轮廓，返回 ``(mask, source)``。

    优先级：显式文件 > alpha 通道 > 本地 ISNet 前景模型 > 四角色估计。
    最后一种是估计值，报告里会写清来源，不当成精确结果。
    """
    if explicit:
        arr = np.array(Image.open(explicit).convert("RGBA"))
        mask = arr[:, :, 3] > 16
        if mask.shape != (image.size[1], image.size[0]):
            raise SystemExit(f"轮廓图尺寸 {mask.shape} 与原图 {image.size} 不一致")
        return mask, f"file:{explicit}"

    rgba = image.convert("RGBA")
    arr = np.array(rgba)
    if arr[:, :, 3].min() < 250:
        return arr[:, :, 3] > 16, "alpha"

    try:
        from core.segment_engine import isnet_onnx

        model = isnet_onnx.find_model()
        if model is not None:
            runner = isnet_onnx.OnnxForegroundModel(model)
            mask = np.asarray(runner.predict(rgba), dtype=bool)
            # ONNX 会话常驻几百 MB；算完立刻释放，否则下一步再加载一个会话
            # 会把进程顶到内存上限（实测 4GB cgroup 下直接被 OOM kill）。
            del runner
            gc.collect()
            return mask, f"model:{model.name}"
    except Exception as exc:  # pragma: no cover - 模型不可用时的兜底
        print(f"[WARN] 前景模型不可用（{exc}），改用四角色估计轮廓")

    rgb = arr[:, :, :3].astype(np.int16)
    corners = np.concatenate([
        rgb[:8, :8].reshape(-1, 3), rgb[:8, -8:].reshape(-1, 3),
        rgb[-8:, :8].reshape(-1, 3), rgb[-8:, -8:].reshape(-1, 3),
    ])
    bg = np.median(corners, axis=0)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    return dist > 40, "corner-color(estimated)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="原图（用于切部件与画预览）")
    ap.add_argument("--layers", default=None, help="已有分层目录；不给就现跑语义分层")
    ap.add_argument("--silhouette", default=None,
                    help="角色轮廓 PNG（用 alpha）。不给则自动取 alpha/前景模型")
    ap.add_argument("--out", default="output/rig_layout")
    ap.add_argument("--spacing", type=int, default=16)
    ap.add_argument("--min-cell", type=int, default=4)
    ap.add_argument("--no-residual", action="store_true",
                    help="不补残差层（覆盖率可能到不了 100%%）")
    args = ap.parse_args()

    image = Image.open(args.image).convert("RGBA")
    target = args.out if args.out else f"{args.layers}.rig"

    if args.layers:
        layers = _load_layers(Path(args.layers), image.size)
        if not layers:
            print(f"[FAIL] {args.layers} 里没有与画布同尺寸的部件 PNG")
            return 2
        method = f"existing:{args.layers}"
    else:
        from core.segment_engine.semantic import SemanticSegmenter

        seg = SemanticSegmenter(model_type="isnet", device="cpu")
        result = seg.layer(image, output_dir=str(Path(target) / "semantic"))
        layers = {}
        for entry in result.get("layers", []):
            path = entry.get("path")
            if path and Path(path).is_file():
                layers[entry["name"]] = Image.open(path).convert("RGBA")
        if not layers:
            print("[FAIL] 语义分层没有产出任何图层")
            return 2
        method = result.get("method", "?")
        # 分层阶段的分割器里还挂着 ONNX 会话，先释放再往下走
        del seg
        del result
        gc.collect()

    silhouette, sil_source = _silhouette(image, args.silhouette)
    layout = build_rig_layout(
        layers,
        image=image,
        silhouette=silhouette,
        spacing=args.spacing,
        min_cell=args.min_cell,
        add_residual=not args.no_residual,
        silhouette_source=sil_source,
    )
    paths = write_rig_layout(layout, target)

    rows = coverage_table(layout)
    overall = layout["report"]["overall"]

    print(f"\n分层来源: {method}")
    print(f"轮廓来源: {sil_source}")
    print(f"网格参数: spacing={args.spacing} min_cell={args.min_cell}")
    print(f"部件数量: {layout['report']['part_count']}"
          f"（残差 {layout['report']['residual_px']} px）")
    print()
    header = (f"{'部件':18s} {'角色':10s} {'父节点':14s} {'像素':>7s} "
              f"{'顶点':>6s} {'三角':>6s} {'覆盖':>6s} {'外框':>6s}")
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['part']:18s} {str(r['role']):10s} {str(r['parent'] or '-'):14s} "
            f"{r['pixels']:7d} {r['vertices']:6d} {r['triangles']:6d} "
            f"{r['coverage'] * 100:5.2f}% {r['over_cover'] * 100:5.1f}%"
        )

    print()
    print(f"角色像素      : {overall['target_px']}")
    print(f"被网格覆盖    : {overall['covered_px']} "
          f"({overall['coverage'] * 100:.2f}%)")
    print(f"未覆盖        : {overall['uncovered_px']}")
    for warn in layout["warnings"]:
        print(f"[WARN] {warn}")
    print()
    print("产出:")
    for key, value in paths.items():
        if value:
            print(f"  {key:10s} {value}")

    return 0 if overall["coverage"] >= 0.9999 else 1


if __name__ == "__main__":
    raise SystemExit(main())
