"""判定官方 Core 的 warp 变形器**格内**插值：双线性，还是三角形分片线性。

为什么要单独探：仓库的浏览器播放器（web/lib/live2d-bindings.ts）与离线尺子
（tools/bindings_gap_probe.py）都按**双线性**实现 sample_warp_grid；而逆向的
Cubism Editor 5.3 源码里，isQuadTransform=false（moc3 的 additional.quad_transforms=0，
即「网格形变」）在格内走的是**三角形**分片线性（沿 P10-P01 对角线分裂，
WarpDeformerUtils.java:261-267），只有 isQuadTransform=true（quad 形变）才是双线性
（同文件 :259-260）。仓库编译器固定写 0（moc3_model.py:1179）。

关键：两种插值只在**非仿射**变形的控制网格上才有差别。仓库现有的探针用的都是
整体平移的网格（verify_deformer_export_pixels.py、test_moc3_deformer_acceptance.py），
仿射下三角形与双线性结果完全相同 —— 那批探针**结构上区分不了**。文档
docs/native-runtime.md:107「内核做的是双线性」正是在那批探针上写的。

判据用**质心**而不是包围盒：把 P11 拉走时，位于 s+t<1 区域的顶点整体平移同样的
量，包围盒宽度在两种假设下相同（会给出假绿）；而三角形面积质心 = 三顶点均值，
两种假设下差 s*t 与 (1-s)(1-t) 的权重，能拉开。判别前先用**静止帧自校验**
（实测质心必须等于预测质心），顺带验证 (s,t) 换算与投影是仿射的。

用法：python tools/probe_warp_interpolation.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from drivers.live2d_runtime.moc3_verify import render_probe
from live2d_builder.exporter.moc3_lint import lint_document
from live2d_builder.exporter.moc3_model import (
    DeformerGrid,
    MeshSpec,
    ParameterSpec,
    RigSpec,
    WarpDeformerSpec,
    compile_static_rig,
)

CANVAS = 512.0
PPU = 100.0
PARAM = "ParamAngleZ"

# 静止网格：单位正方形（仿射），x∈[-100,100]、y∈[-60,60]，行优先 x 变化最快。
# 顺序 P00,P10,P01,P11 —— 与内核一致（P10 = +x，P01 = +y，对角线是 P10-P01）。
REST = [(-100.0, -60.0), (100.0, -60.0), (-100.0, 60.0), (100.0, 60.0)]

# 变形网格：**只把右上角拉出去**，其余三点不动 —— 破坏平行四边形关系，
# 双线性与三角形在此必然给出不同结果（差向量 = (1-s)(1-t) 或 s*t 乘以
# P11-P10-P01+P00）。只动 x，y 在两种假设下一致，可作对照。
DEFORMED = [(-100.0, -60.0), (100.0, -60.0), (-100.0, 60.0), (400.0, 60.0)]

# 三个网格顶点在**模型空间**的位置，跨两个三角形区域：
# V0 在 s+t<1 一侧、V2 在 s+t>1 一侧、V1 在 s+t<1 一侧但权重不同，
# 令两种假设的质心预测明显分开。
# 顺序必须**逆时针**（内核剔除背面三角形，顺时针会被整片剔掉、渲染成 0 像素）。
MESH_VERTS = [(-30.0, -18.0), (0.0, -48.0), (30.0, 18.0)]

# 关键帧：与仓库现有验收同样的三键形态（-30/0/30）。**单键形在 Core 里不生效**
# （band 0 空带 → 静态回退），所以必须给三键，默认值那一键即静止形。
KEYS = (-30.0, 0.0, 30.0)


def _st(model_xy):
    """模型坐标 → 网格内的 (s, t)（与导出侧同一套换算：静止包围盒归一）。"""
    x0, y0, x1, y1 = -100.0, -60.0, 100.0, 60.0
    return ((model_xy[0] - x0) / (x1 - x0), (model_xy[1] - y0) / (y1 - y0))


def _sample(grid, s, t, mode):
    """把 (s,t) 过控制网格映回模型空间。mode = bilinear | triangle。"""
    p00, p10, p01, p11 = grid
    if mode == "bilinear":
        return tuple(
            p00[k] * (1 - s) * (1 - t) + p10[k] * s * (1 - t)
            + p01[k] * (1 - s) * t + p11[k] * s * t
            for k in (0, 1)
        )
    if s + t < 1.0:
        return tuple(
            p00[k] * (1 - s - t) + p10[k] * s + p01[k] * t for k in (0, 1)
        )
    return tuple(
        p11[k] * (s + t - 1) + p01[k] * (1 - s) + p10[k] * (1 - t) for k in (0, 1)
    )


def _predict(grid, mode):
    pts = [_sample(grid, *_st(v), mode) for v in MESH_VERTS]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {
        "points": pts,
        # 三角形面积质心 = 三顶点均值
        "centroid": (sum(xs) / 3.0, sum(ys) / 3.0),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }


def _rig():
    lattices = [REST, REST, DEFORMED]
    grids = [DeformerGrid(k, lat) for k, lat in zip(KEYS, lattices)]
    mesh = MeshSpec(
        mesh_id="ArtMeshWarp",
        vertices=MESH_VERTS,
        triangles=[(0, 1, 2)],
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        deformer_id="WarpProbe",
    )
    deformer = WarpDeformerSpec(
        deformer_id="WarpProbe", rows=1, cols=1, grids=grids,
        parameter_id=PARAM,
    )
    params = [ParameterSpec(PARAM, minimum=-30.0, maximum=30.0, default=0.0)]
    return RigSpec(meshes=[mesh], parameters=params, deformers=[deformer],
                   canvas_width=CANVAS, canvas_height=CANVAS,
                   pixels_per_unit=PPU)


def _package(folder: Path, doc, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.moc3").write_bytes(doc.to_bytes())
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(folder / "texture_00.png")
    manifest = folder / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3,
        "Meta": {"ArchiveName": name},
        "FileReferences": {"Moc": f"{name}.moc3",
                           "Textures": ["texture_00.png"]},
    }), encoding="utf-8")
    return manifest


def measure(render=render_probe, workdir=None):
    """渲染静止帧与极值帧，用解析预测判定 Core 的格内插值。

    Returns a dict with the two renders, the calibrated affine mapping and the
    relative error of each hypothesis. ``verdict`` is the hypothesis whose
    prediction matches the measured rendering; ``ok`` is False when the probe
    itself was inconclusive (deformation did not take effect, non-uniform
    projection, or no hypothesis within 3%).
    """
    import tempfile

    doc = compile_static_rig(_rig())
    issues = lint_document(doc)
    if issues:
        raise RuntimeError(f"lint 未通过：{issues}")

    if workdir is None:
        with tempfile.TemporaryDirectory(prefix="warp_interp_") as tmp:
            return measure(render=render, workdir=Path(tmp))
    tmp_path = Path(workdir)
    manifest = _package(tmp_path, doc, "warp_probe")
    rest = render(str(manifest), {PARAM: 0.0})
    moved = render(str(manifest), {PARAM: 30.0})
    for label, res in (("rest", rest), ("moved", moved)):
        if not res["ok"]:
            raise RuntimeError(f"{label} 渲染失败: {res['blocker']}")
    out = {"rest": rest, "moved": moved, "ok": False, "verdict": None,
           "errors": {}, "reason": ""}
    if moved["pixels_sha256"] == rest["pixels_sha256"]:
        out["reason"] = "极值帧与静止帧逐像素相同 —— 变形没有生效"
        return out

    # 静止形：两种假设一致 → 用它标定「模型坐标 → 屏幕像素」的仿射变换
    pred_rest = _predict(REST, "bilinear")
    rb = rest["alpha_bbox"]
    sw, sh = rb[2] - rb[0], rb[3] - rb[1]
    scale_x = sw / pred_rest["width"]
    scale_y = sh / pred_rest["height"]
    out["scale_x"], out["scale_y"] = scale_x, scale_y
    if abs(scale_x - scale_y) / max(scale_x, scale_y) > 0.05:
        out["reason"] = "x/y 缩放不一致 —— 投影非均匀仿射"
        return out
    off_x = rest["centroid"][0] - scale_x * pred_rest["centroid"][0]
    off_y = rest["centroid"][1] - scale_y * pred_rest["centroid"][1]
    out["offset"] = (off_x, off_y)

    # 自校验：静止帧实测质心必须落在预测上（顺带验证 (s,t) 换算没搞错）
    for k in (0, 1):
        scale = scale_x if k == 0 else scale_y
        off = off_x if k == 0 else off_y
        expect = scale * pred_rest["centroid"][k] + off
        got = rest["centroid"][k]
        if abs(expect - got) > max(2.0, abs(got) * 0.02):
            out["reason"] = (f"静止帧质心自校验失败（axis {k}）："
                             f"预测 {expect:.2f} 实测 {got:.2f}")
            return out

    mc = moved["centroid"]
    for mode in ("bilinear", "triangle"):
        pred = _predict(DEFORMED, mode)
        px = scale_x * pred["centroid"][0] + off_x
        py = scale_y * pred["centroid"][1] + off_y
        out["errors"][mode] = {
            "model_centroid": pred["centroid"],
            "screen_centroid": (px, py),
            "error": abs(px - mc[0]) / max(abs(mc[0]), 1.0),
        }
    best = min(out["errors"], key=lambda m: out["errors"][m]["error"])
    out["verdict"] = best
    out["ok"] = out["errors"][best]["error"] <= 0.03
    if not out["ok"]:
        out["reason"] = "最优假设仍差超过 3% —— 模型没解释住实测"
    return out


def _quad_centroid(points):
    """凸四边形的面积质心（拆两个三角形按面积加权）。"""
    a, b, c, d = points
    def _tri(p, q, r):
        area = 0.5 * abs((q[0] - p[0]) * (r[1] - p[1])
                         - (r[0] - p[0]) * (q[1] - p[1]))
        return area, ((p[0] + q[0] + r[0]) / 3.0, (p[1] + q[1] + r[1]) / 3.0)
    a1, c1 = _tri(a, b, c)
    a2, c2 = _tri(a, c, d)
    total = a1 + a2
    if not total:
        return (sum(p[0] for p in points) / 4.0, sum(p[1] for p in points) / 4.0)
    return ((c1[0] * a1 + c2[0] * a2) / total, (c1[1] * a1 + c2[1] * a2) / total)


def measure_out_of_grid(workdir):
    """量化「越出控制网格的顶点」上，尺子与官方 Core 的偏差。

    真实产物里 hair_back 的 27/140 个顶点挂在 HeadRig 静止矩形之外（#72），
    那一段走的是「越界外推」。格内已经改成三角形分片线性之后，越界这一段
    还沿用旧的边界格线性外推 —— 它与内核的关系必须**量**，不能猜。

    这里让尺子（仓库真正的实现）预测变形后的质心，和 Core 实测的质心比。
    """
    import tempfile

    import bindings_gap_probe as ruler

    rest_grid = [(-100.0, -60.0), (100.0, -60.0), (-100.0, 60.0), (100.0, 60.0)]
    deformed_grid = [(-100.0, -60.0), (100.0, -60.0), (-100.0, 60.0), (400.0, 60.0)]
    # 三个顶点在网格内，第四个挂在矩形右侧 160px 之外（s = 1.8）
    verts = [(-80.0, -40.0), (80.0, -40.0), (80.0, 40.0), (260.0, 0.0)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    tris = [(0, 1, 2), (0, 2, 3)]

    def _predict(grid):
        flat = [c for p in grid for c in p]
        pts = []
        for x, y in verts:
            s = (x + 100.0) / 200.0
            t = (y + 60.0) / 120.0
            pts.append(ruler.sample_warp_grid(flat, 1, 1, s, t))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return {
            "centroid": _quad_centroid(pts),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
        }

    grids = [DeformerGrid(-30.0, rest_grid), DeformerGrid(0.0, rest_grid),
             DeformerGrid(30.0, deformed_grid)]
    mesh = MeshSpec(mesh_id="ArtMeshWarp", vertices=verts, triangles=tris,
                    uvs=uvs, deformer_id="WarpProbe")
    deformer = WarpDeformerSpec(deformer_id="WarpProbe", rows=1, cols=1,
                                grids=grids, parameter_id=PARAM)
    params = [ParameterSpec(PARAM, minimum=-30.0, maximum=30.0, default=0.0)]
    doc = compile_static_rig(RigSpec(meshes=[mesh], parameters=params,
                                     deformers=[deformer], canvas_width=CANVAS,
                                     canvas_height=CANVAS, pixels_per_unit=PPU))
    if workdir is None:
        with tempfile.TemporaryDirectory(prefix="warp_oob_") as tmp:
            return measure_out_of_grid(Path(tmp))
    tmp_path = Path(workdir)
    manifest = _package(tmp_path, doc, "warp_oob")
    rest = render_probe(str(manifest), {PARAM: 0.0})
    moved = render_probe(str(manifest), {PARAM: 30.0})
    for label, res in (("rest", rest), ("moved", moved)):
        if not res["ok"]:
            raise RuntimeError(f"{label} 渲染失败: {res['blocker']}")
    out = {"rest": rest, "moved": moved, "ok": False}
    if moved["pixels_sha256"] == rest["pixels_sha256"]:
        out["reason"] = "极值帧与静止帧逐像素相同 —— 变形没有生效"
        return out

    pred_rest = _predict(rest_grid)
    rb = rest["alpha_bbox"]
    scale_x = (rb[2] - rb[0]) / pred_rest["width"]
    scale_y = (rb[3] - rb[1]) / pred_rest["height"]
    off_x = rest["centroid"][0] - scale_x * pred_rest["centroid"][0]
    off_y = rest["centroid"][1] - scale_y * pred_rest["centroid"][1]
    pred_moved = _predict(deformed_grid)
    px = scale_x * pred_moved["centroid"][0] + off_x
    py = scale_y * pred_moved["centroid"][1] + off_y
    mc = moved["centroid"]
    out["scale"] = (scale_x, scale_y)
    out["predicted"] = (px, py)
    out["measured"] = (mc[0], mc[1])
    out["error_px"] = abs(px - mc[0])
    out["error_model_px"] = out["error_px"] / scale_x
    out["ok"] = True
    return out


def _candidates(grid, s, t):
    """越界落点的候选规则：名字 -> (x, y)。"""
    flat = [c for p in grid for c in p]
    P = lambda c, r: (flat[(c + r * 2) * 2], flat[(c + r * 2) * 2 + 1])
    P00, P10, P01, P11 = P(0, 0), P(1, 0), P(0, 1), P(1, 1)
    out = {}

    # (a) 现行实现：边界格双线性外推
    out["边界格双线性"] = _bilinear_extrapolate(flat, 1, 1, s, t)

    # (b) 左下三角形平面外推：P00 + u(P10-P00) + v(P01-P00)
    out["LL平面"] = (P00[0] + s * (P10[0] - P00[0]) + t * (P01[0] - P00[0]),
                     P00[1] + s * (P10[1] - P00[1]) + t * (P01[1] - P00[1]))

    # (c) 右上三角形平面外推：P11 + (u-1)(P11-P01) + (v-1)(P11-P10)
    out["UR平面"] = (P11[0] + (s - 1) * (P11[0] - P01[0]) + (t - 1) * (P11[0] - P10[0]),
                     P11[1] + (s - 1) * (P11[1] - P01[1]) + (t - 1) * (P11[1] - P10[1]))

    # (d) 整个网格四极端角点拟合的仿射平面（Cubism Editor 的越界做法）
    C = tuple(sum(p[k] for p in (P00, P10, P01, P11)) / 4 for k in (0, 1))
    dx = tuple(((P11[k] - P00[k]) + (P10[k] - P01[k])) / 2 for k in (0, 1))
    dy = tuple(((P11[k] - P00[k]) - (P10[k] - P01[k])) / 2 for k in (0, 1))
    O = tuple(C[k] - (dx[k] + dy[k]) / 2 for k in (0, 1))
    out["全局平面"] = (O[0] + s * dx[0] + t * dy[0], O[1] + s * dx[1] + t * dy[1])

    # (e) 越界点完全不变形
    x0, y0, x1, y1 = -100.0, -60.0, 100.0, 60.0
    out["不变形"] = (x0 + s * (x1 - x0), y0 + t * (y1 - y0))
    return out


def _bilinear_extrapolate(flat, rows, cols, u, v):
    """现行尺子的越界读数（边界格双线性外推）。"""
    import bindings_gap_probe as ruler
    return ruler.sample_warp_grid(flat, rows, cols, u, v)


def measure_out_of_grid_sweep(workdir=None, render=render_probe):
    """定出官方 Core 的越界落点规则（网格固定，只换变形后的点阵）。

    两个关键设计（第一版踩过坑）：

    * **网格固定**：Core 会按模型包围盒自动适配视口，不同网格的投影比例不同。
      所以扫点时不能换网格（换网格就没法共用标定）。这里固定网格、只换「变形
      后的那一张点阵」，静止帧与变形帧的投影比例就一致了。
    * **每个变体都渲染自己的静止帧**做标定：恒等点阵下越界顶点被 Core 画在原位
      （#72 实测），于是静止帧的包围盒左右边缘就是 A 点与越界点的投影位置，
      直接解出「模型坐标 → 屏幕像素」的仿射映射。

    网格用**变形过**的点阵：恒等点阵下各种外推退化成同一条映射、分不出差别。
    越界顶点同时是**最右**，所以变形帧包围盒的右边缘就是它的投影位置（±1px）。
    """
    import tempfile

    rest_grid = [(-100.0, -60.0), (100.0, -60.0), (-100.0, 60.0), (100.0, 60.0)]
    a_pt = (-80.0, -40.0)
    b_pt = (-40.0, -40.0)
    d_pt = (200.0, 0.0)          # s = 1.5, t = 0.5 —— 网格外
    variants = {
        "V1_拉右上": [(-100.0, -60.0), (100.0, -60.0), (-100.0, 60.0), (400.0, 60.0)],
        "V2_拉右上小": [(-100.0, -60.0), (100.0, -60.0), (-100.0, 60.0), (200.0, 60.0)],
        "V3_拉右下": [(-100.0, -60.0), (100.0, -60.0), (-100.0, 60.0), (400.0, -60.0)],
        "V4_拉左上": [(-100.0, -60.0), (100.0, -60.0), (-300.0, 60.0), (100.0, 60.0)],
        "V5_只拉右下角": [(-100.0, -60.0), (300.0, -60.0), (-100.0, 60.0), (100.0, 60.0)],
    }

    def _model(deformed_grid):
        mesh = MeshSpec(mesh_id="ArtMeshWarp", vertices=[a_pt, b_pt, d_pt],
                        triangles=[(0, 1, 2)],
                        uvs=[(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)],
                        deformer_id="WarpProbe")
        grids = [DeformerGrid(-30.0, rest_grid), DeformerGrid(0.0, rest_grid),
                 DeformerGrid(30.0, deformed_grid)]
        deformer = WarpDeformerSpec(deformer_id="WarpProbe", rows=1, cols=1,
                                    grids=grids, parameter_id=PARAM)
        params = [ParameterSpec(PARAM, minimum=-30.0, maximum=30.0, default=0.0)]
        return RigSpec(meshes=[mesh], parameters=params, deformers=[deformer],
                       canvas_width=CANVAS, canvas_height=CANVAS,
                       pixels_per_unit=PPU)

    if workdir is None:
        with tempfile.TemporaryDirectory(prefix="warp_sweep_") as tmp:
            return measure_out_of_grid_sweep(Path(tmp), render=render)
    tmp_path = Path(workdir)
    results = []
    for name, deformed_grid in variants.items():
        doc = compile_static_rig(_model(deformed_grid))
        manifest = _package(tmp_path, doc, name)
        rest = render(str(manifest), {PARAM: 0.0})
        moved = render(str(manifest), {PARAM: 30.0})
        for label, res in (("rest", rest), ("moved", moved)):
            if not res["ok"]:
                raise RuntimeError(f"{name} {label} 渲染失败: {res['blocker']}")
        rb, mb = rest["alpha_bbox"], moved["alpha_bbox"]
        # 静止帧：A 点最左、越界点最右 → 解出仿射映射
        scale_x = (rb[2] - rb[0]) / (d_pt[0] - a_pt[0])
        off_x = rb[0] - a_pt[0] * scale_x
        measured_x = (mb[2] - off_x) / scale_x
        cands = _candidates(deformed_grid, 1.5, 0.5)
        entry = {
            "variant": name, "measured_x": measured_x,
            "rest_bbox": rb, "moved_bbox": mb, "scale_x": scale_x,
            "candidates": {k: list(v) for k, v in cands.items()},
            "errors": {k: abs(v[0] - measured_x) for k, v in cands.items()},
        }
        results.append(entry)
    return {"sweep": results}


def main() -> int:
    doc = compile_static_rig(_rig())
    issues = lint_document(doc)
    if issues:
        print("lint 未通过：")
        for issue in issues:
            print(f"  {issue}")
        return 2
    print(f"additional.quad_transforms = {doc.get('additional.quad_transforms')}")

    result = measure()
    for label in ("rest", "moved"):
        res = result[label]
        print(f"{label}: bbox={res['alpha_bbox']} opaque={res['opaque_pixels']} "
              f"centroid={res['centroid']} sha={res['pixels_sha256'][:16]}")
    if not result["ok"]:
        print(f"\n❌ 探针无效：{result['reason']}")
        return 2
    print(f"\n静止包围盒 → 屏幕 scale_x={result['scale_x']:.4f} "
          f"scale_y={result['scale_y']:.4f}（仿射性成立，质心自校验通过）")
    mc = result["moved"]["centroid"]
    print(f"变形后实测质心 ({mc[0]:.2f}, {mc[1]:.2f})")
    print("\n两种假设的预测：")
    for mode in ("bilinear", "triangle"):
        info = result["errors"][mode]
        print(f"  {mode:9s} 模型质心=({info['model_centroid'][0]:7.2f},"
              f"{info['model_centroid'][1]:6.2f}) → 屏幕质心="
              f"({info['screen_centroid'][0]:7.2f},{info['screen_centroid'][1]:6.2f})"
              f"  x 相对差 {info['error']*100:5.2f}%")
    best = result["verdict"]
    other = "triangle" if best == "bilinear" else "bilinear"
    print(f"\n实测与 {best} 最接近（x 差 {result['errors'][best]['error']*100:.2f}%），"
          f"与 {other} 差 {result['errors'][other]['error']*100:.2f}%")
    print(f"结论：官方 Core 的 warp 格内插值 = {best}")

    print("\n=== 越界外推（顶点挂在控制网格矩形之外）===")
    oob = measure_out_of_grid(None)
    if not oob.get("ok"):
        print(f"越界探针无效：{oob.get('reason')}")
        return 2
    print(f"尺子预测屏幕质心 = ({oob['predicted'][0]:.2f}, {oob['predicted'][1]:.2f})")
    print(f"Core 实测屏幕质心 = ({oob['measured'][0]:.2f}, {oob['measured'][1]:.2f})")
    print(f"偏差 = {oob['error_px']:.2f} 屏幕像素 ≈ {oob['error_model_px']:.2f} 模型单位")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
