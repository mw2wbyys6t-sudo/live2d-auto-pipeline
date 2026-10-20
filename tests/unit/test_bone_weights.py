"""Tests for per-vertex bone weight assignment (skinning).

Regression guard for the "loads but never moves" bug: without skinning
weights ``moc3_pipeline`` bakes no parameter keyforms, so the compiled
moc3 has ``KEYS == 0`` / ``KEYFORM_BINDINGS == 0`` and every parameter
controls zero pixels.
"""

import math

import pytest

from live2d_builder.bones.deformers import BoneHierarchy
from live2d_builder.mesh.generator import MeshGenerator


def _mesh(vertices):
    return {"vertices": [(float(x), float(y)) for x, y in vertices],
            "indices": [], "width": 100, "height": 100}


class TestComputeBoneWeights:
    def test_single_bone_gets_full_weight(self):
        mesh = _mesh([(10, 10), (90, 90)])
        out = MeshGenerator.compute_bone_weights(mesh, {"Head": (0, 0)}, "Head")

        assert out["bone_names"] == ["Head"]
        assert out["weights"] == [[1.0], [1.0]]

    def test_parent_blends_by_inverse_distance(self):
        mesh = _mesh([(0.0, 0.0), (10.0, 0.0)])
        positions = {"Face": (0.0, 0.0), "Head": (10.0, 0.0)}
        out = MeshGenerator.compute_bone_weights(
            mesh, positions, "Face", parent_bone="Head")

        assert out["bone_names"] == ["Face", "Head"]
        near, far = out["weights"]
        # 顶点在 Face 枢轴上：Face 独占
        assert near[0] == pytest.approx(1.0)
        assert near[1] == pytest.approx(0.0)
        # 顶点在 Head 枢轴上：Head 独占
        assert far[1] == pytest.approx(1.0)
        assert far[0] == pytest.approx(0.0)

    def test_weights_sum_to_one(self):
        mesh = _mesh([(3, 7), (40, 12), (77, 88)])
        positions = {"Eye_L": (20.0, 20.0), "Head": (60.0, 30.0)}
        out = MeshGenerator.compute_bone_weights(
            mesh, positions, "Eye_L", parent_bone="Head")

        for row in out["weights"]:
            assert sum(row) == pytest.approx(1.0, abs=1e-9)
            assert all(w >= 0.0 for w in row)

    def test_midpoint_is_split_evenly(self):
        mesh = _mesh([(5.0, 0.0)])
        positions = {"A": (0.0, 0.0), "B": (10.0, 0.0)}
        out = MeshGenerator.compute_bone_weights(
            mesh, positions, "A", parent_bone="B")

        assert out["weights"][0] == pytest.approx([0.5, 0.5])

    def test_missing_pivot_falls_back_to_assigned_bone(self):
        mesh = _mesh([(1, 1)])
        positions = {"Head": (0.0, 0.0)}          # Face 没有位置
        out = MeshGenerator.compute_bone_weights(
            mesh, positions, "Face", parent_bone="Head")

        assert out["bone_names"] == ["Face", "Head"]
        assert out["weights"][0][1] == pytest.approx(1.0)

    def test_no_pivots_at_all_still_emits_single_bone(self):
        """一根骨骼位置都没有时也必须给出可用权重，键形才有东西可绑。"""
        mesh = _mesh([(1, 1), (2, 2)])
        out = MeshGenerator.compute_bone_weights(mesh, {}, "Hair_Front")

        assert out["bone_names"] == ["Hair_Front"]
        assert out["weights"] == [[1.0], [1.0]]

    def test_parent_equal_to_assigned_is_deduplicated(self):
        mesh = _mesh([(1, 1)])
        out = MeshGenerator.compute_bone_weights(
            mesh, {"Head": (0, 0)}, "Head", parent_bone="Head")

        assert out["bone_names"] == ["Head"]

    def test_empty_mesh(self):
        out = MeshGenerator.compute_bone_weights(
            {"vertices": []}, {"Head": (0, 0)}, "Head")
        assert out == {"bone_names": [], "weights": []}

    def test_accepts_numpy_vertices(self):
        """Real meshes carry a numpy array; `or []` would raise ambiguous truth."""
        import numpy as np
        mesh = {"vertices": np.array([[0.0, 0.0], [10.0, 0.0]]),
                "indices": [], "width": 100, "height": 100}
        out = MeshGenerator.compute_bone_weights(
            mesh, {"A": (0.0, 0.0), "B": (10.0, 0.0)}, "A", parent_bone="B")

        assert out["bone_names"] == ["A", "B"]
        assert len(out["weights"]) == 2
        assert sum(out["weights"][0]) == pytest.approx(1.0)

    def test_weights_length_matches_vertex_count(self):
        verts = [(i * 3.0, i * 7.0) for i in range(25)]
        out = MeshGenerator.compute_bone_weights(
            _mesh(verts), {"A": (0, 0), "B": (80, 40)}, "A", parent_bone="B")
        assert len(out["weights"]) == 25

    def test_no_nan_or_inf_for_coincident_vertex_and_pivot(self):
        """顶点与枢轴重合时不能产生 inf/nan —— 那会让 moc3 写出垃圾坐标。"""
        mesh = _mesh([(5.0, 5.0), (5.0, 5.0)])
        out = MeshGenerator.compute_bone_weights(
            mesh, {"A": (5.0, 5.0), "B": (5.0, 5.0)}, "A", parent_bone="B")

        for row in out["weights"]:
            assert all(math.isfinite(w) for w in row)


