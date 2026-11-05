"""父子 warp 链必须由官方 Core 逐级合成 —— 分层主干的全部前提。

产品里从来没写过 parent（真实产物 13/13 变形器 parent 为空），所以这条链
在本地产物上是**未验证**能力。这个测试把它钉成闸门：Core 不求值父级，
分层主干方案就不成立。

两类用例分开：
- 需要官方 Core 渲染的用例带 `requires_live2d`，Core 缺失时**如实 skip**
  （skip = 未验证，不当成通过）；
- 两条纯逻辑用例（判据在 Core 不可用时必须是 None、`chain_applies_parent`
  必须读 parent_only 探针）桩掉 `render_probe`，任何机器都会跑，
  「降级为未验证」这条本身因此也是被钉住的，不依赖本机有没有 GL。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

# tools/ 不是包，门脚本只能靠上面的 sys.path.insert 取到，所以这行 import 必须
# 排在路径设置之后（PEP8 E402 豁免，仓库惯例同上 test_lint_architecture）。
# 全文件只用这一个模块对象 `gate`：可调用性检查与像素用例 certify 的是同一份模块，
# 不再出现 tools.deform_trunk_probe_gate / deform_trunk_probe_gate 两个模块对象。
import deform_trunk_probe_gate as gate  # noqa: E402


def _has_live2d() -> bool:
    """只看 live2d-py 装没装（find_spec，不导入不渲染，收集阶段零 GPU 成本）。"""
    try:
        return importlib.util.find_spec("live2d.v3") is not None
    except (ImportError, ValueError):
        return False


# 复用 tests/integration/test_moc3_keyform_acceptance.py:45 的 requires_live2d 写法。
# 刻意**不**套用同一文件里的 requires_pixels（要 LIVE2D_TEST_PIXELS=1 才跑）：
# 门是闸门，默认关掉等于本任务白做，Core 可用的机器必须真跑。
requires_live2d = pytest.mark.skipif(
    not _has_live2d(), reason="需要官方 Cubism Core（live2d-py）才能验证 warp 父链")


def test_parent_chain_module_exposes_probe():
    """门脚本必须真的可调用，不能只写死一段 main。

    ``tools/`` 不是包；仓库里 import 脚本目录的既有做法是手动 sys.path.insert
    （见 ``tests/unit/test_lint_architecture.py``），所以上面先把 tools 加进去。
    """
    assert callable(gate.main)


def _unavailable_probe(manifest_path, *args, **kwargs):
    """Core 缺失时 `render_probe` 的真实形状：ok=false 且 pixels_sha256 为空串。

    见 drivers/live2d_runtime/moc3_verify.py:203, 241-243 —— 每条非 ok 路径都把
    pixels_sha256 留成 ""，「空串 == 空串」正是假绿的来源。
    """
    return {"ok": False, "pixels_sha256": "", "blocker": "simulated missing Cubism Core",
            "alpha_bbox": None, "opaque_pixels": 0, "centroid": None}


def _package_name(manifest_path) -> str:
    """`rest.model3.json` 取回 `rest`（Path.stem 会留下 `.model3`，所以按点切第一段）。"""
    return Path(manifest_path).name.split(".")[0]


def _stubbed_probe(shas):
    """按包名返回预设 sha 的假渲染：只验判据的**接线**，不花一次 GPU。"""
    def probe(manifest_path, *args, **kwargs):
        return {"ok": True, "pixels_sha256": shas[_package_name(manifest_path)],
                "blocker": None, "alpha_bbox": [0, 0, 10, 10],
                "opaque_pixels": 100, "centroid": [5.0, 5.0]}
    return probe


def test_gate_reports_unverified_when_core_is_unavailable(monkeypatch):
    """Core 不可用时派生判据必须是 None（未验证），不能是 "" == "" 得出的 true。

    变异控制：去掉 tools/deform_trunk_probe_gate.py 里的 core_ok 守卫、让三条判据
    无条件比较，`chain_applies_parent` 与 `identity_chain_neutral_rest` 会双双算成
    true，本用例立刻红。
    """
    monkeypatch.setattr(gate, "render_probe", _unavailable_probe)

    out = gate.main()

    assert out["core_ok"] is False
    assert "simulated missing Cubism Core" in out["blocker"]
    assert out["chain_applies_parent"] is None
    assert out["chain_matches_flattened"] is None
    assert out["identity_chain_neutral_rest"] is None
    # 再显式钉一遍「不是 true」：identity_chain_neutral_rest 是 Task 2 的验收键，
    # 无 Core 的运行只能读到「未验证」，绝不能读到绿灯。
    assert out["chain_applies_parent"] is not True
    assert out["identity_chain_neutral_rest"] is not True


def test_gate_reads_the_parent_only_probe_for_its_headline(monkeypatch):
    """`chain_applies_parent` 只能由「只有父级位移」那条探针推出，不能由 chain 推。

    桩数据刻意违反物理规律（真实 Core 下子级平移被自身 (s, t) 换算抵消，
    chain 与 parent_only 同帧），为的是把两种接法区分开：
    - 场景 A：parent_only 变了、chain 没变 → 读 parent_only 得 true，读 chain 得 false；
    - 场景 B：parent_only 没变、chain 变了 → 读 parent_only 得 false，读 chain 得 true。
    两条合起来才能同时杀掉「拿 chain_sha 比 rest_sha」这个变异体。
    """
    monkeypatch.setattr(gate, "render_probe", _stubbed_probe(
        {"neutral": "n", "rest": "r", "parent_only": "p", "chain": "r", "flat": "f"}))
    only_parent_moves = gate.main()
    assert only_parent_moves["core_ok"] is True
    assert only_parent_moves["parent_only_sha"] == "p"
    assert only_parent_moves["chain_applies_parent"] is True, (
        "chain_applies_parent 读的是 chain_sha 而不是 parent_only_sha："
        "父级求值和子级位移混在一起，判据不可归因")

    monkeypatch.setattr(gate, "render_probe", _stubbed_probe(
        {"neutral": "n", "rest": "r", "parent_only": "r", "chain": "p", "flat": "f"}))
    only_child_moves = gate.main()
    assert only_child_moves["chain_applies_parent"] is False, (
        "只有子级位移时判据仍为 true —— 说明它没有读 parent_only 这条单变量探针")


def _render(tmp_path, rig, name):
    """走官方 Core 的同一条渲染路径；Core 缺失时如实 skip，不当成通过。"""
    result = gate.render_probe(str(gate._package(
        tmp_path, gate.compile_static_rig(rig), name)))
    if not result["ok"]:
        pytest.skip(f"官方 Core 不可用，父链未验证：{result.get('blocker')}")
    return result


@requires_live2d
def test_official_core_applies_warp_parent_chain(tmp_path):
    rest = _render(tmp_path, gate.rest_rig(), "rest")
    chain = _render(tmp_path, gate.chain_shift(), "chain")
    assert chain["pixels_sha256"] != rest["pixels_sha256"], \
        "父级没被求值：分层主干不成立"


@requires_live2d
def test_parent_shift_only_moves_the_frame(tmp_path):
    """单变量证据：只有父级位移、子级恒等 —— 画面必须整体平移。

    网格顶点被编译器按子级静止形换算成 (s, t)，子级自己的平移会被 (s, t) 换算
    抵消（见 tests/integration/test_moc3_deformer_acceptance.py 的恒等对照），
    所以「画面动了」只可能来自父级被逐级求值。这条就是门里
    `chain_applies_parent` 现在读的 parent_only 探针。
    """
    rest = _render(tmp_path, gate.rest_rig(), "rest_parent")
    moved = _render(tmp_path, gate.chain_shift(parent_shift=(40.0, 0.0),
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


@requires_live2d
def test_child_shift_without_parent_motion_is_the_negative_control(tmp_path):
    """负对照：子级自己平移、父级恒等时画面**不许**变化。

    这条钉住「链上一有位移就动」的误读 —— 上一轮的差异必须能归因到父级求值，
    而不是编译产物里的噪声。子级网格的平移被它自己的 (s, t) 换算精确抵消，
    静止帧因此逐像素相同。
    """
    rest = _render(tmp_path, gate.rest_rig(), "rest_control")
    child_only = _render(tmp_path, gate.chain_shift(parent_shift=(0.0, 0.0),
                                                    child_shift=(0.0, 25.0)),
                         "child_only")
    assert child_only["pixels_sha256"] == rest["pixels_sha256"], (
        "恒等父级下的子级平移改变了画面 —— 静止形/参考矩形被算歪了")


@requires_live2d
def test_flattened_chain_is_recorded_not_gated(tmp_path):
    """拍平单级 vs 父子链**只作观察**：计划明说 `chain_matches_flattened` 不作闸门。

    所以这里刻意不比两条 sha 的方向 —— 哪天拍平路线真的等价了（那意味着可以不走
    父链），本用例也不该变红。两条 sha 由 `gate.main()` 如实写进 JSON
    （`chain_sha` / `flat_sha`）留作证据，实测目前不相等。
    """
    chain = _render(tmp_path, gate.chain_shift(), "chain_flat_cmp")
    flat = _render(tmp_path, gate.unparented_shift(), "flat")
    assert chain["pixels_sha256"], "对照用的 chain 没渲染出像素"
    assert flat["pixels_sha256"], "对照用的 flat 没渲染出像素"


@requires_live2d
@pytest.mark.xfail(strict=True, reason=(
    "实测：两级恒等链的静止帧 != 不挂变形器的同一画面（官方 Core 把子级点阵按父级 "
    "(s, t) 再映一次，画面被 2× 放大并偏移：不透明像素 35344 vs 基准 8836）。"
    "Task 2 接线前必须先定父链的坐标基准，否则主干一挂上画面就炸。"
    "strict=True 是有意的：约定修好后本用例 XPASS 会**报错**，逼着删掉标记，"
    "否则 Task 2 的验收键会悄悄失效。"))
def test_attaching_an_identity_chain_leaves_the_rest_frame_alone(tmp_path):
    """挂链不许改变静止画面 —— 分层主干的前提，目前是**未成立**的。"""
    baseline = _render(tmp_path, gate.neutral_rig(), "neutral")
    rest = _render(tmp_path, gate.rest_rig(), "rest_neutral")
    assert baseline["pixels_sha256"] == rest["pixels_sha256"], (
        f"恒等父链移动了静止帧：基准 {baseline['alpha_bbox']} / "
        f"挂链 {rest['alpha_bbox']}")

