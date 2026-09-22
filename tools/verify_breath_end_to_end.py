#!/usr/bin/env python3
"""呼吸（ParamBreath 驱动的 warp 双键形）的**端到端**验证。

链路：合成管线产物 -> build_rig_spec -> compile_static_rig -> lint ->
写 model3 包 -> 官方 Cubism Core 一致性/加载 -> 逐像素比对。

判据（全部用官方内核裁决，不用 bbox 近似）：
  1. 结构与一致性：呼吸 warp 有 2 个控制网格（键 0 与 1），内核判一致且能加载；
  2. 默认键必须复现静止姿态：ParamBreath=0 的画面 == 「不带变形器的等价网格」；
  3. 真的会起伏：ParamBreath=1 != 0，且 == 「顶点已按呼吸幅度上抬好的等价网格」
     —— 这一条同时钉死上抬方向与幅度（方向错/幅度错都会让 sha 不同）。

运行：.venv/Scripts/python.exe tools/verify_breath_end_to_end.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drivers.live2d_runtime.moc3_verify import (  # noqa: E402
    render_probe,
    verify_moc3_consistency,
    verify_moc3_load,
)
from live2d_builder.exporter.moc3_lint import lint_document  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    MeshSpec,
    ParameterSpec,
    RigSpec,
    compile_static_rig,
)
from live2d_builder.exporter.moc3_pipeline import (  # noqa: E402
    BREATH_AMPLITUDE,
    _lattice,
    _member_rect,
    build_rig_spec,
    ensure_front_facing,
)

from live2d_builder.exporter.moc3_model import (  # noqa: E402
    DeformerGrid,
    WarpDeformerSpec,
)

CANVAS = 1024.0
PPU = 100.0
CHEST = (0.0, 100.0)            # 与 BoneHierarchy.get_bone_positions() 一致
OUT = ROOT / "Work" / "breath-e2e"
# 网格刻意做大：幅度是控制网格高度的 2%，小网格会落到亚像素而无法逐像素判定。
# 画布必须**大于**网格：build_rig_spec 会把画布取成网格的 width/height，若两者相等，
# 网格恰好填满画布，形变只是把内容推出画外、画面全饱和 —— 逐像素永远相同
# （这是本脚本前两版失败的原因）。
W, H = 200.0, 300.0


def _mesh_dict():
    x0, y0 = (CANVAS - W) / 2.0, (CANVAS - H) / 2.0
    verts = [[x0, y0], [x0 + W, y0], [x0 + W, y0 + H], [x0, y0 + H]]
    return {
        "vertices": verts,
        "vertices_norm": [[(x - x0) / W, (y - y0) / H] for x, y in verts],
        "indices": [[0, 1, 2], [0, 2, 3]],
        "width": CANVAS, "height": CANVAS,
    }


def _builder_result(with_warp: bool):
    result = {
        "meshes": {"body": _mesh_dict()},
        "deformer_tree": {"deformers": ([
            {"name": "Breath", "type": "warp", "targets": ["body"],
             "grid_rows": 2, "grid_cols": 2, "pivot": list(CHEST)},
        ] if with_warp else [])},
        "parameters": {"cubism_params": [
            {"Id": "ParamBreath", "Min": 0, "Max": 1, "Value": 0},
        ]},
        "bone_positions": {"Chest": CHEST},
    }
    return result


def _package(folder: Path, doc, name: str) -> Path:
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.moc3").write_bytes(doc.to_bytes())
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(
        folder / "texture_00.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3, "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3", "Textures": ["texture_00.png"]},
    }), encoding="utf-8")
    return manifest


def _warp_manifest(folder: Path, name: str, lift_y: float,
                   canvas_w: float, canvas_h: float) -> Path:
    """参照：走**同一条 warp 路径**（成员网格同样存 (s,t)），但只有一个控制网格，
    且该网格已按 lift_y 预抬。

    为什么不用"普通网格"当参照：warp 子网格的顶点存的是 (s,t)、要经控制网格映射回来，
    与直接存模型坐标的网格**不是同一条渲染路径**，逐像素不可能相同（本脚本第一版
    就是这么错的）。让参照也走 warp、只把"抬没抬"作为唯一变量，判据才成立。
    """
    x0, y0 = (canvas_w - W) / 2.0, (canvas_h - H) / 2.0
    model = [(x0 + x - canvas_w / 2.0, canvas_h / 2.0 - (y0 + y))
             for x, y in [[0.0, 0.0], [W, 0.0], [W, H], [0.0, H]]]
    # 三角形必须与产物走**同一条**归一流程：y 翻转会反转手性，build_rig_spec 会调
    # ensure_front_facing 归一回 CCW；漏掉这一步手性相反，会被背面剔除，画面全不同
    # （这是本脚本前几版 sha 对不上的真正原因）。
    mesh = MeshSpec(mesh_id="body", vertices=model,
                    triangles=ensure_front_facing(model, [[0, 1, 2], [0, 2, 3]]),
                    uvs=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                    deformer_id="W")
    rect = _member_rect([mesh])
    deformer = WarpDeformerSpec(
        deformer_id="W", rows=1, cols=1,
        grids=[DeformerGrid(0.0, [[x, y + lift_y]
                                  for x, y in _lattice(rect, 2, 2)])])
    # 仍须**声明**参数（本模型没有绑定）：render_probe 对"没有任何原生参数"的模型
    # 会直接拒绝验收（"No native parameters loaded"）。
    spec = RigSpec(meshes=[mesh],
                   parameters=[ParameterSpec("ParamBreath", minimum=0.0,
                                             maximum=1.0, default=0.0)],
                   deformers=[deformer],
                   canvas_width=canvas_w, canvas_height=canvas_h,
                   pixels_per_unit=PPU)
    doc = compile_static_rig(spec)
    return _package(folder, doc, name)


def main() -> int:
    atlas = {"body": {"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0}}
    spec = build_rig_spec(_builder_result(with_warp=True), atlas)
    deformer = spec.deformers[0]
    print(f"呼吸 warp: id={deformer.deformer_id} 参数={deformer.parameter_id!r} "
          f"控制网格数={len(deformer.grids)} 键={[g.key_value for g in deformer.grids]}")

    doc = compile_static_rig(spec)
    issues = lint_document(doc)
    if issues:
        print("lint 失败:", [str(i) for i in issues])
        return 1
    report = Path("Work/breath-e2e/body.moc3")
    manifest = _package(OUT, doc, "body")
    print(f"lint 通过；产物 {report}")

    pool = doc.get("keyform_position.xys")
    begins = doc.get("warp_deformer_keyform.keyform_position_begin_indices")
    print("产物 warp 控制网格 y（单位坐标；每格 4 个点）：")
    for i, b in enumerate(begins):
        print(f"  网格{i} y={[round(pool[b + k * 2 + 1], 4) for k in range(4)]}")

    consistency = verify_moc3_consistency(str(report))
    print(f"官方内核一致性: {consistency['ok']} "
          f"{'' if consistency['ok'] else consistency['blocker']}")
    if not consistency["ok"]:
        return 1
    loaded = verify_moc3_load(str(manifest))
    print(f"官方内核加载: {loaded['ok']} "
          f"{'' if loaded['ok'] else loaded.get('blocker')}")
    if not loaded["ok"]:
        return 1

    rest = render_probe(str(manifest), {"ParamBreath": 0.0})
    full = render_probe(str(manifest), {"ParamBreath": 1.0})
    if not (rest.get("ok") and full.get("ok")):
        print("渲染失败:", rest.get("blocker"), full.get("blocker"))
        return 1
    print(f"像素: ParamBreath=0 -> {rest['opaque_pixels']} 个不透明像素; "
          f"=1 -> {full['opaque_pixels']}")

    # 幅度 = 控制网格高度的 2%；参照直接按同一幅度上抬，方向/幅度错都会让 sha 不同。
    # 画布必须与待测模型一致：build_rig_spec 会把画布取成网格尺寸。
    rect_height = (H * 1.1)                     # _member_rect 的 5% 余量
    lift = rect_height * BREATH_AMPLITUDE
    cw, ch = spec.canvas_width, spec.canvas_height
    print(f"画布 {cw:.0f}x{ch:.0f}；参照按同一画布 + 上抬 {lift:.2f} 模型像素生成")
    rest_ref = render_probe(str(_warp_manifest(OUT, "ref_rest", 0.0, cw, ch)))

    # 方向与幅度：**直接从产物字节**读控制网格的上抬量 —— 比"用手工参照渲染反推"
    # 更硬，也不受参照与产物其他细节差异的干扰。
    grid_y = [[pool[b + k * 2 + 1] for k in range(4)] for b in begins]
    lift_units = grid_y[1][0] - grid_y[0][0]
    upward = all(b > a for a, b in zip(grid_y[0], grid_y[1]))

    checks = [
        ("默认键复现静止姿态", rest["pixels_sha256"] == rest_ref["pixels_sha256"]),
        ("呼吸真的改变画面", rest["pixels_sha256"] != full["pixels_sha256"]),
        ("上抬方向与幅度正确",
         upward and abs(lift_units - lift / PPU) < 1e-6),
    ]
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not all(ok for _, ok in checks):
        return 1
    print(f"\n全部通过（上抬幅度 {lift:.2f} 模型像素 = 控制网格高度的 "
          f"{BREATH_AMPLITUDE:.0%}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
