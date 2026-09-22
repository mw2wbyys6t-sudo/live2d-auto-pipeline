"""子进程入口：用官方 Core 验证 model3 清单能否加载并暴露参数。

与其他隔离入口同样的理由：Cubism Core 在异常路径下会崩溃（0xC0000005 / 0xC0000409）。
本进程自己持有 GL 上下文与 SDK 生命周期（native.CubismRenderer 要求调用方提供），
并复用 CubismRenderer.load_model() 作为唯一校验路径，不另写一套判定逻辑。

设计文档 §8.3 的判据 2（LoadModelJson 成功）与判据 3（GetParamIds 非空）由本入口给出。
用法: python -m drivers.live2d_runtime.moc3_load <path/to/model.model3.json>
退出码: 0 = 加载成功且有参数；1 = 失败；2 = 用法/依赖错误
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"ok": False, "error": "用法: <model3.json 路径>"}))
        return 2

    manifest = argv[1]
    try:
        import live2d.v3 as sdk
    except ImportError:
        print(json.dumps({"ok": False, "error": "未安装 live2d-py"}))
        return 2

    from drivers.live2d_runtime.native import CubismRenderer

    payload = {"ok": False, "parameter_ids": []}
    code = 1
    renderer = CubismRenderer(64, 64)
    initialized = False
    try:
        import pygame
        # LoadModelJson 需要活动 GL 上下文，与 verify.py 的建立顺序一致
        pygame.display.init()
        pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
        pygame.display.set_mode((64, 64), pygame.OPENGL | pygame.DOUBLEBUF)
        # 实测：不 init 直接调用会崩溃
        sdk.init()
        sdk.glInit()
        initialized = True

        renderer.load_model(manifest)
        ids = [str(i) for i in renderer.parameter_ids]
        payload["parameter_ids"] = ids
        if ids:
            payload["ok"] = True
            code = 0
        else:
            payload["error"] = "no parameters exposed after load"
    except Exception as exc:  # Core 与依赖的失败形态不稳定，统一归为不通过
        payload["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        renderer.close()
        if initialized:
            sdk.dispose()

    print(json.dumps(payload, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
