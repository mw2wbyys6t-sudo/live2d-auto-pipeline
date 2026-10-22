"""打包闸门：被官方内核拒绝的产物不许进 zip。

回归背景（实测，2026-10）：``export_cubism_package`` 直接把输出目录打成 zip，
全程不看内核验收结论。于是「被拒绝的 moc3」照样进包发给用户，而用户装进
Cubism / VTube Studio 才发现加载不了。
"""

from __future__ import annotations

import pytest

from live2d_builder.pipeline import Live2DBuilder


def _builder(tmp_path) -> Live2DBuilder:
    return Live2DBuilder(output_dir=str(tmp_path), character_name="gate")


def test_package_refuses_when_build_never_produced_a_verdict(tmp_path):
    """压根没构建过 → 没有验收结论 → 拒绝打包。"""
    builder = _builder(tmp_path)

    with pytest.raises(RuntimeError, match="拒绝打包未通过官方内核验收的产物"):
        builder.export_cubism_package()

    assert list(tmp_path.glob("*.zip")) == [], "拒绝打包时不得留下任何 zip"


def test_package_refuses_rejected_moc3_and_quotes_the_blocker(tmp_path):
    """内核拒绝 → 拒绝打包，且错误信息逐字带上真实原因。"""
    builder = _builder(tmp_path)
    reason = "官方内核拒绝: inconsistent"
    builder._moc3_result = {"moc3_written": True, "runtime_ready": False,
                            "blocker": reason}

    with pytest.raises(RuntimeError) as excinfo:
        builder.export_cubism_package()

    assert reason in str(excinfo.value), "错误信息必须逐字带上实测 blocker"
    assert list(tmp_path.glob("*.zip")) == []


def test_package_allows_verified_model(tmp_path, monkeypatch):
    """正向：内核验收通过 → 允许打包（否则闸门就变成了「永远拒绝」）。"""
    builder = _builder(tmp_path)
    builder._moc3_result = {"moc3_written": True, "runtime_ready": True,
                            "blocker": None}

    expected = tmp_path / "gate.zip"
    expected.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    monkeypatch.setattr(
        builder.exporter, "package_model",
        lambda output_dir, character_name: str(expected))

    assert builder.export_cubism_package() == str(expected)
