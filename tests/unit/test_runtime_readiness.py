"""导出就绪判定的诚实性：只有官方内核验收结论能点亮 runtime_ready。

回归背景：旧实现按「磁盘上是否存在 .moc3」判定，被内核拒绝的产物同样
会被报成开箱即用。
"""
import json

from api_server import runtime_readiness


def _ready_summary(**overrides):
    summary = {
        "moc3_written": True,
        "moc3_path": "/tmp/out/char.moc3",
        "moc3_bytes": 9600,
        "runtime_ready": True,
        "moc3_blocker": None,
        "moc3_dropped": [],
        "official_core_consistent": True,
    }
    summary.update(overrides)
    return {"moc3": summary}


def test_official_core_acceptance_is_the_only_source_of_truth():
    report = runtime_readiness(_ready_summary())
    assert report["runtime_ready"] is True
    assert report["blocker"] == ""
    assert report["official_core_consistent"] is True


def test_written_but_rejected_moc3_is_not_ready(tmp_path):
    """文件真的存在也不算就绪 —— 必须看内核结论。"""
    model3 = tmp_path / "char.model3.json"
    (tmp_path / "char.moc3").write_bytes(b"MOC3" + b"\x00" * 60)
    model3.write_text(json.dumps({
        "Version": 3,
        "FileReferences": {"Moc": "char.moc3", "Textures": []},
    }), encoding="utf-8")

    report = runtime_readiness(_ready_summary(
        runtime_ready=False,
        official_core_consistent=False,
        moc3_blocker="官方内核拒绝: inconsistent",
    ))
    assert (tmp_path / "char.moc3").is_file(), "前置条件：磁盘上确实有 moc3"
    assert report["runtime_ready"] is False
    assert "官方内核拒绝" in report["blocker"]


def test_missing_moc3_summary_is_reported_as_blocked():
    report = runtime_readiness({"model3_json": "/tmp/out/char.model3.json"})
    assert report["runtime_ready"] is False
    assert report["blocker"]
    assert report["moc_reference"] == ""


def test_dropped_deformers_stay_visible():
    report = runtime_readiness(_ready_summary(
        runtime_ready=False,
        moc3_blocker="部分变形器未编译（['EyeTrack_L(type=rotation)']），"
                     "产物不得用于部署验收",
        moc3_dropped=["deformers"],
        deformers_compiled=["HairFrontSwing", "BodySway"],
        deformers_uncompiled=["EyeTrack_L(type=rotation)"],
    ))
    assert report["runtime_ready"] is False
    assert report["moc3_dropped"] == ["deformers"]
    # 「编了一半」与「完全没编」必须可区分
    assert report["deformers_compiled"] == ["HairFrontSwing", "BodySway"]
    assert report["deformers_uncompiled"] == ["EyeTrack_L(type=rotation)"]


def test_never_reports_ready_with_empty_blocker():
    """未就绪必须附带原因，否则调用方无从判断。"""
    for result in (
        {},
        {"moc3": {}},
        _ready_summary(runtime_ready=False, moc3_blocker=None),
        _ready_summary(runtime_ready=False, moc3_blocker=""),
    ):
        report = runtime_readiness(result)
        assert report["runtime_ready"] is False, result
        assert report["blocker"], f"未就绪却没有 blocker: {result}"
