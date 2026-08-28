"""
接口一致性测试 - 验证 Python CLI 输出的 JSON 结构符合 Go API 解析契约。

这是 v10.1 最重要的接口契约测试：Go API 通过解析 workflow.py --json 的输出来
填充 GenerateImageResponse。如果 Python 输出的 JSON 字段发生变化，Go 端解析会
静默失败或返回空值。这类测试能提前发现接口漂移。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
TEST_IMAGE = PROJECT_ROOT / "docs" / "assets" / "demo_input.png"


def _run_workflow_json(args, timeout=120):
    """运行 workflow.py --json 并返回解析后的 dict

    解析策略：找到第一个顶层 '{' 起始位置，匹配到对应的 '}' 结束。
    日志中可能混入嵌套 JSON（如 {"id":"..."}），必须用括号配对。
    """
    cmd = [sys.executable, "-m", "core.workflow", *args, "--json"]
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = proc.stdout
    # 从第一个 '{' 开始，用括号配对找到对应的 '}'
    json_start = out.find("{")
    if json_start == -1:
        pytest.fail(f"workflow 未返回 JSON\nstdout={out[:500]}\nstderr={proc.stderr[:500]}")

    depth = 0
    in_str = False
    escape = False
    json_end = -1
    for i in range(json_start, len(out)):
        ch = out[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                json_end = i + 1
                break

    if json_end == -1:
        pytest.fail(f"未找到 JSON 结束括号\n片段: {out[json_start:json_start+500]}")

    try:
        return json.loads(out[json_start:json_end]), proc.returncode
    except json.JSONDecodeError as e:
        pytest.fail(f"JSON 解析失败: {e}\n片段: {out[json_start:json_end]}")


@pytest.mark.skipif(not TEST_IMAGE.exists(), reason="测试图片不存在")
class TestWorkflowJSONContract:
    """验证 workflow.py --json 输出的契约"""

    def test_returns_top_level_keys(self, tmp_path):
        """必须返回顶层 success 字段和 steps 字典"""
        result, rc = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        assert "success" in result, f"缺少 'success' 字段: {list(result.keys())}"
        assert isinstance(result.get("success"), bool)

    def test_steps_dict_present(self, tmp_path):
        """steps 必须存在（即使部分失败也应有结构）"""
        result, _ = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        assert "steps" in result, "缺少 'steps' 字段"
        assert isinstance(result["steps"], dict), f"steps 应为 dict，实际 {type(result['steps'])}"

    def test_rigging_step_has_model3_json(self, tmp_path):
        """rigging 步骤必须返回 model3_json 路径（Live2D 导出契约）"""
        result, _ = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        rig = result["steps"].get("rigging", {})
        assert "model3_json" in rig, f"rigging 缺少 model3_json: {list(rig.keys())}"

    def test_psd_step_has_psd_path(self, tmp_path):
        """psd 步骤必须返回 psd_path（PSD 导出契约）"""
        result, _ = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        psd = result["steps"].get("psd", {})
        assert "psd_path" in psd, f"psd 缺少 psd_path: {list(psd.keys())}"
        assert "success" in psd, "psd 步骤缺少 success 字段"

    def test_layers_dir_field(self, tmp_path):
        """顶层 layers_dir 必须返回（Go API 直接读取）"""
        result, _ = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        assert "layers_dir" in result, f"缺少 layers_dir: {list(result.keys())}"
        assert result["layers_dir"], "layers_dir 不应为空"

    def test_output_dir_field(self, tmp_path):
        """顶层 output_dir 必须返回"""
        result, _ = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        assert "output_dir" in result
        assert result["output_dir"] == str(tmp_path)

    def test_character_image_field(self, tmp_path):
        """character_image 字段应回显输入图片"""
        result, _ = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        assert "character_image" in result

    def test_layers_exist_on_disk(self, tmp_path):
        """layers_dir 指向的目录必须存在且包含层文件"""
        result, _ = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        layers_dir = Path(result["layers_dir"])
        assert layers_dir.exists(), f"layers_dir 不存在: {layers_dir}"
        # 至少要有一些 PNG 层文件
        pngs = list(layers_dir.glob("*.png"))
        assert len(pngs) >= 1, f"未生成任何 PNG 层: {list(layers_dir.iterdir())}"

    def test_model3_json_file_exists(self, tmp_path):
        """model3_json 指向的文件必须实际存在"""
        result, _ = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
            "--live2d-export",
        ])
        model3_path = result["steps"]["rigging"].get("model3_json")
        if model3_path:
            assert Path(model3_path).exists(), f"model3_json 不存在: {model3_path}"


@pytest.mark.skipif(not TEST_IMAGE.exists(), reason="测试图片不存在")
class TestWorkflowCLIFlags:
    """验证 CLI 参数传递契约"""

    def test_seed_flag_accepted(self, tmp_path):
        """--seed 参数必须被接受而不报错"""
        result, rc = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
            "--seed", "12345",
        ])
        assert rc == 0, f"退出码非零: {rc}"

    def test_negative_prompt_flag_accepted(self, tmp_path):
        """--negative-prompt 参数必须被接受"""
        result, rc = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
            "--negative-prompt", "low quality, blurry",
        ])
        assert rc == 0

    def test_json_flag_outputs_json(self, tmp_path):
        """--json 必须输出 JSON 而非人类可读日志"""
        result, rc = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        assert rc == 0
        # 必须能被解析为 dict
        assert isinstance(result, dict)

    def test_no_semantic_uses_kmeans(self, tmp_path):
        """--no-semantic 时应使用 KMeans 而非 semantic"""
        result, _ = _run_workflow_json([
            "--input", str(TEST_IMAGE),
            "--output", str(tmp_path),
            "--no-semantic",
        ])
        layering = result["steps"].get("layering", {})
        # 验证使用了 _kmeans 后缀目录
        layers_dir = result.get("layers_dir", "")
        if layering.get("method") == "kmeans":
            assert "kmeans" in layers_dir.lower(), \
                f"KMeans 模式但 layers_dir 不含 kmeans: {layers_dir}"


class TestWorkflowHelpOutput:
    """验证 --help 输出包含 v10.1 关键参数"""

    def test_help_shows_new_flags(self):
        proc = subprocess.run(
            [sys.executable, "-m", "core.workflow", "--help"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = proc.stdout
        for flag in ["--seed", "--negative-prompt", "--json", "--no-semantic"]:
            assert flag in out, f"help 缺少参数 {flag}"
