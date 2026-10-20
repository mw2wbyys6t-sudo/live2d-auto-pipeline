"""像素格网与关节拆分：绑定前准备的两条硬保证。

1. **覆盖可证明**：图层里每一个像素都必须落在某个三角形内。这是「把人物
   全部瞄点住」的唯一可验收定义，所以用最坏形状（细缝、凹口、孤立点）
   逐个验证，而不是只测一个方形。
2. **拆分不丢像素**：按关节拆手臂、按段拆长发时，前后像素数必须守恒——
   少掉的像素在成图里就是透明的洞。
"""

import numpy as np
import pytest
from PIL import Image

from core.segment_engine.rig_split import (
    build_rig_parts,
    principal_axis,
    split_into_segments,
    split_limb_at_joint,
)
from live2d_builder.mesh.generator import MeshGenerator
from live2d_builder.mesh.pixel_grid import (
    PixelGridMesher,
    coverage_of_parts,
    measure_coverage,
)


def _rgba(mask: np.ndarray) -> Image.Image:
    arr = np.zeros(mask.shape + (4,), dtype=np.uint8)
    arr[mask] = (200, 80, 80, 255)
    return Image.fromarray(arr, "RGBA")


def _canvas(h=120, w=120):
    return np.zeros((h, w), dtype=bool)


class TestPixelGridCoverage:
    @pytest.mark.parametrize("spacing,min_cell", [(16, 4), (12, 4), (8, 2), (24, 6)])
    def test_block_is_fully_covered(self, spacing, min_cell):
        mask = _canvas()
        mask[20:90, 30:70] = True

        mesh = PixelGridMesher(spacing=spacing, min_cell=min_cell).build(mask)
        cov = measure_coverage(mask, mesh)

        assert cov["coverage"] == 1.0, cov
        assert cov["uncovered_px"] == 0

    def test_thin_sliver_is_covered(self):
        """比间距还窄的细条：Delaunay 重心过滤会整条漏掉，格网不能漏。"""
        mask = _canvas()
        mask[40:80, 10:12] = True          # 2px 宽的竖线

        mesh = PixelGridMesher(spacing=16, min_cell=2).build(mask)
        cov = measure_coverage(mask, mesh)

        assert cov["coverage"] == 1.0

    def test_concave_shape_is_covered(self):
        """凹口（U 形）：每个凹角里都不能有未覆盖像素。"""
        mask = _canvas()
        mask[20:100, 20:100] = True
        mask[40:80, 45:75] = False         # 挖掉中间的凹槽

        mesh = PixelGridMesher(spacing=16, min_cell=2).build(mask)
        cov = measure_coverage(mask, mesh)

        assert cov["coverage"] == 1.0

    def test_isolated_single_pixel_is_covered(self):
        mask = _canvas()
        mask[60:80, 60:80] = True
        mask[5, 5] = True                  # 与主体完全不相连的孤立点

        mesh = PixelGridMesher(spacing=16, min_cell=2).build(mask)
        cov = measure_coverage(mask, mesh)

        assert cov["coverage"] == 1.0

    def test_diagonal_edge_is_covered(self):
        """对角边：格网必须沿对角线细分，不能只保留重心在内的三角形。"""
        mask = _canvas()
        for y in range(120):
            mask[y, : max(1, 120 - y)] = True

        mesh = PixelGridMesher(spacing=16, min_cell=4).build(mask)
        cov = measure_coverage(mask, mesh)

        assert cov["coverage"] == 1.0

    def test_empty_mask_returns_empty_mesh(self):
        mesh = PixelGridMesher().build(_canvas())

        assert len(mesh["vertices"]) == 0
        assert mesh["indices"] == []


    def test_vertices_stay_on_pixel_lattice(self):
        """「像素网格」的字面要求：顶点必须落在整数像素格点上。"""
        mask = _canvas()
        mask[10:100, 15:95] = True

        mesh = PixelGridMesher(spacing=16, min_cell=4).build(mask)

        pts = np.asarray(mesh["vertices"], dtype=float)
        assert np.allclose(pts, np.round(pts)), "顶点偏离像素格点"

    def test_triangles_are_inside_their_cells(self):
        """三角形不能越出所属格子，否则覆盖就不是按格计算的。"""
        mask = _canvas()
        mask[12:70, 20:90] = True

        mesh = PixelGridMesher(spacing=16, min_cell=4).build(mask)

        pts = np.asarray(mesh["vertices"], dtype=float)
        cells = mesh["cells"]
        for tri in mesh["indices"]:
            tri_pts = pts[list(tri)]
            # 找出包含该三角形的格
            hit = [
                c for c in cells
                if c[0] <= tri_pts[:, 0].min() and tri_pts[:, 0].max() <= c[2]
                and c[1] <= tri_pts[:, 1].min() and tri_pts[:, 1].max() <= c[3]
            ]
            assert hit, f"三角形 {tri} 不属于任何格"

    def test_coverage_of_parts_reports_residual(self):
        a = _canvas()
        a[10:40, 10:40] = True
        b = _canvas()
        b[50:90, 50:90] = True
        sil = _canvas()
        sil[10:40, 10:40] = True
        sil[50:90, 50:90] = True
        sil[100:110, 100:110] = True        # 没有任何部件认领

        masks = {"a": a, "b": b}
        mesher = PixelGridMesher(spacing=8, min_cell=2)
        meshes = {n: mesher.build(m) for n, m in masks.items()}

        overall = coverage_of_parts(masks, meshes, sil)

        assert overall["uncovered_px"] == 100
        assert overall["coverage"] < 1.0


