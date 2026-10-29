"""GPU 探测单真源：硬件枚举与推理运行时能力分开采集，推荐规则可注入测试。

采集侧（WMI / nvidia-smi / import onnxruntime|torch）只负责产出事实；
probe_gpu 只依赖注入的 collector，判定逻辑因此在无 GPU 的 CI 上也可复现。
"""

import json
import subprocess
import sys
import time
import weakref
from typing import Any, Callable, Dict, List, Optional

_ADAPTER_CACHE: Dict[int, tuple] = {}
_IS_WINDOWS = sys.platform.startswith("win")

#: Win32_VideoController 一次查询要几百毫秒，取回这三列就够判定与展示了。
_WMI_QUERY = (
    "Get-CimInstance -ClassName Win32_VideoController"
    " | Select-Object Name,DriverVersion,AdapterRAM | ConvertTo-Json"
)


def diagnostics_row(probe: Optional[Dict[str, Any]], error: Optional[BaseException] = None) -> Dict[str, Any]:
    """把探测结果压成自检页的一行（与 Go 侧 gpuDiagnosticCheck 同口径）。

    「装了但用不上」必须看得出来：硬件在列表里、但运行时只有 CPU provider 时
    记为不通过，并点出卡型 —— 用户据此判断该装 CUDA 还是 DirectML。
    探测异常只报「暂不可用」，异常原文（常含本机路径）不进结果。
    """
    row: Dict[str, Any] = {"key": "gpu", "label": "GPU 与推理运行时"}
    if error is not None or probe is None:
        row["ok"] = False
        row["detail"] = "GPU 探测暂不可用"
        row["issues"] = ["GPU 探测暂不可用，分层按 CPU 运行"]
        return row

    names = [a["name"] for a in probe.get("adapters", []) if a.get("name")]
    # issue 文案只点出真正的显卡：虚拟显示器（远程桌面/采集卡）也带驱动版本，
    # 把它们写进「已检测到」会让人以为换运行时就能上 GPU。
    gpu_names = [a["name"] for a in probe.get("adapters", []) if a.get("vendor") in ("NVIDIA", "AMD", "INTEL")]
    summary = " / ".join(names) or "未枚举到显示适配器"
    device = probe.get("recommendation", {}).get("device", "cpu")

    if not probe.get("available"):
        row["ok"] = False
        row["detail"] = f"{summary} → cpu"
        row["issues"] = ["未检测到可用独立显卡，分层按 CPU 运行"]
    elif device == "cpu":
        row["ok"] = False
        row["detail"] = f"{summary} → cpu"
        row["issues"] = [
            f"{'/'.join(gpu_names)} 已检测到，但当前推理运行时只有 CPU provider"
            "（onnxruntime 缺 CUDA/DirectML），分层按 CPU 运行"
        ]
    else:
        row["ok"] = True
        row["detail"] = f"{summary} → {device}"
        row["issues"] = []
    return row


def parse_wmi_adapters(raw: str) -> List[Dict[str, Any]]:

    """把 WMI 的 JSON 输出归一成本项目的适配器契约。

    PowerShell 被执行策略拦掉时 stdout 是报错文本；单块显卡时 ConvertTo-Json
    还会退化成对象而不是数组。两种情况都不能假装数出了显卡。
    """
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []

    rows: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = item.get("Name")
        if not name:
            continue
        vram = item.get("AdapterRAM")
        rows.append({
            "name": str(name),
            "driver_version": item.get("DriverVersion"),
            # AdapterRAM 常为 null（驱动不上报）：保留未知，不写成 0 显存。
            "vram_bytes": vram if isinstance(vram, int) else None,
        })
    return rows


def _default_adapter_collector() -> List[Dict[str, Any]]:
    if not _IS_WINDOWS:
        return []
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _WMI_QUERY],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    return parse_wmi_adapters(completed.stdout)


def collect_runtime(
    import_module: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """本进程真正可用的推理运行时（装了不等于能用，以 import 结果为准）。"""
    def _import(name: str):
        try:
            import importlib
            return importlib.import_module(name)
        except Exception:
            return None

    loader = import_module or _import

    ort = loader("onnxruntime")
    providers: List[str] = []
    if ort is not None:
        try:
            providers = list(ort.get_available_providers())
        except Exception:
            providers = []

    torch = loader("torch")
    torch_cuda = False
    device_name = None
    if torch is not None:
        try:
            torch_cuda = bool(torch.cuda.is_available())
            if torch_cuda:
                device_name = torch.cuda.get_device_name(0)
        except Exception:
            torch_cuda = False

    return {
        "onnxruntime_available": ort is not None,
        "onnxruntime_providers": providers,
        "torch_available": torch is not None,
        "torch_cuda_available": torch_cuda,
        "torch_device_name": device_name,
    }


def _default_runtime_collector() -> Dict[str, Any]:
    return collect_runtime()


def _vendor_of(name: str) -> str:
    upper = name.upper()
    if "NVIDIA" in upper or "GEFORCE" in upper or "QUADRO" in upper:
        return "NVIDIA"
    if "RADEON" in upper or "AMD" in upper:
        return "AMD"
    if "ARC" in upper or "INTEL" in upper:
        return "INTEL"
    return "OTHER"


def probe_gpu(
    adapter_collector: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    runtime_collector: Optional[Callable[[], Dict[str, Any]]] = None,
    ttl: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
    cache: Optional[Dict[int, tuple]] = None,
) -> Dict[str, Any]:
    entries = cache if cache is not None else _ADAPTER_CACHE
    collect = adapter_collector or _default_adapter_collector
    key = id(collect)
    hit = entries.get(key)
    # 命中必须同时验证 TTL 与「同一对象」：id() 在对象回收后会被复用，
    # 只按 id 命中会把上一个 collector 的显卡表当成本次结果返回。
    # 存弱引用，既能在对象还活着时辨伪，也不会因为缓存而拖延回收。
    if hit is not None and hit[2]() is collect and clock() - hit[0] < ttl:
        adapters = hit[1]
    else:
        adapters = collect()
        entries[key] = (clock(), adapters, weakref.ref(collect))

    runtime = (runtime_collector or _default_runtime_collector)()
    for adapter in adapters:
        adapter["vendor"] = _vendor_of(adapter["name"])

    gpu_vendors = {a["vendor"] for a in adapters} & {"NVIDIA", "AMD", "INTEL"}
    available = bool(gpu_vendors)
    providers = runtime.get("onnxruntime_providers") or []

    device = "cpu"
    if available and runtime.get("torch_cuda_available"):
        device = "cuda"
    elif available and "DmlExecutionProvider" in providers:
        device = "dml"

    return {
        "available": available,
        "adapters": adapters,
        "runtime": runtime,
        "recommendation": {"device": device},
    }
