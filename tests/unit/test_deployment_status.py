"""部署结论必须由官方内核的实测结论推导，不能硬编码。

回归背景（实测，2026-10）：编排层复制 rigging 结果时把 ``moc3`` 整个丢掉，
收尾处又把 ``runtime_verified`` / ``deployment_ready`` 写死为 False 并配一句
与内核结论无关的静态文案。结果是「内核接受」与「内核拒绝」得到同一个答案，
信息量为零 —— 用户看到的是假阻塞，真正的拒绝理由反而消失。

这里做**成对断言**：只测反向往会漏掉「永远 False」这种退化写法。
"""

from __future__ import annotations

import pytest

from core.workflow import derive_deployment_status


def _rigging(**moc3):
    return {"moc3": moc3}


def test_ready_moc3_makes_the_result_deployable():
    """正向：内核验收通过 → 可部署，且不留阻塞。"""
    status = derive_deployment_status(_rigging(
        runtime_ready=True, moc3_blocker=None, motion_verified=True))

    assert status["deployment_ready"] is True
    assert status["runtime_verified"] is True
    assert status["artifact_type"] == "deployable_model"
    assert status["deployment_blockers"] == []


def test_rejected_moc3_reports_the_real_blocker_verbatim():
    """反向：内核拒绝 → 不可部署，blocker 逐字等于实测原因。"""
    reason = "官方内核拒绝: motion 未被官方内核播放: error: Could not load EGL library"
    status = derive_deployment_status(_rigging(
        runtime_ready=False, moc3_blocker=reason))

    assert status["deployment_ready"] is False
    assert status["runtime_verified"] is False
    assert status["artifact_type"] == "preparation_bundle"
    # 必须是实测原因，不能是那句通用文案。
    assert status["deployment_blockers"] == [reason]


@pytest.mark.parametrize("rigging", [
    None,
    {},
    {"output_dir": "/tmp/out"},          # 有产物但没有 moc3 结论
    {"moc3": {}},
])
def test_missing_moc3_verdict_is_blocked_with_a_reason(rigging):
    """没有验收结论一律按未就绪处理，且必须给出可读原因。"""
    status = derive_deployment_status(rigging)

    assert status["deployment_ready"] is False
    assert status["deployment_blockers"], "未就绪却没有 blocker，调用方无从判断"


def test_moc3_written_alone_does_not_make_it_deployable():
    """磁盘上写出了 .moc3 ≠ 可部署 —— 这正是原始缺陷的判据。"""
    status = derive_deployment_status(_rigging(
        moc3_written=True, runtime_ready=False,
        moc3_blocker="官方内核拒绝: inconsistent"))

    assert status["deployment_ready"] is False
    assert status["deployment_blockers"] == ["官方内核拒绝: inconsistent"]


# ---------- FF-2：层序错位同样计入阻塞 ----------

def test_layer_order_issues_block_an_otherwise_ready_model():
    """内核接受只说明「能加载」，不说明「图层前后关系对」。

    FF-2 承诺「任何层错位 0 容忍，阻断导出并高亮错层」—— 这里锁住
    「错层会真的让 deployment_ready 变 False」这一半。
    """
    issue = {
        "code": "layer_order_mismatch",
        "layer": "mouth_cavity",
        "expected_index": 11,
        "sources": {"render": 12},
        "direction": "moved_forward",
        "message": "Live2D 渲染层序 中 'mouth_cavity' 位于第 12 位，应为第 11 位",
    }
    status = derive_deployment_status(
        _rigging(runtime_ready=True, moc3_blocker=None),
        layer_order_issues=[issue],
    )

    assert status["deployment_ready"] is False, "层序错位却仍判为可部署"
    assert status["runtime_verified"] is False
    assert status["artifact_type"] == "preparation_bundle"
    assert status["deployment_blockers"] == [issue["message"]]


def test_no_layer_order_issues_keeps_ready_model_ready():
    """成对断言：没问题时不得被误判为阻塞（否则闸门等于永远关闭）。"""
    status = derive_deployment_status(
        _rigging(runtime_ready=True, moc3_blocker=None),
        layer_order_issues=[],
    )
    assert status["deployment_ready"] is True
    assert status["deployment_blockers"] == []


def test_moc3_rejection_and_layer_order_issues_are_both_reported():
    """两类失败同时存在时都要报出来，不能只留一条。"""
    issue = {"code": "layer_order_mismatch", "layer": "mouth",
             "message": "层序错位: mouth"}
    status = derive_deployment_status(
        _rigging(runtime_ready=False, moc3_blocker="官方内核拒绝: inconsistent"),
        layer_order_issues=[issue],
    )

    assert status["deployment_ready"] is False
    assert status["deployment_blockers"] == [
        "官方内核拒绝: inconsistent", "层序错位: mouth"]
