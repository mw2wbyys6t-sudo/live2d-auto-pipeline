"""settings.json → 环境变量覆盖：只放行非敏感项，且 UI 设置高于 .env。"""

import json

from core.settings_file import load_overrides


def _write(tmp_path, payload):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestLoadOverrides:
    def test_maps_engines_and_provider_fields(self, tmp_path):
        path = _write(tmp_path, {
            "engines": {
                "comfyui_base_url": "http://127.0.0.1:9999",
                "sd_webui_base_url": "http://192.168.1.20:7860",
            },
            "providers": {
                "openai": {"base_url": "https://gateway.example.com/v1", "model": "my-model"},
                "seedream": {"version": "seedream-4.0", "size": "1024x1536"},
                "sensenova": {"base_url": "https://api.sensenova.cn/v1"},
            },
        })
        overrides = load_overrides(path)
        assert overrides["COMFYUI_BASE_URL"] == "http://127.0.0.1:9999"
        assert overrides["SD_WEBUI_BASE_URL"] == "http://192.168.1.20:7860"
        assert overrides["OPENAI_BASE_URL"] == "https://gateway.example.com/v1"
        assert overrides["IMAGE_MODEL"] == "my-model"
        assert overrides["SEEDREAM_VERSION"] == "seedream-4.0"
        assert overrides["SEEDREAM_SIZE"] == "1024x1536"
        assert overrides["SENSENOVA_BASE_URL"] == "https://api.sensenova.cn/v1"

    def test_missing_or_broken_file_returns_empty(self, tmp_path):
        assert load_overrides(tmp_path / "nope.json") == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{not-json", encoding="utf-8")
        assert load_overrides(bad) == {}

    def test_control_characters_are_dropped(self, tmp_path):
        path = _write(tmp_path, {"engines": {"comfyui_base_url": "http://127.0.0.1:8188\u0001"}})
        assert "COMFYUI_BASE_URL" not in load_overrides(path)

    def test_empty_values_do_not_override(self, tmp_path):
        path = _write(tmp_path, {"engines": {"comfyui_base_url": "   "}})
        assert load_overrides(path) == {}

    def test_bom_from_notepad_is_tolerated(self, tmp_path):
        path = tmp_path / "bom.json"
        payload = json.dumps({"engines": {"comfyui_base_url": "http://127.0.0.1:9999"}})
        path.write_text("\ufeff" + payload, encoding="utf-8")
        assert load_overrides(path)["COMFYUI_BASE_URL"] == "http://127.0.0.1:9999"
