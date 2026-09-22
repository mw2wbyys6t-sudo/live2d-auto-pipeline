"""motion3.json 的官方内核验收：生成的曲线必须真的驱动参数。

宿主模型是这里自己编译的最小 rig（参数范围 ±30，与曲线一致），因此
「参数不存在」「范围被夹取」「几何对不对」都不是本测试的变量 —— 它们各有
自己的验收测试。本测试只判一件事：我们写出的 motion 文件，官方内核读了之后
参数到底按不按文件的值走。

两条实测出来的写法约束也钉在这里：
1. 段布局 = 初始点 + (类型前缀 + 点)，0 线性 1 点 / 1 贝塞尔 3 点 / 2 阶跃 1 点
   （官方 Haru 六个动作文件与 Meta 的 P/S 计数 6/6 对账）；
2. 文件必须**带缩进**。同一份内容压成紧凑单行，内核的加载路径直接拒绝
   （`Load extra motion failed`），参数一动不动 —— 这是判据不是风格问题。
复现脚本：tools/bisect_motion_encoding.py、tools/diag_extra_motion.py。
"""
import json
import os
from pathlib import Path

import pytest

from drivers.live2d_runtime.moc3_verify import verify_motion_playback
from live2d_builder.exporter.moc3_model import (
    MeshSpec,
    ParameterSpec,
    RigSpec,
    compile_static_rig,
)
from live2d_builder.motion.motion3 import Motion3Builder

_PARAM = "ParamAngleZ"
_KEYS = [-30.0, 0.0, 30.0]


def _has_live2d() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("live2d.v3") is not None
    except (ImportError, ValueError):
        return False


requires_live2d = pytest.mark.skipif(
    not _has_live2d(), reason="需要 live2d-py 运行时（pip install live2d-py）")
requires_pixels = pytest.mark.skipif(
    os.environ.get("LIVE2D_TEST_PIXELS") != "1",
    reason="需要 OpenGL：设 LIVE2D_TEST_PIXELS=1 开启真实播放验证")


def _package(tmp_path: Path, *, indent: bool = True) -> Path:
    """最小可加载包：一张网格 + 一个 ±30 参数 + 我们生成的 idle 曲线。"""
    from PIL import Image

    verts = [(-100.0, -50.0), (100.0, -50.0), (100.0, 50.0), (-100.0, 50.0)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    rig = RigSpec(
        meshes=[MeshSpec(mesh_id="ArtMeshBody", vertices=verts,
                         triangles=[(0, 1, 2), (0, 2, 3)], uvs=uvs)],
        parameters=[ParameterSpec(_PARAM, minimum=-30.0, maximum=30.0,
                                  default=0.0)],
        canvas_width=512.0, canvas_height=512.0, pixels_per_unit=100.0)
    document = Motion3Builder(duration=6.0).build_idle({_PARAM: _KEYS})
    assert document is not None, "三个键值必须能生成曲线"

    folder = tmp_path / "motionpkg"
    folder.mkdir()
    (folder / "m.moc3").write_bytes(compile_static_rig(rig).to_bytes())
    Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(folder / "t.png")
    (folder / "motions").mkdir()
    text = (json.dumps(document, ensure_ascii=False, indent="\t") if indent
            else json.dumps(document, ensure_ascii=False, separators=(",", ":")))
    (folder / "motions" / "idle.motion3.json").write_text(text, encoding="utf-8")

    manifest = folder / "m.model3.json"
    manifest.write_text(json.dumps({
        "Version": 3,
        "Meta": {"ArchiveName": "motionpkg"},
        "FileReferences": {
            "Moc": "m.moc3",
            "Textures": ["t.png"],
            # 淡入淡出置零：混合姿态会让逐帧对账失去意义
            "Motions": {"idle": [{"File": "motions/idle.motion3.json",
                                  "FadeInTime": 0.0, "FadeOutTime": 0.0}]},
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


@requires_live2d
@requires_pixels
def test_generated_motion_drives_parameters_in_official_core(tmp_path):
    result = verify_motion_playback(str(_package(tmp_path)), frames=180)
    assert result["ok"] is True, (
        f"官方内核没有按我们生成的 motion 驱动参数: {result['blocker']}\n"
        f"stdout={result['stdout'][-800:]}")
    curve = result["curves"][_PARAM]
    assert curve["moved"] is True
    # 键形区间是 -30..30：曲线必须扫满整个行程，而不是只抖一点
    assert curve["observed_range"] == pytest.approx(60.0, abs=1.0), curve
    # 全线性曲线要逐帧对得上（允许一帧内的时间对齐偏差）
    assert curve["max_abs_error"] == pytest.approx(0.0, abs=1e-2), (
        result["error_per_second"])


@requires_live2d
@requires_pixels
def test_compact_motion_file_is_rejected_by_the_runtime(tmp_path):
    """负对照：同一份内容压成单行，内核就不播 —— 钉住那条写法约束。"""
    result = verify_motion_playback(str(_package(tmp_path, indent=False)),
                                    frames=180)
    assert result["ok"] is False, (
        "紧凑单行的 motion 文件被内核正常播放了，说明这条产物约束已不成立")
    assert result["curves"], result["blocker"]
    assert all(not curve["moved"] for curve in result["curves"].values()), (
        result["curves"])
