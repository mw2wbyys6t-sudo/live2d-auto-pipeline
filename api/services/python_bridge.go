package services

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"live2d-api/config"
	"live2d-api/models"
)

// ErrPythonTimeout 标记「子进程被预算掐掉」，与「Python 真的报错」区分开：
// 前者该回 504 并说明预算，后者该回 500/422。
var ErrPythonTimeout = errors.New("python 子进程超时")

type PythonBridge struct {
	cfg *config.Config
}

func NewPythonBridge(cfg *config.Config) *PythonBridge {
	return &PythonBridge{cfg: cfg}
}

// validatePath validates path safety, preventing command injection and path traversal
func validatePath(path string) error {
	if path == "" {
		return fmt.Errorf("路径不能为空")
	}
	if matched, _ := regexp.MatchString(`[;&|*$\x00]`, path); matched {
		return fmt.Errorf("路径包含非法字符")
	}
	// 拒绝目录穿越：任一 ".." 路径段都不允许。此前只靠后续 os.Stat 的存在性兜底，
	// 已存在的越界文件仍可被读取。两种分隔符都切分，避免跨平台差异。
	for _, seg := range strings.FieldsFunc(path, func(r rune) bool {
		return r == '/' || r == '\\'
	}) {
		if seg == ".." {
			return fmt.Errorf("路径不允许包含 '..' 目录穿越")
		}
	}
	if strings.HasPrefix(filepath.Base(path), "-") {
		return fmt.Errorf("文件名不能以 - 开头")
	}
	return nil
}

// executePythonScript safely executes a Python script with configurable timeout
func (pb *PythonBridge) executePythonScript(scriptPath string, args []string, timeout time.Duration) ([]byte, error) {
	if err := validatePath(scriptPath); err != nil {
		return nil, fmt.Errorf("脚本路径验证失败: %v", err)
	}
	if _, err := os.Stat(scriptPath); os.IsNotExist(err) {
		return nil, fmt.Errorf("脚本不存在: %s", scriptPath)
	}

	fullArgs := append([]string{scriptPath}, args...)

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, pb.cfg.Python.PythonPath, fullArgs...)
	cmd.Dir = pb.cfg.Python.ScriptsDir

	cmd.Env = append(os.Environ(),
		"PYTHONIOENCODING=utf-8",
		"PYTHONPATH=" + pb.cfg.Python.ScriptsDir,
		"HOME=" + os.Getenv("HOME"),
		"PATH=" + os.Getenv("PATH"),
		"LANG=" + os.Getenv("LANG"),
		"LIVE2D_PROJECT_ROOT=" + pb.cfg.Python.ScriptsDir,
	)

	configurePythonProcess(cmd)

	output, err := cmd.CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		if cmd.Process != nil {
			killPythonProcess(cmd)
		}
		return nil, fmt.Errorf("脚本执行超时（限制%d秒）", int(timeout.Seconds()))
	}
	if err != nil {
		sanitizedOutput := sanitizeOutput(string(output))
		return nil, fmt.Errorf("脚本执行失败: %v\n输出: %s", err, sanitizedOutput)
	}

	return output, nil
}

// sanitizeOutput redacts sensitive information from output
func sanitizeOutput(output string) string {
	patterns := []string{
		`sk-[a-zA-Z0-9]{20,}`,
		`api[_-]?key["\s]*[:=]["\s]*[^\s"]+`,
		`secret["\s]*[:=]["\s]*[^\s"]+`,
		`password["\s]*[:=]["\s]*[^\s"]+`,
		`token["\s]*[:=]["\s]*[^\s"]+`,
	}
	result := output
	for _, pattern := range patterns {
		re := regexp.MustCompile(pattern)
		result = re.ReplaceAllString(result, "[REDACTED]")
	}
	return result
}

