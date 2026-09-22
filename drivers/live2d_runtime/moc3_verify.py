"""调用官方 Cubism Core 的检查：subprocess 隔离 + 超时 + 崩溃归类。

契约：本模块的函数**永不抛出**。任何异常路径都返回 ok=False 并给出 blocker 原因。
Cubism Core 是非官方第三方绑定，崩溃视为验证失败而非程序错误。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

_CONSISTENCY_MODULE = "drivers.live2d_runtime.moc3_consistency"
_LOAD_MODULE = "drivers.live2d_runtime.moc3_load"
_RUNTIME_MODULE = "drivers.live2d_runtime.verify"
_RENDER_MODULE = "drivers.live2d_runtime.moc3_render_probe"
_MOTION_MODULE = "drivers.live2d_runtime.moc3_motion_probe"
_PHYSICS_MODULE = "drivers.live2d_runtime.moc3_physics_probe"
_RUNTIME_PREFIX = "LIVE2D_VERIFICATION="
_RENDER_PREFIX = "LIVE2D_RENDER="
_MOTION_PREFIX = "LIVE2D_MOTION="
_PHYSICS_PREFIX = "LIVE2D_PHYSICS="
_DEFAULT_TIMEOUT = 30


def verify_moc3_consistency(
    moc3_path: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """对 moc3 跑官方一致性检查（设计文档 §8.3 判据 1）。"""
    return _run_isolated(_CONSISTENCY_MODULE, moc3_path, timeout, {
        "不一致": "官方 Cubism Core 判定 moc3 不一致",
    })


def verify_moc3_load(
    manifest_path: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """用官方 Core 加载 model3 清单并读取参数 id（§8.3 判据 2、3）。

    成功时额外返回 ``parameter_ids``。
    """
    result = _run_isolated(_LOAD_MODULE, manifest_path, timeout, {
        "不一致": "官方 Cubism Core 判定 moc3 不一致",
    })
    result["parameter_ids"] = _payload_field(result["stdout"], "parameter_ids") or []
    return result


def verify_motion_playback(
    manifest_path: str,
    group: str = "idle",
    frames: int = 180,
    timeout: int = 120,
) -> Dict[str, Any]:
    """让官方内核播放清单里声明的 motion，并逐帧把参数值与文件对账。

    「文件写得对」不等于「画面会动」：本函数是唯一判据 —— 内核没把参数打到
    文件声称的行程上就是不合格。契约同本模块其它函数：永不抛出。
    """
    result: Dict[str, Any] = {
        "ok": False, "exit_code": None, "stdout": "", "stderr": "",
        "timed_out": False, "crashed": False, "blocker": None,
        "curves": {}, "error_per_second": [],
    }
    path = Path(manifest_path)
    if not path.is_file():
        result["blocker"] = f"文件不存在: {manifest_path}"
        return result

    cmd = [sys.executable, "-m", _MOTION_MODULE, str(path),
           "--group", group, "--frames", str(frames)]
    try:
        code, stdout, stderr = _spawn(cmd, timeout)
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["blocker"] = f"motion 播放验证 {timeout}s 超时"
        return result
    except OSError as exc:
        result["blocker"] = f"无法启动 motion 验证子进程: {exc}"
        return result

    result["exit_code"] = code
    result["stdout"] = stdout
    result["stderr"] = stderr

    payload = _prefixed_payload(stdout, _MOTION_PREFIX)
    if not isinstance(payload, dict):
        if code not in (0, 1):
            result["crashed"] = True
            result["blocker"] = (
                f"cubism core crashed during motion playback (code={code})")
        else:
            result["blocker"] = "motion 播放验证没有输出结果"
        return result

    result["curves"] = payload.get("curves") or {}
    result["error_per_second"] = payload.get("error_per_second") or []
    result["ok"] = bool(payload.get("ok"))
    if not result["ok"]:
        moved = sum(1 for c in result["curves"].values() if c.get("moved"))
        result["blocker"] = payload.get("error") or (
            f"motion 未被官方内核播放：动到的参数 {moved}/"
            f"{len(result['curves'])}，逐帧超限的曲线 {payload.get('mismatched') or []}")
    return result


def verify_physics_playback(
    manifest_path: str,
    frames: int = 90,
    timeout: int = 120,
) -> Dict[str, Any]:
    """验证产物里的 physics3 在官方内核里真的产生**带滞后、可回摆**的运动。

    「文件合 schema、被加载」都不算 —— 输出 Type 写成 "X" 时内核会静默丢掉整条链，
    参数一动不动。判据来自 moc3_physics_probe：moved / lagged / settled 三者齐备。
    契约同本模块其它函数：永不抛出。
    """
    result: Dict[str, Any] = {
        "ok": False, "exit_code": None, "stdout": "", "stderr": "",
        "timed_out": False, "crashed": False, "blocker": None, "chains": [],
    }
    path = Path(manifest_path)
    if not path.is_file():
        result["blocker"] = f"文件不存在: {manifest_path}"
        return result
    cmd = [sys.executable, "-m", _PHYSICS_MODULE, str(path),
           "--frames", str(frames)]
    try:
        code, stdout, stderr = _spawn(cmd, timeout)
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["blocker"] = f"physics 验证 {timeout}s 超时"
        return result
    except OSError as exc:
        result["blocker"] = f"无法启动 physics 验证子进程: {exc}"
        return result

    result["exit_code"] = code
    result["stdout"] = stdout
    result["stderr"] = stderr
    payload = _prefixed_payload(stdout, _PHYSICS_PREFIX)
    if not isinstance(payload, dict):
        if code not in (0, 1):
            result["crashed"] = True
            result["blocker"] = (
                f"cubism core crashed during physics playback (code={code})")
        else:
            result["blocker"] = "physics 验证没有输出结果"
        return result
    result["chains"] = payload.get("chains") or []
    result["ok"] = bool(payload.get("ok"))
    if not result["ok"]:
        dead = [f"{c['input']}->{c['output']}" for c in result["chains"]
                if not (c.get("moved") and c.get("lagged") and c.get("settled"))]
        result["blocker"] = payload.get("error") or (
            f"physics 未产生带滞后的可回摆运动：{dead or '没有可验的参数链'}")
    return result


def _spawn(cmd: List[str], timeout: int):
    """跑子进程并按 UTF-8 解码输出。

    不能用 text=True 的默认解码：中文 Windows 的 locale 是 cp936，而子进程
    （live2d-py 的日志）会输出 UTF-8 字节，解码失败会让 stdout 变成 None，
    整条验收链因此随机崩溃。
    """
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _prefixed_payload(stdout: str, prefix: str):
    """取出 <prefix><json> 行；没有则返回 None。"""
    for line in reversed((stdout or "").strip().splitlines()):
        if line.startswith(prefix):
            try:
                return json.loads(line[len(prefix):])
            except ValueError:
                return None
    return None


def render_probe(
    manifest_path: str,
    params: Dict[str, float] | None = None,
    png: str = None,
    width: int = 400,
    height: int = 500,
    timeout: int = 120,
    prime: bool = True,
) -> Dict[str, Any]:
    """离屏渲染一帧并取几何统计（像素 bbox / 质心 / 不透明像素数）。

    与一致性检查互补：字节合法不等于画面对。契约同样永不抛出。
    """
    result: Dict[str, Any] = {
        "ok": False, "exit_code": None, "stdout": "", "stderr": "",
        "timed_out": False, "crashed": False, "blocker": None,
        "alpha_bbox": None, "opaque_pixels": 0, "centroid": None,
        "pixels_sha256": "", "png": None,
    }
    path = Path(manifest_path)
    if not path.is_file():
        result["blocker"] = f"文件不存在: {manifest_path}"
        return result

    cmd = [sys.executable, "-m", _RENDER_MODULE, str(path),
           "--width", str(width), "--height", str(height)]
    for name, value in (params or {}).items():
        cmd += ["--param", f"{name}={value}"]
    if png:
        cmd += ["--png", str(png)]
    if not prime:
        cmd.append("--no-prime")

    try:
        code, stdout, stderr = _spawn(cmd, timeout)
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["blocker"] = f"渲染探针超时（{timeout}s）"
        return result
    except OSError as exc:
        result["blocker"] = f"无法启动渲染子进程: {exc}"
        return result

    result["exit_code"] = code
    result["stdout"] = stdout
    result["stderr"] = stderr

    payload = _prefixed_payload(stdout, _RENDER_PREFIX)
    if payload is None:
        if code not in (0, 1, 2):
            result["crashed"] = True
            result["blocker"] = f"cubism core crashed during render (code={code})"
        else:
            result["blocker"] = _payload_field(stdout, "error") or "渲染探针没有输出结果"
        return result
    if not payload.get("ok"):
        result["blocker"] = payload.get("error") or "渲染失败"
        return result
    result.update({
        "ok": True,
        "alpha_bbox": payload.get("alpha_bbox"),
        "opaque_pixels": payload.get("opaque_pixels", 0),
        "centroid": payload.get("centroid"),
        "pixels_sha256": payload.get("pixels_sha256", ""),
        "png": payload.get("png"),
    })
    return result


def _prefixed_payload(stdout: str, prefix: str):
    """取出 <prefix><json> 行；没有则返回 None。"""
    for line in reversed(stdout.strip().splitlines()):
        if line.startswith(prefix):
            try:
                return json.loads(line[len(prefix):])
            except ValueError:
                return None
    return None


def verify_moc3_runtime(
    manifest_path: str,
    evidence_dir: str = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """受控像素验证：官方内核里参数真的改变了画面（§8.3 判据 4）。

    与一致性 / 加载检查不同，这里需要 OpenGL 上下文，耗时更长；
    返回值携带逐项 checks，供证据文件记录。契约不变：永不抛出。
    """
    result: Dict[str, Any] = {
        "ok": False, "exit_code": None, "stdout": "", "stderr": "",
        "timed_out": False, "crashed": False, "blocker": None, "checks": [],
    }
    path = Path(manifest_path)
    if not path.is_file():
        result["blocker"] = f"文件不存在: {manifest_path}"
        return result

    cmd = [sys.executable, "-m", _RUNTIME_MODULE, str(path)]
    if evidence_dir:
        cmd += ["--evidence-dir", str(evidence_dir)]
    try:
        code, stdout, stderr = _spawn(cmd, timeout)
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["blocker"] = f"受控像素验证超时（{timeout}s）"
        return result
    except OSError as exc:
        result["blocker"] = f"无法启动验证子进程: {exc}"
        return result

    result["exit_code"] = code
    result["stdout"] = stdout
    result["stderr"] = stderr

    payload = _runtime_payload(stdout)
    if payload is None:
        if code not in (0, 1):
            result["crashed"] = True
            result["blocker"] = (
                f"cubism core crashed during pixel verification "
                f"(code={code})")
        else:
            result["blocker"] = "受控像素验证没有输出结果"
        return result

    result["checks"] = payload.get("checks") or []
    result["ok"] = bool(payload.get("runtime_verified"))
    if not result["ok"]:
        result["blocker"] = (payload.get("error")
                             or "官方内核受控像素验证未通过")
    return result


def _runtime_payload(stdout: str):
    """取出 LIVE2D_VERIFICATION=<json> 行；没有则返回 None。"""
    return _prefixed_payload(stdout, _RUNTIME_PREFIX)


def _run_isolated(
    module: str,
    target: str,
    timeout: int,
    failure_messages: Dict[str, str],
) -> Dict[str, Any]:
    """在独立进程里跑一个检查入口，并把各种失败形态归类为 blocker。"""
    result: Dict[str, Any] = {
        "ok": False,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "timed_out": False,
        "crashed": False,
        "blocker": None,
    }

    path = Path(target)
    if not path.is_file():
        result["blocker"] = f"文件不存在: {target}"
        return result

    cmd = [sys.executable, "-m", module, str(path)]
    try:
        code, stdout, stderr = _spawn(cmd, timeout)
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["blocker"] = f"consistency check timed out after {timeout}s"
        return result
    except OSError as exc:
        result["blocker"] = f"无法启动检查子进程: {exc}"
        return result

    result["exit_code"] = code
    result["stdout"] = stdout
    result["stderr"] = stderr

    if code == 0:
        result["ok"] = True
        return result

    if code == 2:
        detail = _payload_field(stdout, "error") or "live2d-py 不可用"
        result["blocker"] = f"检查环境不可用: {detail}"
        return result

    if code == 1:
        detail = _payload_field(stdout, "error")
        result["blocker"] = detail or failure_messages["不一致"]
        return result

    # 其余退出码：视为崩溃（Windows 访问违规为 3221225477 / 0xC0000005）
    result["crashed"] = True
    result["blocker"] = f"cubism core crashed (code={code})"
    return result


def _payload_field(stdout: str, field: str):
    """读取子进程最后一行 JSON 里的字段；读不到返回 None。"""
    try:
        data = json.loads((stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return data.get(field) if isinstance(data, dict) else None
