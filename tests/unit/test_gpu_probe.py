"""core/gpu_probe 的行为测试：采集与判断分离，判断逻辑用注入的假数据验证。"""

from core.gpu_probe import probe_gpu


def _fake_runtime(**overrides):
    runtime = {
        "onnxruntime_available": False,
        "onnxruntime_providers": [],
        "torch_available": False,
        "torch_cuda_available": False,
        "torch_device_name": None,
    }
    runtime.update(overrides)
    return runtime


def _fake_adapters(*rows):
    return [dict(name=n, driver_version=d, vram_bytes=v) for n, d, v in rows]


class _DeadCollector:
    """可弱引用占位，用来构造「缓存条目的原 collector 已被回收」。"""


class TestRecommendation:
    def test_nvidia_with_torch_cuda_recommends_cuda(self):
        """有 NVIDIA 卡且 torch CUDA 可用时，推荐 device=cuda。"""
        adapters = _fake_adapters(("NVIDIA GeForce RTX 3060", "546.17", 12 * 2**30))
        runtime = _fake_runtime(
            torch_available=True,
            torch_cuda_available=True,
            torch_device_name="NVIDIA GeForce RTX 3060",
        )
        out = probe_gpu(adapter_collector=lambda: adapters, runtime_collector=lambda: runtime)

        assert out["available"] is True
        assert out["recommendation"]["device"] == "cuda"

    def test_nvidia_without_cuda_runtime_degrades_to_cpu(self):
        """检测到 NVIDIA 硬件但运行时不具备 CUDA 时，必须如实回落 cpu。"""
        adapters = _fake_adapters(("NVIDIA GeForce RTX 3060", "546.17", 12 * 2**30))
        out = probe_gpu(adapter_collector=lambda: adapters, runtime_collector=_fake_runtime)

        assert out["available"] is True
        assert out["recommendation"]["device"] == "cpu"

    def test_amd_card_with_directml_provider_recommends_dml(self):
        """非 NVIDIA 卡（AMD/Intel Arc）经 onnxruntime DirectML 也能上 GPU。"""
        adapters = _fake_adapters(("AMD Radeon RX 6600", "31.0.21.0", 8 * 2**30))
        runtime = _fake_runtime(
            onnxruntime_available=True,
            onnxruntime_providers=["DmlExecutionProvider", "CPUExecutionProvider"],
        )
        out = probe_gpu(adapter_collector=lambda: adapters, runtime_collector=lambda: runtime)

        assert out["available"] is True
        assert out["recommendation"]["device"] == "dml"
        assert out["adapters"][0]["vendor"] == "AMD"

    def test_basic_display_adapter_is_not_counted_as_gpu(self):
        """虚拟机/裸驱动下的 Basic Display Adapter 不能被当成可用 GPU。"""
        adapters = _fake_adapters(("Microsoft Basic Display Adapter", "10.0.0", 0))
        out = probe_gpu(adapter_collector=lambda: adapters, runtime_collector=_fake_runtime)

        assert out["available"] is False
        assert out["recommendation"]["device"] == "cpu"


class TestDiagnosticsRow:
    """自检页那一行的判定也放在单真源里，避免 Go/Python 各写一套口径。"""

    def test_hardware_without_usable_runtime_is_not_green(self):
        from core.gpu_probe import diagnostics_row, probe_gpu

        adapters = _fake_adapters(("NVIDIA GeForce RTX 2060", "32.0.15.8183", 6 * 2**30))
        runtime = _fake_runtime(onnxruntime_available=True, onnxruntime_providers=["CPUExecutionProvider"])
        row = diagnostics_row(probe_gpu(adapter_collector=lambda: adapters, runtime_collector=lambda: runtime))

        assert row["key"] == "gpu"
        assert row["ok"] is False
        joined = " ".join(row["issues"])
        assert "RTX 2060" in joined
        assert "CPU" in joined

    def test_issue_names_only_real_gpus_not_virtual_display_adapters(self):
        """远程桌面/采集卡的虚拟显示器不能被写进「已检测到显卡」里。"""
        from core.gpu_probe import diagnostics_row, probe_gpu

        adapters = _fake_adapters(
            ("GameViewer Virtual Display Adapter", "15.6.5.199", None),
            ("NVIDIA GeForce RTX 2060", "32.0.15.8183", 6 * 2**30),
        )
        runtime = _fake_runtime(onnxruntime_available=True, onnxruntime_providers=["CPUExecutionProvider"])
        row = diagnostics_row(probe_gpu(adapter_collector=lambda: adapters, runtime_collector=lambda: runtime))

        joined = " ".join(row["issues"])
        assert "RTX 2060" in joined
        assert "GameViewer" not in joined

    def test_cuda_capable_runtime_is_green(self):
        from core.gpu_probe import diagnostics_row, probe_gpu

        adapters = _fake_adapters(("NVIDIA GeForce RTX 2060", "32.0.15.8183", 6 * 2**30))
        runtime = _fake_runtime(
            torch_available=True, torch_cuda_available=True,
            torch_device_name="NVIDIA GeForce RTX 2060",
        )
        row = diagnostics_row(probe_gpu(adapter_collector=lambda: adapters, runtime_collector=lambda: runtime))

        assert row["ok"] is True
        assert "cuda" in row["detail"]

    def test_probe_failure_keeps_row_without_leaking_details(self):
        from core.gpu_probe import diagnostics_row

        row = diagnostics_row(None, error=RuntimeError("boom: C:\\Users\\secret\\python.exe"))

        assert row["key"] == "gpu"
        assert row["ok"] is False
        assert "boom" not in str(row)
        assert "secret" not in str(row)