// GenerateImageViaPython generates image via Python workflow (v0.10.1: delegates to WorkflowEngine via --json)
// Deprecated: Use ImageGenerator.GenerateWithCharacter() instead, which properly returns structured results.
func (pb *PythonBridge) GenerateImageViaPython(prompt string, width, height, seed int) (string, error) {
	// v0.10.1: Use core/workflow.py --json mode
	scriptPath := filepath.Join(pb.cfg.Python.ScriptsDir, "core", "workflow.py")
	if _, err := os.Stat(scriptPath); os.IsNotExist(err) {
		return "", fmt.Errorf("工作流脚本不存在: %s", scriptPath)
	}

	outputDir := filepath.Join(pb.cfg.Python.ScriptsDir, "output")
	os.MkdirAll(outputDir, 0755)

	args := []string{
		"--json",
		"--output", outputDir,
		"--width", fmt.Sprintf("%d", width),
		"--height", fmt.Sprintf("%d", height),
		"--seed", fmt.Sprintf("%d", seed),
		"--no-semantic",
	}
	if prompt != "" {
		if strings.HasPrefix(prompt, "-") {
			prompt = " " + prompt
		}
		args = append(args, prompt)
	}

	timeout := pb.cfg.GetPythonTimeout() * 3
	output, err := pb.executePythonScript(scriptPath, args, timeout)
	if err != nil {
		return "", err
	}

	outputStr := string(output)
	jsonStart := strings.LastIndex(outputStr, "{")
	if jsonStart == -1 {
		return "", fmt.Errorf("工作流未返回有效结果")
	}
	var result struct {
		Success        bool                   `json:"success"`
		CharacterImage string                 `json:"character_image"`
		Error          string                 `json:"error,omitempty"`
		Steps          map[string]interface{} `json:"steps,omitempty"`
	}
	if err := json.Unmarshal([]byte(outputStr[jsonStart:]), &result); err != nil {
		return "", fmt.Errorf("解析结果失败: %v", err)
	}
	if !result.Success {
		return "", fmt.Errorf("生成失败: %s", result.Error)
	}
	imagePath := result.CharacterImage
	if imagePath == "" {
		if genStep, ok := result.Steps["generate"].(map[string]interface{}); ok {
			if p, ok := genStep["path"].(string); ok {
				imagePath = p
			}
		}
	}
	if !filepath.IsAbs(imagePath) {
		imagePath = filepath.Join(pb.cfg.Python.ScriptsDir, imagePath)
	}
	return imagePath, nil
}

// CreatePSDPlan creates PSD layer plan using the core segment engine
// v0.10.1: Uses the same KMeans/semantic pipeline as workflow, returns PSD path
func (pb *PythonBridge) CreatePSDPlan(imagePath string) (*models.PSDLayerResponse, error) {
	if err := validatePath(imagePath); err != nil {
		return nil, fmt.Errorf("路径验证失败: %v", err)
	}
	if _, err := os.Stat(imagePath); os.IsNotExist(err) {
		return nil, fmt.Errorf("图片不存在: %s", imagePath)
	}

	outputDir := filepath.Join(pb.cfg.Output.BaseDir, fmt.Sprintf("psd_plan_%d", time.Now().Unix()))
	os.MkdirAll(outputDir, 0755)

	// Run KMeans layerer + PSD creator inline via Python
	pyCode := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from PIL import Image
from pathlib import Path
from core.segment_engine.kmeans import KMeansLayerer
from core.psd.creator import PSDCreator

img_path = %q
out_dir = %q
img = Image.open(img_path).convert("RGBA")

layerer = KMeansLayerer(k_clusters=12)
layer_result = layerer.layer(img, output_dir=out_dir)

psd_path = str(Path(out_dir) / "character.psd")
psd_creator = PSDCreator()
psd_result = psd_creator.create_psd(out_dir, psd_path)

print(json.dumps({
    "output_dir": out_dir,
    "layer_count": layer_result["layer_count"],
    "psd_path": psd_result.get("psd_path", psd_path),
    "layers": [l["name"] for l in layer_result["layers"]],
}, ensure_ascii=False))
`, pb.cfg.Python.ScriptsDir, imagePath, outputDir)

	result, err := pb.runInlinePython(pyCode)
	if err != nil {
		return nil, err
	}

	// Extract data from result
	var planDir, psdPath string
	var layerCount int
	var layers []string
	if resData, ok := result["result"].(map[string]interface{}); ok {
		if v, ok := resData["output_dir"].(string); ok {
			planDir = v
		}
		if v, ok := resData["psd_path"].(string); ok {
			psdPath = v
		}
		if v, ok := resData["layer_count"].(float64); ok {
			layerCount = int(v)
		}
		if layerList, ok := resData["layers"].([]interface{}); ok {
			for _, l := range layerList {
				if s, ok := l.(string); ok {
					layers = append(layers, s)
				}
			}
		}
	}

	return &models.PSDLayerResponse{
		PlanDir:    planDir,
		PSDPath:    psdPath,
		LayerCount: layerCount,
		Layers:     layers,
		CreatedAt:  time.Now(),
	}, nil
}

// RunSeeThroughWorkflow runs See-through workflow
func (pb *PythonBridge) RunSeeThroughWorkflow(imagePath string) (*models.SeeThroughResponse, error) {
	comfyuiDir := pb.cfg.ComfyUI.BaseDir
	if _, err := os.Stat(comfyuiDir); os.IsNotExist(err) {
		return &models.SeeThroughResponse{
			Status:  "error",
			Message: "ComfyUI 未安装，请先运行 python install_comfyui_advanced.py",
		}, nil
	}
	seeThroughDir := filepath.Join(comfyuiDir, "custom_nodes", "ComfyUI-See-through")
	if _, err := os.Stat(seeThroughDir); os.IsNotExist(err) {
		return &models.SeeThroughResponse{
			Status:  "error",
			Message: "See-through 未安装，请先运行 python install_comfyui_advanced.py",
		}, nil
	}
	return &models.SeeThroughResponse{
		TaskID:    fmt.Sprintf("st_%d", time.Now().Unix()),
		Status:    "pending",
		Message:   "请启动 ComfyUI 并加载 See-through 工作流",
		CreatedAt: time.Now(),
	}, nil
}

// ======================================================================
// v0.10.0: 角色管理（通过 Python CharacterManager）
// ======================================================================

// AddReferenceImage 添加参考图并提取 embedding
func (pb *PythonBridge) AddReferenceImage(characterID, imagePath, view string) error {
	if view == "" {
		view = "front"
	}
	pyCode := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from core.character.manager import CharacterManager
mgr = CharacterManager(storage_dir=%q)
path = mgr.add_reference_image(%q, %q, %q)
print(json.dumps({"ref_path": path}))
`, pb.cfg.Python.ScriptsDir, pb.cfg.Character.StorageDir, characterID, imagePath, view)
	_, err := pb.runInlinePython(pyCode)
	return err
}

