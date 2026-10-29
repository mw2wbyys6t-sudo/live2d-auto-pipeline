package services

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"live2d-api/config"
	"live2d-api/models"
)

type ImageGenerator struct {
	cfg *config.Config
}

func NewImageGenerator(cfg *config.Config) *ImageGenerator {
	return &ImageGenerator{
		cfg: cfg,
	}
}

// GenerateImage 生成图片，使用自研本地生成器（保持向后兼容）
func (g *ImageGenerator) GenerateImage(req models.GenerateImageRequest) (*models.GenerateImageResponse, error) {
	// 设置默认值
	if req.Width <= 0 {
		req.Width = 512
	}
	if req.Height <= 0 {
		req.Height = 768
	}
	req.Width, req.Height = ClampImageDimensions(req.Width, req.Height)
	if req.Seed == 0 {
		req.Seed = rand.Intn(999999999)
	}

	return g.generateWithLocalGenerator(req)
}

// GenerateWithCharacter 使用角色一致性和语义分割生成（v0.10.0）
func (g *ImageGenerator) GenerateWithCharacter(req models.GenerateRequest) (*models.GenerateImageResponse, error) {
	// 设置默认值
	if req.Width <= 0 {
		req.Width = 1024
	}
	if req.Height <= 0 {
		req.Height = 1024
	}
	req.Width, req.Height = ClampImageDimensions(req.Width, req.Height)
	if req.Seed == 0 {
		req.Seed = rand.Intn(999999999)
	}
	if !req.UseSemantic {
		req.UseSemantic = true // 默认使用语义分割
	}

	// 优先使用 workflow.py 新工作流
	resp, err := g.generateWithWorkflow(req)
	if err == nil {
		return resp, nil
	}

	// 回退到本地生成器
	fmt.Fprintf(os.Stderr, "[WARN] 工作流生成失败，回退到本地生成器: %v\n", err)
	legacyReq := models.GenerateImageRequest{
		Prompt:  req.Prompt,
		Width:   req.Width,
		Height:  req.Height,
		Seed:    req.Seed,
		ModelID: req.ModelID,
	}
	return g.generateWithLocalGenerator(legacyReq)
}

// pythonWorkflowResult 对应 Python WorkflowEngine.run() 返回的 JSON 结构
type pythonWorkflowResult struct {
	Success        bool                   `json:"success"`
	Version        string                 `json:"version"`
	CharacterImage string                 `json:"character_image"`
	LayersDir      string                 `json:"layers_dir"`
	OutputDir      string                 `json:"output_dir"`
	CharacterID    string                 `json:"character_id,omitempty"`
	Error          string                 `json:"error,omitempty"`
	ErrorState     string                 `json:"error_state,omitempty"`
	Steps          map[string]interface{} `json:"steps,omitempty"`
	Prompt         *workflowPrompt        `json:"prompt,omitempty"`
}

// workflowPrompt 是 core/workflow.py 写下的提示词链路记录。
type workflowPrompt struct {
	User      string `json:"user"`
	Effective string `json:"effective"`
	Sent      string `json:"sent"`
	Negative  string `json:"negative,omitempty"`
	Provider  string `json:"provider"`
	Lang      string `json:"lang"`
	Mismatch  bool   `json:"mismatch"`
}

// applyWorkflowPrompt 把 Python 侧的提示词记录映射到响应；缺失时保持空值。
func applyWorkflowPrompt(resp *models.GenerateImageResponse, py *pythonWorkflowResult) {
	if resp == nil || py == nil || py.Prompt == nil {
		return
	}
	resp.EffectivePrompt = py.Prompt.Effective
	resp.PromptSent = py.Prompt.Sent
	resp.PromptLang = py.Prompt.Lang
	resp.PromptMismatch = py.Prompt.Mismatch
}

// workflowOutputDir 返回 Python 工作流的产物目录。
// 该目录必须与 /output 静态路由（ServeOutput）的服务根目录 cfg.Output.BaseDir
// 保持一致；桌面版把脚本装在只读的 app/、把产物写在文档目录，两者不同，
// 若仍按 ScriptsDir/output 写入，前端拿到的 /output/<file> 会全部 404。
func workflowOutputDir(cfg *config.Config, characterID string) string {
	if characterID == "" {
		return cfg.Output.BaseDir
	}
	return filepath.Join(cfg.Output.BaseDir, "characters", characterID)
}

