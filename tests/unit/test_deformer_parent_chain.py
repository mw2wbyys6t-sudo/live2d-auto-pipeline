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
import hashlib
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

from live2d_builder.exporter.moc3_model import (  # noqa: E402
    DeformerGrid, MeshSpec, ParameterSpec, RigSpec, WarpDeformerSpec,
    compile_static_rig)


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


_RENDER_CACHE = {}


def _render(tmp_path, rig, name, params=None):
    """走官方 Core 的同一条渲染路径；Core 缺失时如实 skip，不当成通过。

    一次官方核渲染约 7s，所以同一份 (moc3 字节, 姿态) 全文件只求一次：Step 2/4/5
    的六条用例共用同一批对照帧。缓存键取产物字段的 sha256，不是用例名 —— 用例名
    相同但规格不同的时候必须重新渲。
    """
    doc = compile_static_rig(rig)
    key = (hashlib.sha256(doc.to_bytes()).hexdigest(),
           tuple(sorted((params or {}).items())))
    if key in _RENDER_CACHE:
        return _RENDER_CACHE[key]
    result = gate.render_probe(str(gate._package(tmp_path, doc, name)), params)
    if not result["ok"]:
        pytest.skip(f"官方 Core 不可用，父链未验证：{result.get('blocker')}")
    _RENDER_CACHE[key] = result
    return result


# ----------------------------------------------------------------------
# 主干形状的探针夹具：父级带键形、子级恒等、方块挂在子级下
# ----------------------------------------------------------------------
#
# Task 1.5 之前的这批探针用「把静止点阵整体平移」表达「父级动了」，那是无效的：
# 编译器用同一张静止矩形归一成员顶点，静态平移在静止帧里自抵消（实测：
# gate.chain_shift() 与 gate.rest_rig() 现在都逐像素等于不挂变形器的对照）。
# 主干里父级之所以能动，是因为它有**键形**：静止矩形不变、形变键形离开静止矩形。
# 所以下面这批探针一律用三键 ParamAngleZ 的父级，Step 5 的承重对照也建在同一套上。

_CANVAS = 512.0
_PPU = 100.0
_LATTICE = [[-100.0, -100.0], [100.0, -100.0], [-100.0, 100.0], [100.0, 100.0]]
_SQUARE = [[-60.0, -60.0], [60.0, -60.0], [-60.0, 60.0], [60.0, 60.0]]
_UVS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
_TRIS = [(0, 1, 2), (0, 2, 3)]
_SIDE_PX = 120.0          # 方块边长（画布像素），用来把渲染像素换算回模型像素


def _angle_param():
    return ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0)


def _shifted(points, dx, dy):
    return [[x + dx, y + dy] for x, y in points]


def _warp(deformer_id, points, parent=""):
    """静态恒等 warp（单控制网格）。"""
    return WarpDeformerSpec(deformer_id=deformer_id, rows=1, cols=1,
                            grids=[DeformerGrid(0.0, points)],
                            parent_deformer_id=parent)


def _keyform_parent(dx_full=40.0):
    """三键 ParamAngleZ 的父级：静止 = 恒等点阵，满幅 = 整体平移 ±dx_full 像素。"""
    return WarpDeformerSpec(
        deformer_id="Parent", rows=1, cols=1, parameter_id="ParamAngleZ",
        grids=[DeformerGrid(-30.0, _shifted(_LATTICE, -dx_full, 0.0)),
               DeformerGrid(0.0, _LATTICE),
               DeformerGrid(30.0, _shifted(_LATTICE, dx_full, 0.0))])


def _square_mesh(mesh_id, deformer_id, verts=None):
    return MeshSpec(mesh_id=mesh_id,
                    vertices=verts if verts is not None else _SQUARE,
                    triangles=_TRIS, uvs=list(_UVS), deformer_id=deformer_id)


def _rig(deformers, deformer_id, meshes=None, parameters=None):
    return RigSpec(meshes=meshes if meshes is not None else
                   [_square_mesh("ArtProbe", deformer_id)],
                   deformers=list(deformers),
                   parameters=parameters if parameters is not None else [_angle_param()],
                   canvas_width=_CANVAS, canvas_height=_CANVAS, pixels_per_unit=_PPU)


def chain_rig():
    """父（键形）→ 子（恒等）→ 方块：主干要的形状。"""
    return _rig([_keyform_parent(), _warp("Child", _LATTICE, "Parent")], "Child")


def no_middle_rig():
    """**拆掉中间恒等层**的同一个方块：只挂在那个带键形的父级上。

    Step 5 的对照。刻意不是「手工展平」：展平要算仿射复合，而双线性重采样不等于
    仿射复合，那种 oracle 会造出假失败。拆层不需要算任何数学。
    """
    return _rig([_keyform_parent()], "Parent")