// ExportLive2DModel 导出 Live2D 模型
func (pb *PythonBridge) ExportLive2DModel(characterID, layersDir, outputDir string) (map[string]interface{}, error) {
	if layersDir == "" {
		layersDir = filepath.Join(pb.cfg.Output.BaseDir, "layers_"+characterID[:min(8, len(characterID))])
	}
	if outputDir == "" {
		outputDir = filepath.Join(pb.cfg.Output.BaseDir, "live2d_exports", characterID)
	}
	if _, err := os.Stat(layersDir); os.IsNotExist(err) {
		return nil, fmt.Errorf("图层目录不存在: %s", layersDir)
	}
	pyCode := fmt.Sprintf(`
import sys, json, os, glob
sys.path.insert(0, %q)
from pathlib import Path
from PIL import Image
from collections import OrderedDict
from live2d_builder.pipeline import Live2DBuilder

layers_dir = %q
out_dir = %q
Path(out_dir).mkdir(parents=True, exist_ok=True)

layers = OrderedDict()
for p in sorted(glob.glob(os.path.join(layers_dir, "*.png"))):
    name = os.path.splitext(os.path.basename(p))[0]
    layers[name] = Image.open(p).convert("RGBA")

if not layers:
    print(json.dumps({"success": False, "message":
                      "图层目录内没有可用 PNG: " + layers_dir}, ensure_ascii=False))
    raise SystemExit(0)

# 必须走完整构建：只有它生成网格、编译 .moc3 并用官方 Cubism Core 验收。
# 早期版本直接调用 Model3Exporter.export(meshes={})，产物里根本没有 moc3。
builder = Live2DBuilder(output_dir=out_dir, character_name=%q)
result = builder.build(layers)

# 只回传路径与状态；网格 / 骨骼等中间数据可达数 MB，不进 API 响应。
keys = ("output_dir", "model3_json", "moc3_ref", "textures", "texture_files",
        "physics", "expressions", "mesh_data", "guide", "validation",
        "compatibility", "mesh_guide", "build_meta", "elapsed_seconds", "moc3")
summary = {k: result.get(k) for k in keys if k in result}
summary["success"] = True
summary["mesh_count"] = len(result.get("meshes") or {})
print(json.dumps(summary, ensure_ascii=False, default=str))
`, pb.cfg.Python.ScriptsDir, layersDir, outputDir, characterID)
	// 完整导出要走网格生成 + 图集烘焙 + 官方内核验收，用独立预算
	// （实测规模见 tools/measure_export_duration.py 与 config.GetExportTimeout）。
	return pb.runInlinePythonTimeout(pyCode, pb.cfg.GetExportTimeout())
}