# 真实管线（前景剪影 + 合成器拆分）在 demo 输入上产出的完整层名词表。
PRODUCTION_LAYERS = {
    "accessories": "Torso",
    "arm_L": "ArmBack_L",
    "arm_R": "ArmBack_R",
    "clothes_bottom": "Skirt",
    "clothes_top": "Torso",
    "collar": "Neck",
    "cuff_L": "ForearmBack_L",
    "cuff_R": "ForearmBack_R",
    "eyebrow_L": "Brow_L",
    "eyebrow_R": "Brow_R",
    "eyewhite_left": "Eyeball_L",
    "eyewhite_right": "Eyeball_R",
    "face": "Face",
    "hair_back": "Hair_Back",
    "hair_front": "Hair_Front",
    "hand_L": "ForearmBack_L",
    "hand_R": "ForearmBack_R",
    "irides_left": "Eyeball_L",
    "irides_right": "Eyeball_R",
    "leg_L": "Leg_L",
    "leg_R": "Leg_R",
    "mouth": "Mouth",
    "neck": "Neck",
    "nose": "Nose",
    "skirt_hem_L": "Skirt",
    "skirt_hem_R": "Skirt",
    "sock_L": "Calf_L",
    "sock_R": "Calf_R",
    "waistband": "Waist",
}


def _centroids(layers, w=600, h=1000):
    """给每个层一个落在身体对应高度的假质心（图像坐标）。"""
    height_ratio = {
        "hair_back": 0.25, "hair_front": 0.10, "face": 0.17, "neck": 0.23,
        "clothes_top": 0.33, "collar": 0.26, "waistband": 0.47,
        "clothes_bottom": 0.55, "skirt_hem_L": 0.60, "skirt_hem_R": 0.60,
        "leg_L": 0.70, "leg_R": 0.70, "sock_L": 0.90, "sock_R": 0.90,
    }
    out = {}
    for name in layers:
        y = h * height_ratio.get(name, 0.33)
        x = w * (0.40 if name.endswith("_L") or name.endswith("_left") else 0.60)
        out[name] = (x, y)
    return out


