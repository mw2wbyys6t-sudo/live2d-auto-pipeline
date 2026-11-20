"""图像生成 provider 的唯一枚举来源。

此前上游名字散落三处：``core/image_gen/router.py`` 的 PRIORITY、
``core/workflow.py`` 的 argparse choices、Go 侧 ``knownImageProviders``。
改一个名字要翻三个文件，而且顺序（优先级）与可用性判断容易漂移。

本模块只描述"有哪些上游、谁优先、要不要 Key、测试连接用多大图"，
不导入任何 provider 实现，因此可以被 workflow 的 argparse 轻量引用。

未来的自训专精模型：在 ``PROVIDER_SPECS`` 里加一项，并在
``core/image_gen/router.py`` 注册对应类即可，UI 从 ``/api/providers``
自动出现，不需要再改前端枚举。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ProviderSpec:
    """一个图像上游的静态描述（不含运行时状态）。"""

    name: str
    display_name: str
    local: bool
    requires_key: bool
    key_env: str = ""
    #: 「测试连接」真实出图使用的尺寸。取各上游的最小合法尺寸：
    #: Seedream 最小 1024²，OpenAI Images 常用 1024²，其余可更小。
    probe_size: Tuple[int, int] = (512, 512)
    #: 测试会消耗额度的提示文案（空 = 免费/本地，不提示成本）。
    probe_note: str = ""


#: 顺序即 ``ProviderRouter.PRIORITY``（本机引擎优先，其次云端）。
#: ``local_placeholder`` 只注册、不进默认顺序（它是离线占位图，
#: 显式选择才生效），因此排在最后。
PROVIDER_SPECS: Tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="comfyui",
        display_name="ComfyUI（本地）",
        local=True,
        requires_key=False,
        probe_size=(512, 512),
    ),
    ProviderSpec(
        name="sd_webui",
        display_name="SD WebUI（本地）",
        local=True,
        requires_key=False,
        probe_size=(512, 512),
    ),
    ProviderSpec(
        name="seedream",
        display_name="Seedream / 火山引擎 ARK",
        local=False,
        requires_key=True,
        key_env="ARK_API_KEY",
        probe_size=(1024, 1024),
        probe_note="测试会真实调用一次 Seedream（最小 1024×1024），消耗一次额度",
    ),
    ProviderSpec(
        name="sensenova",
        display_name="SenseNova（商汤日日新）",
        local=False,
        requires_key=True,
        key_env="SENSENOVA_API_KEY",
        probe_size=(512, 512),
        probe_note="测试会真实调用一次 SenseNova（512×512），消耗一次额度",
    ),
    ProviderSpec(
        name="openai",
        display_name="OpenAI 兼容端点",
        local=False,
        requires_key=True,
        key_env="OPENAI_API_KEY",
        probe_size=(1024, 1024),
        probe_note="测试会真实调用一次 OpenAI 兼容端点（1024×1024），按服务商计费",
    ),
    ProviderSpec(
        name="pollinations",
        display_name="Pollinations.ai（免密）",
        local=False,
        requires_key=False,
        probe_size=(256, 256),
    ),
    ProviderSpec(
        name="local_placeholder",
        display_name="本地占位图（离线）",
        local=True,
        requires_key=False,
        probe_size=(512, 512),
    ),
)

_SPEC_BY_NAME: Dict[str, ProviderSpec] = {s.name: s for s in PROVIDER_SPECS}

#: 默认尝试顺序：与旧 PRIORITY 完全一致（不含 local_placeholder）。
PRIORITY_NAMES: Tuple[str, ...] = tuple(
    s.name for s in PROVIDER_SPECS if s.name != "local_placeholder"
)


def spec_for(name: str) -> Optional[ProviderSpec]:
    """按名字取 spec；未知名字返回 None（调用方自行决定降级）。"""
    return _SPEC_BY_NAME.get(name)


def provider_names(include_placeholder: bool = True) -> List[str]:
    """全部已注册上游名（UI/校验用）。"""
    return [s.name for s in PROVIDER_SPECS if include_placeholder or s.name != "local_placeholder"]


def provider_choices(include_auto: bool = True) -> List[str]:
    """workflow ``--provider`` 的 argparse choices。"""
    choices = ["auto"] if include_auto else []
    choices.extend(provider_names())
    return choices
