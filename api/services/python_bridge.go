package services

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"syscall"
	"time"

	"live2d-api/config"
	"live2d-api/models"
)

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

	cmd.Env = []string{
		"PYTHONIOENCODING=utf-8",
		"PYTHONPATH=" + pb.cfg.Python.ScriptsDir,
		"HOME=" + os.Getenv("HOME"),
		"PATH=" + os.Getenv("PATH"),
		"LANG=" + os.Getenv("LANG"),
		"LIVE2D_PROJECT_ROOT=" + pb.cfg.Python.ScriptsDir,
	}

	if runtime.GOOS != "windows" {
		cmd.SysProcAttr = &syscall.SysProcAttr{
			Setpgid: true,
		}
	}

	output, err := cmd.CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		if cmd.Process != nil {
			syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
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

// GenerateImageViaPython generates image via Python workflow (v10.1: delegates to WorkflowEngine via --json)
// Deprecated: Use ImageGenerator.GenerateWithCharacter() instead, which properly returns structured results.
func (pb *PythonBridge) GenerateImageViaPython(prompt string, width, height, seed int) (string, error) {
	// v10.1: Use core/workflow.py --json mode
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
// v10.1: Uses the same KMeans/semantic pipeline as workflow, returns PSD path
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
// v10.0: 角色管理（通过 Python CharacterManager）
// ======================================================================

// CreateCharacter 通过 Python 创建角色
func (pb *PythonBridge) CreateCharacter(name string, params map[string]interface{}) (map[string]interface{}, error) {
	// 使用内联 Python 脚本调用 CharacterManager
	pyCode := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from core.character.manager import CharacterManager
mgr = CharacterManager(storage_dir=%q)
card = mgr.create_character(name=%q, **%s)
print(json.dumps(card.to_dict(), ensure_ascii=False))
`, pb.cfg.Python.ScriptsDir, pb.cfg.Character.StorageDir, name, mustMarshal(params))

	return pb.runInlinePython(pyCode)
}

// ListCharacters 通过 Python 列出所有角色
func (pb *PythonBridge) ListCharacters() ([]map[string]interface{}, error) {
	pyCode := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from core.character.manager import CharacterManager
mgr = CharacterManager(storage_dir=%q)
print(json.dumps(mgr.list_characters(), ensure_ascii=False))
`, pb.cfg.Python.ScriptsDir, pb.cfg.Character.StorageDir)

	result, err := pb.runInlinePython(pyCode)
	if err != nil {
		return nil, err
	}
	var chars []map[string]interface{}
	if list, ok := result["result"]; ok {
		if jsonBytes, err := json.Marshal(list); err == nil {
			json.Unmarshal(jsonBytes, &chars)
		}
	}
	return chars, nil
}

// GetCharacter 获取角色详情
func (pb *PythonBridge) GetCharacter(characterID string) (map[string]interface{}, error) {
	pyCode := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from core.character.manager import CharacterManager
mgr = CharacterManager(storage_dir=%q)
card = mgr.load_character(%q)
print(json.dumps(card.to_dict(), ensure_ascii=False))
`, pb.cfg.Python.ScriptsDir, pb.cfg.Character.StorageDir, characterID)
	return pb.runInlinePython(pyCode)
}

// DeleteCharacter 删除角色
func (pb *PythonBridge) DeleteCharacter(characterID string) error {
	pyCode := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from core.character.manager import CharacterManager
mgr = CharacterManager(storage_dir=%q)
ok = mgr.delete_character(%q)
print(json.dumps({"deleted": ok}))
`, pb.cfg.Python.ScriptsDir, pb.cfg.Character.StorageDir, characterID)
	_, err := pb.runInlinePython(pyCode)
	return err
}

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
		layersDir = filepath.Join(pb.cfg.Output.BaseDir, "layers_"+characterID[:8])
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
from live2d_builder.exporter.model3_exporter import Model3Exporter

layers_dir = %q
out_dir = %q
Path(out_dir).mkdir(parents=True, exist_ok=True)

