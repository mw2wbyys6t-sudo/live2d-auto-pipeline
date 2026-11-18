"""Provider 单一真源：顺序、能力与 argparse choices 必须一致。"""

from core.image_gen.registry import (
    PRIORITY_NAMES,
    PROVIDER_SPECS,
    provider_choices,
    provider_names,
    spec_for,
)


class TestProviderRegistry:
    def test_priority_order_is_local_then_cloud(self):
        assert PRIORITY_NAMES == (
            "comfyui",
            "sd_webui",
            "seedream",
            "sensenova",
            "openai",
            "pollinations",
        )

    def test_placeholder_is_registered_but_not_default(self):
        assert "local_placeholder" in provider_names()
        assert "local_placeholder" not in PRIORITY_NAMES

    def test_cloud_providers_require_key(self):
        for name in ("seedream", "sensenova", "openai"):
            spec = spec_for(name)
            assert spec is not None
            assert spec.requires_key is True
            assert spec.key_env.endswith("_API_KEY")

    def test_local_providers_are_marked_local(self):
        for name in ("comfyui", "sd_webui", "local_placeholder"):
            assert spec_for(name).local is True

    def test_probe_sizes_are_sane(self):
        for spec in PROVIDER_SPECS:
            width, height = spec.probe_size
            assert width >= 256 and height >= 256

    def test_cli_choices_include_auto_and_all_registered(self):
        choices = provider_choices()
        assert choices[0] == "auto"
        assert set(choices[1:]) == {spec.name for spec in PROVIDER_SPECS}
