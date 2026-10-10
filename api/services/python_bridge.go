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

// strictIDPattern 桥接函数内部使用的角色 ID 白名单：这些 ID 会被拼进
// 内联 Python 源码与文件路径，不能依赖调用方校验（纵深防御）。
var strictIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

func validateStrictID(id string) error {
	if !strictIDPattern.MatchString(id) {
		return fmt.Errorf("非法角色 ID: %.64q", id)
	}
	return nil
}

type PythonBridge struct {
	cfg *config.Config
}

func NewPythonBridge(cfg *config.Config) *PythonBridge {
	return &PythonBridge{cfg: cfg}
}

// validatePath 校验路径安全性，拦截命令注入与目录穿越。
// Go→Python 桥接的纵深防御层：即便上游放过，这里也要拦住 shell 元字符、
// 控制字符、URL 编码前导符与 Unicode 规范化攻击向量。
func validatePath(path string) error {
	if path == "" {
		return fmt.Errorf("路径不能为空")
	}
	// 防御 Unicode 规范化攻击：全角点 ＂．＂ 在视觉上等同 ".."，但不会命中
	// 「段 == ..」检查。先把全角分隔符规范化为半角，再做后续校验。
	// 这里只覆盖三种分隔符，用标准库 strings.Map 即可，无需引入 x/text。
	path = strings.Map(func(r rune) rune {
		switch r {
		case '\uFF0E': // 全角句号 → 半角点
			return '.'
		case '\uFF0F': // 全角斜杠 → 半角斜杠
			return '/'
		case '\uFF3C': // 全角反斜杠 → 半角反斜杠
			return '\\'
		}
		return r
	}, path)
	// 字符黑名单：shell 元字符 + 控制字符 + URL 编码前导符。
	//   - `;` `&` `|` `*` `$`：原有 shell 元字符（命令拼接/通配/变量展开）
	//   - 反引号：命令替换 `...`
	//   - 换行(LF)/回车(CR)：日志伪造、HTTP 头注入、配置文件污染
	//   - %：%00 截断攻击、双重 URL 编码绕过
	//   - NUL：C 字符串截断、参数注入
	for _, c := range path {
		switch c {
		case ';', '&', '|', '*', '$', '`', '%', '\x00', '\x0a', '\x0d':
			return fmt.Errorf("路径包含非法字符")
		}
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
		"PYTHONPATH="+pb.cfg.Python.ScriptsDir,
		"HOME="+os.Getenv("HOME"),
		"PATH="+os.Getenv("PATH"),
		"LANG="+os.Getenv("LANG"),
		"LIVE2D_PROJECT_ROOT="+pb.cfg.Python.ScriptsDir,
	)

	// 便携版（桌面安装包）：把 HF_HOME / HUGGINGFACE_HUB_CACHE 指向打包的
	// 权重目录，让 transformers / huggingface_hub 从 {app}\runtime\hf-cache
	// 读权重，而不是用户目录下的默认缓存。开发模式（HfCache 为空）不注入，
	// 完全沿用系统默认行为。
	if hf := pb.cfg.Python.HfCache; hf != "" {
		cmd.Env = append(cmd.Env,
			"HF_HOME="+hf,
			"HUGGINGFACE_HUB_CACHE="+hf,
			"TRANSFORMERS_CACHE="+filepath.Join(hf, "hub"),
		)
	}

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
// 桥接任务执行：常量脚本 + JSON 参数文件（argv 传参，无代码拼接）
// ======================================================================

// runBridgeTask 把「常量 Python 任务脚本 + JSON 参数文件」交给
// executePythonScript 以 argv 参数列表执行。
//
// 安全设计：任务脚本是**不含任何外部输入的常量**，参数经 JSON 文件
// 从 sys.argv[1] 读取 —— 源码字符串零拼接，从根源上消除注入面。
// 返回 stdout 最后一个可解析的 JSON 对象（与 runInlinePython 同契约）。
func (pb *PythonBridge) runBridgeTask(script string,
	params map[string]interface{}, timeout time.Duration) (map[string]interface{}, error) {
	tmpDir, err := os.MkdirTemp("", "live2d_bridge_")
	if err != nil {
		return nil, fmt.Errorf("创建桥接临时目录失败: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	paramsPath := filepath.Join(tmpDir, "params.json")
	b, err := json.Marshal(params)
	if err != nil {
		return nil, fmt.Errorf("序列化桥接参数失败: %v", err)
	}
	if err := os.WriteFile(paramsPath, b, 0o600); err != nil {
		return nil, fmt.Errorf("写入桥接参数失败: %v", err)
	}
	scriptPath := filepath.Join(tmpDir, "task.py")
	if err := os.WriteFile(scriptPath, []byte(script), 0o600); err != nil {
		return nil, fmt.Errorf("写入桥接脚本失败: %v", err)
	}

	output, err := pb.executePythonScript(scriptPath, []string{paramsPath}, timeout)
	if err != nil {
		return nil, err
	}
	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		var result interface{}
		if json.Unmarshal([]byte(strings.TrimSpace(lines[i])), &result) == nil && result != nil {
			return map[string]interface{}{"result": result}, nil
		}
	}
	return nil, fmt.Errorf("桥接任务未返回 JSON 结果")
}

// ======================================================================
// v0.10.0: 角色管理（通过 Python CharacterManager）
// ======================================================================

// bridgeAddReferenceScript 常量任务脚本：登记参考图并提取 embedding。
// 参数从 argv[1] 指向的 JSON 文件读取，脚本本身不含任何外部输入。
const bridgeAddReferenceScript = `
import sys, json
from core.character.manager import CharacterManager
params = json.loads(open(sys.argv[1], encoding="utf-8").read())
mgr = CharacterManager(storage_dir=params["storage_dir"])
path = mgr.add_reference_image(params["character_id"], params["image_path"], params["view"])
print(json.dumps({"ref_path": path}))
`

// AddReferenceImage 添加参考图并提取 embedding
func (pb *PythonBridge) AddReferenceImage(characterID, imagePath, view string) error {
	if err := validateStrictID(characterID); err != nil {
		return err
	}
	if view == "" {
		view = "front"
	}
	_, err := pb.runBridgeTask(bridgeAddReferenceScript, map[string]interface{}{
		"storage_dir":  pb.cfg.Character.StorageDir,
		"character_id": characterID,
		"image_path":   imagePath,
		"view":         view,
	}, pb.cfg.GetPythonTimeout())
	return err
}

// SegmentImage 对单张立绘运行真实语义/聚类分层并导出 PSD。
// 与 Python 后端 api_server.py 的 _run_segment 完全同构，供「分层工作台」
// 在 Go 后端部署下使用。语义分割在 CPU 上可能耗时数分钟，预算给足。
// bridgeSegmentScript 常量任务脚本：对单张立绘运行真实分层并导出 PSD。
const bridgeSegmentScript = `
import sys, json
from pathlib import Path
from PIL import Image

params = json.loads(open(sys.argv[1], encoding="utf-8").read())
img = Image.open(params["image_path"]).convert("RGBA")
out_dir = Path(params["out_dir"])
out_dir.mkdir(parents=True, exist_ok=True)
method = params["method"]
if method == "kmeans":
    from core.segment_engine.kmeans import KMeansLayerer
    result = KMeansLayerer().layer(img, output_dir=str(out_dir))
else:
    from core.segment_engine.semantic import SemanticSegmenter
    result = SemanticSegmenter(model_type="auto").layer(img, output_dir=str(out_dir))
names = [l.get("name") for l in (result.get("layers") or []) if l.get("name")]
from core.psd.creator import PSDCreator
psd_result = PSDCreator().create_psd(str(out_dir), str(out_dir / "character.psd"), ordered_names=names or None)
layers = [{"name": l.get("name", ""), "part_name": l.get("part_name", l.get("name", "")), "path": l.get("path", ""), "pixel_count": l.get("pixel_count", 0)} for l in (result.get("layers") or [])]
print(json.dumps({"method": result.get("method", method), "layers_dir": str(out_dir), "layers": layers, "composite_preview": result.get("composite_preview", "") or "", "psd_path": psd_result.get("psd_path", "") or "", "psd_success": bool(psd_result.get("success"))}))
`

func (pb *PythonBridge) SegmentImage(imagePath, method string) (map[string]interface{}, error) {
	if err := validatePath(imagePath); err != nil {
		return nil, err
	}
	if _, err := os.Stat(imagePath); err != nil {
		return nil, fmt.Errorf("源图片不存在: %s", imagePath)
	}
	if method != "kmeans" {
		method = "semantic"
	}
	outDir := filepath.Join(pb.cfg.Output.BaseDir, fmt.Sprintf("layers_%d", time.Now().Unix()))
	// 语义分割在 CPU 上可能耗时数分钟，预算给足
	res, err := pb.runBridgeTask(bridgeSegmentScript, map[string]interface{}{
		"image_path": imagePath,
		"out_dir":    outDir,
		"method":     method,
	}, 15*time.Minute)
	if err != nil {
		return nil, err
	}
	inner, ok := res["result"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("分层脚本返回了意外的结果结构")
	}
	return inner, nil
}

// bridgeRecomputeEmbeddingScript 常量任务脚本：重算角色视觉 embedding。
const bridgeRecomputeEmbeddingScript = `
import sys, json
from core.character.manager import CharacterManager

params = json.loads(open(sys.argv[1], encoding="utf-8").read())
mgr = CharacterManager(storage_dir=params["storage_dir"])
card = mgr.load_character(params["character_id"])
img_path = None
for attr in ("front_view_path", "side_view_path", "back_view_path"):
    p = getattr(card, attr, None)
    if p:
        img_path = p
        break
if not img_path:
    print(json.dumps({"has_embedding": False, "dim": 0, "reason": "角色没有任何参考图，无法提取视觉 embedding"}))
else:
    from PIL import Image
    emb = mgr.extract_embedding(Image.open(img_path).convert("RGB"))
    card.visual_embedding = emb
    mgr.save_character(card)
    print(json.dumps({"has_embedding": True, "dim": len(emb)}))
`

// RecomputeCharacterEmbedding 为角色重新提取视觉 embedding（CLIP 可用时用
// CLIP，否则退化为直方图 embedding —— 由 core/character/embedding.py 决定）。
// 返回 {has_embedding, dim, reason?}，让前端能如实展示降级情况。
func (pb *PythonBridge) RecomputeCharacterEmbedding(characterID string) (map[string]interface{}, error) {
	if err := validateStrictID(characterID); err != nil {
		return nil, err
	}
	res, err := pb.runBridgeTask(bridgeRecomputeEmbeddingScript, map[string]interface{}{
		"storage_dir":  pb.cfg.Character.StorageDir,
		"character_id": characterID,
	}, 10*time.Minute)
	if err != nil {
		return nil, err
	}
	inner, ok := res["result"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("embedding 脚本返回了意外的结果结构")
	}
	return inner, nil
}

// bridgeListProvidersScript 常量任务脚本：查询上游生成服务状态。
const bridgeListProvidersScript = `
import sys, json
from core.image_gen.router import ProviderRouter
router = ProviderRouter()
info = router.get_provider_info()
print(json.dumps({
    "available": router.get_available_providers(),
    "registered": [i.get("name") for i in info],
    "detail": info,
}))
`

// ListImageProviders 查询 Python ProviderRouter 的上游生成服务状态：
// 哪些已配置可用（含新接入的 openai 兼容端点）、哪些已注册但缺 Key。
func (pb *PythonBridge) ListImageProviders() ([]map[string]interface{}, []string, error) {
	res, err := pb.runBridgeTask(bridgeListProvidersScript, nil, 60*time.Second)
	if err != nil {
		return nil, nil, err
	}
	inner, ok := res["result"].(map[string]interface{})
	if !ok {
		return nil, nil, fmt.Errorf("provider 查询返回了意外的结果结构")
	}
	available, _ := inner["available"].([]interface{})
	registered, _ := inner["registered"].([]interface{})
	registeredNames := make([]string, 0, len(registered))
	for _, r := range registered {
		if s, ok := r.(string); ok {
			registeredNames = append(registeredNames, s)
		}
	}
	outAvailable := make([]map[string]interface{}, 0, len(available))
	for _, a := range available {
		if m, ok := a.(map[string]interface{}); ok {
			outAvailable = append(outAvailable, m)
		}
	}
	return outAvailable, registeredNames, nil
}

// ImportPSDLayers 用 psd-tools 把外部 PSD 的每个像素图层按原始坐标
// 合成为整幅画布 RGBA PNG（底层在前、前景在后），供分层工作台直接
// 预览与后续 Live2D 导出使用。
func (pb *PythonBridge) ImportPSDLayers(psdPath string) (map[string]interface{}, error) {
	if err := validatePath(psdPath); err != nil {
		return nil, err
	}
	if _, err := os.Stat(psdPath); err != nil {
		return nil, fmt.Errorf("PSD 文件不存在: %s", psdPath)
	}
	outDir := filepath.Join(pb.cfg.Output.BaseDir, fmt.Sprintf("psd_import_%d", time.Now().Unix()))
	// psd-tools 逐层导出在超大 PSD 上可能较慢，预算给足
	res, err := pb.runBridgeTask(bridgeImportPSDScript, map[string]interface{}{
		"psd_path": psdPath,
		"out_dir":  outDir,
	}, 10*time.Minute)
	if err != nil {
		return nil, err
	}
	inner, ok := res["result"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("PSD 导入脚本返回了意外的结果结构")
	}
	if okFlag, _ := inner["ok"].(bool); !okFlag {
		msg, _ := inner["error"].(string)
		return nil, fmt.Errorf("PSD 导入失败: %s", msg)
	}
	return inner, nil
}

// bridgeImportPSDScript 常量任务脚本：外部 PSD 逐层导出为整幅画布 PNG，
// 并按图层名识别语义部件（借鉴 psd2live 的思路）。
const bridgeImportPSDScript = `
import sys, json, re
from pathlib import Path
from psd_tools import PSDImage
from core.psd.part_naming import recognize_part

params = json.loads(open(sys.argv[1], encoding="utf-8").read())
psd_path = params["psd_path"]
out_dir = Path(params["out_dir"])
out_dir.mkdir(parents=True, exist_ok=True)
psd = PSDImage.open(psd_path)
if psd.width < 64 or psd.height < 64 or psd.width > 8192 or psd.height > 8192:
    print(json.dumps({"ok": False, "error": "画布尺寸异常: %dx%d，需在 64~8192 之间" % (psd.width, psd.height)}))
    raise SystemExit

leaves = []
def walk(group):
    for child in group:
        if child.is_group():
            walk(child)
        else:
            leaves.append(child)
walk(psd)

layers = []
for idx, child in enumerate(reversed(leaves)):  # 底层在前，前景最后绘制
    if idx >= 500:
        break
    try:
        comp = child.composite(viewport=psd.viewbox)
    except Exception:
        continue
    if comp is None:
        continue
    comp = comp.convert("RGBA")
    if not comp.getbbox():
        continue
    raw_name = child.name or "layer"
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", raw_name)[:40] or "layer"
    out = out_dir / ("%03d_%s.png" % (len(layers), safe))
    comp.save(out)
    alpha_hist = comp.getchannel("A").histogram()
    opaque = sum(alpha_hist[1:])
    # 中/英/日图层名 -> 语义部件 + 左右侧向，为下游参数绑定提供稳定目标
    named = recognize_part(raw_name)
    layers.append({
        "name": raw_name,
        "part_name": named.get("part") or safe,
        "part": named.get("part"),
        "side": named.get("side"),
        "mouth_vowel": named.get("mouth_vowel"),
        "path": str(out),
        "pixel_count": opaque,
        "group": getattr(child.parent, "name", "") or "",
    })

print(json.dumps({
    "ok": True,
    "canvas": [psd.width, psd.height],
    "layers_dir": str(out_dir),
    "layer_count": len(layers),
    "layers": layers,
    "total_found": len(leaves),
    "recognized_count": sum(1 for l in layers if l["part"]),
}))
`

// ExportLive2DModel 导出 Live2D 模型
func (pb *PythonBridge) ExportLive2DModel(characterID, layersDir, outputDir string) (map[string]interface{}, error) {
	if err := validateStrictID(characterID); err != nil {
		return nil, err
	}
	if layersDir == "" {
		layersDir = filepath.Join(pb.cfg.Output.BaseDir, "layers_"+characterID[:min(8, len(characterID))])
	}
	if outputDir == "" {
		outputDir = filepath.Join(pb.cfg.Output.BaseDir, "live2d_exports", characterID)
	}
	if _, err := os.Stat(layersDir); os.IsNotExist(err) {
		return nil, fmt.Errorf("图层目录不存在: %s", layersDir)
	}
	// 完整导出要走网格生成 + 图集烘焙 + 官方内核验收，用独立预算
	// （实测规模见 tools/measure_export_duration.py 与 config.GetExportTimeout）。
	return pb.runBridgeTask(bridgeExportLive2DScript, map[string]interface{}{
		"layers_dir":   layersDir,
		"out_dir":      outputDir,
		"character_id": characterID,
	}, pb.cfg.GetExportTimeout())
}

// bridgeExportLive2DScript 常量任务脚本：完整 Live2D 构建（网格 + moc3 + 官方内核验收）。
const bridgeExportLive2DScript = `
import sys, json, os, glob
from pathlib import Path
from PIL import Image
from collections import OrderedDict
from live2d_builder.pipeline import Live2DBuilder

params = json.loads(open(sys.argv[1], encoding="utf-8").read())
layers_dir = params["layers_dir"]
out_dir = params["out_dir"]
character_id = params["character_id"]
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
builder = Live2DBuilder(output_dir=out_dir, character_name=character_id)
result = builder.build(layers)

# 只回传路径与状态；网格 / 骨骼等中间数据可达数 MB，不进 API 响应。
keys = ("output_dir", "model3_json", "moc3_ref", "textures", "texture_files",
        "physics", "expressions", "mesh_data", "guide", "validation",
        "compatibility", "mesh_guide", "build_meta", "elapsed_seconds", "moc3")
summary = {k: result.get(k) for k in keys if k in result}
summary["success"] = True
summary["mesh_count"] = len(result.get("meshes") or {})
print(json.dumps(summary, ensure_ascii=False, default=str))
`

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
	// 环境探测必须有超时上限：损坏/挂起的解释器（如等待输入、损坏的 venv）
	// 不能让健康检查永久阻塞。--version 很快；import 检查给 30s 余量。
	versionCtx, cancelVersion := context.WithTimeout(context.Background(), 15*time.Second)
	cmd := exec.CommandContext(versionCtx, pb.cfg.Python.PythonPath, "--version")
	if _, err := cmd.CombinedOutput(); err != nil {
		cancelVersion()
		issues = append(issues, fmt.Sprintf("Python 不可用: %v", err))
		return false, issues
	}
	cancelVersion()
	deps := []string{"PIL", "numpy"}
	for _, dep := range deps {
		importCtx, cancelImport := context.WithTimeout(context.Background(), 30*time.Second)
		cmd := exec.CommandContext(importCtx, pb.cfg.Python.PythonPath, "-c", fmt.Sprintf("import %s", dep))
		if err := cmd.Run(); err != nil {
			issues = append(issues, fmt.Sprintf("缺少依赖: %s", dep))
		}
		cancelImport()
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
		{"core/workflow.py", "完整工作流引擎 v0.10.2（图像生成→QA→分割→绑定→PSD→Live2D导出）"},
		{"core/cli.py", "交互式命令行工具 v0.10.2"},
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
