#!/usr/bin/env python3
"""
Live2D Master Agent - 本地占位图 Provider（完全离线兜底）

当所有云端 provider（Seedream/SenseNova/OpenAI/Pollinations）都不可用
（无 API Key 或断网）时，此 provider 保证「输入文本 → 产出图片」主链路
依然可以走通。它产出一张**确定性**的立绘占位图（渐变背景 + 角色剪影 +
提示词水印），并如实在 metadata 里标注 ``local_placeholder=True``，
绝不冒充 AI 生成结果。

这是「下载双击就能在本地沙箱跑通」的最后一道保险：哪怕用户完全断网、
没有任何 API Key，生成 → 分层 → 导出 → 预览的完整流程也不会在第一步崩掉。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.image_gen.base import ImageProvider, GenerationResult
from core.logger import get_logger

log = get_logger("image_gen.local")


class LocalPlaceholderProvider(ImageProvider):
    """完全离线的占位图生成器 —— 只依赖 Pillow，无需网络。"""

    name = "local_placeholder"
    display_name = "本地占位图（离线兜底）"
    requires_api_key = False
    max_retries = 0
    timeout = 30

    def is_available(self) -> bool:
        """只在 Pillow 可用时可用 —— 这是唯一的硬依赖。"""
        try:
            from PIL import Image  # noqa: F401
            return True
        except ImportError:
            return False

    def generate(
        self,
        prompt: str,
        output_path: str,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> GenerationResult:
        self._report_progress("生成本地占位图（离线模式）", 10)
        import time

        if seed is None:
            seed = int(time.time())

        try:
            # 复用 local_image_generator.py 的确定性绘图逻辑，保证占位图
            # 风格与 Go 旧路径 generateWithLocalGenerator 产出的完全一致。
            from local_image_generator import _draw

            self._report_progress("渲染占位图", 50)
            img = _draw(width, height, seed, prompt or "")

            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            img.save(p)

            self._report_progress("完成", 100)
            log.warning(
                "使用本地占位图（离线兜底）。这张图是确定性剪影，不是 AI 生成结果；"
                "配置 API Key 或联网后可获得真实立绘。"
            )
            return GenerationResult(
                success=True,
                image_path=str(p),
                provider=self.name,
                model="local-placeholder-v1",
                prompt=prompt or "",
                width=width,
                height=height,
                metadata={"local_placeholder": True, "seed": seed},
            )
        except Exception as e:
            return GenerationResult(
                success=False,
                provider=self.name,
                error=f"本地占位图生成失败: {e}",
            )

    def get_setup_guide(self) -> str:
        return (
            "本地占位图无需任何配置，只需安装 Pillow（pip install Pillow）。"
            "它在所有云端 provider 不可用时自动启用，保证离线也能跑通完整流程。"
        )
