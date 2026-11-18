"""部分成功契约（Python 侧）：立绘已生成、后续阶段失败时，真图必须留在结果里。

背景：2026-11-13 实测，Pollinations 已经成功出图，随后分层阶段因
`_fblas` DLL 缺失抛错；旧实现把整条结果判失败，Go 侧再回退本地占位图 ——
用户手里那张真图被几何剪影替换。这里钉住：失败结果必须带
`partial=true` 与 `character_image`，供上层交出真图。
"""

from PIL import Image

from core.image_gen.base import GenerationResult
from core.workflow import WorkflowEngine


def test_later_stage_failure_keeps_generated_image(tmp_path, monkeypatch):
    engine = WorkflowEngine(
        output_dir=str(tmp_path),
        width=1024,
        height=1536,
        use_semantic_segmentation=False,
    )
    image_path = tmp_path / "character_partial.png"
    Image.new("RGBA", (1024, 1536), (255, 255, 255, 0)).save(image_path)

    def fake_generate(**kwargs):
        return GenerationResult(
            success=True,
            image_path=str(image_path),
            provider="fake",
            model="fake",
            prompt=kwargs.get("prompt", ""),
            width=1024,
            height=1536,
            metadata={"prompt_lang": "zh"},
        )

    monkeypatch.setattr(engine.router, "generate", fake_generate)

    def boom(_img):
        raise RuntimeError("分层阶段崩了")

    monkeypatch.setattr(engine, "_optimize_image", boom)

    result = engine.run(prompt="粉色双马尾少女")

    assert result["success"] is False
    assert result["partial"] is True, "立绘已落盘时必须标记 partial，否则上层只能回退占位图"
    assert result["character_image"] == str(image_path)
    assert "分层阶段崩了" in result["error"]
    assert result["error_state"]


def test_failure_before_generation_has_no_partial(tmp_path, monkeypatch):
    engine = WorkflowEngine(
        output_dir=str(tmp_path),
        width=1024,
        height=1536,
        use_semantic_segmentation=False,
    )

    def no_engine(**kwargs):
        raise RuntimeError("还没有可用的图像引擎")

    monkeypatch.setattr(engine.router, "generate", no_engine)

    result = engine.run(prompt="粉色双马尾少女")

    assert result["success"] is False
    assert "partial" not in result, "没画出图时不能声称部分成功"
    assert not result.get("character_image")