def control_rig():
    """不挂任何变形器的同一张方块 —— 「挂链/挂层是不是中性」的基准。"""
    return _rig([], "")


def _model_px_delta(rest_bbox, moved_bbox, known_px):
    """把「渲染 bbox 左边界移动了多少」换算回模型像素。

    视图缩放从静止帧自己推：静止帧是边长 ``known_px`` 的正方形，渲染宽度 / 该边长
    就是比例，所以这条不依赖探针窗口尺寸。
    """
    scale = (rest_bbox[2] - rest_bbox[0] + 1) / known_px
    return ((moved_bbox[0] - rest_bbox[0]) / scale,
            (moved_bbox[1] - rest_bbox[1]) / scale)


@requires_live2d
def test_official_core_applies_warp_parent_chain(tmp_path):
    """带键形的父级必须把子级（以及子级身上的网格）一起带走 —— 主干的前提。

    静止帧就是 `control_rig()` 的那一帧（Step 2 的恒等链用例钉住），所以这里
    「画面动了」只可能来自父级键形被逐级求值：子级是恒等的、网格顶点没动。
    """
    rest = _render(tmp_path, chain_rig(), "kf_rest")
    control = _render(tmp_path, control_rig(), "kf_control")
    moved = _render(tmp_path, chain_rig(), "kf_full", {"ParamAngleZ": 30.0})
    assert rest["pixels_sha256"] == control["pixels_sha256"], (
        "带键形的父链在静止上就不等于无变形器了")
    assert moved["pixels_sha256"] != rest["pixels_sha256"], \
        "父级没被求值：分层主干不成立"
    assert moved["pixels_sha256"] != control["pixels_sha256"], \
        "满幅帧仍然等于静止：父级键形没有逐级求值"