class TestBoneResolution:
    """层名 -> 骨骼的解析。

    历史缺陷：``LAYER_BONE_MAP`` 只认识 ``face_base``/``eye_left``/``thigh_left``
    这类旧名字，而真实产出是 ``face``/``arm_L``/``sock_L``/``clothes_top``，
    22 层里 15 层掉进默认的 ``Face`` —— 衣服、腿、袜子全挂在脸的骨骼下，
    头一转整件衣服跟着甩。
    """

    @pytest.mark.parametrize("layer,expected", sorted(PRODUCTION_LAYERS.items()))
    def test_every_production_layer_name_resolves(self, layer, expected):
        bone, reason = BoneHierarchy().resolve_bone(layer)

        assert bone == expected
        assert reason in ("map", "sided")

    def test_unknown_layer_never_lands_in_the_head(self):
        """兜底不能是 Face/Head，否则未知层会跟着头一起转。"""
        bone, reason = BoneHierarchy().resolve_bone("mystery_patch")

        assert reason == "default"
        assert bone == BoneHierarchy.DEFAULT_BONE
        info = BoneHierarchy.STANDARD_BONES[bone]
        assert info["group"] not in {"head", "face", "hair", "eyes", "brows"}

    def test_side_suffix_selects_the_matching_limb(self):
        hierarchy = BoneHierarchy()

        assert hierarchy.resolve_bone("sock_l")[0] == "Calf_L"
        assert hierarchy.resolve_bone("sock_r")[0] == "Calf_R"
        assert hierarchy.resolve_bone("leg_left")[0] == "Leg_L"
        assert hierarchy.resolve_bone("leg_right")[0] == "Leg_R"

    def test_unsided_limb_is_split_by_centroid_side(self):
        hierarchy = BoneHierarchy()
        span = (0.0, 0.0, 600.0, 1000.0)

        left, _ = hierarchy.resolve_bone("legs", (100.0, 700.0), span)
        right, _ = hierarchy.resolve_bone("legs", (500.0, 700.0), span)

        assert left == "Leg_L"
        assert right == "Leg_R"

    def test_position_inference_covers_body_regions(self):
        hierarchy = BoneHierarchy()
        span = (0.0, 0.0, 600.0, 1000.0)

        assert hierarchy.resolve_bone("weird_top_thing", (300.0, 100.0), span)[0] == "Head"
        assert hierarchy.resolve_bone("weird_chest_thing", (300.0, 300.0), span)[0] == "Chest"
        assert hierarchy.resolve_bone("weird_waist_thing", (300.0, 550.0), span)[0] == "Waist"
        assert hierarchy.resolve_bone("weird_bottom_thing", (300.0, 800.0), span)[0].startswith("Leg_")


class TestBonePivots:
    """枢轴必须是图像坐标，落在角色身上。"""

    def test_pivots_are_image_space_not_canvas_centred(self):
        """历史上默认比例模型是"画布中心为原点"，被调用方再减一次半画布，
        Head 枢轴因此跑到画布左下角外，头一转就绕着角落甩出去。"""
        hierarchy = BoneHierarchy()
        layers = list(PRODUCTION_LAYERS)
        centroids = _centroids(layers)

        positions = hierarchy.get_bone_positions(centroids)

        head = positions["Head"]
        assert 0.0 < head[0] < 600.0 and 0.0 < head[1] < 1000.0
        # 头顶在脚上方
        assert positions["Head"][1] < positions["Skirt"][1] < positions["Calf_L"][1]

    def test_head_pivot_sits_at_the_neck(self):
        """头骨枢轴取颈根：既不是脸中心，也不是画布原点。"""
        hierarchy = BoneHierarchy()
        centroids = _centroids(list(PRODUCTION_LAYERS))

        positions = hierarchy.get_bone_positions(centroids)

        assert positions["Head"][0] == pytest.approx(positions["Face"][0])
        assert positions["Head"][1] == pytest.approx(positions["Neck"][1])

    def test_bone_positions_and_assignments_agree(self):
        """build() 与 get_bone_positions() 必须用同一套解析，否则键形绕着
        一根没人绑的骨骼转。"""
        hierarchy = BoneHierarchy()
        layers = list(PRODUCTION_LAYERS)
        centroids = _centroids(layers)

        tree = hierarchy.build(layers, centroids=centroids)
        positions = hierarchy.get_bone_positions(centroids)

        for layer, bone in tree["layer_assignments"].items():
            assert bone in positions, f"{layer} 绑到 {bone}，但该骨骼没有枢轴"


class TestHeadAssemblyExclusion:
    """衣服/腿/袜子不能进入"头部总成"，否则 ParamAngle 会把它们一起带走。"""

    def test_body_parts_are_outside_the_head_assembly(self):
        from live2d_builder.exporter.moc3_pipeline import _head_assembly

        hierarchy = BoneHierarchy()
        layers = list(PRODUCTION_LAYERS)
        tree = hierarchy.build(layers, centroids=_centroids(layers))
        assembly = _head_assembly(BoneHierarchy.STANDARD_BONES)

        moving_with_head = {
            layer for layer, bone in tree["layer_assignments"].items()
            if bone in assembly
        }

        assert "clothes_top" not in moving_with_head
        assert "clothes_bottom" not in moving_with_head
        assert "leg_L" not in moving_with_head
        assert "sock_L" not in moving_with_head
        # 头/发/五官仍然要跟着头动
        assert {"face", "hair_back", "hair_front"} <= moving_with_head
