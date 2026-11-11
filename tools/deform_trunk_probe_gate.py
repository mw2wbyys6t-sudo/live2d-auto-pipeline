"""分层主干的可行性闸门：官方 Core 是否**逐级合成** warp 父链。

用法：`PYTHONIOENCODING=utf-8 python tools/deform_trunk_probe_gate.py`
打印一份 JSON。三条判据（#70 换成键形带以后）全部由官方 Core 的像素推出：

  chain_rest_matches_control     挂 `父(键形带)→子(恒等)` 的静止帧 == 同一张方块
                                 **不挂任何变形器**的对照 ⇒ 接这条链本身中性。
  chain_inherits_parent_band     父级键形走到满幅（ParamAngleZ=±30，键形相对默认键
                                 ±40 模型像素）时画面确实平移，而且**幅度对得上**：
                                 实测质心位移按静止对照帧自己推出的渲染比例换算回
                                 模型像素，必须是 +40 / −40（±2px），y 不许动，
                                 不透明像素数基本不变（= 纯平移，不是撕裂或缩放）。
  neutral_middle_level_invisible 去掉那条恒等子级、把方块**直接挂到同一个带键形的
                                 父级**上，静止与两侧满幅都逐像素相同 ⇒ 主干多加的
                                 那层恒等变形器在内核里真的是中性的。

为什么判据必须是**相对默认键的键形带**，不能用「静态整体平移点阵」（Task 1 用的
就是后者，#70 把它作废了）：编译器把成员顶点按 `reference_rect(deformer)` 归一成
(s, t)，而那张参考矩形取的正是**默认键**那张点阵。整张静止点阵连同它的矩形一起
平移 dx，归一以后 dx 自我抵消 ⇒ 画面一动不动。这条在**根级**于 Task 1.5 修复前后
都成立（控制者在 `69b39399` 上量到 +10px 的 x 与 y 位移都是 Δ=0.00）。所以 Task 1
当年 `chain_applies_parent=true` 那条正证据能读到位移，靠的正是我们要修掉的那个
缺陷 —— 修复前子级不被父级矩形重归一，父级的静态位移才会显形。**能力是真的，
证明形式是被 bug 撑起来的。** 键形带不一样：默认键那张不变（矩形不变、顶点归一
不变），只有形变键离开静止矩形，于是「父级动了」在像素上看得见。

旧键 `chain_applies_parent` 因此被**删除**（它的语义是「静态平移父级会让画面动」，
现在恒为 false，留着只会让人误读成「内核不求值父链 ⇒ 分层主干不成立」，而那条
路线是被实测批准的）。它的读数改由 `static_translation_moves_frame` 如实记录并
写明只作观察；`chain_sha` / `flat_sha` 两条静态观察值继续原样写进 JSON。

失败安全（不许退化）：任何一条探针没真出像素（Core 缺失 / 渲染失败 / 超时），
`core_ok=false`，所有派生判据一律 `null` = **未验证**。每条失败探针的
`pixels_sha256` 都是空串（`drivers/live2d_runtime/moc3_verify.py:203, 241-243`），
`"" == ""` 会把「未验证」洗成绿灯，所以判据一律走在 `core_ok` 后面。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple

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
# 点阵与方块是**共享尺子**：tests/unit/test_bindings_gap_probe.py:49-71 有同一把
# 尺子的另一份（tuple 写法），任一边漂了都必须同步 —— 那里「父 +20 / 子 +10 两条
# 带相加」的读数与本文件的键形幅度靠同一组常数对账。
LATTICE = [[-100.0, -100.0], [100.0, -100.0], [-100.0, 100.0], [100.0, 100.0]]
SQUARE = [[-60.0, -60.0], [60.0, -60.0], [60.0, 60.0], [-60.0, 60.0]]
TRIS = [(0, 1, 2), (0, 2, 3)]
UVS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

SIDE_PX = 120.0              # 方块边长（模型像素）：渲染比例的标尺
BAND_PARAMETER = "ParamAngleZ"
BAND_KEY_FULL = 30.0         # 满幅键值（参数区间 ±30）
BAND_DX_MODEL_PX = 40.0      # 满幅键相对**默认键**的平移（模型像素）
AMPLITUDE_TOL_MODEL_PX = 2.0
OPAQUE_TOL_RATIO = 0.05      # 纯平移允许栅格化边缘抖动；撕裂/缩放会远超这条

# 判据的名字 = 简报点名的三条；三条都是闸门。
VERDICT_KEYS = ("chain_rest_matches_control",
                "chain_inherits_parent_band",
                "neutral_middle_level_invisible")
OBSERVATION_KEYS = ("identity_chain_neutral_rest",
                    "static_translation_moves_frame",
                    "chain_matches_flattened")


def _grid(points: Sequence[Sequence[float]]) -> list:
    """单键形控制网格：不随参数变化的**静态** warp 网格（旧判据用的就是它）。"""
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


def _rig(deformers, deformer_id: str) -> RigSpec:
    """一张 120px 的方块，可选地挂在某个变形器下；参数一律是那条 ±30 的 ParamAngleZ。

    参数在 `control_rig()` / `rest_rig()` 这类探针里没有绑定，声明它不改变画面，
    但让**所有**探针共用同一份参数表，读数里就不会混进「声明不同」这种无关差异。
    """
    mesh = MeshSpec("ArtMeshProbe", [list(v) for v in SQUARE], list(TRIS),
                    list(UVS), deformer_id=deformer_id)
    return RigSpec(
        meshes=[mesh], deformers=list(deformers),
        parameters=[ParameterSpec(BAND_PARAMETER, -BAND_KEY_FULL, BAND_KEY_FULL, 0.0)],
        canvas_width=CANVAS, canvas_height=CANVAS, pixels_per_unit=PPU)


# ----------------------------------------------------------------------
# 承重探针：父级带**相对默认键**的键形带，子级恒等，方块挂在子级上
# ----------------------------------------------------------------------

def band_parent(dx: float = BAND_DX_MODEL_PX) -> WarpDeformerSpec:
    """三键父级带：默认键 = 恒等点阵，±30 键 = 整张点阵相对默认键平移 ±dx 模型像素。

    这就是主干里父级的形状（BodySway 那种「随参数走的变形器」）。静止矩形取默认键
    那张，所以 dx 不会像静态平移那样自我抵消 —— 它是判据唯一看得见的量。
    """
    return WarpDeformerSpec(
        deformer_id="ProbeParent", rows=1, cols=1, parameter_id=BAND_PARAMETER,
        grids=[DeformerGrid(-BAND_KEY_FULL, _shift(LATTICE, -dx, 0.0)),
               DeformerGrid(0.0, [list(p) for p in LATTICE]),
               DeformerGrid(BAND_KEY_FULL, _shift(LATTICE, dx, 0.0))])


def identity_child(parent_id: str = "ProbeParent") -> WarpDeformerSpec:
    """恒等子级：主干要多加的那一层，静止与满幅都必须「等于不存在」。"""
    return WarpDeformerSpec(deformer_id="ProbeChild", rows=1, cols=1,
                            grids=_grid(LATTICE), parent_deformer_id=parent_id)


def band_chain_rig() -> RigSpec:
    """主干形状：方块挂在 恒等子级 下，子级挂在 带键形的父级 下。"""
    return _rig([band_parent(), identity_child()], "ProbeChild")


def band_parent_only_rig() -> RigSpec:
    """**拆掉恒等中间层**的同一个方块：直接挂在那个带键形的父级上。

    oracle 刻意不是「手工展平」：展平要算仿射复合，而内核做的是父级点阵的
    三角形分片线性重采样，两者在弯曲的父级上天生不等，那种 oracle 会造出假失败。
    拆层不算数学。
    """
    return _rig([band_parent()], "ProbeParent")


def control_rig() -> RigSpec:
    """同一张方块**不挂任何变形器** —— 「接这条链动不动画面」的基准帧。

    它就是旧的 `neutral_rig()`（#70 换名）：`chain_rest_matches_control` 与
    `identity_chain_neutral_rest` 共用这一帧当基准。
    """
    return _rig([], "")


# ----------------------------------------------------------------------
# 旧形式的静态探针（只作观察：静态整体平移在像素上是空操作）
# ----------------------------------------------------------------------

def chain_shift(parent_shift=(40.0, 0.0), child_shift=(0.0, 25.0)):
    """Task 1 的旧判据探针：两级点阵各自**静态**整体平移。

    实测（#70，官方 Core；父 +40 / 子 +25 这对静态点阵）：**画面一动不动**，与
    `rest_rig()` 逐像素相同。原因见模块 docstring —— 静态平移被自己的 (s, t) 归一
    抵消了。这条现在只作为 `static_translation_moves_frame` 的读数留在 JSON 里
    （它是「旧判据为什么必须换掉」的证据，不是判据）。
    """
    parent = WarpDeformerSpec(
        deformer_id="ProbeParent", rows=1, cols=1,
        grids=_grid(_shift(LATTICE, *parent_shift)))
    child = WarpDeformerSpec(
        deformer_id="ProbeChild", rows=1, cols=1,
        grids=_grid(_shift(LATTICE, *child_shift)),
        parent_deformer_id="ProbeParent")
    return _rig([parent, child], "ProbeChild")


def rest_rig():
    """两级都是恒等静态点阵 —— 「挂链本身中不中性」的静态版探针。

    实测结论（Task 1.5 修好嵌套空间基准之后）：**成立**。恒等父链的静止帧逐像素
    等于 `control_rig()`。Task 1 记的那条「放大 2× 并偏移、不透明像素 35344 对
    基准 8836」是**修复前**的缺陷行为（内核按父级 (s, t) 再映一次子级点阵，而那时
    写出去的还是模型像素），不是父链的固有性质。它同时是旧 `parent_only` 静态探针
    的比较基准。
    """
    ident = WarpDeformerSpec(deformer_id="ProbeParent", rows=1, cols=1,
                             grids=_grid(LATTICE))
    child = WarpDeformerSpec(deformer_id="ProbeChild", rows=1, cols=1,
                             grids=_grid(LATTICE), parent_deformer_id="ProbeParent")
    return _rig([ident, child], "ProbeChild")


def unparented_shift(parent_shift=(40.0, 0.0), child_shift=(0.0, 25.0)):
    """同样的两段位移，但拍平成**一个**没有父级的静态 warp（`chain_sha` 的对照）。

    修好空间基准后这条与 `chain_shift()` 逐像素相同 —— 两边都是**静态**平移，都被
    自己的 (s, t) 归一抵消，于是「拍平」与「挂链」在这对探针上同帧。这句只说明
    静态平移量在两条路线里都丢了，**不能**被读成「父链可省」：父链能不能省要问
    带键形那三条判据，而 `neutral_middle_level_invisible` 用的是拆层 oracle，不是
    这种展平。计划里 `chain_matches_flattened` 从头到尾只作观察，不作闸门。
    """
    merged = WarpDeformerSpec(
        deformer_id="ProbeChild", rows=1, cols=1,
        grids=_grid(_shift(LATTICE, parent_shift[0] + child_shift[0],
                                     parent_shift[1] + child_shift[1])))
    return _rig([merged], "ProbeChild")


# ----------------------------------------------------------------------
# 探针清单 / 渲染 / 判据计算
# ----------------------------------------------------------------------

def probe_rigs() -> Dict[str, Tuple[RigSpec, Dict[str, float]]]:
    """名字 -> (rig, 姿态)。名字与姿态都是判据的一部分，改任何一条都要同批改测试。"""
    full = {BAND_PARAMETER: BAND_KEY_FULL}
    neg = {BAND_PARAMETER: -BAND_KEY_FULL}
    return {
        "control": (control_rig(), {}),
        "chain_rest": (band_chain_rig(), {}),
        "chain_full": (band_chain_rig(), full),
        "chain_neg": (band_chain_rig(), neg),
        "no_middle_rest": (band_parent_only_rig(), {}),
        "no_middle_full": (band_parent_only_rig(), full),
        "no_middle_neg": (band_parent_only_rig(), neg),
        "identity_chain": (rest_rig(), {}),
        "static_chain": (chain_shift(), {}),
        "static_flat": (unparented_shift(), {}),
    }


PROBE_NAMES = tuple(probe_rigs())


def _chdir_into_tree_under_test() -> None:
    """`render_probe` 起的是 `python -m drivers...`，而 `drivers` 按 **cwd** 解析。

    从别的目录跑本工具会得到一条假的「渲染探针没有输出结果」，所以当前目录里没有
    这个模块时就先切进树。（工具与测试都从仓库根跑，对它们是零操作。）
    """
    marker = Path.cwd() / "drivers" / "live2d_runtime" / "moc3_render_probe.py"
    if not marker.is_file() and (ROOT / "drivers").is_dir():
        os.chdir(ROOT)


def render_probes(render=None) -> Dict[str, dict]:
    """**同一进程内**批量渲染所有探针：官方核一次约 7-15s，别一帧开一个进程。

    `render` 的默认值必须在**调用时**从模块全局取（`render_probe`），不能写进参数
    默认值：默认值在 def 时就绑死了，桩掉 `gate.render_probe` 的用例会继续真渲染。
    """
    render = render or render_probe
    _chdir_into_tree_under_test()
    rigs = probe_rigs()
    docs = {name: compile_static_rig(rig) for name, (rig, _pose) in rigs.items()}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        return {name: render(str(_package(root, docs[name], name)), rigs[name][1])
                for name in PROBE_NAMES}


def _rendered(result) -> bool:
    """探针真的出像素才算可用。

    `render_probe` 在**每条**非 ok 路径上把 `pixels_sha256` 留成空串
    （drivers/live2d_runtime/moc3_verify.py:203, 241-243），所以 ok 之外还要认
    sha 非空，否则两条空串一比就成了「相等」。
    """
    return bool(result and result.get("ok") and result.get("pixels_sha256"))


def _bbox_width(probe) -> int:
    box = probe["alpha_bbox"]
    return int(box[2]) - int(box[0]) + 1


def _render_scale(probes) -> float:
    """「渲染像素 / 模型像素」比例，从**静止对照帧**自己推：方块边长已知 SIDE_PX。

    不写死 400/512：`render_probe` 的窗口尺寸是有默认值的参数，一改它，整个幅度
    判据就会静默偏移；这条比例是量出来的，改窗口照样对。
    """
    return _bbox_width(probes["control"]) / SIDE_PX


def _model_px_shift(ref: Mapping, moved: Mapping, scale: float) -> Tuple[float, float]:
    """质心位移换算回模型像素。质心是浮点均值，比 bbox 边界少一个像素的量化误差。"""
    return ((moved["centroid"][0] - ref["centroid"][0]) / scale,
            (moved["centroid"][1] - ref["centroid"][1]) / scale)


def evaluate(rendered: Mapping[str, Mapping]) -> dict:
    """纯函数：探针统计 -> 三条判据 + 观察项 + 归因数字。**不碰 GPU。**

    拆出来是为了让「判据读哪条探针、幅度怎么算」这两件事能被桩数据钉住
    （见 tests/unit/test_deformer_parent_chain.py），不必为了验证接线再渲十帧。
    """
    given = dict(rendered or {})
    missing = [name for name in PROBE_NAMES if name not in given]
    probes = {name: given[name] for name in PROBE_NAMES if name in given}
    failed = [name for name in PROBE_NAMES if not _rendered(probes.get(name))]
    core_ok = not missing and not failed
    blocker = ""
    if not core_ok:
        blocker = next((str(probes[name].get("blocker")) for name in failed
                        if probes.get(name) and probes[name].get("blocker")), "")
        if not blocker:
            blocker = "缺少探针：" + ",".join(missing)

    out: Dict[str, object] = {
        "core_ok": core_ok,
        "blocker": blocker,
        "probe_names": list(PROBE_NAMES),
        "probes": {name: {
            "pixels_sha256": (probes[name].get("pixels_sha256") or ""),
            "opaque_pixels": probes[name].get("opaque_pixels"),
            "centroid": probes[name].get("centroid"),
            "alpha_bbox": probes[name].get("alpha_bbox"),
        } for name in probes},
        # 静态那两条观察值的旧键名留着（简报与历史报告按这两个名字引用证据）。
        "chain_sha": (probes.get("static_chain", {}).get("pixels_sha256") or ""),
        "flat_sha": (probes.get("static_flat", {}).get("pixels_sha256") or ""),
        "removed_keys": {
            "chain_applies_parent":
                "旧判据由「静态整体平移父级点阵」推出，实测是空操作（见 "
                "static_translation_moves_frame）；那个语义与「继承」无关，已删除。"
                "继承判据 = chain_inherits_parent_band（键形带驱动）。"},
        # 这两条**只作观察**，不是闸门（写成字段，免得下一个读 JSON 的人猜）：
        "static_translation_is_not_a_verdict": True,
        "chain_matches_flattened_is_not_a_gate": True,
    }
    if not core_ok:
        # Core 缺失/渲染失败时**不能**算这些：每条失败探针的 pixels_sha256 都是 ""，
        # "" == "" 会把「未验证」洗成绿灯，而 neutral_middle_level_invisible 正是
        # Task 2 的验收键。按计划的绑定规则，ok=false / blocker 一律记为未验证 = None。
        out.update({key: None for key in VERDICT_KEYS + OBSERVATION_KEYS})
        out.update({"render_scale": None, "band_amplitude_model_px": None,
                    "chain_full_shift_model_px": None,
                    "chain_neg_shift_model_px": None,
                    "opaque_pixels": None})
        return out

    sha = {name: probes[name]["pixels_sha256"] for name in PROBE_NAMES}
    scale = _render_scale(probes)
    full_shift = _model_px_shift(probes["chain_rest"], probes["chain_full"], scale)
    neg_shift = _model_px_shift(probes["chain_rest"], probes["chain_neg"], scale)
    rest_count = int(probes["chain_rest"]["opaque_pixels"])
    tolerance = rest_count * OPAQUE_TOL_RATIO

    def _amplitude_ok(shift, expected) -> bool:
        return (abs(shift[0] - expected) <= AMPLITUDE_TOL_MODEL_PX
                and abs(shift[1]) <= AMPLITUDE_TOL_MODEL_PX)

    def _count_ok(name) -> bool:
        return abs(int(probes[name]["opaque_pixels"]) - rest_count) <= tolerance

    out.update({
        "render_scale": round(scale, 6),
        "band_amplitude_model_px": BAND_DX_MODEL_PX,
        "chain_full_shift_model_px": [round(full_shift[0], 3), round(full_shift[1], 3)],
        "chain_neg_shift_model_px": [round(neg_shift[0], 3), round(neg_shift[1], 3)],
        "opaque_pixels": {name: int(probes[name]["opaque_pixels"])
                          for name in ("control", "chain_rest", "chain_full",
                                       "chain_neg", "no_middle_rest",
                                       "no_middle_full", "identity_chain",
                                       "static_chain")},
        # ---- 三条判据（全部来自官方 Core 像素）----
        # 1) 接上 父(键形带)→子(恒等) 这条链，静止帧仍然 == 不挂任何变形器。
        "chain_rest_matches_control": sha["chain_rest"] == sha["control"],
        # 2) 父级键形带到满幅时，子级身上的方块**跟着走**，走的正是那条带写出的
        #    幅度：两侧都 ±BAND_DX 模型像素、y 不动、面积不变。
        #    这条只读键形带那三条帧，读不到静态平移探针（变异对照 B 钉的就是这点）。
        "chain_inherits_parent_band": bool(
            sha["chain_full"] != sha["chain_rest"]
            and sha["chain_neg"] != sha["chain_rest"]
            and _amplitude_ok(full_shift, BAND_DX_MODEL_PX)
            and _amplitude_ok(neg_shift, -BAND_DX_MODEL_PX)
            and _count_ok("chain_full") and _count_ok("chain_neg")),
        # 3) 恒等中间层在每个姿态都「不存在」：拆层与不拆层逐像素相同。
        "neutral_middle_level_invisible": bool(
            sha["chain_rest"] == sha["no_middle_rest"]
            and sha["chain_full"] == sha["no_middle_full"]
            and sha["chain_neg"] == sha["no_middle_neg"]),
        # ---- 观察项（非闸门）----
        # 静态恒等两级链的静止帧是否等于对照：Task 2 的验收键，修复后为 true。
        "identity_chain_neutral_rest": sha["identity_chain"] == sha["control"],
        # 旧 chain_applies_parent 的**计算形式**：静态平移父级会不会让画面动。
        # 实测 false —— 这是「判据为什么必须换成键形带」的证据，不是「内核不求值
        # 父链」的证据。
        "static_translation_moves_frame": sha["static_chain"] != sha["identity_chain"],
        # 拍平 vs 挂链（静态那对）：计划明说只作观察。
        "chain_matches_flattened": sha["static_chain"] == sha["static_flat"],
    })
    return out


def main() -> dict:
    return evaluate(render_probes())


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
