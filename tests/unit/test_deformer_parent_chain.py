"""父子 warp 链必须由官方 Core 逐级合成 —— 分层主干的全部前提。

产品里从来没写过 parent（真实产物 13/13 变形器 parent 为空），所以这条链
在本地产物上是**未验证**能力。这个测试把它钉成闸门：Core 不求值父级，
分层主干方案就不成立。

判据的形状（#70 换过一次，别再换回去）：**父级点阵带一条相对默认键的键形带**
（`ParamAngleZ` 三键 −30 / 0 / +30，满幅键 = 整张点阵平移 ±40 模型像素），子级
恒等，方块挂在子级上。不能用「把静止点阵整体平移」表达「父级动了」：编译器按
`reference_rect(deformer)`（取的就是默认键那张）把成员顶点归一成 (s, t)，静态
平移连同矩形一起动，归一之后 dx 自我抵消 ⇒ 画面一动不动。这条在根级于 Task 1.5
修复前后**都**成立（+10px 的 x 与 y 位移实测 Δ=0.00）；Task 1 那条
`chain_applies_parent=true` 能读到位移，靠的正是修复前的缺陷 —— 那时子级不被父级
矩形重归一。所以闸门现在把静态平移如实降级成观察项
`static_translation_moves_frame`（实测 false），继承判据叫
`chain_inherits_parent_band`，由键形带的像素**与幅度**一起支撑。

三类用例分开：
- 需要官方 Core 渲染的用例带 `requires_live2d`，Core 缺失时**如实 skip**
  （skip = 未验证，不当成通过）；
- 桩掉 `render_probe` / 直接喂合成读数的纯逻辑用例（Core 不可用时判据必须是
  None、每条判据读哪条探针、幅度算不算得对、静态观察值怎么记录）任何机器都跑，
  「降级为未验证」这条本身因此也是被钉住的，不依赖本机有没有 GL；
- 承重夹具（点阵 / 方块 / 键形父级 / 恒等子级）一律取自闸门本身
  （`gate.band_chain_rig()` 等）：尺子只有一份，而且本文件的渲染缓存与闸门读的是
  同一批产物字节。
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


def test_parent_chain_module_exposes_the_three_verdicts():
    """门脚本必须真的可调用，而且发布的正是简报点名的那三条判据。

    ``tools/`` 不是包；仓库里 import 脚本目录的既有做法是手动 sys.path.insert
    （见 ``tests/unit/test_lint_architecture.py``），所以上面先把 tools 加进去。
    判据名字是对外契约（Task 2 与报告都按这三个名字读 JSON），漂了就一起红。
    """
    assert callable(gate.main)
    assert callable(gate.evaluate)
    assert callable(gate.probe_rigs)
    assert gate.VERDICT_KEYS == ("chain_rest_matches_control",
                                 "chain_inherits_parent_band",
                                 "neutral_middle_level_invisible"), (
        "三条判据的名字/数量与简报不一致：Task 2 按这三个名字读闸门 JSON")
    assert "chain_applies_parent" not in gate.VERDICT_KEYS, (
        "旧判据由静态整体平移推出（空操作），#70 已经删掉它；换回来就是继续骗人")


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


# ----------------------------------------------------------------------
# 合成读数：给 `gate.evaluate` 喂「与实测同量纲」的假探针，验接线不花一次 GPU
# ----------------------------------------------------------------------
#
# 三条静止数字（bbox [153,203,246,296]、质心 [199.5,249.5]、不透明 8836）是
# tools/deform_trunk_probe_gate.py 在官方 Core 上的实测读数，不是随手编的：
# 桩要能被真机读数替换，量纲就必须一样。
_CONTROL_BBOX = [153, 203, 246, 296]
_CONTROL_CENTROID = [199.5, 249.5]
_CONTROL_OPAQUE = 8836
_RENDER_SCALE = (_CONTROL_BBOX[2] - _CONTROL_BBOX[0] + 1) / gate.SIDE_PX


def _frame(tag, dx=0.0, dy=0.0, opaque=_CONTROL_OPAQUE):
    """一条「方块平移过」的假探针：几何按渲染比例换算，`tag` 当像素身份。

    `dx` / `dy` 用**模型像素**（与判据的口径一致），换算成渲染像素在这条里做。
    """
    dx_pixels = dx * _RENDER_SCALE
    dy_pixels = dy * _RENDER_SCALE
    return {"ok": True, "blocker": None, "pixels_sha256": tag,
            "opaque_pixels": opaque,
            "alpha_bbox": [_CONTROL_BBOX[0] + dx_pixels, _CONTROL_BBOX[1] + dy_pixels,
                           _CONTROL_BBOX[2] + dx_pixels, _CONTROL_BBOX[3] + dy_pixels],
            "centroid": [_CONTROL_CENTROID[0] + dx_pixels,
                         _CONTROL_CENTROID[1] + dy_pixels]}


def _frames(**override):
    """一套「三条判据全绿」的合成读数，用例只覆盖自己要动的那一条。

    形状与官方核实测同构（`gate.main()` 的 JSON 就是这组数）：静止那六条帧
    —— 对照、挂链、拆层、静态恒等链、静态平移链、静态拍平 —— **全是同一帧**
    （恒等层中性、静态平移空操作），只有键形满幅的 ±40 两帧不同。
    """
    band = gate.BAND_DX_MODEL_PX
    frames = {
        "control": _frame("rest"),
        "chain_rest": _frame("rest"),
        "chain_full": _frame("band+40", dx=band),
        "chain_neg": _frame("band-40", dx=-band),
        "no_middle_rest": _frame("rest"),
        "no_middle_full": _frame("band+40", dx=band),
        "no_middle_neg": _frame("band-40", dx=-band),
        "identity_chain": _frame("rest"),
        "static_chain": _frame("rest"),
        "static_flat": _frame("rest"),
    }
    unknown = sorted(set(override) - set(gate.PROBE_NAMES))
    assert not unknown, (
        f"覆盖了闸门不认识的探针名 {unknown}：闸门改名要同改这张合成表")
    frames.update(override)
    return frames


def _stubbed_render(frames):
    """把合成表按**探针名**接回 `render_probe` 的调用形状。

    P1-m2：以前这里硬编码 `main()` 传给 `_package` 的五个包名，闸门一改名字就抛
    KeyError（报出来的还是一句看不出所以然的栈）。现在名字来自 `gate.PROBE_NAMES`
    这一份共享常量，缺条/多条都直接说清是哪一条。
    """
    missing = [name for name in gate.PROBE_NAMES if name not in frames]
    assert not missing, f"合成表没有覆盖探针 {missing}（闸门与本文件漂移了）"

    def render(manifest_path, params=None, *args, **kwargs):
        name = _package_name(manifest_path)
        assert name in frames, (
            f"闸门渲染了合成表里没有的探针 {name!r}：PROBE_NAMES 与本文件漂移了")
        return dict(frames[name])
    return render


def test_gate_reports_unverified_when_core_is_unavailable(monkeypatch):
    """Core 不可用时所有派生判据必须是 None（三态），不能是 "" == "" 得出的 true。

    变异控制：去掉 tools/deform_trunk_probe_gate.py 里的 `core_ok` 守卫、让判据
    无条件比较，`chain_rest_matches_control` 与 `neutral_middle_level_invisible`
    会双双算成 true（失败探针的 sha 都是空串），本用例立刻红。
    """
    monkeypatch.setattr(gate, "render_probe", _unavailable_probe)

    out = gate.main()

    assert out["core_ok"] is False
    assert "simulated missing Cubism Core" in out["blocker"]
    for key in gate.VERDICT_KEYS + gate.OBSERVATION_KEYS:
        assert out[key] is None, f"{key} 在 Core 不可用时必须记成未验证"
        assert out[key] is not True
    # 再显式钉一遍最承重的那两条：neutral_middle_level_invisible 与
    # chain_inherits_parent_band 是 Task 2 的验收键，无 Core 的运行只能读到未验证。
    assert out["neutral_middle_level_invisible"] is not True
    assert out["chain_inherits_parent_band"] is not True
    assert "chain_applies_parent" not in out, (
        "旧键已删除（语义见 removed_keys），别把它加回 JSON")
    # 空串要**如实**留在 probes 里，不能伪造成有值。
    assert out["probes"]["chain_rest"]["pixels_sha256"] == ""


def test_a_missing_probe_is_recorded_as_unverified_not_true():
    """少一条探针（名字漂了 / 渲染没跑到）也只能得到 None，不能拿残缺集算判据。

    这条钉的是 `evaluate` 的入口守卫：缺条时 `core_ok=false` + blocker 点名，
    而不是让 `sha[...]` 抛 KeyError 或者更糟 —— 用能凑齐的那几条算出 true。
    """
    frames = _frames()
    del frames["no_middle_full"]

    out = gate.evaluate(frames)

    assert out["core_ok"] is False
    assert "no_middle_full" in out["blocker"]
    for key in gate.VERDICT_KEYS + gate.OBSERVATION_KEYS:
        assert out[key] is None


def test_inheritance_verdict_reads_the_keyform_band_not_the_static_translation():
    """`chain_inherits_parent_band` 只能读键形带那三条帧，读不到静态平移那两条。

    这是变异对照 B 的夹子：把判据退回「比较 static_chain 与 identity_chain」
    （= 已删除的 `chain_applies_parent` 的计算形式）时，两个场景一起把它杀掉 ——
    - 场景 A：键形带**没**动画面、只有静态平移动了 → 诚实接线 false，读静态 true；
    - 场景 B：键形带动、静态平移不动（官方核实测正是这样）→ 诚实接线 true，读静态 false。
    单给一个场景杀不掉「无条件 return True」这种更懒的变异，所以两条都写。
    """
    only_static_moves = gate.evaluate(_frames(
        chain_full=_frame("rest"), chain_neg=_frame("rest"),
        static_chain=_frame("band+40", dx=gate.BAND_DX_MODEL_PX)))
    assert only_static_moves["core_ok"] is True
    assert only_static_moves["static_translation_moves_frame"] is True, (
        "静态那对探针的差异没被如实记下来：观察项也被接线改掉了")
    assert only_static_moves["chain_inherits_parent_band"] is False, (
        "继承判据读到了静态整体平移探针 —— 那条是空操作，#70 换掉的就是它")

    honest = gate.evaluate(_frames())
    assert honest["chain_inherits_parent_band"] is True
    assert honest["static_translation_moves_frame"] is False


def test_inheritance_verdict_checks_the_amplitude_not_only_inequality():
    """「画面确实动了」不够，必须动**成**那条带写出的幅度，否则不算继承。

    简报明说「不许只比不等」。四个变异体各杀一次：
    幅度写错（2× 带值 / 多除一次 ppu）、往 y 漂、把方块撕了、只有满幅那侧动、
    负侧符号反了。每个场景只覆盖一条合成读数，其余保持与实测同构。
    """
    band = gate.BAND_DX_MODEL_PX
    assert gate.evaluate(_frames())["chain_inherits_parent_band"] is True

    double = gate.evaluate(_frames(chain_full=_frame("chain+2band", dx=2 * band)))
    assert double["chain_inherits_parent_band"] is False, (
        f"幅度错了（实测 {double['chain_full_shift_model_px']}）还判 true")

    drifting = gate.evaluate(_frames(chain_full=_frame("chain+diag", dx=band, dy=12.0)))
    assert drifting["chain_inherits_parent_band"] is False, (
        f"y 方向漂了 12 模型像素还判成纯平移：{drifting['chain_full_shift_model_px']}")

    torn = gate.evaluate(_frames(chain_full=_frame("chain+band", dx=band,
                                                  opaque=_CONTROL_OPAQUE // 3)))
    assert torn["chain_inherits_parent_band"] is False, (
        f"不透明像素掉到三分之一（撕裂/缩放在这里），纯平移判据不该放行："
        f"{torn['opaque_pixels']}")

    one_side = gate.evaluate(_frames(chain_neg=_frame("rest")))
    assert one_side["chain_inherits_parent_band"] is False, (
        "只有 +30 那侧跟了、−30 那侧没跟：不是父级键形带在逐级合成")

    flipped = gate.evaluate(_frames(chain_neg=_frame("chain+band", dx=band)))
    assert flipped["chain_inherits_parent_band"] is False, (
        "−30 的质心往 +x 走（符号反了）还判 true")


def test_rest_and_middle_verdicts_read_their_own_probe_pairs():
    """另两条判据各读自己那对探针，不能互相顶替、也不能共用一个 bool。

    `chain_rest_matches_control` 读 control ↔ chain_rest；
    `neutral_middle_level_invisible` 读拆层那三条 ↔ 挂链那三条。
    变异：把两条都写成同一个 `chain_rest == no_middle_rest`，第一条断言就红。
    """
    green = gate.evaluate(_frames())
    assert green["chain_rest_matches_control"] is True
    assert green["neutral_middle_level_invisible"] is True

    control_moves = gate.evaluate(_frames(control=_frame("别的帧")))
    assert control_moves["chain_rest_matches_control"] is False
    assert control_moves["neutral_middle_level_invisible"] is True, (
        "拆层对照里不该读 control：它变了说明接线被并成一条了")
    assert control_moves["chain_rest_matches_control"] is not (
        control_moves["neutral_middle_level_invisible"])

    middle_visible = gate.evaluate(_frames(
        no_middle_full=_frame("中间层还在", dx=gate.BAND_DX_MODEL_PX / 2)))
    assert middle_visible["neutral_middle_level_invisible"] is False, (
        "恒等中间层在满幅上留下了半个带的差异，判据却看不见到")
    assert middle_visible["chain_rest_matches_control"] is True, (
        "静止那条不该被满幅的场景带红")


def test_static_observations_are_recorded_but_never_gate(monkeypatch):
    """静态那两条读数只进 JSON、不进闸门：计划明说 `chain_matches_flattened` 不作闸门。

    原来这条（`test_flattened_chain_is_recorded_not_gated`）渲两次官方核帧却只断言
    「sha 非空」—— 修好空间基准后两条帧都逐像素等于无变形器，那 ~15s 什么方向也没
    钉。现在**不渲染**，只断言记录本身与「它不是判据」这件事。

    它 docstring 里那段知识没有丢，已搬进闸门：静态整体平移被自己的 (s, t) 归一
    抵消 ⇒ 「拍平」与「挂链」在这对**静态**探针上同帧（`chain_sha == flat_sha`），
    于是这对探针既不能当「父链被求值」的证据，也不能被读成「父链可省」——
    见 tools/deform_trunk_probe_gate.py 的模块 docstring 与
    `chain_shift()` / `unparented_shift()` 的 docstring。真机像素上的这条空操作由
    `test_gate_verdicts_hold_on_official_core_pixels` 钉住。
    """
    monkeypatch.setattr(gate, "render_probe", _stubbed_render(_frames()))

    out = gate.main()

    assert out["core_ok"] is True
    assert out["chain_sha"] == out["flat_sha"], "合成表里静态那对本来就该同帧"
    assert out["probes"]["static_chain"]["pixels_sha256"] == \
        out["probes"]["static_flat"]["pixels_sha256"]
    for key in ("static_translation_moves_frame", "chain_matches_flattened",
                "identity_chain_neutral_rest"):
        assert key in gate.OBSERVATION_KEYS, f"{key} 是观察项，不是判据"
        assert key not in gate.VERDICT_KEYS
    assert out["static_translation_is_not_a_verdict"] is True
    assert out["chain_matches_flattened_is_not_a_gate"] is True


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
# 主干形状的探针夹具：**全部取自闸门本身**
# ----------------------------------------------------------------------
#
# 尺子只有一份。#70 之前这里自己抄了一份 ±100 点阵与 ±60 方块，和
# tests/unit/test_bindings_gap_probe.py:49-71 那份已经开始漂移（tuple 与 list 混用、
# `rect=` 参数一份有一份没有）。仓库惯例是每文件自带夹具（`_package` 有 13 份独立
# 定义），所以不并模块；这条文件改为**引用闸门**那份常数与键形父级，漂移面就只剩
# bindings_gap_probe 那一处 —— 它那边也写了注释指回这里，改一边必须同步另一边。
_CANVAS = gate.CANVAS
_PPU = gate.PPU
_LATTICE = [list(p) for p in gate.LATTICE]
_SQUARE = [list(v) for v in gate.SQUARE]
_UVS = list(gate.UVS)
_TRIS = list(gate.TRIS)
_SIDE_PX = gate.SIDE_PX       # 方块边长（画布像素），用来把渲染像素换算回模型像素


def _angle_param():
    return ParameterSpec(gate.BAND_PARAMETER, -gate.BAND_KEY_FULL,
                         gate.BAND_KEY_FULL, 0.0)


def _shifted(points, dx, dy):
    """点阵平移 —— 收敛到闸门那一份（`gate._shift`），两边不可能各漂各的。"""
    return gate._shift(points, dx, dy)


def _warp(deformer_id, points, parent=""):
    """静态恒等 warp（单控制网格）：Step 4 那几种形态用的中性层。"""
    return WarpDeformerSpec(deformer_id=deformer_id, rows=1, cols=1,
                            grids=[DeformerGrid(0.0, points)],
                            parent_deformer_id=parent)


def _square_mesh(mesh_id, deformer_id, verts=None):
    return MeshSpec(mesh_id=mesh_id,
                    vertices=verts if verts is not None else _SQUARE,
                    triangles=_TRIS, uvs=list(_UVS), deformer_id=deformer_id)


def _rig(deformers, deformer_id, meshes=None, parameters=None):
    """本文件额外形状（三级链 / 宽父矩形 / 父级自带成员网格）的本地装配。"""
    return RigSpec(meshes=meshes if meshes is not None else
                   [_square_mesh("ArtProbe", deformer_id)],
                   deformers=list(deformers),
                   parameters=parameters if parameters is not None else [_angle_param()],
                   canvas_width=_CANVAS, canvas_height=_CANVAS, pixels_per_unit=_PPU)


def chain_rig():
    """父（键形）→ 子（恒等）→ 方块：主干要的形状。取闸门那份，不另抄。"""
    return gate.band_chain_rig()


def no_middle_rig():
    """**拆掉中间恒等层**的同一个方块：只挂在那个带键形的父级上。

    Step 5 的对照。刻意不是「手工展平」：展平要算仿射复合，而双线性重采样不等于
    仿射复合，那种 oracle 会造出假失败。拆层不需要算任何数学。
    """
    return gate.band_parent_only_rig()


def control_rig():
    """不挂任何变形器的同一张方块 —— 「挂链/挂层是不是中性」的基准。"""
    return gate.control_rig()


def _model_px_delta(rest_bbox, moved_bbox, known_px):
    """把「渲染 bbox 左边界移动了多少」换算回模型像素。

    视图缩放从静止帧自己推：静止帧是边长 ``known_px`` 的正方形，渲染宽度 / 该边长
    就是比例，所以这条不依赖探针窗口尺寸。闸门里同一条换算用**质心**
    （`gate._model_px_shift`，少一个像素的量化误差），这里保留 bbox 版本是为了
    和 alpha_bbox 的读数直接对账 —— 两条一起看才排掉「bbox 抖动」。
    """
    scale = (rest_bbox[2] - rest_bbox[0] + 1) / known_px
    return ((moved_bbox[0] - rest_bbox[0]) / scale,
            (moved_bbox[1] - rest_bbox[1]) / scale)


@requires_live2d
def test_gate_verdicts_hold_on_official_core_pixels(tmp_path):
    """闸门发布的三条判据在**官方 Core 像素**上必须同时为 true —— 主干的承重证据。

    探针取自 `gate.probe_rigs()`、判据由 `gate.evaluate()` 算，所以这条 certify 的
    就是工具真发布的那份数字，不是测试里的副本；本文件的渲染缓存与闸门用同一批
    产物字节，除静态那两条以外都是命中缓存（不额外花 GPU）。

    顺带钉住 #70 的两条读数：静态整体平移在像素上是空操作
    （`static_translation_moves_frame` 为 false），恒等静态链的静止帧等于对照。
    变异对照 A 的杀法在这里：去掉编译器的嵌套空间映射（`deformer_space_map`），
    `chain_rest_matches_control` 与 `neutral_middle_level_invisible` 双双变红。
    """
    frames = {name: _render(tmp_path, rig, f"gate_{name}", pose)
              for name, (rig, pose) in gate.probe_rigs().items()}
    out = gate.evaluate(frames)

    assert out["core_ok"] is True, out["blocker"]
    for key in gate.VERDICT_KEYS:
        assert out[key] is True, (key, out["probes"],
                                  out["chain_full_shift_model_px"],
                                  out["chain_neg_shift_model_px"])
    band = gate.BAND_DX_MODEL_PX
    assert out["chain_full_shift_model_px"][0] == pytest.approx(band, abs=2.0)
    assert out["chain_full_shift_model_px"][1] == pytest.approx(0.0, abs=2.0)
    assert out["chain_neg_shift_model_px"][0] == pytest.approx(-band, abs=2.0)
    assert out["chain_neg_shift_model_px"][1] == pytest.approx(0.0, abs=2.0)
    assert out["static_translation_moves_frame"] is False, (
        "静态整体平移又能让画面动了 —— (s, t) 归一那条机制变了，"
        "#70 换判据的理由要重新审，别直接把旧判据加回来")
    assert out["identity_chain_neutral_rest"] is True


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

    同一个机制正是 #70 作废静态平移判据的理由：父级点阵整体平移也被父级自己的
    静止矩形抵消，所以「静态平移父级 ⇒ 画面动」在修复后**永远**读 false；
    旧 `chain_applies_parent` 的这条负读数不是「内核不求值父链」。
    """
    rest = _render(tmp_path, gate.rest_rig(), "rest_control")
    child_only = _render(tmp_path, gate.chain_shift(parent_shift=(0.0, 0.0),
                                                    child_shift=(0.0, 25.0)),
                         "child_only")
    assert child_only["pixels_sha256"] == rest["pixels_sha256"], (
        "恒等父级下的子级平移改变了画面 —— 静止形/参考矩形被算歪了")


@requires_live2d
def test_attaching_an_identity_chain_leaves_the_rest_frame_alone(tmp_path):
    """挂两级恒等链不许改变静止画面 —— 分层主干的前提。

    Task 1 留的 `xfail(strict=True)` 就是这条：官方 Core 把子级点阵按父级 (s, t)
    再映一次，静止帧被线性放大 2× 并朝父矩形最小角偏移（实测不透明像素
    35344 vs 基准 8836，面积比 4.0）。Task 1.5 按规则 I 修正编译器的空间基准后，
    这里必须是普通断言：恒等 = 逐像素等于「不挂任何变形器」（基准帧 =
    `gate.control_rig()`，也就是旧 `neutral_rig()` 那条 #70 换名的探针）。
    """
    baseline = _render(tmp_path, gate.control_rig(), "neutral")
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
    静态恒等链成立，键形驱动的继承在这里第一次被测。闸门里同一条 claim 叫
    `neutral_middle_level_invisible`（见上面那条官方核用例）。
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