class TestJointSplit:
    def test_vertical_limb_splits_at_midpoint(self):
        mask = _canvas(h=200, w=60)
        mask[40:160, 20:40] = True

        split = split_limb_at_joint(mask, 0.5)

        assert split is not None
        prox_y = np.nonzero(split["proximal"])[0]
        dist_y = np.nonzero(split["distal"])[0]
        assert prox_y.max() < dist_y.min() or prox_y.max() <= dist_y.min() + 2
        # 关节落在上下两段的接缝处
        assert 90 <= split["joint"][1] <= 110

    def test_pixel_count_is_conserved(self):
        mask = _canvas(h=200, w=60)
        mask[40:160, 20:40] = True

        split = split_limb_at_joint(mask, 0.5)

        total = int(split["proximal"].sum() + split["distal"].sum())
        assert total == int(mask.sum())
        assert not (split["proximal"] & split["distal"]).any()

    def test_tilted_limb_cut_follows_principal_axis(self):
        """斜向肢体：切口要垂直主轴，不能简单按水平线切。"""
        mask = _canvas(h=200, w=200)
        for i in range(120):
            mask[30 + i, 30 + i : 30 + i + 20] = True

        split = split_limb_at_joint(mask, 0.5)

        assert split is not None
        prox = np.argwhere(split["proximal"])
        dist = np.argwhere(split["distal"])
        # 近端应集中在左上，远端在右下
        assert prox[:, 1].mean() < dist[:, 1].mean()
        assert prox[:, 0].mean() < dist[:, 0].mean()

    def test_tiny_part_is_not_split(self):
        mask = _canvas()
        mask[50:56, 50:56] = True

        assert split_limb_at_joint(mask, 0.5) is None

    def test_principal_axis_on_a_line(self):
        pts = np.stack([np.arange(50), np.arange(50)], axis=1).astype(float)

        _c, axis = principal_axis(pts)

        assert abs(abs(axis[0]) - 0.7071) < 1e-3
        assert abs(abs(axis[1]) - 0.7071) < 1e-3


class TestHairSegments:
    def test_three_segments_conserve_pixels(self):
        mask = _canvas(h=200, w=80)
        mask[20:180, 30:50] = True

        segs = split_into_segments(mask, 3)

        assert segs is not None and len(segs) == 3
        assert sum(int(s["mask"].sum()) for s in segs) == int(mask.sum())
        # 段与段之间不重叠
        union = np.zeros_like(mask)
        for s in segs:
            assert not (union & s["mask"]).any()
            union |= s["mask"]

    def test_first_segment_starts_at_the_root(self):
        """第 1 段必须是最上面那段（发根），否则骨骼父子顺序会反。"""
        mask = _canvas(h=200, w=80)
        mask[20:180, 30:50] = True

        segs = split_into_segments(mask, 3)

        tops = [np.nonzero(s["mask"])[0].min() for s in segs]
        assert tops == sorted(tops)

    def test_wide_short_bangs_split_vertically(self):
        """短而宽的刘海：必须按上下切，不能被 PCA 主轴切成左右两半。"""
        mask = _canvas(h=200, w=200)
        mask[40:80, 20:180] = True        # 160 宽、40 高

        segs = split_into_segments(mask, 2)

        y0 = np.nonzero(segs[0]["mask"])[0]
        y1 = np.nonzero(segs[1]["mask"])[0]
        assert y0.max() < y1.min() or y0.min() < y1.min()


