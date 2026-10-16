"""v0.11.0：OUTPUT_DIR 环境变量优先于配置/默认（Go 桥注入可写产物目录）。"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_output_dir_env_overrides_config(monkeypatch):
    from core.config import config

    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("OUTPUT_DIR", str(Path(td) / "custom-output"))
        # 单例可能已缓存；直接验证 property 读取 env 优先。
        assert config.output_dir == str(Path(td) / "custom-output")
        assert Path(config.output_dir).is_dir()


def test_output_dir_falls_back_to_project_root(monkeypatch):
    from core.config import config

    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    monkeypatch.delenv("LIVE2D_PROJECT_ROOT", raising=False)
    d = config.output_dir
    # 回落到工程根/output，且目录被创建
    assert d.endswith("output")
    assert Path(d).is_dir()
