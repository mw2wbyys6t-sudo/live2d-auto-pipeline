"""分层主干的可行性闸门：官方 Core 是否逐级合成 warp 父链。

用法：python tools/deform_trunk_probe_gate.py
输出 JSON；core_ok=false 时三条派生判据（chain_applies_parent /
chain_matches_flattened / identity_chain_neutral_rest）一律是 null = **未验证**，
不得当作通过。派生判据只在五条探针都真出像素时才计算。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image

from drivers.live2d_runtime.moc3_verify import render_probe
from live2d_builder.exporter.moc3_model import (
    DeformerGrid, MeshSpec, ParameterSpec, RigSpec, WarpDeformerSpec,
    compile_static_rig,
)

CANVAS = 512.0
PPU = 100.0
LATTICE = [[-100.0, -100.0], [100.0, -100.0], [-100.0, 100.0], [100.0, 100.0]]


def _grid(points):
    """单键形控制网格：不随参数变化的静态 warp 网格。"""
    return [DeformerGrid(0.0, points)]


def _shift(points, dx, dy):
    return [[x + dx, y + dy] for x, y in points]


def _package(root: Path, doc, name: str) -> Path:
    folder = root / name
    folder.mkdir(exist_ok=True)  # 同名包可复用（参数化重跑不要再抛 FileExistsError）
    (folder / f"{name}.moc3").write_bytes(doc.to_bytes())
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(folder / "texture_00.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3,
        "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3", "Textures": ["texture_00.png"]},
    }), encoding="utf-8")
    return manifest


def _rig(deformers, deformer_id):
    verts = [[-60.0, -60.0], [60.0, -60.0], [60.0, 60.0], [-60.0, 60.0]]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    mesh = MeshSpec("ArtMeshProbe", verts, [(0, 1, 2), (0, 2, 3)], uvs,
                    deformer_id=deformer_id)
    return RigSpec(
        meshes=[mesh], deformers=deformers,
        parameters=[ParameterSpec("ParamUnused", -1.0, 1.0, 0.0)],
        canvas_width=CANVAS, canvas_height=CANVAS, pixels_per_unit=PPU)


def chain_shift(parent_shift=(40.0, 0.0), child_shift=(0.0, 25.0)):
    """Child 挂在 Parent 下：Core 若逐级求值，画面应移动 parent+child 的合成量。"""
    parent = WarpDeformerSpec(
        deformer_id="ProbeParent", rows=1, cols=1,
        grids=_grid(_shift(LATTICE, *parent_shift)))
    child = WarpDeformerSpec(
        deformer_id="ProbeChild", rows=1, cols=1,
        grids=_grid(_shift(LATTICE, *child_shift)),
        parent_deformer_id="ProbeParent")
    return _rig([parent, child], "ProbeChild")


def rest_rig():
    """两级都是恒等点阵 —— 用来确认「挂链」本身不改变静止画面。

    实测结论（2026-11-05，官方 Core 5.0.0）：**这条不成立**。恒等父链把静止帧
    放大 2× 并偏移（见 neutral_rig 与 identity_chain_neutral_rest 观察项）。
    """
    ident = WarpDeformerSpec(deformer_id="ProbeParent", rows=1, cols=1,
                             grids=_grid(LATTICE))
    child = WarpDeformerSpec(deformer_id="ProbeChild", rows=1, cols=1,
                             grids=_grid(LATTICE), parent_deformer_id="ProbeParent")
    return _rig([ident, child], "ProbeChild")


def unparented_shift(parent_shift=(40.0, 0.0), child_shift=(0.0, 25.0)):
    """同样的两段位移，但 child 没有父级：合成量应当**不同**（负对照）。

    实测结论：单级 warp 的平移被自己的 (s, t) 换算精确抵消，拍平后的画面与
    「不挂变形器」逐像素相同 —— 两段位移在拍平路线里全丢了，父链不是可省结构。
    """
    merged = WarpDeformerSpec(
        deformer_id="ProbeChild", rows=1, cols=1,
        grids=_grid(_shift(LATTICE, parent_shift[0] + child_shift[0],
                                     parent_shift[1] + child_shift[1])))
    return _rig([merged], "ProbeChild")


def neutral_rig():
    """同一块网格**不挂任何变形器** —— 「挂链是否改变静止画面」的基准。

    rest_rig 只说「两级恒等」，没有基准就没法回答接线本身动不动画面；这条基准
    是 rest_rig 的对照组（门把它作为观察项输出，不作闸门判据）。
    """
    return _rig([], "")


def _rendered(result) -> bool:
    """探针真的出像素才算可用。

    `render_probe` 在**每条**非 ok 路径上把 `pixels_sha256` 留成空串
    （drivers/live2d_runtime/moc3_verify.py:203, 241-243），所以 ok 之外还要认
    sha 非空，否则两条空串一比就成了「相等」。
    """
    return bool(result["ok"] and result["pixels_sha256"])


def main() -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        neutral = render_probe(str(_package(
            root, compile_static_rig(neutral_rig()), "neutral")))
        rest = render_probe(str(_package(root, compile_static_rig(rest_rig()), "rest")))
        # 单变量探针：只有父级位移、子级恒等。`chain` 那条同时改父级和子级，
        # 画面变了也说不清是谁干的；父级求值这条判据一律读 parent_only
        # （计划 Interfaces 段点名 parent_only_sha 就是这个用途）。
        parent_only = render_probe(str(_package(
            root,
            compile_static_rig(chain_shift(parent_shift=(40.0, 0.0),
                                          child_shift=(0.0, 0.0))),
            "parent_only")))
        chain = render_probe(str(_package(root, compile_static_rig(chain_shift()), "chain")))
        flat = render_probe(str(_package(root, compile_static_rig(unparented_shift()), "flat")))

        probes = (neutral, rest, parent_only, chain, flat)
        core_ok = all(_rendered(p) for p in probes)
        out = {
            "core_ok": core_ok,
            "blocker": next((p["blocker"] for p in probes
                             if not _rendered(p) and p["blocker"]), ""),
            "neutral_sha": neutral["pixels_sha256"],
            "rest_sha": rest["pixels_sha256"],
            "parent_only_sha": parent_only["pixels_sha256"],
            "chain_sha": chain["pixels_sha256"],
            "flat_sha": flat["pixels_sha256"],
            # 判据的原始证据（报告 §4 的归因表就读这几个数）：
            # 父级 +40 画布像素 → 质心移动、不透明像素数不变 = 纯刚体平移。
            "rest_opaque_pixels": rest["opaque_pixels"],
            "parent_only_opaque_pixels": parent_only["opaque_pixels"],
            "neutral_opaque_pixels": neutral["opaque_pixels"],
            "rest_centroid": rest["centroid"],
            "parent_only_centroid": parent_only["centroid"],
        }
        if core_ok:
            out.update({
                # 门的两条判据：只有父级位移就足以改变画面（父级被求值），
                # 且链与扁平合成等价（顺序可预期）。
                "chain_applies_parent": (
                    parent_only["pixels_sha256"] != rest["pixels_sha256"]),
                "chain_matches_flattened": (
                    chain["pixels_sha256"] == flat["pixels_sha256"]),
                # 观察项（非闸门）：两级恒等链的静止帧是否等于「不挂变形器」。
                # false = 接线本身改变了静止画面，Task 2 开工前必须先定父链的坐标基准。
                "identity_chain_neutral_rest": (
                    rest["pixels_sha256"] == neutral["pixels_sha256"]),
            })
        else:
            # Core 缺失/渲染失败时**不能**算这三条：每条失败探针的
            # pixels_sha256 都是 ""，"" == "" 会把「未验证」洗成绿灯，而
            # identity_chain_neutral_rest 正是 Task 2 的验收键。按计划的绑定规则，
            # ok=false / blocker 一律记为未验证 = None。
            out.update({
                "chain_applies_parent": None,
                "chain_matches_flattened": None,
                "identity_chain_neutral_rest": None,
            })
        return out


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