// runInlinePython 执行内联 Python 代码
func (pb *PythonBridge) runInlinePython(code string) (map[string]interface{}, error) {
	return pb.runInlinePythonTimeout(code, pb.cfg.GetPythonTimeout())
}

// runInlinePythonTimeout 用给定预算执行内联 Python。
// 超时返回包装了 ErrPythonTimeout 的错误，调用方据此区分「慢」与「坏」。
func (pb *PythonBridge) runInlinePythonTimeout(code string,
	timeout time.Duration) (map[string]interface{}, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, pb.cfg.Python.PythonPath, "-c", code)
	cmd.Dir = pb.cfg.Python.ScriptsDir
	cmd.Env = append(os.Environ(),
		"PYTHONIOENCODING=utf-8",
		"PYTHONPATH="+pb.cfg.Python.ScriptsDir,
	)

	configurePythonProcess(cmd)

	output, err := cmd.CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		if cmd.Process != nil {
			killPythonProcess(cmd)
		}
		return nil, fmt.Errorf("%w（%s）: Python执行超时",
			ErrPythonTimeout, timeout)
	}
	if err != nil {
		return nil, fmt.Errorf("Python执行失败: %v\n%s", err, sanitizeOutput(string(output)))
	}

	// Accept both object and array results; missing JSON is an error.
	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		var result interface{}
		if json.Unmarshal([]byte(strings.TrimSpace(lines[i])), &result) == nil && result != nil {
			return map[string]interface{}{"result": result}, nil
		}
	}
	return nil, fmt.Errorf("Python did not return a valid JSON result")
}

// CheckPythonEnvironment checks Python environment availability
func (pb *PythonBridge) CheckPythonEnvironment() (bool, []string) {
	var issues []string
	cmd := exec.Command(pb.cfg.Python.PythonPath, "--version")
	if _, err := cmd.CombinedOutput(); err != nil {
		issues = append(issues, fmt.Sprintf("Python 不可用: %v", err))
		return false, issues
	}
	deps := []string{"PIL", "numpy"}
	for _, dep := range deps {
		cmd := exec.Command(pb.cfg.Python.PythonPath, "-c", fmt.Sprintf("import %s", dep))
		if err := cmd.Run(); err != nil {
			issues = append(issues, fmt.Sprintf("缺少依赖: %s", dep))
		}
	}
	return len(issues) == 0, issues
}

// CheckSeeThroughInstalled checks if See-through is installed
func (pb *PythonBridge) CheckSeeThroughInstalled() bool {
	seeThroughDir := filepath.Join(pb.cfg.ComfyUI.BaseDir, "custom_nodes", "ComfyUI-See-through")
	_, err := os.Stat(seeThroughDir)
	return err == nil
}

// GetPythonScripts lists available Python scripts (v0.10.1)
func (pb *PythonBridge) GetPythonScripts() []map[string]string {
	scripts := []map[string]string{}
	scriptFiles := []struct {
		Name string
		Desc string
	}{
		{"core/workflow.py", "完整工作流引擎 v0.10.1（图像生成→QA→分割→绑定→PSD→Live2D导出）"},
		{"core/cli.py", "交互式命令行工具 v0.10.1"},
		{"install.py", "项目安装脚本（依赖+模型）"},
		{"install.sh", "Linux/macOS 一键安装脚本"},
	}
	for _, sf := range scriptFiles {
		path := filepath.Join(pb.cfg.Python.ScriptsDir, sf.Name)
		available := "true"
		if _, err := os.Stat(path); os.IsNotExist(err) {
			available = "false"
		}
		scripts = append(scripts, map[string]string{
			"name":        sf.Name,
			"description": sf.Desc,
			"path":        path,
			"available":   available,
		})
	}
	return scripts
}