class TestWmiParsing:
    def test_wmi_rows_are_normalized(self):
        """WMI 的字段名与本项目契约不同，必须归一化后再交给判定层。"""
        from core.gpu_probe import parse_wmi_adapters

        raw = (
            '[{"Name":"NVIDIA GeForce RTX 3060","DriverVersion":"546.17",'
            '"AdapterRAM":4293918720}]'
        )
        rows = parse_wmi_adapters(raw)

        assert rows == [{
            "name": "NVIDIA GeForce RTX 3060",
            "driver_version": "546.17",
            "vram_bytes": 4293918720,
        }]

    def test_missing_adapter_ram_stays_unknown_not_zero(self):
        """WMI 常把 AdapterRAM 报成 null：必须保留「未知」，不能谎报成 0 显存。"""
        from core.gpu_probe import parse_wmi_adapters

        raw = '[{"Name":"AMD Radeon RX 6600","DriverVersion":"31.0.21.0","AdapterRAM":null}]'
        rows = parse_wmi_adapters(raw)

        assert rows[0]["vram_bytes"] is None

    def test_unparsable_wmi_output_yields_no_adapters(self):
        """PowerShell 被策略拦截时输出的是报错文本，不能当成显卡。"""
        from core.gpu_probe import parse_wmi_adapters

        assert parse_wmi_adapters("Get-Counter : Access denied") == []


class TestRuntimeCollection:
    def test_runtime_report_marks_absent_torch_as_unavailable(self):
        """未安装 torch 时 torch_cuda_available 必须为 False（而不是探测失败）。"""
        from core.gpu_probe import collect_runtime

        runtime = collect_runtime(import_module=lambda name: None)

        assert runtime["torch_available"] is False
        assert runtime["torch_cuda_available"] is False

    def test_runtime_reads_onnxruntime_providers(self):
        """ORT 提供的 provider 列表决定能否走 DirectML/CUDA。"""
        from core.gpu_probe import collect_runtime

        class _Ort:
            @staticmethod
            def get_available_providers():
                return ["DmlExecutionProvider", "CPUExecutionProvider"]

        runtime = collect_runtime(import_module=lambda name: _Ort if name == "onnxruntime" else None)

        assert runtime["onnxruntime_available"] is True
        assert "DmlExecutionProvider" in runtime["onnxruntime_providers"]

class TestAdapterCache:
    def test_same_collector_is_invoked_once_within_ttl(self):
        """默认探测要缓存硬件枚举：WMI 一次要几百毫秒，前端会轮询。"""
        calls = []

        def collector():
            calls.append(1)
            return _fake_adapters(("NVIDIA GeForce RTX 3060", "546.17", 12 * 2**30))

        probe_gpu(adapter_collector=collector, runtime_collector=_fake_runtime)
        probe_gpu(adapter_collector=collector, runtime_collector=_fake_runtime)
        assert len(calls) == 1

    def test_cache_expiry_recollects(self):
        """超过 TTL 后必须重新采集（驱动/显卡状态会变，缓存不能是永久的）。"""
        calls = []

        def collector():
            calls.append(1)
            return []

        clock = {"now": 1000.0}
        probe_gpu(
            adapter_collector=collector, runtime_collector=_fake_runtime,
            ttl=60.0, clock=lambda: clock["now"],
        )
        clock["now"] += 61.0
        probe_gpu(
            adapter_collector=collector, runtime_collector=_fake_runtime,
            ttl=60.0, clock=lambda: clock["now"],
        )
        assert len(calls) == 2

    def test_stale_entry_with_recycled_id_is_not_served(self):
        """id 被回收复用时，只按 id+TTL 命中就会把别人的显卡表当成本次结果。"""
        import gc
        import weakref

        def collector():
            return _fake_adapters(("AMD Radeon RX 6600", "31.0.21.0", 8 * 2**30))

        previous = _DeadCollector()  # 上一个 collector 的占位：id 已归还给系统
        ref = weakref.ref(previous)
        del previous
        gc.collect()
        assert ref() is None

        stale = {
            id(collector): (
                0.0,
                _fake_adapters(("NVIDIA GeForce RTX 3060", "546.17", 12 * 2**30)),
                ref,
            )
        }

        out = probe_gpu(
            adapter_collector=collector, runtime_collector=_fake_runtime,
            cache=stale, clock=lambda: 1.0, ttl=30.0,
        )

        assert [a["name"] for a in out["adapters"]] == ["AMD Radeon RX 6600"]