@requires_live2d
def test_parent_shift_only_moves_the_frame(tmp_path):
    """单变量证据：只有父级键形动、子级恒等 —— 画面整体平移，幅度按键形线性。

    Task 1.5 之前这条用的是「把根级静止点阵整体平移」，而静态平移被编译器自己的
    (s, t) 归一精确抵消（实测 chain_shift 与 rest_rig 逐像素相同），所以那个探针
    证的其实是空间基准错了、不是父链被求值。现在改成键形驱动，并把幅度也钉住：
    ``ParamAngleZ=12`` 是满幅的 0.4 倍 ⇒ 平移 16 画布像素，``y`` 不许动，
    像素数不许变（撕裂/形变都会改掉不透明像素数）。
    """
    rest = _render(tmp_path, chain_rig(), "rest_parent")
    moved = _render(tmp_path, chain_rig(), "parent_only", {"ParamAngleZ": 12.0})
    assert moved["pixels_sha256"] != rest["pixels_sha256"], \
        "父级位移没有作用到子级：Core 不求值 warp 父链，分层主干不成立"
    dx, dy = _model_px_delta(rest["alpha_bbox"], moved["alpha_bbox"], _SIDE_PX)
    assert dx == pytest.approx(16.0, abs=2.0), (rest["alpha_bbox"], moved["alpha_bbox"])
    assert dy == pytest.approx(0.0, abs=2.0)
    # 纯平移：沿 x 正向移动、y 不动、面积不变
    assert moved["centroid"][0] > rest["centroid"][0] + 10, (
        rest["centroid"], moved["centroid"])
    assert abs(moved["centroid"][1] - rest["centroid"][1]) < 1.0, (
        rest["centroid"], moved["centroid"])
    assert abs(moved["opaque_pixels"] - rest["opaque_pixels"]) \
        < rest["opaque_pixels"] * 0.02, (rest["opaque_pixels"], moved["opaque_pixels"])
    full = _render(tmp_path, chain_rig(), "parent_full", {"ParamAngleZ": 30.0})
    dx_full, _dy = _model_px_delta(rest["alpha_bbox"], full["alpha_bbox"], _SIDE_PX)
    assert dx_full == pytest.approx(40.0, abs=2.0), (dx_full, dx)


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
    （`chain_sha` / `flat_sha`）留作证据。

    Task 1.5 的读数变化要记在这里：这两条探针都是**静态**平移，修好空间基准后
    两边都逐像素等于无变形器（`chain_sha == flat_sha`）—— 静态平移在单级里本来
    就被自己的 (s, t) 归一抵消，拍平与挂链于是同帧。「父级带动子级」的证据在
    `test_official_core_applies_warp_parent_chain`（键形驱动）与 Step 5 那条。
    """
    chain = _render(tmp_path, gate.chain_shift(), "chain_flat_cmp")
    flat = _render(tmp_path, gate.unparented_shift(), "flat")
    assert chain["pixels_sha256"], "对照用的 chain 没渲染出像素"
    assert flat["pixels_sha256"], "对照用的 flat 没渲染出像素"


@requires_live2d
def test_attaching_an_identity_chain_leaves_the_rest_frame_alone(tmp_path):
    """挂两级恒等链不许改变静止画面 —— 分层主干的前提。

    Task 1 留的 `xfail(strict=True)` 就是这条：官方 Core 把子级点阵按父级 (s, t)
    再映一次，静止帧被线性放大 2× 并朝父矩形最小角偏移（实测不透明像素
    35344 vs 基准 8836，面积比 4.0）。Task 1.5 按规则 I 修正编译器的空间基准后，
    这里必须是普通断言：恒等 = 逐像素等于「不挂任何变形器」。
    """
    baseline = _render(tmp_path, gate.neutral_rig(), "neutral")
    rest = _render(tmp_path, gate.rest_rig(), "rest_neutral")
    assert baseline["pixels_sha256"] == rest["pixels_sha256"], (
        f"恒等父链移动了静止帧：基准 {baseline['alpha_bbox']} / "
        f"挂链 {rest['alpha_bbox']}（不透明像素 "
        f"{baseline['opaque_pixels']} vs {rest['opaque_pixels']}）")


# ----------------------------------------------------------------------
# Step 4：诊断里已证、但还没进仓库的三种形态（每种一次渲染）
# ----------------------------------------------------------------------

def three_level_rig():
    """三级恒等链（L0 根 → L1 → L2，都是 ±100 恒等点阵），方块挂在最深一级。"""
    return _rig([_warp("L0", _LATTICE), _warp("L1", _LATTICE, "L0"),
                 _warp("L2", _LATTICE, "L1")], "L2")


def wider_parent_rig():
    """父矩形比子级宽（父 ±150 / 子 ±100）。

    这条专门排「pixels_per_unit 被乘两次」和「父级 rows/cols 差一」：两者都会
    随父矩形大小改变比例或留下常数偏差，恒等链因此不可能等于对照。
    """
    wide = [[x, y] for x, y in
            [(-150.0, -150.0), (150.0, -150.0), (-150.0, 150.0), (150.0, 150.0)]]
    return _rig([_warp("L0", wide), _warp("L1", _LATTICE, "L0")], "L1")


_SQUARE_TOP_LEFT = [[x, y] for x, y in
                    [(-110.0, 50.0), (-50.0, 50.0), (-110.0, 110.0), (-50.0, 110.0)]]
_SQUARE_BOTTOM_RIGHT = [[x, y] for x, y in
                        [(50.0, -110.0), (110.0, -110.0), (50.0, -50.0), (110.0, -50.0)]]


def parent_with_members_rig():
    """父级**自带成员网格**（ArtA），同时又是子变形器的父级（ArtB 挂在子级下）。

    排掉闸门担心的那条：「父级没有成员网格 ⇒ reference_rect 退化」。
    """
    root = [[x, y] for x, y in
            [(-150.0, -150.0), (150.0, -150.0), (-150.0, 150.0), (150.0, 150.0)]]
    child = [[x, y] for x, y in
             [(30.0, -130.0), (130.0, -130.0), (30.0, -30.0), (130.0, -30.0)]]
    meshes = [_square_mesh("ArtA", "L0", _SQUARE_TOP_LEFT),
              _square_mesh("ArtB", "L1", _SQUARE_BOTTOM_RIGHT)]
    return _rig([_warp("L0", root), _warp("L1", child, "L0")], "", meshes=meshes)


def parent_with_members_control_rig():
    """同样两张方块，都不挂变形器。"""
    meshes = [_square_mesh("ArtA", "", _SQUARE_TOP_LEFT),
              _square_mesh("ArtB", "", _SQUARE_BOTTOM_RIGHT)]
    return _rig([], "", meshes=meshes)


@requires_live2d
def test_a_three_level_identity_chain_leaves_the_rest_frame_alone(tmp_path):
    """三级恒等链的静止帧 == 不挂变形器（诊断 R4 已证，仓库里还没有这条）。"""
    control = _render(tmp_path, control_rig(), "neutral_three_level")
    rest = _render(tmp_path, three_level_rig(), "three_level")
    assert rest["pixels_sha256"] == control["pixels_sha256"], (
        f"三级恒等链移动了静止帧：基准 {control['alpha_bbox']} / "
        f"挂链 {rest['alpha_bbox']}（不透明像素 {control['opaque_pixels']} vs "
        f"{rest['opaque_pixels']}）")


@requires_live2d
def test_an_identity_chain_under_a_wider_parent_rect_is_still_neutral(tmp_path):
    """父矩形与子级不同尺寸（父 ±150、子 ±100）时恒等链仍然 == 对照。

    这条是「pixels_per_unit 被乘两次 / 父级行列差一格」的排除证据：那两种机制
    都会把偏差留成随父矩形大小改变的比例（200/300 之类），静止帧不可能重合。
    """
    control = _render(tmp_path, control_rig(), "neutral_wider_parent")
    rest = _render(tmp_path, wider_parent_rig(), "wider_parent")
    assert rest["pixels_sha256"] == control["pixels_sha256"], (
        f"不同宽度的父矩形让恒等链不恒等了：基准 {control['alpha_bbox']} / "
        f"挂链 {rest['alpha_bbox']}")
    dx, dy = _model_px_delta(control["alpha_bbox"], rest["alpha_bbox"], _SIDE_PX)
    assert (dx, dy) == (0.0, 0.0)


@requires_live2d
def test_a_parent_with_its_own_member_mesh_still_leaves_rest_alone(tmp_path):
    """父级自带成员网格时，「挂法改变」仍然不改变静止帧。"""
    control = _render(tmp_path, parent_with_members_control_rig(), "members_control")
    rest = _render(tmp_path, parent_with_members_rig(), "parent_with_members")
    assert rest["pixels_sha256"] == control["pixels_sha256"], (
        f"父级带成员网格时挂链改变了静止帧：基准 {control['alpha_bbox']} / "
        f"挂链 {rest['alpha_bbox']}（不透明像素 {control['opaque_pixels']} vs "
        f"{rest['opaque_pixels']}）")


# ----------------------------------------------------------------------
# Step 5：唯一还没测过的东西 —— 带键形的继承
# ----------------------------------------------------------------------

@requires_live2d
def test_a_neutral_middle_level_is_invisible_at_every_pose(tmp_path):
    """同一方块：挂 父(3 键 ParamAngleZ 带)→子(恒等) 与 只挂 父，必须逐像素相同。

    三个姿态各比一次：默认键（静止）、一个非默认键（部分跟随）、满幅键（两侧也各
    一次）。静止还必须等于「不挂任何变形器」的 SHA —— 那是「恒等层不额外挪东西」
    的另一半。

    对照刻意用**拆掉中间层**而不是手工展平：展平要算仿射复合，而内核做的是父级
    点阵的双线性重采样，两者在弯曲的父级上天生不等 —— 拿展平当 oracle 会造出
    假失败。拆层不需要算任何数学，中间层若是中性的就必须逐像素相同。

    这条是主干（父级带动子级）的承重 claim：诊断的十个 rig 全是静态的，只有
    静态恒等链成立，键形驱动的继承在这里第一次被测。
    """
    control = _render(tmp_path, control_rig(), "inherit_control")
    for pose in ({}, {"ParamAngleZ": 12.0}, {"ParamAngleZ": 30.0},
                 {"ParamAngleZ": -30.0}):
        with_middle = _render(tmp_path, chain_rig(), "inherit_chain", pose)
        without_middle = _render(tmp_path, no_middle_rig(), "inherit_no_middle", pose)
        assert with_middle["pixels_sha256"] == without_middle["pixels_sha256"], (
            f"姿态 {pose or '默认'}：中间恒等层改变了画面 —— "
            f"带 {with_middle['alpha_bbox']} / 不带 {without_middle['alpha_bbox']}，"
            f"不透明像素 {with_middle['opaque_pixels']} vs "
            f"{without_middle['opaque_pixels']}。"
            "键形混合的空间约定还有一条没对齐，停下来报告，不要调常数")
        if not pose:
            assert with_middle["pixels_sha256"] == control["pixels_sha256"], (
                "恒等层在静止帧上就不中性：基准与挂链的 alpha_bbox 分别是 "
                f"{control['alpha_bbox']} / {with_middle['alpha_bbox']}")
    full = _render(tmp_path, chain_rig(), "inherit_chain", {"ParamAngleZ": 30.0})
    assert full["pixels_sha256"] != control["pixels_sha256"], (
        "满幅帧与静止逐像素相同 —— 父级键形根本没接到子级上，上面三条比的是两个"
        "都没动的画面，对照退化")
    dx, _dy = _model_px_delta(control["alpha_bbox"], full["alpha_bbox"], _SIDE_PX)
    assert dx == pytest.approx(40.0, abs=2.0), dx

