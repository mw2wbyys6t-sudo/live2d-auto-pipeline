"""子进程入口：只做官方 Cubism Core 一致性检查，不启 OpenGL。

独立进程运行，因为 Cubism Core 在异常路径下会段错误（0xC0000005）。
用法: python -m drivers.live2d_runtime.moc3_consistency <path/to/model.moc3>
退出码: 0 = 一致；1 = 不一致；2 = 用法/依赖错误
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"ok": False, "error": "用法: <moc3 路径>"}))
        return 2

    path = argv[1]
    try:
        import live2d.v3 as sdk
    except ImportError:
        print(json.dumps({"ok": False, "error": "未安装 live2d-py"}))
        return 2

    # 实测：不 init 直接调用会段错误
    sdk.init()
    try:
        model = sdk.LAppModel()
        ok = bool(model.HasMocConsistencyFromFile(path))
    finally:
        sdk.dispose()

    print(json.dumps({"ok": ok}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