layers = OrderedDict()
for p in sorted(glob.glob(os.path.join(layers_dir, "*.png"))):
    name = os.path.splitext(os.path.basename(p))[0]
    layers[name] = Image.open(p).convert("RGBA")

# Model3Exporter.export expects a builder_result dict with a "layers" key.
builder_result = {"layers": layers, "meshes": {}}
exporter = Model3Exporter()
result = exporter.export(builder_result, output_dir=out_dir, character_name=%q)
# Package a downloadable zip alongside the model files.
try:
    result["zip"] = exporter.package_model(output_dir=out_dir, character_name=%q)
except Exception as _e:
    result["zip"] = ""
print(json.dumps(result, ensure_ascii=False, default=str))
`, pb.cfg.Python.ScriptsDir, layersDir, outputDir, characterID, characterID)
	return pb.runInlinePython(pyCode)
}

// GetExpressions 获取可用表情列表
func (pb *PythonBridge) GetExpressions(characterID string) ([]map[string]interface{}, error) {
	expressions := []map[string]interface{}{
		{"name": "默认", "id": "default", "params": []string{"ParamEyeLOpen", "ParamMouthForm"}},
		{"name": "微笑", "id": "smile", "params": []string{"ParamEyeLSmile", "ParamMouthForm"}},
		{"name": "生气", "id": "angry", "params": []string{"ParamBrowLY", "ParamMouthForm"}},
		{"name": "惊讶", "id": "surprised", "params": []string{"ParamEyeLOpen", "ParamMouthOpenY"}},
		{"name": "害羞", "id": "shy", "params": []string{"ParamBrowLAngle", "ParamMouthForm"}},
		{"name": "闭眼", "id": "closed_eyes", "params": []string{"ParamEyeLOpen", "ParamEyeROpen"}},
		{"name": "开心", "id": "happy", "params": []string{"ParamEyeLSmile", "ParamMouthForm"}},
		{"name": "难过", "id": "sad", "params": []string{"ParamBrowLAngle", "ParamMouthForm"}},
	}
	return expressions, nil
}

// runInlinePython 执行内联 Python 代码
func (pb *PythonBridge) runInlinePython(code string) (map[string]interface{}, error) {
	ctx, cancel := context.WithTimeout(context.Background(), pb.cfg.GetPythonTimeout())
	defer cancel()

	cmd := exec.CommandContext(ctx, pb.cfg.Python.PythonPath, "-c", code)
	cmd.Dir = pb.cfg.Python.ScriptsDir
	cmd.Env = append(os.Environ(),
		"PYTHONIOENCODING=utf-8",
		"PYTHONPATH="+pb.cfg.Python.ScriptsDir,
	)

	if runtime.GOOS != "windows" {
		cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	}

	output, err := cmd.CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		if cmd.Process != nil {
			syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		}
		return nil, fmt.Errorf("Python执行超时")
	}
	if err != nil {
		return nil, fmt.Errorf("Python执行失败: %v\n%s", err, sanitizeOutput(string(output)))
	}

	// 解析最后一行 JSON
	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		var result map[string]interface{}
		if err := json.Unmarshal([]byte(line), &result); err == nil {
			return map[string]interface{}{"result": result}, nil
		}
	}

	// 返回原始输出
	return map[string]interface{}{
		"raw_output": string(output),
	}, nil
}

// mustMarshal 将值序列化为 JSON 字符串，用于嵌入 Python 代码
func mustMarshal(v interface{}) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "{}"
	}
	return string(b)
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

// GetPythonScripts lists available Python scripts (v10.1)
func (pb *PythonBridge) GetPythonScripts() []map[string]string {
	scripts := []map[string]string{}
	scriptFiles := []struct {
		Name string
		Desc string
	}{
		{"core/workflow.py", "完整工作流引擎 v10.1（图像生成→QA→分割→绑定→PSD→Live2D导出）"},
		{"core/cli.py", "交互式命令行工具 v10.1"},
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
