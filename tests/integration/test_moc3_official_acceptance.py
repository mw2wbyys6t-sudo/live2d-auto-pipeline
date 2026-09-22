"""P0 验收：官方 Cubism Core 必须接受我们生成的 moc3。

若这些测试失败，说明最小模型的自洽结构仍有缺失，
需要回到 live2d_builder/exporter/moc3_builder.py 迭代补齐。
"""
import importlib.util
import json
from pathlib import Path

import pytest

from drivers.live2d_runtime.moc3_verify import (
    verify_moc3_consistency,
    verify_moc3_load,
)
from live2d_builder.exporter.moc3_builder import MinimalModelSpec, build_minimal_model
from live2d_builder.exporter.moc3_lint import lint_document

_HARU = Path("Work/native-sample/Haru/Haru.moc3")


def _has_live2d() -> bool:
    """live2d-py 是否可用（决定官方内核类测试能否运行）。

    注意：`find_spec` 对点号名（如 "live2d.v3"）在父包缺失时会抛
    ModuleNotFoundError，而不是返回 None；必须捕获，否则本模块会在**收集期**
    报错，而不是被跳过。
    """
    try:
        return importlib.util.find_spec("live2d.v3") is not None
    except (ImportError, ValueError):
        return False


requires_live2d = pytest.mark.skipif(
    not _has_live2d(), reason="需要 live2d-py 运行时（pip install live2d-py）"
)


def _write_minimal(tmp_path: Path) -> Path:
    doc = build_minimal_model(MinimalModelSpec())
    assert lint_document(doc) == [], "生成的模型必须通过自洽性校验"
    out = tmp_path / "minimal.moc3"
    out.write_bytes(doc.to_bytes())
    return out


def _write_package(tmp_path: Path, moc3_bytes: bytes, name: str = "minimal") -> Path:
    """把 moc3 包成官方清单校验器接受的 model3 包。"""
    from PIL import Image

    package = tmp_path / name
    package.mkdir()
    (package / f"{name}.moc3").write_bytes(moc3_bytes)
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(package / "texture_00.png")
    manifest = package / f"{name}.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3,
        "Meta": {"ArchiveName": name},
        "FileReferences": {
            "Moc": f"{name}.moc3",
            "Textures": ["texture_00.png"],
        },
    }), encoding="utf-8")
    return manifest


def _write_minimal_package(tmp_path: Path) -> Path:
    return _write_package(tmp_path, _write_minimal(tmp_path).read_bytes())


@requires_live2d
def test_minimal_model_is_accepted_by_official_core(tmp_path):
    moc3 = _write_minimal(tmp_path)
    result = verify_moc3_consistency(str(moc3))
    assert result["ok"] is True, (
        f"官方内核拒绝了我们生成的 moc3: {result['blocker']}\n"
        f"stdout={result['stdout']}\nstderr={result['stderr']}"
    )


@requires_live2d
@pytest.mark.skipif(not _HARU.is_file(), reason="缺少官方参考模型")
def test_official_haru_regression_stays_consistent():
    """回归保护：检查器本身必须对官方模型返回一致。"""
    result = verify_moc3_consistency(str(_HARU))
    assert result["ok"] is True, (
        f"官方 Haru.moc3 应通过一致性检查，实际: {result['blocker']}"
    )


@requires_live2d
def test_tampered_model_is_rejected(tmp_path):
    """负例：篡改后必须判为不一致或崩溃，绝不能通过。"""
    moc3 = _write_minimal(tmp_path)
    raw = bytearray(moc3.read_bytes())
    # 破坏 Section Offset Table 中的若干偏移
    for offset in range(64, 64 + 40, 4):
        raw[offset] = 0xFF
    bad = tmp_path / "tampered.moc3"
    bad.write_bytes(bytes(raw))

    result = verify_moc3_consistency(str(bad))
    assert result["ok"] is False