// generateWithWorkflow 使用 core/workflow.py 新工作流（v0.10.1: 使用 --json 模式）
func (g *ImageGenerator) generateWithWorkflow(req models.GenerateRequest) (*models.GenerateImageResponse, error) {
	scriptPath := filepath.Join(g.cfg.Python.ScriptsDir, "core", "workflow.py")
	if _, err := os.Stat(scriptPath); os.IsNotExist(err) {
		return nil, fmt.Errorf("工作流脚本不存在: %s", scriptPath)
	}

	// 确定输出目录：必须与 /output 静态路由的服务根目录一致，
	// 否则桌面版（安装目录 app/ 与文档产物目录分离）会出现「生成成功但图片 404」。
	if req.CharacterID != "" {
		// character_id 来自 JSON body 且会被拼进文件路径，必须过白名单
		// （全局中间件只校验 URL path 不校验 body），否则 "../../x" 可穿越建目录。
		if err := validateStrictID(req.CharacterID); err != nil {
			return nil, err
		}
	}
	outputDir := workflowOutputDir(g.cfg, req.CharacterID)
	if err := os.MkdirAll(outputDir, 0755); err != nil {
		return nil, fmt.Errorf("创建输出目录失败: %v", err)
	}

	// 构建参数（使用 --json 模式，可靠解析结果）
	args := []string{
		scriptPath,
		"--json",
		"--output", outputDir,
		"--width", fmt.Sprintf("%d", req.Width),
		"--height", fmt.Sprintf("%d", req.Height),
		"--seed", fmt.Sprintf("%d", req.Seed),
		"--live2d-export", // 默认导出 Live2D
	}

	if req.CharacterID != "" {
		args = append(args, "--character-id", req.CharacterID)
	}
	if !req.UseSemantic {
		args = append(args, "--no-semantic")
	}
	if req.DeployDesktop {
		args = append(args, "--deploy-desktop")
	}
	if req.NegativePrompt != "" {
		args = append(args, "--negative-prompt", req.NegativePrompt)
	}

	// 添加提示词（按字符截断：中文一个字算一个字符，切点不落在字符中间）
	prompt := truncatePromptRunes(req.Prompt, promptMaxRunes)
	if prompt != "" {
		if strings.HasPrefix(prompt, "-") {
			prompt = " " + prompt
		}
		args = append(args, prompt)
	}

	cmd := exec.Command(g.cfg.Python.PythonPath, args...)
	cmd.Dir = g.cfg.Python.ScriptsDir
	// LIVE2D_PROJECT_ROOT 必须指向项目根：core/logger.py 用它决定日志文件位置，
	// 缺省回退到 %USERPROFILE%\logs，在受控/沙箱环境下不可写会导致 logging
	// 每次 emit 都抛 OSError 并把回溯刷进 stderr，进而污染 JSON 结果解析。
	cmd.Env = append(os.Environ(),
		"PYTHONIOENCODING=utf-8",
		"PYTHONPATH="+g.cfg.Python.ScriptsDir,
		"LIVE2D_PROJECT_ROOT="+g.cfg.Python.ScriptsDir,
	)

	// 工作流超时更长（生图+分割+绑定比较耗时）
	timeout := g.cfg.GetPythonTimeout() * 5
	done := make(chan struct{})
	var output []byte
	var runErr error

	go func() {
		output, runErr = cmd.CombinedOutput()
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(timeout):
		if cmd.Process != nil {
			cmd.Process.Kill()
		}
		return nil, fmt.Errorf("工作流执行超时（限制%d秒）", int(timeout.Seconds()))
	}

	// 从输出中提取 JSON（日志与结果混流，往前定位真正的结果对象）
	outputStr := string(output)
	jsonStart, jsonEnd := findWorkflowJSON(outputStr)
	if jsonStart == -1 {
		fmt.Fprintf(os.Stderr, "[ERROR] 工作流未返回 JSON，输出: %s\n", outputStr)
		if runErr != nil {
			return nil, fmt.Errorf("工作流执行失败: %v", runErr)
		}
		return nil, fmt.Errorf("工作流未返回有效结果")
	}
	jsonStr := outputStr[jsonStart:jsonEnd]

	// 解析 JSON 结果
	var pyResult pythonWorkflowResult
	if err := json.Unmarshal([]byte(jsonStr), &pyResult); err != nil {
		fmt.Fprintf(os.Stderr, "[ERROR] JSON解析失败: %v\n输出片段: %s\n", err, jsonStr[:min(500, len(jsonStr))])
		if runErr != nil {
			return nil, fmt.Errorf("工作流执行失败: %v", runErr)
		}
		return nil, fmt.Errorf("解析工作流结果失败: %v", err)
	}

	if !pyResult.Success {
		errMsg := pyResult.Error
		if errMsg == "" {
			errMsg = "unknown error"
		}
		return nil, fmt.Errorf("工作流失败: %s (state: %s)", errMsg, pyResult.ErrorState)
	}

	// 构建响应
	imagePath := pyResult.CharacterImage
	if imagePath == "" {
		// 尝试从 steps.generate.path 获取
		if genStep, ok := pyResult.Steps["generate"].(map[string]interface{}); ok {
			if p, ok := genStep["path"].(string); ok {
				imagePath = p
			}
		}
	}
	if !filepath.IsAbs(imagePath) {
		imagePath = filepath.Join(g.cfg.Output.BaseDir, imagePath)
	}

	// 角色产物落在 output/characters/<id>/ 下，URL 必须由真实路径推导；
	// 「/output/ + basename」会让生成结果在角色模式下直接 404。
	imageURL := OutputURLFor(g.cfg, imagePath)
	if imageURL == "" {
		imageURL = "/output/" + filepath.Base(imagePath)
	}

	resp := &models.GenerateImageResponse{
		Success:     true,
		ImagePath:   imagePath,
		ImageURL:    imageURL,
		Seed:        req.Seed,
		Width:       req.Width,
		Height:      req.Height,
		Source:      "workflow_v0.10.2",
		CreatedAt:   time.Now(),
		LayersDir:   pyResult.LayersDir,
		OutputDir:   pyResult.OutputDir,
		CharacterID: pyResult.CharacterID,
		Steps:       pyResult.Steps,
	}

	// 提取 PSD 路径和 model3.json 路径
	if psdStep, ok := pyResult.Steps["psd"].(map[string]interface{}); ok {
		if p, ok := psdStep["psd_path"].(string); ok {
			resp.PSDPath = p
		}
	}
	if rigStep, ok := pyResult.Steps["rigging"].(map[string]interface{}); ok {
		if p, ok := rigStep["model3_json"].(string); ok {
			resp.Model3JSON = p
		}
	}

	applyWorkflowPrompt(resp, &pyResult)

	return resp, nil
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// generateWithLocalGenerator 使用自研本地生成器生成图片
func (g *ImageGenerator) generateWithLocalGenerator(req models.GenerateImageRequest) (*models.GenerateImageResponse, error) {
	scriptPath := filepath.Join(g.cfg.Python.ScriptsDir, "local_image_generator.py")

	if _, err := os.Stat(scriptPath); os.IsNotExist(err) {
		return nil, fmt.Errorf("本地生成器脚本不存在: %s", scriptPath)
	}

	if err := os.MkdirAll(g.cfg.Output.BaseDir, 0755); err != nil {
		return nil, fmt.Errorf("创建输出目录失败: %v", err)
	}

	args := []string{
		scriptPath,
		"--output", g.cfg.Output.BaseDir,
		"--width", fmt.Sprintf("%d", req.Width),
		"--height", fmt.Sprintf("%d", req.Height),
		"--steps", "25",
		"--seed", fmt.Sprintf("%d", req.Seed),
		"--quality", "standard",
	}

	if req.ModelID != "" {
		args = append(args, "--model", req.ModelID)
	}

	if !req.NoLive2DOpt {
		// 默认启用Live2D优化
	}

	if req.Prompt != "" {
		prompt := truncatePromptRunes(req.Prompt, promptMaxRunes)
		if strings.HasPrefix(prompt, "-") {
			prompt = " " + prompt
		}
		args = append(args, "--", prompt)
	}

	// 单图生成本地调用：沿用 Python 执行预算（默认 120s，受配置控制），
	// 防止生成脚本挂起（模型加载卡死/等待输入）导致 goroutine 与请求永久泄漏。
	genCtx, cancelGen := context.WithTimeout(context.Background(), g.cfg.GetPythonTimeout())
	defer cancelGen()
	cmd := exec.CommandContext(genCtx, g.cfg.Python.PythonPath, args...)
	cmd.Dir = g.cfg.Python.ScriptsDir
	cmd.Env = append(os.Environ(),
		"PYTHONIOENCODING=utf-8",
		"LIVE2D_PROJECT_ROOT="+g.cfg.Python.ScriptsDir,
	)

	output, err := cmd.CombinedOutput()
	if err != nil {
		if genCtx.Err() == context.DeadlineExceeded {
			fmt.Fprintf(os.Stderr, "[ERROR] 本地生成器执行超时（限制%ds）\n", int(g.cfg.GetPythonTimeout().Seconds()))
			return nil, fmt.Errorf("本地生成器执行超时（限制%d秒）", int(g.cfg.GetPythonTimeout().Seconds()))
		}
		fmt.Fprintf(os.Stderr, "[ERROR] 本地生成器执行失败: %v\n输出: %s\n", err, string(output))
		return nil, fmt.Errorf("本地生成器执行失败，请检查服务端日志")
	}

	outputStr := string(output)
	outputPath := g.parseOutputPath(outputStr)

	if outputPath == "" {
		return nil, fmt.Errorf("无法从输出中解析图片路径")
	}

	if !filepath.IsAbs(outputPath) {
		outputPath = filepath.Join(g.cfg.Output.BaseDir, outputPath)
	}

	imageURL := OutputURLFor(g.cfg, outputPath)
	if imageURL == "" {
		imageURL = "/output/" + filepath.Base(outputPath)
	}

	return &models.GenerateImageResponse{
		ImagePath: outputPath,
		ImageURL:  imageURL,
		Seed:      req.Seed,
		Width:     req.Width,
		Height:    req.Height,
		Source:    "local_generator_v3",
		CreatedAt: time.Now(),
		// 这条路径已经产出了图片文件，success 必须为 true：前端（web/lib/api-client.ts
		// 的 mapLegacyResponse）按 data.success 判断成败，漏赋值会把「生成成功」
		// 显示成失败。
		Success: true,
	}, nil
}

// parseOutputPath 从输出中解析图片路径
func (g *ImageGenerator) parseOutputPath(output string) string {
	lines := strings.Split(output, "\n")

	for _, line := range lines {
		if strings.Contains(line, "文件:") || strings.Contains(line, "图片已保存:") ||
			strings.Contains(line, "character_") || strings.Contains(line, "optimized_") {
			parts := strings.Fields(line)
			for _, part := range parts {
				if strings.HasSuffix(part, ".png") {
					return part
				}
			}
		}
	}

	// 尝试从output目录查找最新文件
	outputDir := g.cfg.Output.BaseDir
	if files, err := os.ReadDir(outputDir); err == nil {
		var latestFile os.DirEntry
		var latestTime time.Time
		for _, file := range files {
			if strings.HasSuffix(file.Name(), ".png") {
				if info, err := file.Info(); err == nil {
					if info.ModTime().After(latestTime) {
						latestTime = info.ModTime()
						latestFile = file
					}
				}
			}
		}
		if latestFile != nil {
			return filepath.Join(outputDir, latestFile.Name())
		}
	}

	return ""
}

// CheckLocalGeneratorStatus 检查本地生成器状态
func (g *ImageGenerator) CheckLocalGeneratorStatus() (bool, string) {
	scriptPath := filepath.Join(g.cfg.Python.ScriptsDir, "local_image_generator.py")
	if _, err := os.Stat(scriptPath); os.IsNotExist(err) {
		return false, "本地生成器脚本不存在"
	}

	// 检查新工作流
	workflowPath := filepath.Join(g.cfg.Python.ScriptsDir, "core", "workflow.py")
	if _, err := os.Stat(workflowPath); err == nil {
		return true, "工作流就绪"
	}

	// import torch/diffusers 冷启动较慢，给 60s 上限；挂起的解释器不能
	// 让状态检查请求永久阻塞。
	depCtx, cancelDep := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancelDep()
	cmd := exec.CommandContext(depCtx, g.cfg.Python.PythonPath, "-c", "import diffusers; import torch; import PIL")
	output, err := cmd.CombinedOutput()
	if err != nil {
		if depCtx.Err() == context.DeadlineExceeded {
			return false, "依赖检查超时（>60s），Python 环境可能异常"
		}
		return false, fmt.Sprintf("缺少依赖: %s", string(output))
	}

	return true, "本地生成器就绪"
}

// GetAvailableModels 获取可用模型列表
func (g *ImageGenerator) GetAvailableModels() []map[string]interface{} {
	return []map[string]interface{}{
		{
			"id":      "Linaqruf/anything-v3.0",
			"name":    "Anything V3",
			"desc":    "通用动漫风格",
			"size":    "约 4GB",
			"type":    "sd15",
			"quality": "standard",
		},
		{
			"id":      "stablediffusionapi/anything-v5",
			"name":    "Anything V5",
			"desc":    "高质量动漫",
			"size":    "约 4GB",
			"type":    "sd15",
			"quality": "high",
		},
		{
			"id":      "gsdf/Counterfeit-V3.0",
			"name":    "Counterfeit V3",
			"desc":    "细腻画风",
			"size":    "约 4GB",
			"type":    "sd15",
			"quality": "ultra",
		},
		{
			"id":      "Meina/MeinaMix",
			"name":    "MeinaMix",
			"desc":    "萌系风格",
			"size":    "约 4GB",
			"type":    "sd15",
			"quality": "high",
		},
		{
			"id":      "andite/pastel-mix",
			"name":    "Pastel Mix",
			"desc":    "柔和色彩",
			"size":    "约 4GB",
			"type":    "sd15",
			"quality": "high",
		},
		{
			"id":      "WarriorMama777/OrangeMixs",
			"name":    "AbyssOrangeMix",
			"desc":    "丰富色彩",
			"size":    "约 4GB",
			"type":    "sd15",
			"quality": "high",
		},
		{
			"id":      "Vsukiyaki/ShiitakeMix",
			"name":    "Shiitake-Mix",
			"desc":    "SDXL高质量动漫",
			"size":    "约 7GB",
			"type":    "sdxl",
			"quality": "ultra",
		},
	}
}

// findTopLevelJSON 在 stdout 中找到第一个最外层 JSON 对象的起止位置。
// 返回的 end 不含；找不到返回 (-1, -1)。
func findTopLevelJSON(s string) (int, int) {
	spans := topLevelJSONSpans(s)
	if len(spans) == 0 {
		return -1, -1
	}
	return spans[0][0], spans[0][1]
}

// findWorkflowJSON 定位工作流结果 JSON。
//
// workflow.py --json 把结果对象打印在输出末尾，但 CombinedOutput 会把它和日志
// 混在一起，日志文本里可能夹带花括号（例如 f-string 源码行 {self.model_type}）。
// 因此不能简单取第一个 {...}：这里从后往前挑第一个能解析出 "success" 字段的
// 顶层对象（即真正的结果）；若都解析不了则退回最后一个配平对象。
func findWorkflowJSON(s string) (int, int) {
	spans := topLevelJSONSpans(s)
	for i := len(spans) - 1; i >= 0; i-- {
		var probe map[string]json.RawMessage
		if err := json.Unmarshal([]byte(s[spans[i][0]:spans[i][1]]), &probe); err != nil {
			continue
		}
		if _, ok := probe["success"]; ok {
			return spans[i][0], spans[i][1]
		}
	}
	if n := len(spans); n > 0 {
		return spans[n-1][0], spans[n-1][1]
	}
	return -1, -1
}

// topLevelJSONSpans 返回 s 中所有花括号配平的顶层 {...} 区间 [start, end)。
// 字符串内部的括号与转义不参与计数。
func topLevelJSONSpans(s string) [][2]int {
	var spans [][2]int
	depth := 0
	start := -1
	inStr := false
	escape := false
	for i := 0; i < len(s); i++ {
		ch := s[i]
		if escape {
			escape = false
			continue
		}
		if ch == '\\' && inStr {
			escape = true
			continue
		}
		if ch == '"' {
			inStr = !inStr
			continue
		}
		if inStr {
			continue
		}
		if ch == '{' {
			if depth == 0 {
				start = i
			}
			depth++
		} else if ch == '}' && depth > 0 {
			depth--
			if depth == 0 {
				spans = append(spans, [2]int{start, i + 1})
			}
		}
	}
	return spans
}
