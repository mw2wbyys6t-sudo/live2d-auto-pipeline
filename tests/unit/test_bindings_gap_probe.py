"""离线求值器（tools/bindings_gap_probe.py）必须和官方内核用同一条空间约定。

Task 6 的配对距离闸门、以及一切「跨层相对位移」读数都出自这把尺子。尺子量的
是 bindings.json（**模型像素**规格），内核量的是 moc3（逐级 (s,t) 存储），两者
之间隔着 Task 1.5 修的这条空间基准 —— 所以这里的对照是三条独立的实现：

  1. 从规格出发的离线求值 `Field.chain()`；
  2. 从**产物字节**出发、按实测规则 I（上一级的输出原样当父级点阵的 (s,t)，
     不再按父级静止矩形归一）复算的参考实现 `_rule_i_from_moc3()`；
  3. 官方 Core 的渲染位移（有 Core 时）。

实测依据：F:\\tmp\\trunk-diag\\nested-warp-diagnosis.md（10 帧官方 Core），
`experiments.json` 里 R2/R3/R5/R6 的离线预测与渲染读数一致。
"""
import importlib.util
import json
import shutil
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


def root_level_spec():
    """只有一级（根 warp）的同一套键形：今天产物的形状，同步不许把它改坏。"""
    parent = _keyform_parent()
    mesh = MeshSpec("ArtProbe", list(_SQUARE), _TRIS, list(_UVS),
                    deformer_id="Parent")
    return RigSpec(meshes=[mesh], deformers=[parent], parameters=[_angle()],
                   canvas_width=512.0, canvas_height=512.0, pixels_per_unit=PPU)


# ----------------------------------------------------------------------
# 参考实现：直接按实测的规则 I 在**产物**上求值
# ----------------------------------------------------------------------

def _stored_warps(doc, spec):
    """moc3 里写出去的东西：每级每键的点阵（单位）+ 父级下标 + 成员顶点的 (s,t)。"""
    warps = [d for d in spec.deformers if isinstance(d, WarpDeformerSpec)]
    assert len(warps) == len(spec.deformers), "这条参考实现只覆盖全 warp 的链"
    for deformer in warps:
        assert deformer.rows == 1 and deformer.cols == 1, (
            "rows/cols 必须是 1：那时「矩形线性映射」才严格等于内核的双线性采样")
    ids = doc.get("deformer.ids")
    parents = doc.get("deformer.parent_deformer_indices")
    assert ids == [d.deformer_id for d in warps], (
        "这条参考实现假设变形器表全是 warp 且顺序一致（deformers 与 warps 同一张表）")
    begins = doc.get("warp_deformer_keyform.keyform_position_begin_indices")
    counts = doc.get("warp_deformer.keyform_counts")
    xs = doc.get("keyform_position.xys")
    lattices = {}
    running = 0
    for index, deformer in enumerate(warps):
        lattices[deformer.deformer_id] = []
        for grid in range(counts[index]):
            begin = begins[running + grid]
            flat = xs[begin:begin + 2 * deformer.vertex_count]
            lattices[deformer.deformer_id].append(
                [(flat[k], flat[k + 1]) for k in range(0, len(flat), 2)])
        running += counts[index]
    mesh_began = doc.get("art_mesh_keyform.keyform_position_begin_indices")
    mesh_counts = doc.get("art_mesh.position_index_counts")   # = 唯一顶点数
    mesh_deformers = doc.get("art_mesh.parent_deformer_indices")
    mesh_st = [[(xs[mesh_began[m] + 2 * k], xs[mesh_began[m] + 2 * k + 1])
                for k in range(mesh_counts[m])]
               for m in range(len(mesh_counts))]
    return warps, ids, parents, lattices, mesh_st, mesh_deformers


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