@requires_live2d
@pytest.mark.skipif(not _HARU.is_file(), reason="缺少官方参考模型")
def test_lint_has_no_false_positives_on_official_model():
    """官方模型必须零告警，否则规则会在真实规模上误杀合法结构。"""
    Moc3 = pytest.importorskip("moc3").Moc3

    class _AsContainer:
        """把 py-moc3 的读回对象适配成 lint_document 的输入形状。"""

        def __init__(self, moc3):
            self.version = moc3.header.version
            self.counts = moc3.counts
            self._doc = moc3

        def get(self, name):
            return self._doc.get(name)

    haru = Moc3.from_file(str(_HARU))
    assert lint_document(_AsContainer(haru)) == []


@requires_live2d
def test_minimal_model_loads_in_official_core(tmp_path):
    """判据 2：官方 Core 能用 LAppModel.LoadModelJson 加载它。"""
    manifest = _write_minimal_package(tmp_path)
    result = verify_moc3_load(str(manifest))
    assert result["ok"] is True, (
        f"官方内核加载失败: {result['blocker']}\n"
        f"stdout={result['stdout']}\nstderr={result['stderr']}"
    )


@requires_live2d
def test_loaded_model_exposes_parameters(tmp_path):
    """判据 3：加载后 GetParamIds 非空，且是我们声明的参数。"""
    manifest = _write_minimal_package(tmp_path)
    result = verify_moc3_load(str(manifest))
    assert result["parameter_ids"] == ["ParamAngleX"], (
        f"参数表与编译输入不符: {result['parameter_ids']} "
        f"blocker={result['blocker']}"
    )


def _real_scale_rig(mesh_count: int = 13, cells: int = 20,
                    param_count: int = 28):
    """合成接近真实导出的规模：官方样例实测 13 层 / 5814 顶点 / 2522 三角形。"""
    from live2d_builder.exporter.moc3_model import (
        MeshSpec, ParameterSpec, RigSpec, compile_static_rig,
    )

    step = 100.0 / cells
    meshes = []
    for index in range(mesh_count):
        verts = [(col * step, row * step)
                 for row in range(cells + 1) for col in range(cells + 1)]
        uvs = [(col / cells, row / cells)
               for row in range(cells + 1) for col in range(cells + 1)]
        tris = []
        for row in range(cells):
            for col in range(cells):
                a = row * (cells + 1) + col
                tris += [(a, a + cells + 1, a + 1),
                         (a + 1, a + cells + 1, a + cells + 2)]
        meshes.append(MeshSpec(mesh_id=f"ArtMesh{index}", vertices=verts,
                               triangles=tris, uvs=uvs,
                               draw_order=float(index)))
    params = [ParameterSpec(f"ParamTest{i}") for i in range(param_count)]
    spec = RigSpec(meshes=meshes, parameters=params,
                   canvas_width=512.0, canvas_height=512.0)
    return spec, compile_static_rig(spec)


@requires_live2d
def test_real_scale_static_rig_is_accepted_by_official_core(tmp_path):
    spec, doc = _real_scale_rig()
    assert lint_document(doc) == []
    moc3 = tmp_path / "rig.moc3"
    moc3.write_bytes(doc.to_bytes())
    result = verify_moc3_consistency(str(moc3))
    assert result["ok"] is True, (
        f"{len(spec.meshes)} 网格 / "
        f"{sum(len(m.vertices) for m in spec.meshes)} 顶点的模型被拒绝: "
        f"{result['blocker']}\nstdout={result['stdout']}")


@requires_live2d
def test_real_scale_rig_loads_and_exposes_every_parameter(tmp_path):
    spec, doc = _real_scale_rig()
    manifest = _write_package(tmp_path, doc.to_bytes(), name="rig")
    result = verify_moc3_load(str(manifest))
    assert result["ok"] is True, f"加载失败: {result['blocker']}"
    assert result["parameter_ids"] == [
        p.parameter_id for p in spec.parameters], result["parameter_ids"]
