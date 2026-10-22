"""导出就绪判定的唯一真相源。

为什么住在领域层：这条契约的**计算**在 ``live2d_builder``（``moc3_pipeline`` 算出
``runtime_ready``），**决策点**也在 ``live2d_builder``（``pipeline.py`` 决定是否打
``Built``、是否允许打包）。早先它只定义在服务层 ``api_server``，于是领域层既不
执行也不据此阻断 —— 被官方内核拒绝的产物照样打「构建成功」并进 zip。

放到这里而不是让 builder 反向 import ``api_server``：``scripts/lint_architecture.py``
的 FF-1 规则 4 明确禁止 ``live2d_builder`` 依赖服务层。方向必须是「服务层读领域层」。

唯一可信来源是构建期官方 Cubism Core 的验收结论（``result["moc3"]``）。
磁盘上存在 ``.moc3`` 文件不代表它通过了内核一致性检查 —— 早先版本按「文件是否存在」
判定，会把被内核拒绝的产物报成开箱即用。
"""

from __future__ import annotations

from typing import Any, Dict

__all__ = ["runtime_readiness"]


def runtime_readiness(result: Dict[str, Any]) -> Dict[str, Any]:
    """导出包能否被 Live2D 运行时直接加载。

    Args:
        result: 构建结果（``Live2DBuilder.build()`` 的返回值，或等价的摘要 dict）。

    Returns:
        ``runtime_ready`` 与配套的验证明细；未就绪时 ``blocker`` 一定非空。
    """
    moc3 = result.get("moc3") or {}
    ready = bool(moc3.get("runtime_ready"))
    if not moc3:
        blocker = "构建结果缺少 moc3 验收信息，不能视为可部署"
    else:
        blocker = moc3.get("moc3_blocker") or "moc3 未通过官方 Cubism Core 验收"
    return {
        "runtime_ready": ready,
        "moc_reference": moc3.get("moc3_path") or "",
        "official_core_consistent": bool(moc3.get("official_core_consistent")),
        "moc3_dropped": moc3.get("moc3_dropped") or [],
        "deformers_compiled": moc3.get("deformers_compiled") or [],
        "deformers_uncompiled": moc3.get("deformers_uncompiled") or [],
        # true = 官方内核真的在播放产物里的 motion；null = 没有可动参数，
        # 不适用（不等于通过）；false = 写了 motion 但内核不播，属于阻塞
        "motion_verified": moc3.get("motion_verified"),
        # true = 官方内核里 physics 确实产生带滞后的运动；null = 无可验物理链
        "physics_verified": moc3.get("physics_verified"),
        "blocker": "" if ready else blocker,
    }
