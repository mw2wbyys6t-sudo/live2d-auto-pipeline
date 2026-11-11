"""浏览器物理（TS 的 Verlet）必须与官方内核**逐帧**一致。

为什么要有这条：自研播放器里的物理原先是弹簧-阻尼近似 —— 刚度/阻尼按阶跃响应
相关系数标定（头发 0.66、身体 0.53、呼吸 0.87），形状像但与内核不是同一个动力
学；同一份 physics3.json 在浏览器与桌面上动得不一样。

现在播放器复刻的是内核自己的 Verlet 位置约束链（逆向 Cubism Editor 5.3 的
`CPhysicsFtlAlgorithm`）。判据：**同一份 physics3.json、同一个输入阶跃，两边
逐帧比较输出参数**，physics3 里的**每一条链**都要量到（含输出权重 < 100 的
Breathing 链）。

帧对齐：**同帧号直接比（位移 0）**。内核的物理响应比参数写入晚一帧，而 TS
引擎内部已经用 previousInputs 建模了这条滞后，所以两边的第 n 帧就是同一个
物理时刻。曾经这里强制 Core[1:] 对 TS[:-1]，那是 TS 还没建模滞后时的写法；
补上滞后之后再位移一次等于把滞后算两遍，实测会报出 2.85 的假偏差
（= |1.62848 − (−1.2222)|，HairFront 链相邻两帧之差）。

物理输出是**当帧瞬态**：跑手每帧先 restoreOutputs 撤回上一帧输出、再快照本帧
基准，与播放器的帧契约一致。内核的 weight<100 混合基准不含上一帧物理输出，
不撤回会让 Breathing 链峰值差 80%（tools/probe_breathing_split.py 的 A/B/C 三段
实验：改权重 100 后动力学逐位相同 ⇒ 差异只在混合基准取谁）。

判据同时是 fail-safe 的：Core/TS 跑失败必须显式报出来（不许静默跳过而让
"偏差 0" 变成死链的假绿），链必须一条不漏地量到，且每条链的 Core 峰值必须
真的非零。

复现脚本：tools/probe_physics_parity.py、tools/probe_physics_trace.py、
tools/probe_parity_alignment.py（逐链打印两种位移下的偏差）、
tools/probe_breathing_split.py（权重混合与动力学分离实验）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

probe_physics_parity = pytest.importorskip("probe_physics_parity")


def _has_live2d() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("live2d.v3") is not None
    except (ImportError, ValueError):
        return False


requires_live2d = pytest.mark.skipif(
    not _has_live2d(), reason="需要官方 Cubism Core（live2d-py）才能比对物理轨迹")


@requires_live2d
def test_the_browser_physics_matches_the_official_core_frame_by_frame(tmp_path):
    """TS 的 Verlet 与官方 Core 的逐帧轨迹必须数值相同（同帧号对齐）。"""
    manifest, physics = probe_physics_parity._build(tmp_path)
    assert physics is not None and physics.is_file(), "产物里没有 physics3.json"
    physics_doc = json.loads(physics.read_text(encoding="utf-8"))
    model = json.loads(manifest.read_text(encoding="utf-8"))

    ranges: dict = {}
    defaults: dict = {}
    raw = model.get("Parameters") or []
    for spec in raw:
        name = str(spec.get("Id") or "")
        if not name:
            continue
        ranges[name] = {"min": float(spec.get("Min", -30.0)),
                        "max": float(spec.get("Max", 30.0))}
        defaults[name] = float(spec.get("Value", spec.get("Default", 0.0)))

    measured = []
    unmeasured = []
    expected = 0
    for src, dest, system, _kind in probe_physics_parity._chains(physics_doc):
        if src not in ranges or dest not in ranges:
            continue
        expected += 1
        label = f"{system} {src}→{dest}"
        core = probe_physics_parity._run_core(
            str(manifest), src, dest, 30, 1 / 60.0, defaults.get(src, 0.0))
        if not core.get("ok"):
            unmeasured.append(f"{label}: Core 失败 {core.get('error')}")
            continue
        ts = probe_physics_parity._run_ts(
            physics_doc, ranges, defaults, src, dest, 30, 1 / 60.0,
            ranges[src]["max"] or 1.0)
        if not ts.get("ok"):
            unmeasured.append(f"{label}: TS 失败 {ts.get('error')}")
            continue
        ct, tt = core["trace"], ts["trace"]
        n = min(len(ct), len(tt))
        if n < 5:
            unmeasured.append(f"{label}: 轨迹太短 core={len(ct)} ts={len(tt)}")
            continue
        deviation = max(abs(a - b) for a, b in zip(ct[:n], tt[:n]))
        peak = max(abs(v - core["baseline"]) for v in ct[:n])
        measured.append((label, deviation, peak, ct, tt, core["baseline"]))

    detail = "\n".join(
        f"  {label}: 偏差 {dev:.9f}  Core 峰值 {peak:.6f}"
        for label, dev, peak, _ct, _tt, _base in measured)
    assert not unmeasured, "有物理链没能测到（不许静默跳过）：\n" + "\n".join(unmeasured)
    assert len(measured) == expected and expected > 0, (
        f"物理链应测 {expected} 条，实测 {len(measured)} 条")
    # 死链的"偏差 0"是假绿：Core 必须真的被这个输入推动过。
    dead = [label for label, _dev, peak, _ct, _tt, _base in measured if peak <= 1e-6]
    assert not dead, f"这些链在官方内核里根本没动，比对无意义：{dead}\n{detail}"
    # 相位签名：阶跃当帧内核还没有响应（这正是 TS 内部那一帧滞后的由来）。
    out_of_phase = [
        label for label, _dev, _peak, ct, _tt, base in measured
        if abs(ct[0] - base) > 1e-6]
    assert not out_of_phase, (
        f"这些链在阶跃当帧就动了，与内核的一帧滞后不符：{out_of_phase}\n{detail}")

    worst = max(dev for _label, dev, _peak, _ct, _tt, _base in measured)
    assert worst <= 1e-6, (
        f"浏览器物理与官方内核逐帧偏差 {worst:.9f}（超过 1e-6）："
        f"Verlet 移植与内核不同源\n{detail}")
