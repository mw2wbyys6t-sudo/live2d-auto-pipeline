"""离线求值器（tools/bindings_gap_probe.py）必须和官方内核用同一条空间约定。

Task 6 的配对距离闸门、以及一切「跨层相对位移」读数都出自这把尺子。尺子量的
是 bindings.json（**模型像素**规格），内核量的是 moc3（逐级 (s,t) 存储），两者
之间隔着 Task 1.5 修的这条空间基准 —— 所以这里的对照是三条独立的实现：

  1. 从规格出发的离线求值 `Field.chain()`；
  2. 从**产物字节**出发、按实测规则 I（上一级的输出原样当父级点阵的 (s,t)，
     不再按父级静止矩形归一）复算的参考实现 `_rule_i_from_moc3()`；
  3. 官方 Core 的渲染位移（有 Core 时）。

实测依据（全部钉在仓库里）：
  * 规则 I 的两条结构断言 ——
    ``test_moc3_deformers.py::test_nested_warp_lattice_is_written_in_the_parent_fraction_space``
    与 ``::test_third_level_is_normalized_by_the_level_directly_above_it``；
  * 官方 Core 的逐级合成 —— ``test_deformer_parent_chain.py``（键形带判据）；
  * 根级不受影响的字节基准 —— 夹具
    ``tests/unit/fixtures/moc3_root_level_flat_warps_golden.b64``
    （sha256 ``66ab863352dba657295fa45db39684070061ce1d0b0143f2be0f9c10eca5e8c8``、
    10880 字节）与真实歌手产物 ``transition.moc3``
    （sha256 ``c062538d9ee41c2d26b9ef5322699bc884f76f3f24100857302682ab5f66956f``、
    877440 字节）。
规则 N（每级按自身静止矩形重归一）与上面的判据在父/子矩形不同尺寸时反号，
被这两条用例否掉。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

# tools/ 不是包，只能靠上面的 sys.path.insert 取到（仓库惯例见 test_lint_architecture）
import bindings_gap_probe as probe_mod  # noqa: E402

from live2d_builder.exporter.bindings_export import build_bindings_document  # noqa: E402
from live2d_builder.exporter.moc3_model import (  # noqa: E402
    DeformerGrid, MeshSpec, ParameterSpec, RigSpec, WarpDeformerSpec,
    compile_static_rig)


def _has_live2d() -> bool:
    try:
        return importlib.util.find_spec("live2d.v3") is not None
    except (ImportError, ValueError):
        return False


requires_live2d = pytest.mark.skipif(
    not _has_live2d(), reason="需要官方 Cubism Core（live2d-py）才能比对渲染位移")

# 共享尺子（**同一组常数在两个地方各写一份，改一边必须同步另一边**）：
# `tools/deform_trunk_probe_gate.py:63-66` 那份 LATTICE / SQUARE / TRIS / UVS 是
# 现在的主源 —— tests/unit/test_deformer_parent_chain.py 已经改成引用它，不再自抄
# （#70 收敛掉的那把重复 `_shifted`）。这里保留仓库「每文件自带夹具」的惯例，所以
# 只留注释指回去。必须对齐的是**数值**：点阵 ±100、方块 ±60（边长 120 模型像素）、
# 键形满幅 ±40；两条带相加（父 +20 / 子 +10）与闸门那边的幅度读数靠同一把尺子换算。
# 顶点顺序这边是 (…,-60,60) 先、那边是 (…,60,60) 先，同一片正方形、只是扇形起点不同
# （贴图纯色 ⇒ 渲染逐像素相同），但产物字节不同，别拿一边的字节去比另一边。
PPU = 100.0
_LATTICE = [(-100.0, -100.0), (100.0, -100.0), (-100.0, 100.0), (100.0, 100.0)]
_SQUARE = [(-60.0, -60.0), (60.0, -60.0), (-60.0, 60.0), (60.0, 60.0)]
_UVS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
_TRIS = [(0, 1, 2), (0, 2, 3)]


def _shifted(points, dx, dy):
    return [[x + dx, y + dy] for x, y in points]


def _angle():
    return ParameterSpec("ParamAngleZ", -30.0, 30.0, 0.0)


def _keyform_parent(dx_full=40.0, rect=None):
    rect = rect or _LATTICE
    return WarpDeformerSpec(
        deformer_id="Parent", rows=1, cols=1, parameter_id="ParamAngleZ",
        grids=[DeformerGrid(-30.0, _shifted(rect, -dx_full, 0.0)),
               DeformerGrid(0.0, list(rect)),
               DeformerGrid(30.0, _shifted(rect, dx_full, 0.0))])


def nested_chain_spec():
    """父（3 键带）→ 子（恒等）→ 方块；父矩形与子矩形**不同尺寸**（±150 / ±100）。

    两条矩形的尺寸差异就是判据本身：按被否的规则 N（每级按自身静止矩形重归一），
    父级 +40px 的平移会被换算成 40×200/300 ≈ 26.67px，与内核实测的 40px 反号。
    """
    wide = [[x, y] for x, y in
            [(-150.0, -150.0), (150.0, -150.0), (-150.0, 150.0), (150.0, 150.0)]]
    parent = _keyform_parent(rect=wide)
    child = WarpDeformerSpec("Child", 1, 1, [DeformerGrid(0.0, list(_LATTICE))],
                             parent_deformer_id="Parent")
    mesh = MeshSpec("ArtProbe", list(_SQUARE), _TRIS, list(_UVS),
                    deformer_id="Child")
    return RigSpec(meshes=[mesh], deformers=[parent, child], parameters=[_angle()],
                   canvas_width=512.0, canvas_height=512.0, pixels_per_unit=PPU)


def dense_nested_chain_spec():
    """4×2 恒等中间点阵：矩形线性映射只有在混合点阵仍为矩形时才成立。"""
    dense = [[x, y] for y in (-100.0, -100.0 / 3.0, 100.0 / 3.0, 100.0)
             for x in (-100.0, 100.0)]
    parent = _keyform_parent()
    child = WarpDeformerSpec("Child", 3, 1, [DeformerGrid(0.0, dense)],
                             parent_deformer_id="Parent")
    mesh = MeshSpec("ArtProbe", list(_SQUARE), _TRIS, list(_UVS),
                    deformer_id="Child")
    return RigSpec(meshes=[mesh], deformers=[parent, child], parameters=[_angle()],
                   canvas_width=512.0, canvas_height=512.0, pixels_per_unit=PPU)


def root_level_spec():
    """只有一级（根 warp）的同一套键形：今天产物的形状，同步不许把它改坏。"""
    parent = _keyform_parent()
    mesh = MeshSpec("ArtProbe", list(_SQUARE), _TRIS, list(_UVS),
                    deformer_id="Parent")
    return RigSpec(meshes=[mesh], deformers=[parent], parameters=[_angle()],
                   canvas_width=512.0, canvas_height=512.0, pixels_per_unit=PPU)


def child_first_nested_chain_spec():
    """与 :func:`nested_chain_spec` 同一套链，但**子级写在父级之前**。

    这正是 Task 3 作者表的形状（``HairFrontSwing`` 写在 ``HeadRig`` 之前，
    ``HeadRig`` 又写在 ``BodySway`` 之前）。``compile_static_rig`` 会把它规范成
    parent-first 再算槽位，所以**参考实现必须按产物的槽位序取数**；照作者顺序比
    就会以「参考实现假设顺序一致」的名义在 Task 6 点出一条假的产品 bug。
    """
    from dataclasses import replace
    spec = nested_chain_spec()
    return replace(spec, deformers=list(reversed(spec.deformers)))


# ----------------------------------------------------------------------
# 参考实现：直接按实测的规则 I 在**产物**上求值
# ----------------------------------------------------------------------

def _stored_warps(doc, spec):
    """moc3 里写出去的东西：每级每键的点阵（单位）+ 父级下标 + 成员顶点的 (s,t)。

    **按名字取槽位，不按调用方的书写顺序**：写出层会先把变形器表规范化成
    parent-first（``compile_static_rig`` → ``parent_first_deformer_order``），所以
    ``deformer.ids`` 的顺序是**产物的顺序**，而 ``spec.deformers`` 是**作者顺序**。
    Task 3 的作者表第一次出现子先父后（HairFrontSwing 写在 HeadRig 之前），拿作者
    顺序去比槽位就会以「这条参考实现假设顺序一致」失败，看起来像产品 bug ——
    参考实现要复现的是内核看到的表，不是作者键入的表。
    """
    warps = [d for d in spec.deformers if isinstance(d, WarpDeformerSpec)]
    assert len(warps) == len(spec.deformers), "这条参考实现只覆盖全 warp 的链"
    ids = doc.get("deformer.ids")
    parents = doc.get("deformer.parent_deformer_indices")
    by_name = {d.deformer_id: d for d in warps}
    assert sorted(ids) == sorted(by_name), (
        "变形器表必须全是 warp 且名字一一对应（顺序由写出层规范化决定，"
        "与作者书写顺序无关，所以下面按 ids 的顺序取槽位）")
    begins = doc.get("warp_deformer_keyform.keyform_position_begin_indices")
    counts = doc.get("warp_deformer.keyform_counts")
    xs = doc.get("keyform_position.xys")
    lattices = {}
    running = 0
    for index, name in enumerate(ids):
        deformer = by_name[name]
        lattices[name] = []
        for grid in range(counts[index]):
            begin = begins[running + grid]
            flat = xs[begin:begin + 2 * deformer.vertex_count]
            lattices[name].append(
                [(flat[k], flat[k + 1]) for k in range(0, len(flat), 2)])
        running += counts[index]
    mesh_began = doc.get("art_mesh_keyform.keyform_position_begin_indices")
    mesh_counts = doc.get("art_mesh.position_index_counts")   # = 唯一顶点数
    mesh_deformers = doc.get("art_mesh.parent_deformer_indices")
    mesh_st = [[(xs[mesh_began[m] + 2 * k], xs[mesh_began[m] + 2 * k + 1])
                for k in range(mesh_counts[m])]
               for m in range(len(mesh_counts))]
    # 交回**产物槽位序**的那份表：`by_slot = {i: w for i, w in enumerate(...)}`
    # 靠的就是这个顺序与 parents / mesh_deformers 的下标同构。
    ordered = [by_name[name] for name in ids]
    return ordered, ids, parents, lattices, mesh_st, mesh_deformers


def _blend(lattice_set, deformer, resolve):
    """按键形混合某一级**写出去**的点阵（混合权重与离线求值器同一套公式）。"""
    if len(lattice_set) == 1:
        return list(lattice_set[0])
    terms = probe_mod.keyform_terms(
        [(pid, keys) for pid, keys in deformer.axes_spec], resolve)
    width = 2 * len(lattice_set[0])
    flat_tables = [[c for p in grid for c in p] for grid in lattice_set]
    blended = probe_mod.blend_flat(terms, flat_tables, width)
    return [(blended[k], blended[k + 1]) for k in range(0, width, 2)]


def _assert_axis_aligned_rectangle(grid, deformer):
    """矩形包围盒映射只在混合点阵仍为轴对齐矩形时等价于双线性采样。"""
    rows, cols = deformer.rows + 1, deformer.cols + 1
    assert len(grid) == rows * cols
    for row in range(rows):
        ys = [grid[row * cols + col][1] for col in range(cols)]
        assert all(abs(y - ys[0]) <= 1e-9 for y in ys), (
            f"{deformer.deformer_id} 混合后的点阵必须构成轴对齐矩形：第 {row} 行弯曲")
    for col in range(cols):
        xs = [grid[row * cols + col][0] for row in range(rows)]
        assert all(abs(x - xs[0]) <= 1e-9 for x in xs), (
            f"{deformer.deformer_id} 混合后的点阵必须构成轴对齐矩形：第 {col} 列弯曲")


def _rule_i_from_moc3(doc, spec, values):
    """实测规则 I：每一级的输出**就是**父级点阵的 (s,t)，不再归一一次。

    与 `Field.chain()` 完全独立：这条从 moc3 的字节出发，那条从模型像素规格出发。
    """
    warps, _ids, parents, lattices, mesh_st, mesh_deformers = _stored_warps(doc, spec)
    by_slot = {i: w for i, w in enumerate(warps)}
    resolve = lambda pid: float(values.get(pid, 0.0))  # noqa: E731

    out = []
    for mesh_index, st_points in enumerate(mesh_st):
        slot = mesh_deformers[mesh_index]
        if slot < 0:
            # 不挂变形器的网格：存的就是模型单位
            out.extend([(x * PPU, y * PPU) for x, y in st_points])
            continue
        pts = list(st_points)
        guard = 0
        while slot is not None and slot >= 0 and guard < 32:
            deformer = by_slot[slot]
            grid = _blend(lattices[deformer.deformer_id], deformer, resolve)
            _assert_axis_aligned_rectangle(grid, deformer)
            xs = [p[0] for p in grid]
            ys = [p[1] for p in grid]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            pts = [(x0 + (x1 - x0) * s, y0 + (y1 - y0) * t) for s, t in pts]
            slot = parents[slot]
            guard += 1
        out.extend([(x * PPU, y * PPU) for x, y in pts])
    return out


def _offline_positions(spec, values):
    """离线求值器给的位置（模型像素、原点居中）。"""
    doc = build_bindings_document(spec)
    field = probe_mod.Field(doc, values)
    positions = []
    for mesh in doc["meshes"]:
        _rest, moved = field.deformed(mesh)
        positions.extend(moved)
    return positions


@pytest.mark.parametrize("values", [
    {}, {"ParamAngleZ": 12.0}, {"ParamAngleZ": 30.0}, {"ParamAngleZ": -30.0},
])
def test_offline_chain_uses_the_same_space_convention_as_the_compiled_moc3(values):
    """同一份规格：从产物按规则 I 复算 == 从规格离线求值，逐顶点相同。

    这条锁的是「尺子和内核用同一条空间约定」。子级与父级矩形刻意不同尺寸，
    所以尺子一旦被改成「拿上一级写出的 st 再按父级静止矩形归一一次」，
    读数会从 40px 变成 26.67px 而立刻红。
    """
    spec = nested_chain_spec()
    doc = compile_static_rig(spec)
    reference = _rule_i_from_moc3(doc, spec, values)
    offline = _offline_positions(spec, values)
    assert len(offline) == len(reference) == 4
    for (rx, ry), (ox, oy) in zip(reference, offline):
        assert ox == pytest.approx(rx, abs=1e-9), (reference, offline)
        assert oy == pytest.approx(ry, abs=1e-9), (reference, offline)


@pytest.mark.parametrize("values", [{}, {"ParamAngleZ": 30.0}])
def test_dense_identity_middle_grid_agrees_with_offline_evaluation(values):
    """非 1×1 恒等链必须可用；弯曲混合点阵则由矩形性守卫明确拒绝。"""
    spec = dense_nested_chain_spec()
    doc = compile_static_rig(spec)
    reference = _rule_i_from_moc3(doc, spec, values)
    offline = _offline_positions(spec, values)
    assert len(reference) == len(offline) == 4
    for (rx, ry), (ox, oy) in zip(reference, offline):
        # bindings_export 固定保留千分像素；4×2 的 1/3 行距因此有 0.0002px 量化差。
        assert ox == pytest.approx(rx, abs=0.001), (values, reference, offline)
        assert oy == pytest.approx(ry, abs=0.001), (values, reference, offline)


def test_dense_reference_rejects_a_non_rectangular_blended_grid():
    """非 1×1 不是通行证：弯曲后的混合点阵不能用包围盒线性映射冒充。"""
    from dataclasses import replace
    spec = dense_nested_chain_spec()
    child = spec.deformers[1]
    bent = [list(p) for p in child.grids[0].points]
    bent[2][0] += 7.0
    keyed = replace(child, grids=[DeformerGrid(-30.0, child.grids[0].points),
                                 child.grids[0],
                                 DeformerGrid(30.0, bent)],
                    parameter_id="ParamAngleZ")
    spec = replace(spec, deformers=[spec.deformers[0], keyed])
    doc = compile_static_rig(spec)
    with pytest.raises(AssertionError, match="轴对齐矩形"):
        _rule_i_from_moc3(doc, spec, {"ParamAngleZ": 30.0})


def test_offline_chain_root_level_rig_still_agrees_with_the_compiled_moc3():
    """根级链也必须跑一遍：它今天就是对的，同步不许把它改坏。"""
    spec = root_level_spec()
    doc = compile_static_rig(spec)
    for values in ({}, {"ParamAngleZ": 30.0}):
        reference = _rule_i_from_moc3(doc, spec, values)
        offline = _offline_positions(spec, values)
        for (rx, ry), (ox, oy) in zip(reference, offline):
            assert ox == pytest.approx(rx, abs=1e-9), (values, reference, offline)
            assert oy == pytest.approx(ry, abs=1e-9), (values, reference, offline)


@pytest.mark.parametrize("values", [{}, {"ParamAngleZ": 30.0}])
def test_the_reference_implementation_follows_the_compiled_slot_order(values):
    """作者把子级写在父级**之前**时，参考实现必须照样对上离线求值。

    这条是 Task 3 的作者表形状带来的新前提：规范化会搬动槽位，而槽位下标就是
    ``deformer.parent_deformer_indices`` 与 ``art_mesh.parent_deformer_indices``
    引用变形器的方式。照**作者顺序**比 ids 的话，这条会以「参考实现假设顺序一致」
    失败，看起来像产品 bug —— 实际是尺子的实现细节在替作者手感说话。
    """
    spec = child_first_nested_chain_spec()
    doc = compile_static_rig(spec)
    assert doc.get("deformer.ids") == ["Parent", "Child"], (
        "写出层必须已经把这张子先父后的表规范成 parent-first")
    reference = _rule_i_from_moc3(doc, spec, values)
    offline = _offline_positions(spec, values)
    assert len(reference) == len(offline) == 4
    for (rx, ry), (ox, oy) in zip(reference, offline):
        assert ox == pytest.approx(rx, abs=1e-9), (values, reference, offline)
        assert oy == pytest.approx(ry, abs=1e-9), (values, reference, offline)


def test_offline_selfcheck_rest_holds_with_a_nested_chain():
    """自检 1（默认参数重放 == 静止）在**有父链**时也必须成立。

    bindings_gap_probe 今天看不见这条：产物里全是根级。父链一旦接上，静止重放
    不再自抵消就是求值器坏了，而 Task 6 的所有读数都建立在这条自检上。
    """
    spec = nested_chain_spec()
    doc = build_bindings_document(spec)
    meshes = doc["meshes"]
    worst, where = probe_mod.selfcheck_rest(probe_mod.Field(doc, {}), meshes)
    assert worst == pytest.approx(0.0, abs=1e-9), (worst, where)


def _package(target: Path, doc_bytes: bytes, name: str) -> Path:
    folder = target / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / f"{name}.moc3").write_bytes(doc_bytes)
    from PIL import Image
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(folder / "texture_00.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3, "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3", "Textures": ["texture_00.png"]},
    }), encoding="utf-8")
    return manifest


@requires_live2d
def test_offline_chain_matches_official_core_on_a_nested_chain(tmp_path):
    """尺子的读数必须等于官方 Core 画出来的位移（嵌套链，父级带键形）。

    静止帧是基准：渲染宽度 → 模型像素的比例从静止帧自己推，所以不依赖探针窗口。
    """
    from drivers.live2d_runtime.moc3_verify import render_probe

    spec = nested_chain_spec()
    doc = compile_static_rig(spec)
    manifest = _package(tmp_path, doc.to_bytes(), "probe_chain")
    rest = render_probe(str(manifest))
    if not rest["ok"]:
        pytest.skip(f"官方 Core 不可用：{rest.get('blocker')}")
    scale = (rest["alpha_bbox"][2] - rest["alpha_bbox"][0] + 1) / 120.0
    full = render_probe(str(manifest), {"ParamAngleZ": 30.0})
    assert full["ok"], full.get("blocker")
    rendered_dx = (full["alpha_bbox"][0] - rest["alpha_bbox"][0]) / scale

    offline = _offline_positions(spec, {"ParamAngleZ": 30.0})
    offline_rest = _offline_positions(spec, {})
    predicted_dx = [round(moved[0] - was[0], 6)
                    for moved, was in zip(offline, offline_rest)]
    assert set(predicted_dx) == {40.0}, predicted_dx
    assert rendered_dx == pytest.approx(40.0, abs=2.0), (
        f"内核画的是 {rendered_dx:.2f}px，尺子读的是 {predicted_dx[0]:.2f}px："
        "两者不再是同一条空间约定")


# ======================================================================
# warp 格内插值：官方 Core 是**三角形分片线性**，不是双线性
# ======================================================================


class TestWarpGridInterpolationFollowsTheCore:
    """warp 变形器格内插值必须按 P10-P01 对角线分裂的三角形算。

    实测（``tools/probe_warp_interpolation.py``，官方 Cubism Core 5-r.5）：
    控制网格被拉成**非仿射**（只把一个角点拉出去）时，Core 渲染出的质心与
    三角形预测差 0.43%，与双线性预测差 9.49%。

    为什么仓库一直没人发现：仿射网格（整体平移）下两种插值结果**完全相同**，
    而仓库所有 Core 对照用例用的都是整体平移 —— 结构性看不见这个差异。

    ``quad_transforms=0``（网格形变）走三角形；``=1``（quad 形变）才走双线性。
    编译器固定写 0（``moc3_model.py`` 的 ``additional.quad_transforms``），
    所以尺子与自研播放器都必须按三角形算。
    """

    @staticmethod
    def _non_affine_grid():
        """一格 2x2 控制点，只把右上角拉出去 —— 破坏平行四边形关系。"""
        return [-100.0, -60.0, 100.0, -60.0,
                -100.0, 60.0, 400.0, 60.0]

    def test_lower_left_triangle_uses_p00_p10_p01(self):
        grid = self._non_affine_grid()
        # (u,v)=(0.35,0.35) → fu+fv=0.7 < 1 → 落在 P00 一侧，用 P00/P10/P01
        got = probe_mod.sample_warp_grid(grid, 1, 1, 0.35, 0.35)
        # 三角形：P00(1-fu-fv) + P10·fu + P01·fv（不含 P11）
        assert got == pytest.approx((-30.0, -18.0)), got
        # 双线性会给出 (6.75, -18.0)：差 36.75 个模型单位
        bilinear_x = (-100.0 * 0.65 * 0.65 + 100.0 * 0.35 * 0.65
                      + (-100.0) * 0.65 * 0.35 + 400.0 * 0.35 * 0.35)
        assert abs(got[0] - bilinear_x) > 30.0, "双线性没被排除，判据失效"

    def test_upper_right_triangle_uses_p11_p01_p10(self):
        grid = self._non_affine_grid()
        # (u,v)=(0.65,0.65) → fu+fv=1.3 > 1 → 落在 P11 一侧
        got = probe_mod.sample_warp_grid(grid, 1, 1, 0.65, 0.65)
        # 三角形：P11(fu+fv-1) + P01(1-fu) + P10(1-fv)
        expect = (400.0 * 0.3 + (-100.0) * 0.35 + 100.0 * 0.35,
                  60.0 * 0.3 + 60.0 * 0.35 + (-60.0) * 0.35)
        assert got == pytest.approx(expect), got

    def test_affine_grid_is_unaffected(self):
        """整体平移（仿射）下两种插值相同 —— 仓库原有的对照用例必须照样过。"""
        grid = [-100.0, -60.0, 100.0, -60.0, -100.0, 60.0, 100.0, 60.0]
        got = probe_mod.sample_warp_grid(grid, 1, 1, 0.35, 0.35)
        assert got == pytest.approx((-30.0, -18.0)), got


# ======================================================================
# #72：越出父级静止矩形的画（art outside parent grid）
#
# 上面那些用例的几何**全部**落在父级静止矩形之内，所以它们看不见这个缺陷：
# `sample_warp_grid` 旧版先把 (u,v) 夹到 [0,1] 再采样，而官方 Core 不夹 ——
# 挂在父级矩形之外的画会被整段塌到矩形边界上。实测（诊断
# `step3_core_arbiter.py` / `step4_ruler_vs_core.py`，2026-11-06）：
#   C_out_4x3_rest / D_out_1x1_rest（子级画挂在父矩形外 160px，恒等点阵，静止）
#   与「根本没有变形器」的对照 A2_ctrl_out **逐字节相同**（Core SHA 5ef39e13…，
#   alpha_bbox [153,203,246,452]）；夹取版尺子却读 [153,203,246,328]。
#   124px 的幻影 = 160px 的越界量 × 400/512 的屏幕比例，正好等于越界量本身。
# 真实产物里 hair_back 之于 HeadRig 就是这个形状（27/140 个顶点在 HeadRig 矩形
# 之外），于是"静止帧偏 27.183px"被当成产品缺陷查了一整轮 —— 那是尺子在撒谎。
#
# 同一份夹取在 shipped player 里也有一份：web/lib/live2d-bindings.ts 的
# `sampleWarpGrid`（自研渲染器的采样器），所以两边都要按 Core 外推。
# ======================================================================

#: 与诊断同一套几何：父矩形 ±100 模型像素（直接由上面的共享尺子 _LATTICE 导出，
#: 不再抄第二份数字），画从 y=-260 起（底部 160px 越界）。
_OUT_PARENT_RECT = (min(p[0] for p in _LATTICE), min(p[1] for p in _LATTICE),
                    max(p[0] for p in _LATTICE), max(p[1] for p in _LATTICE))
_OUT_MESH_RECT = (-60.0, -260.0, 60.0, 60.0)
#: Core 渲染窗口 400x500（`render_probe` 的默认值）对 512 画布的比例与中心，
#: 在两个对照渲染上标定到 ≤1px（诊断 `step4_ruler_vs_core.py:26-27`）。
_SCREEN_SCALE = 400.0 / 512.0
_SCREEN_CX, _SCREEN_CY = 199.5, 249.5


def _lattice_of(rect, rows, cols):
    """矩形上的 (rows+1)×(cols+1) 恒等点阵：行优先、x 最快、行号随 y 增。"""
    x0, y0, x1, y1 = rect
    return [[x0 + (x1 - x0) * c / cols, y0 + (y1 - y0) * r / rows]
            for r in range(rows + 1) for c in range(cols + 1)]


def _quad_grid_mesh(rect, deformer_id, n=4):
    """rect 上的 n×n 顶点方块（贴图纯色，所以顶点密度只影响 bbox 读数）。"""
    x0, y0, x1, y1 = rect
    pts = [[x0 + (x1 - x0) * c / (n - 1), y0 + (y1 - y0) * r / (n - 1)]
           for r in range(n) for c in range(n)]
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            i = r * n + c
            tris.append((i, i + 1, i + n + 1))
            tris.append((i, i + n + 1, i + n))
    uvs = [[c / (n - 1), r / (n - 1)] for r in range(n) for c in range(n)]
    return MeshSpec("ArtProbe", pts, tris, uvs, deformer_id=deformer_id)


def out_of_rect_chain_spec(rows=4, cols=3):
    """恒等点阵的父级 → 1×1 子级 → 挂出父级矩形 160px 的方块（静止）。

    诊断的 `C_out_4x3_rest`：父级与子级的默认点阵都是各自静止矩形上的恒等映射，
    Core 在这种链上画得**与没有变形器时逐字节相同**，所以任何静止偏差都是尺子的。
    """
    parent = WarpDeformerSpec(
        "Parent", rows=rows, cols=cols,
        grids=[DeformerGrid(0.0, _lattice_of(_OUT_PARENT_RECT, rows, cols))],
        parameter_id="ParamP")
    child = WarpDeformerSpec(
        "Child", 1, 1, [DeformerGrid(0.0, _lattice_of(_OUT_MESH_RECT, 1, 1))],
        parent_deformer_id="Parent")
    return RigSpec(meshes=[_quad_grid_mesh(_OUT_MESH_RECT, "Child")],
                   deformers=[parent, child],
                   parameters=[ParameterSpec("ParamP", 0.0, 1.0, 0.0),
                               ParameterSpec("ParamQ", 0.0, 1.0, 0.0)],
                   canvas_width=512.0, canvas_height=512.0, pixels_per_unit=PPU)


def _screen_y(doc, y_local):
    """尺子的局部坐标（原点画布中心、y 向上）→ Core 渲染窗口的屏幕行。"""
    return _SCREEN_CY - _SCREEN_SCALE * y_local


@pytest.mark.parametrize("rows,cols", [(1, 1), (4, 3)],
                         ids=["1x1_parent", "4x3_parent"])
def test_art_outside_the_parent_grid_is_extrapolated_not_clamped(rows, cols):
    """挂在父级静止矩形之外的画必须按 Core 那样外推，不许塌到矩形边界。

    判据来自 Core 实样（诊断 M3/M4）：越界顶点停在静止位置，屏幕行 452；
    旧夹取版读 328，差的 124px 就是那 160px 越界量的屏幕投影。
    """
    spec = out_of_rect_chain_spec(rows=rows, cols=cols)
    doc = build_bindings_document(spec)
    field = probe_mod.Field(doc, {})
    _rest, moved = field.deformed(doc["meshes"][0])

    bottom = min(y for _x, y in moved)
    assert bottom == pytest.approx(-260.0, abs=1e-3), (
        f"越界顶点被读成 y={bottom:.3f}（塌到父矩形边界 -100 = 夹取），"
        "而 Core 把它画在静止位置 -260")
    assert _screen_y(doc, bottom) == pytest.approx(452.0, abs=1.5), (
        "Core 实测屏幕行 452；夹取版读 328")

    worst, where = probe_mod.selfcheck_rest(field, doc["meshes"])
    # 1e-3px 就是探针自检 1 自己的门限（bindings_gap_probe.main）；4×3 那种
    # 非 1×1 点阵过一遍 float32 会留 2e-4 的舍入，与夹取无关。
    assert worst < 1e-3, f"恒等链静止重放偏了 {worst:.4f}px（{where}）—— 夹取的幻影"


def test_the_ruler_names_art_outside_the_parent_grid_as_its_own_metric():
    """越界画要**单列命名**，不许混进偏差数：外推与 Core 的差别在非仿射弯曲
    父级上仍有 29px（诊断 H_out_4x3_bend），读的人必须能看出这条数不在
    「Core、播放器、尺子三方一致」的区域内。
    """
    spec = out_of_rect_chain_spec()
    doc = build_bindings_document(spec)
    field = probe_mod.Field(doc, {})
    metric = field.art_outside_parent_grid(doc["meshes"][0])
    # 方块 4×4 个点、y 从 -260 到 60（步长 106.67）⇒ 两行（-260、-153.3）落在
    # 父矩形下沿 -100 之外 = 8 个顶点；子级矩形就是画的矩形，所以那一读数是 0。
    assert metric["total"] == 8, metric
    assert metric["vertices"] == 16, metric
    assert metric["levels"] == {"Parent": 8}, metric


@requires_live2d
def test_official_core_draws_out_of_rect_art_where_the_ruler_now_puts_it(tmp_path):
    """三方一致的唯一硬判据：Core 画在哪，尺子就要读在哪（越界画，静止帧）。"""
    from drivers.live2d_runtime.moc3_verify import render_probe

    spec = out_of_rect_chain_spec()
    doc = compile_static_rig(spec)
    manifest = _package(tmp_path, doc.to_bytes(), "probe_out_of_grid")
    rest = render_probe(str(manifest))
    if not rest["ok"]:
        pytest.skip(f"官方 Core 不可用：{rest.get('blocker')}")
    # 诊断实样：与无变形器对照 A2_ctrl_out 逐字节相同，bbox 下沿 452
    assert rest["alpha_bbox"][3] == pytest.approx(452, abs=3), rest["alpha_bbox"]

    bindings = build_bindings_document(spec)
    field = probe_mod.Field(bindings, {})
    _rest, moved = field.deformed(bindings["meshes"][0])
    predicted = max(_screen_y(bindings, y) for _x, y in moved)
    assert predicted == pytest.approx(rest["alpha_bbox"][3], abs=2.0), (
        f"内核画到 {rest['alpha_bbox'][3]}，尺子读 {predicted:.1f}："
        "越界画又被夹回去了")


#: 自研播放器里那一份采样器的判据点：矩形 ±100 的恒等 1×1 网格，
#: 采样 (u,v)=(0.5,-0.8) —— 即 x=0、y 挂在矩形下沿之外 160px 处。
#: Core 画在 y=-260（诊断 A2/C_out/D_out：屏幕行 452），夹取版读 y=-100（327.6）。
_PLAYER_SAMPLER_HARNESS = """
import { pathToFileURL } from 'node:url';
const mod = await import(pathToFileURL(process.env.TS_PATH).href);
const grid = new Float32Array([-100, -100, 100, -100, -100, 100, 100, 100]);
const out = { x: NaN, y: NaN };
mod.sampleWarpGrid(grid, 1, 1, 0.5, -0.8, out);
console.log(JSON.stringify(out));
"""


def _player_sampler_out_of_grid():
    """用 node 跑 `web/lib/live2d-bindings.ts` 的 sampleWarpGrid（同一处夹取）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 node 才能跑 shipped 播放器里那一份采样器")
    ts = ROOT / "web" / "lib" / "live2d-bindings.ts"
    assert ts.is_file(), ts
    env = dict(os.environ, TS_PATH=str(ts))
    proc = subprocess.run([node, "--input-type=module", "-e",
                           _PLAYER_SAMPLER_HARNESS],
                          capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr[-500:]
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    assert lines, proc.stdout[-500:]
    return json.loads(lines[-1])


def test_the_shipped_player_extrapolates_out_of_grid_art_like_the_core_and_the_ruler():
    """#72 的浏览器那一半：播放器与尺子共用同一条越界约定，而且都是 Core 的那条。

    `bindings_gap_probe` 的模块文档就自称是 `live2d-player.ts:updateArtMeshes` 的
    移植，两处 `min(max(u,0),1)` 是同一只手加的。修一边只会让两个渲染器**更加**
    不一致，所以这条同时钉住：TS 的读数 == Python 的读数 == Core 的 -260。
    """
    got = _player_sampler_out_of_grid()
    assert got["y"] == pytest.approx(-260.0, abs=1e-3), (
        f"播放器把越界的画读成 y={got['y']}（塌到父矩形边界），"
        "Core 画的是 -260 —— 浏览器里头发会脱离头")
    assert got["x"] == pytest.approx(0.0, abs=1e-3), got
    want = probe_mod.sample_warp_grid(
        [-100.0, -100.0, 100.0, -100.0, -100.0, 100.0, 100.0, 100.0],
        1, 1, 0.5, -0.8)
    assert got["x"] == pytest.approx(want[0], abs=1e-3), (got, want)
    assert got["y"] == pytest.approx(want[1], abs=1e-3), (got, want)


# ----------------------------------------------------------------------
# 播放器格内采样：必须与尺子、与官方 Core 同为三角形分片线性
# ----------------------------------------------------------------------

_PLAYER_IN_GRID_HARNESS = """
import { pathToFileURL } from 'node:url';
const mod = await import(pathToFileURL(process.env.TS_PATH).href);
// 刻意非仿射：只把右上角拉出去。双线性与三角形在此必然给出不同结果。
const grid = new Float32Array([-100, -60, 100, -60, -100, 60, 400, 60]);
const pts = [[0.35, 0.35], [0.65, 0.65], [0.5, 0.1]];
const samples = [];
for (const [u, v] of pts) {
  const o = { x: NaN, y: NaN };
  mod.sampleWarpGrid(grid, 1, 1, u, v, o);
  samples.push([o.x, o.y]);
}
console.log(JSON.stringify({ samples }));
"""


def _player_sampler_in_grid():
    """用 node 跑 shipped 播放器里的 sampleWarpGrid（非仿射网格的格内采样）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 node 才能跑 shipped 播放器里那一份采样器")
    ts = ROOT / "web" / "lib" / "live2d-bindings.ts"
    assert ts.is_file(), ts
    env = dict(os.environ, TS_PATH=str(ts))
    proc = subprocess.run([node, "--input-type=module", "-e",
                           _PLAYER_IN_GRID_HARNESS],
                          capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr[-500:]
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    assert lines, proc.stdout[-500:]
    return json.loads(lines[-1])["samples"]


def test_the_shipped_player_samples_in_grid_like_the_core_and_the_ruler():
    """浏览器那一半：播放器格内采样必须与尺子一致（三角形分片线性）。

    两处实现是同一只手移植的，改一边不改另一边，浏览器预览就会与官方内核
    画出不一样的变形 —— 这正是"动起来观感不一致"的来源。
    """
    grid = [-100.0, -60.0, 100.0, -60.0, -100.0, 60.0, 400.0, 60.0]
    samples = _player_sampler_in_grid()
    for (u, v), (gx, gy) in zip([(0.35, 0.35), (0.65, 0.65), (0.5, 0.1)], samples):
        want = probe_mod.sample_warp_grid(grid, 1, 1, u, v)
        assert gx == pytest.approx(want[0], abs=1e-3), (u, v, gx, want)
        assert gy == pytest.approx(want[1], abs=1e-3), (u, v, gy, want)
    # 双线性在这个网格上给出 (6.75, -18.0)；Core 实测走的是三角形 (-30, -18)
    assert samples[0][0] == pytest.approx(-30.0, abs=1e-3), samples


# ----------------------------------------------------------------------
# Task 1.5 审查前移的那块未测区：**子级矩形整体越出父级矩形**
#
# 官方实样里约 15% 的嵌套矩形就在父级之外，而编译器今天照收（st 读到 -0.25/1.25）。
# Task 3 的 HeadRig 成员（发梢、耳、饰物）正是这一类，所以这条必须有用例。
# 判据按诊断的实样写：Core 在这种链上既不裁也不歪 —— C_out_4x3_rest 与
# D_out_1x1_rest 都跟「没有变形器」的对照**逐字节相同**，驱动键（E/G：平移与仿射
# 旋转）与对照差恰好等于场本身。也就是说「中间层是恒等的」就等于「没有中间层」，
# 与它在不在父级矩形之内无关。
# ----------------------------------------------------------------------

def child_outside_parent_spec(middle=True):
    """1×1 父级（±100、带 ±40px 平移键形）→ 方块；``middle`` 决定要不要中间层。

    中间层 Child 的点阵就是它自己静止矩形的恒等映射，而它的矩形整体挂在父级
    矩形之下 100px 处 —— 对照（``middle=False``）是同一个方块直接挂到父级。
    两者读数的差就是「中间层到底改变了什么」的答案。
    """
    rect = (-60.0, -260.0, 60.0, 60.0)
    parent = _keyform_parent()
    corners = [[x, y] for x, y in [rect[0:2], (rect[2], rect[1]),
                                   rect[2:4], (rect[0], rect[3])]]
    if middle:
        child = WarpDeformerSpec(
            "Child", 1, 1, [DeformerGrid(0.0, _lattice_of(rect, 1, 1))],
            parent_deformer_id="Parent")
        mesh = MeshSpec("ArtProbe", corners, _TRIS, list(_UVS),
                        deformer_id="Child")
        deformers = [parent, child]
    else:
        mesh = MeshSpec("ArtProbe", corners, _TRIS, list(_UVS),
                        deformer_id="Parent")
        deformers = [parent]
    return RigSpec(meshes=[mesh], deformers=deformers, parameters=[_angle()],
                   canvas_width=512.0, canvas_height=512.0, pixels_per_unit=PPU)


@pytest.mark.parametrize("values", [{}, {"ParamAngleZ": 30.0}, {"ParamAngleZ": -30.0}],
                         ids=["rest", "z+30", "z-30"])
def test_a_child_rect_outside_the_parent_compiles_and_stays_neutral(values):
    """越出父级矩形不崩、也不改变画面：恒等中间层 == 没有中间层（静止与满幅）。"""
    nested_spec = child_outside_parent_spec(middle=True)
    plain_spec = child_outside_parent_spec(middle=False)
    doc = compile_static_rig(nested_spec)      # 崩溃就是这条用例的失败
    assert doc.get("deformer.parent_deformer_indices") is not None

    nested = _offline_positions(nested_spec, values)
    plain = _offline_positions(plain_spec, values)
    assert len(nested) == len(plain) == 4
    for (nx, ny), (px, py) in zip(nested, plain):
        assert nx == pytest.approx(px, abs=1e-9), (values, nested, plain)
        assert ny == pytest.approx(py, abs=1e-9), (values, nested, plain)

    # 尺子的读数必须与**产物字节**按规则 I 复算的一致（外推后两边才是同一个约定）
    reference = _rule_i_from_moc3(doc, nested_spec, values)
    for (rx, ry), (ox, oy) in zip(reference, nested):
        assert ox == pytest.approx(rx, abs=1e-9), (values, reference, nested)
        assert oy == pytest.approx(ry, abs=1e-9), (values, reference, nested)

    # 而越界这件事本身要单独点名，不能悄悄算进静止偏差
    bindings = build_bindings_document(nested_spec)
    field = probe_mod.Field(bindings, {})
    metric = field.art_outside_parent_grid(bindings["meshes"][0])
    assert metric["levels"] == {"Parent": 2}, metric   # 四个角里下面那两个在父矩形之外
