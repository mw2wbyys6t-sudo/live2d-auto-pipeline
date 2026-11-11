"""端到端测量「变形时的纹理拉伸」：网格形状直接决定 warp 变形下纹理被拉成什么样。

为什么要有这个指标：静态三角形质量（最小内角、长宽比）只是**代理指标**。真正
决定观感的是变形那一刻纹理被拉成什么样 —— 一个 sliver 三角形在 warp 下会被拉成
长条，纹理出现拖影与条纹。

量法：对每个三角形取「静止 → 变形」的仿射映射 A，算它的奇异值比 σ1/σ2：
σ1/σ2 == 1 表示这块纹理只是被平移/旋转（没被拉伸），越大表示被拉得越厉害。
把每个网格的所有三角形汇总，取中位数与 p90 —— 这就是「动起来时糊不糊」。

对照：**同一个模型、同一姿态**，旧网格生成器（正方形网格 + 无松弛 + 无内缩余量）
与新生成器（六角交错 + 边界钉死松弛 + innerMargin）的这个数字。

用法：python tools/probe_deform_quality.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
from PIL import Image

import bindings_gap_probe as ruler
import probe_seam_delta as seam
from live2d_builder.pipeline import Live2DBuilder


def _layer(w=300, h=420, color=(200, 120, 100, 255), box=None):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.array(img)
    x0, y0, x1, y1 = box or (30, 30, w - 30, h - 30)
    arr[y0:y1, x0:x1] = color
    return Image.fromarray(arr, "RGBA")


def _layers():
    return {
        "face": _layer(box=(90, 40, 210, 200), color=(255, 220, 180, 255)),
        "hair_back": _layer(box=(70, 20, 230, 300), color=(80, 80, 200, 255)),
        "hair_front": _layer(box=(80, 30, 220, 160), color=(60, 60, 180, 255)),
        "nose": _layer(box=(140, 110, 160, 140), color=(250, 200, 160, 255)),
        "cheek": _layer(box=(100, 150, 130, 180), color=(250, 170, 170, 255)),
        "clothes_top": _layer(box=(70, 210, 230, 390), color=(50, 150, 50, 255)),
        "arm_L": _layer(box=(230, 180, 280, 360), color=(120, 90, 60, 255)),
    }


def _old_generator_class():
    """从 git 里取出改动前的 MeshGenerator（改动前的正方形网格 + 无松弛）。"""
    src = subprocess.run(
        ["git", "-C", str(ROOT), "show", "HEAD:live2d_builder/mesh/generator.py"],
        capture_output=True, text=True, encoding="utf-8")
    if src.returncode != 0 or not src.stdout:
        return None
    with tempfile.TemporaryDirectory(prefix="oldgen_") as tmp:
        path = Path(tmp) / "old_generator.py"
        path.write_text(src.stdout, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("old_generator", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["old_generator"] = module
        spec.loader.exec_module(module)
        return module.MeshGenerator


def _build(workdir: Path, generator_class=None):
    import live2d_builder.pipeline as pipeline

    original = pipeline.MeshGenerator
    if generator_class is not None:
        pipeline.MeshGenerator = generator_class
    try:
        builder = Live2DBuilder(output_dir=str(workdir), character_name="dq")
        builder.build(_layers())
    finally:
        pipeline.MeshGenerator = original
    return json.loads(next(workdir.glob("*.bindings.json")).read_text(encoding="utf-8"))


def _auto_pose(doc):
    pose = {}
    for d in doc.get("deformers", []):
        for pid, keys in d.get("axes", []) or []:
            if keys:
                pose[str(pid)] = float(max(keys, key=lambda v: abs(float(v))))
    return pose


def _anisotropy(doc):
    """每个三角形的「静止 → 变形」仿射映射的奇异值比。"""
    # 离线尺子只认 warp 变形器（旋转变形器没有静止矩形）；旋转是刚体，
    # 对纹理拉伸的贡献恒为 1，不影响这个指标。
    doc = seam._warp_only(doc)
    field = ruler.Field(doc, _auto_pose(doc))
    ratios = []
    worst_stretch = 0.0
    for mesh in doc.get("meshes", []):
        rest, deformed = field.deformed(mesh)
        indices = mesh.get("indices") or []
        # bindings 的 indices 可能是「三角形三元组的列表」，也可能是扁平索引流
        if indices and not isinstance(indices[0], (list, tuple)):
            indices = [tuple(indices[i:i + 3]) for i in range(0, len(indices) - 2, 3)]
        for tri in indices:
            if max(tri) >= len(rest) or max(tri) >= len(deformed):
                continue
            p = np.array([rest[i] for i in tri], dtype=float)
            q = np.array([deformed[i] for i in tri], dtype=float)
            m = np.array([[p[1][0] - p[0][0], p[2][0] - p[0][0]],
                          [p[1][1] - p[0][1], p[2][1] - p[0][1]]])
            n = np.array([[q[1][0] - q[0][0], q[2][0] - q[0][0]],
                          [q[1][1] - q[0][1], q[2][1] - q[0][1]]])
            try:
                a = n @ np.linalg.inv(m)
            except np.linalg.LinAlgError:
                continue
            sv = np.linalg.svd(a, compute_uv=False)
            hi, lo = float(sv[0]), float(sv[1])
            if lo <= 1e-9:
                continue
            ratios.append(hi / lo)
            worst_stretch = max(worst_stretch, hi)
    arr = np.asarray(ratios) if ratios else np.zeros(1)
    return {
        "triangles": int(arr.size),
        "median": float(np.median(arr)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(arr.max()),
        "worst_stretch": worst_stretch,
    }


def main() -> int:
    print("同一模型、同一姿态下「纹理被拉得多厉害」（σ1/σ2，1 = 没被拉伸）")
    with tempfile.TemporaryDirectory(prefix="dq_") as tmp:
        root = Path(tmp)
        old_class = _old_generator_class()
        rows = []
        for label, cls in (("旧网格生成器", old_class), ("新网格生成器", None)):
            if label.startswith("旧") and cls is None:
                print("  （拿不到 git 里的旧生成器，跳过对照）")
                continue
            doc = _build(root / label.replace("网格生成器", ""), cls)
            stats = _anisotropy(doc)
            rows.append((label, stats))
            print(f"  {label}: 三角形 {stats['triangles']:5d}  "
                  f"中位 {stats['median']:.4f}  p90 {stats['p90']:.4f}  "
                  f"最大 {stats['max']:.4f}  最坏拉伸倍数 {stats['worst_stretch']:.3f}")
    if len(rows) == 2:
        (_, old), (_, new) = rows
        print(f"\n中位拉伸：{old['median']:.4f} → {new['median']:.4f} "
              f"（降 {(1 - new['median'] / max(old['median'], 1e-9)) * 100:.1f}%）")
        print(f"p90 拉伸：{old['p90']:.4f} → {new['p90']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
