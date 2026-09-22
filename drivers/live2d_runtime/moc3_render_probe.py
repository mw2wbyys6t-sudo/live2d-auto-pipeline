"""离屏渲染探针：加载模型包，可选设置参数，回读像素并输出几何统计。

用于回答「渲染出来的东西在哪、有多大、朝哪个方向」这类问题 —— 一致性检查
只能告诉我们字节串合法，像素统计才能暴露坐标系/翻转/缩放约定错误。
契约与其它检查一致：由子进程运行，崩溃由调用方归类为验证失败。

运行：python -m drivers.live2d_runtime.moc3_render_probe <model3.json>
        [--param Name=value ...] [--png out.png] [--width 400] [--height 500]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from drivers.live2d_runtime.native import CubismRenderer


def _parse_param(text: str) -> tuple[str, float]:
    name, _, value = text.partition("=")
    if not name or value == "":
        raise ValueError(f"--param 需要 Name=value 形式，收到 {text!r}")
    return name, float(value)


def probe(manifest: str, params=(), width: int = 400, height: int = 500,
          png: str | None = None, prime: bool = True) -> dict:
    import pygame
    import live2d.v3 as sdk
    from OpenGL.GL import glReadPixels, glFinish, GL_RGBA, GL_UNSIGNED_BYTE

    renderer = CubismRenderer(width, height)
    initialized = gl_ready = False
    applied: dict[str, float] = {}
    parameter_ids: list[str] = []
    try:
        pygame.display.init()
        pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
        pygame.display.set_mode((width, height), pygame.OPENGL | pygame.DOUBLEBUF)
        pygame.display.set_caption("Live2D offscreen render probe")
        sdk.init()
        initialized = True
        sdk.glInit()
        gl_ready = True
        renderer.load_model(manifest)
        model = renderer._model
        model.StopAllMotions()
        model.SetAutoBlinkEnable(False)
        model.SetAutoBreathEnable(False)

        applied = {}
        # 参数预热：不写任何参数的首帧会得到退化画面（整屏铺满或整体消失），
        # 因此先把每个参数按当前值写一遍，保证几何与声明坐标一致可比。
        if prime:
            for name in renderer.parameter_ids:
                index = renderer.parameter_ids.index(name)
                renderer.set_parameter(name, model.GetParameterValue(index))
        for name, value in params:
            applied[name] = renderer.set_parameter(name, value)

        # 两次空更新让物理/插值稳定，然后冻结时间取帧（与 verify.py 同一手法）。
        for _ in range(2):
            sdk.clearBuffer()
            model._model.Update(0.0)
            model.Draw()
            glFinish()
        sdk.clearBuffer()
        model._model.Update(0.0)
        model.Draw()
        glFinish()
        pixels = np.frombuffer(
            glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE),
            dtype=np.uint8).reshape(height, width, 4).copy()
        pygame.display.flip()
        pygame.event.pump()
        parameter_ids = list(renderer.parameter_ids)
    finally:
        renderer.close()
        if gl_ready:
            sdk.glRelease()
        if initialized:
            sdk.dispose()
        pygame.display.quit()

    alpha = pixels[:, :, 3]
    mask = alpha > 8
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    bbox = [int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())] \
        if cols.size and rows.size else None
    total = int(mask.sum())
    if bbox:
        ys, xs = np.nonzero(mask)
        centroid = [float(xs.mean()), float(ys.mean())]
    else:
        centroid = None
    if png:
        from PIL import Image
        dest = Path(png)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # glReadPixels 自下而上，翻转成正立的图像
        Image.fromarray(np.flipud(pixels)).save(str(dest))
    return {
        "ok": True,
        "width": width,
        "height": height,
        "alpha_bbox": bbox,
        "opaque_pixels": total,
        "centroid": centroid,
        "pixels_sha256": hashlib.sha256(pixels.tobytes()).hexdigest(),
        "parameters_applied": applied,
        "parameter_ids": parameter_ids,
        "png": str(png) if png else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--param", action="append", default=[])
    parser.add_argument("--png")
    parser.add_argument("--width", type=int, default=400)
    parser.add_argument("--height", type=int, default=500)
    parser.add_argument("--no-prime", dest="prime", action="store_false",
                        help="不预热参数，用于观察未写参数时的退化画面")
    args = parser.parse_args()
    try:
        params = [_parse_param(p) for p in args.param]
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    try:
        result = probe(args.manifest, params, args.width, args.height,
                     args.png, prime=args.prime)
    except Exception as exc:  # Core 崩溃由调用方按退出码归类
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print("LIVE2D_RENDER=" + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
