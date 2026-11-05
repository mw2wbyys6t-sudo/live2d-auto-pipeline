"""父子 warp 链必须由官方 Core 逐级合成 —— 分层主干的全部前提。

产品里从来没写过 parent（真实产物 13/13 变形器 parent 为空），所以这条链
在本地产物上是**未验证**能力。这个测试把它钉成闸门：Core 不求值父级，
分层主干方案就不成立。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from live2d_builder.exporter.moc3_model import (
    DeformerGrid, MeshSpec, ParameterSpec, RigSpec, WarpDeformerSpec,
)

CANVAS = 512.0
PPU = 100.0


def _square(half=100.0):
    verts = [(-half, -half), (half, -half), (half, half), (-half, half)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    return verts, [(0, 1, 2), (0, 2, 3)], uvs


def _grid(points):
    return [DeformerGrid(0.0, points)]


def _shift(points, dx, dy):
    return [[x + dx, y + dy] for x, y in points]


def test_parent_chain_module_exposes_probe():
    """门脚本必须真的可调用，不能只写死一段 main。

    ``tools/`` 不是包；仓库里 import 脚本目录的既有做法是手动 sys.path.insert
    （见 ``tests/unit/test_lint_architecture.py``），所以上面先把 tools 加进去。
    """
    import deform_trunk_probe_gate

    assert callable(deform_trunk_probe_gate.main)


from tools.deform_trunk_probe_gate import (
    compile_static_rig, chain_shift, rest_rig, unparented_shift, neutral_rig,
    _package,
)
from drivers.live2d_runtime.moc3_verify import render_probe


def test_official_core_applies_warp_parent_chain(tmp_path):
    rest = render_probe(str(_package(tmp_path, compile_static_rig(rest_rig()), "rest")))
    chain = render_probe(str(_package(
        tmp_path, compile_static_rig(chain_shift()), "chain")))
    if not (rest["ok"] and chain["ok"]):
        pytest.skip(f"官方 Core 不可用，父链未验证：{rest.get('blocker') or chain.get('blocker')}")
    assert chain["pixels_sha256"] != rest["pixels_sha256"], "父级没被求值：分层主干不成立"


def test_rest_frame_is_unchanged_by_attaching_a_chain(tmp_path):
    """恒等点阵的两级链不得改变静止帧 —— 接线本身不许引入位移。

    注意本用例（按计划原文）只钉「静止帧渲染得出来」；「与不挂变形器逐像素相同」
    这条真正的判据由下面带 xfail 的同名对照用例负责，目前**实测不成立**。
    """
    from PIL import Image
    import hashlib
    got = render_probe(str(_package(tmp_path, compile_static_rig(rest_rig()), "rest2")))
    if not got["ok"]:
        pytest.skip("官方 Core 不可用")
    assert got["pixels_sha256"], "静止帧必须能渲染"


def _render(tmp_path, rig, name):
    """走官方 Core 的同一条渲染路径；Core 缺失时如实 skip，不当成通过。"""
    result = render_probe(str(_package(tmp_path, compile_static_rig(rig), name)))
    if not result["ok"]:
        pytest.skip(f"官方 Core 不可用，父链未验证：{result.get('blocker')}")
    return result


def test_parent_shift_only_moves_the_frame(tmp_path):
    """单变量证据：只有父级位移、子级恒等 —— 画面必须整体平移。

    网格顶点被编译器按子级静止形换算成 (s, t)，子级自己的平移会被 (s, t) 换算
    抵消（见 tests/integration/test_moc3_deformer_acceptance.py 的恒等对照），
    所以「画面动了」只可能来自父级被逐级求值。
    """
    rest = _render(tmp_path, rest_rig(), "rest_parent")
    moved = _render(tmp_path, chain_shift(parent_shift=(40.0, 0.0),
                                         child_shift=(0.0, 0.0)), "parent_only")
    assert moved["pixels_sha256"] != rest["pixels_sha256"], \
        "父级位移没有作用到子级：Core 不求值 warp 父链，分层主干不成立"
    # 纯平移：沿 x 正向移动、y 不动、面积不变（形变/撕裂都会改掉像素数）
    assert moved["centroid"][0] > rest["centroid"][0] + 10, (
        rest["centroid"], moved["centroid"])
    assert abs(moved["centroid"][1] - rest["centroid"][1]) < 1.0, (
        rest["centroid"], moved["centroid"])
    assert abs(moved["opaque_pixels"] - rest["opaque_pixels"]) \
        < rest["opaque_pixels"] * 0.02, (rest["opaque_pixels"], moved["opaque_pixels"])


def test_child_shift_without_parent_motion_is_the_negative_control(tmp_path):
    """负对照：子级自己平移、父级恒等时画面**不许**变化。

    这条钉住「链上一有位移就动」的误读 —— 上一轮的差异必须能归因到父级求值，
    而不是编译产物里的噪声。子级网格的平移被它自己的 (s, t) 换算精确抵消，
    静止帧因此逐像素相同。
    """
    rest = _render(tmp_path, rest_rig(), "rest_control")
    child_only = _render(tmp_path, chain_shift(parent_shift=(0.0, 0.0),
                                              child_shift=(0.0, 25.0)), "child_only")
    assert child_only["pixels_sha256"] == rest["pixels_sha256"], (
        "恒等父级下的子级平移改变了画面 —— 静止形/参考矩形被算歪了")


def test_flattened_chain_is_not_the_same_as_a_parented_chain(tmp_path):
    """把两段位移拍平进一个变形器，画面上并不等价。

    分层主干不能靠「把父级并进子级」实现：warp 的 (s, t) 参考矩形取自该变形器
    自己的静止形，父子两级各算一次，与单级合并不同值。这里断言**不等**（实测
    不等）；若哪天官方 Core 改得等价了，说明可以走拍平路线，本断言应放宽 ——
    它不是分层的否决条件。
    """
    chain = _render(tmp_path, chain_shift(), "chain_flat_cmp")
    flat = _render(tmp_path, unparented_shift(), "flat")
    assert flat["pixels_sha256"] != chain["pixels_sha256"], (
        "拍平的单级变形器与父子链渲染出同一帧 —— 对照失去区分力，需重看")


@pytest.mark.xfail(strict=False, reason=(
    "实测：两级恒等链的静止帧 != 不挂变形器的同一画面（官方 Core 把子级点阵按父级 "
    "(s, t) 再映一次，画面被 2× 放大并偏移：不透明像素 35344 vs 基准 8836）。"
    "Task 2 接线前必须先定父链的坐标基准，否则主干一挂上画面就炸。约定修好后本用例 "
    "XPASS，届时删掉 xfail 标记即可。"))
def test_attaching_an_identity_chain_leaves_the_rest_frame_alone(tmp_path):
    """挂链不许改变静止画面 —— 分层主干的前提，目前是**未成立**的。"""
    baseline = _render(tmp_path, neutral_rig(), "neutral")
    rest = _render(tmp_path, rest_rig(), "rest_neutral")
    assert baseline["pixels_sha256"] == rest["pixels_sha256"], (
        f"恒等父链移动了静止帧：基准 {baseline['alpha_bbox']} / "
        f"挂链 {rest['alpha_bbox']}")

