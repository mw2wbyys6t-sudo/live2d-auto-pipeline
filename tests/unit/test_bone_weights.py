"""Tests for per-vertex bone weight assignment (skinning).

Regression guard for the "loads but never moves" bug: without skinning
weights ``moc3_pipeline`` bakes no parameter keyforms, so the compiled
moc3 has ``KEYS == 0`` / ``KEYFORM_BINDINGS == 0`` and every parameter
controls zero pixels.
"""

import math

import pytest

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