class TestBuildRigParts:
    @staticmethod
    def _parts():
        h, w = 240, 120
        arm = np.zeros((h, w), dtype=bool)
        arm[60:200, 30:50] = True
        hair = np.zeros((h, w), dtype=bool)
        hair[20:230, 60:100] = True
        face = np.zeros((h, w), dtype=bool)
        face[20:70, 80:110] = True
        return {"arm_L": arm, "hair_back": hair, "face": face, "face_extra": face}

    def test_limb_becomes_two_parts_with_parent_chain(self):
        parts, meta, warnings = build_rig_parts(self._parts())

        assert warnings == []
        assert "upper_arm_L" in parts and "forearm_L" in parts
        assert meta["upper_arm_L"]["parent"] == "chest"
        assert meta["forearm_L"]["parent"] == "upper_arm_L"
        assert meta["upper_arm_L"]["joint"] is not None
        assert meta["forearm_L"]["pivot"] == meta["upper_arm_L"]["joint"]

    def test_hair_becomes_three_chained_segments(self):
        parts, meta, _warnings = build_rig_parts(self._parts())

        assert {"hair_back_1", "hair_back_2", "hair_back_3"} <= set(parts)
        assert meta["hair_back_1"]["parent"] == "head"
        assert meta["hair_back_2"]["parent"] == "hair_back_1"
        assert meta["hair_back_3"]["parent"] == "hair_back_2"

    def test_untouched_parts_are_passed_through(self):
        parts, meta, _warnings = build_rig_parts(self._parts())

        assert "face" in parts
        assert meta["face"]["parent"] == "head"

    def test_total_pixels_are_preserved(self):
        src = self._parts()
        parts, meta, _warnings = build_rig_parts(src)

        grouped = {}
        for name, mask in parts.items():
            grouped.setdefault(meta[name].get("source") or name, []).append(mask)
        for source_name, source_mask in src.items():
            got = grouped.get(source_name, [])
            if not got:
                continue
            total = int(np.logical_or.reduce(got).sum())
            assert total == int(source_mask.sum()), f"{source_name} 拆分后像素不守恒"


class TestTinyFeatureMesh:
    """五官这类小图层必须被网格**完整**覆盖。

    历史缺陷：`generate_combined_mesh` 对 39×30 的眼睛按 12px 间距采样轮廓，
    只取到 6 个边界点，三角化+质心过滤后剩 9 个三角形，实测只盖住眼睛的
    27%（眼白 166px 里 118px 落在网格外，瞳孔 236px 里漏 64px）。渲染出来
    就是几块碎片——被脸层复制了同样的眼睛像素掩盖了很久，直到脸层被清理成
    干净皮肤才显形。
    """

    @staticmethod
    def _eye(h=40, w=50, cy=20, cx=25, ry=8, rz=12):
        mask = np.zeros((h, w), dtype=bool)
        yy, xx = np.mgrid[0:h, 0:w]
        mask[((yy - cy) / ry) ** 2 + ((xx - cx) / rz) ** 2 <= 1.0] = True
        return mask

    def test_small_feature_is_fully_covered_by_a_quad(self):
        mask = self._eye()
        assert 100 < int(mask.sum()) <= MeshGenerator.TINY_FEATURE_PIXELS

        mesh = MeshGenerator().generate_combined_mesh(_rgba(mask))
        cov = measure_coverage(mask, mesh)

        assert len(mesh["vertices"]) == 4, "小图层应走四顶点面片"
        assert cov["coverage"] == 1.0, cov
        assert cov["uncovered_px"] == 0

    def test_quad_extends_one_pixel_past_the_mask(self):
        """抗锯齿边低于 alpha 阈值不算 mask，面片要各留 1px 才不切边。"""
        mask = np.zeros((40, 40), dtype=bool)
        mask[10:20, 12:24] = True

        mesh = MeshGenerator().generate_combined_mesh(_rgba(mask))
        pts = np.asarray(mesh["vertices"], dtype=float)

        assert pts[:, 0].min() == 11 and pts[:, 0].max() == 24
        assert pts[:, 1].min() == 9 and pts[:, 1].max() == 20

    def test_large_layer_keeps_the_delaunay_mesh(self):
        mask = np.zeros((200, 200), dtype=bool)
        mask[20:180, 30:170] = True
        assert int(mask.sum()) > MeshGenerator.TINY_FEATURE_PIXELS

        mesh = MeshGenerator().generate_combined_mesh(_rgba(mask))

        assert len(mesh["vertices"]) > 4, "大图层不该被降级成面片"

    def test_empty_layer_stays_empty(self):
        mesh = MeshGenerator().generate_combined_mesh(
            _rgba(np.zeros((40, 40), dtype=bool)))

        assert len(mesh["vertices"]) == 0
