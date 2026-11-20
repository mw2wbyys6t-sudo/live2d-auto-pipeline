package services

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
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

	return g.generateWithLocalGenerator(req,
		"没有可用的图像服务：这是本地几何占位图，不是 AI 画出的人物。")
}

// GenerateWithCharacter 使用角色一致性和语义分割生成（v0.10.0）
func (g *ImageGenerator) GenerateWithCharacter(req models.GenerateRequest) (*models.GenerateImageResponse, error) {
	// 引擎参数不合法是**请求错误**，不是运行时故障：必须先拦下来。
	// 否则它会走完整条工作流、失败、再回退成占位图 —— 用户看到"生成成功"
	// 但图片没变，比直接报错更难排查。
	if err := validateEngineSelection(req); err != nil {
		return nil, err
	}

	// 设置默认值：竖版立绘是 Live2D 的主流构图，方图会切脚。
	if req.Width <= 0 {
		req.Width = 1024
	}
	if req.Height <= 0 {
		req.Height = 1536
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
	// 真实原因必须跟着产物走，不能只写 stderr：实测（2026-10-30 安装包）
	// 免密上游 402 时用户看到的是一次正常成功 + 一张占位图，
	// 而"为什么没生成"只在服务端日志里。
	return g.generateWithLocalGenerator(legacyReq,
		fmt.Sprintf("生成链路失败，已回退本地占位图：%v", err))
}

// ErrNoImageEngine 表示本机没有任何可用的图像引擎（没配 Key、也没起 ComfyUI /
// SD WebUI）。这是**用户能自己解决**的失败，handler 必须把原文递出去，
// 而不是并入「生成服务暂不可用，请稍后重试」——后者会让人白等。
var ErrNoImageEngine = errors.New("没有可用的图像引擎")

// ErrInvalidEngineSelection 表示请求里的「引擎 / 模型 / LoRA」组合不可能生效。
// 与 ErrNoImageEngine 的区别：这是调用方传错了参数，重试没有意义；
// handler 必须以 400 原样回怼，而不是折叠成"服务暂不可用"。
var ErrInvalidEngineSelection = errors.New("图像引擎参数不合法")

// knownImageProviders 必须与 core/workflow.py 的 --provider choices 保持一致：
// Go 是把这些值原样拼进 CLI 参数的，多一个少一个都会让 Python 侧 argparse
// 直接退出（退出码 2、没有 JSON），最终表现为"生成成功但拿到占位图"。
var knownImageProviders = map[string]struct{}{
	"auto": {}, "comfyui": {}, "sd_webui": {}, "pollinations": {},
	"sensenova": {}, "seedream": {}, "openai": {}, "local_placeholder": {},
}

// validateEngineSelection 在启动 Python 之前校验引擎组合。
//
// 只做两条规则（与 Python 侧 _validate_engine_request 同义）：
//  1. provider 必须是已注册的引擎名；
//  2. model / lora 是**本地文件名**，只能由 comfyui / sd_webui 加载。
//
// 全空表示"按优先级自动选择"，保持旧行为。
func validateEngineSelection(req models.GenerateRequest) error {
	provider := strings.TrimSpace(req.Provider)
	model := strings.TrimSpace(req.Model)
	lora := strings.TrimSpace(req.Lora)
	if provider == "" && model == "" && lora == "" {
		return nil
	}
	if provider != "" {
		if _, ok := knownImageProviders[provider]; !ok {
			return fmt.Errorf("%w：未知的图像引擎 %q", ErrInvalidEngineSelection, provider)
		}
	}
	local := provider == "comfyui" || provider == "sd_webui"
	pollinations := provider == "pollinations"
	if lora != "" {
		if provider == "" {
			return fmt.Errorf("%w：指定 LoRA 时必须同时指定引擎（comfyui 或 sd_webui），"+
				"否则无法确定由谁加载本地文件", ErrInvalidEngineSelection)
		}
		if !local {
			return fmt.Errorf("%w：引擎 %q 不支持指定模型 / LoRA；"+
				"只有 comfyui 与 sd_webui 使用本地模型文件",
				ErrInvalidEngineSelection, provider)
		}
	}
	if model != "" {
		switch {
		case local:
			// 本地底模文件名，原样透传。
		case pollinations:
			if !pollinationsModelPattern.MatchString(model) {
				return fmt.Errorf("%w：pollinations 模型名只能包含字母、数字、点、下划线与短横线",
					ErrInvalidEngineSelection)
			}
		case provider == "":
			return fmt.Errorf("%w：指定模型时必须同时指定引擎，否则无法确定由谁加载",
				ErrInvalidEngineSelection)
		default:
			return fmt.Errorf("%w：引擎 %q 不支持指定模型；只有 comfyui / sd_webui 用本地底模，"+
				"pollinations 可用 flux / turbo / kontext",
				ErrInvalidEngineSelection, provider)
		}
	}
	return nil
}

// pollinationsModelPattern 限制透传到上游 URL 的模型名，避免任意字符串。
var pollinationsModelPattern = regexp.MustCompile(`^[A-Za-z0-9._-]{1,40}$`)

// pythonWorkflowResult 对应 Python WorkflowEngine.run() 返回的 JSON 结构
type pythonWorkflowResult struct {
	Success        bool   `json:"success"`
	Version        string `json:"version"`
	CharacterImage string `json:"character_image"`
	LayersDir      string `json:"layers_dir"`
	OutputDir      string `json:"output_dir"`
	CharacterID    string `json:"character_id,omitempty"`
	Error          string `json:"error,omitempty"`
	ErrorState     string `json:"error_state,omitempty"`
	// ErrorCode 是机器可读的失败原因（见 core/image_gen/base.GenerationError.code）。
	// 只有它非空时，Error 的原文才允许直接递到用户面前。
	ErrorCode string                 `json:"error_code,omitempty"`
	Steps     map[string]interface{} `json:"steps,omitempty"`
	Prompt    *workflowPrompt        `json:"prompt,omitempty"`
	// Partial：worker 已产出立绘、但后续阶段失败。此时 CharacterImage 有效，
	// 不能按「整条失败」处理（那会把真图换成占位图）。
	Partial bool `json:"partial,omitempty"`
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
	// 用户原文必须回传：历史记录的 prompt 取自这里，缺了它左栏会把作品
	// 显示成「未命名作品」，用户也看不出这件是哪句提示词生成的。
	resp.PromptUser = py.Prompt.User
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

	// 4K 画布：>0 时规范化阶段把立绘放大到该高度，分层/PSD/moc3 按高分辨率产出。
	if req.CanvasHeight > 0 {
		args = append(args, "--canvas-height", fmt.Sprintf("%d", req.CanvasHeight))
	}

	if req.CharacterID != "" {
		args = append(args, "--character-id", req.CharacterID)
	}
	// 引擎 / 底模 / LoRA：留空即保持旧的"按优先级自动选择"行为，一个参数都不加。
	// 值已经过 validateEngineSelection 校验，这里只做透传。
	if provider := strings.TrimSpace(req.Provider); provider != "" && provider != "auto" {
		args = append(args, "--provider", provider)
	}
	if model := strings.TrimSpace(req.Model); model != "" {
		args = append(args, "--model", model)
	}
	if lora := strings.TrimSpace(req.Lora); lora != "" {
		args = append(args, "--lora", lora)
	}
	if !req.UseSemantic {
		args = append(args, "--no-semantic")
	}
	if req.DeployDesktop {
		args = append(args, "--deploy-desktop")
	}
	// 多候选择优：1 = 关闭（不传，保持旧参数形态）。
	if req.Candidates > 1 {
		candidates := req.Candidates
		if candidates > 4 {
			candidates = 4
		}
		args = append(args, "--candidates", fmt.Sprintf("%d", candidates))
	}
	if req.NormalizeCanvas != nil && !*req.NormalizeCanvas {
		args = append(args, "--no-normalize-canvas")
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
	// 设置页的非敏感项（本地引擎地址/模型/画幅）由 Python 侧读取 settings.json。
	if settingsFile := os.Getenv("LIVE2D_SETTINGS_FILE"); settingsFile != "" {
		cmd.Env = append(cmd.Env, "LIVE2D_SETTINGS_FILE="+settingsFile)
	}

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

	imagePath, imageOK := workflowImagePath(g.cfg, &pyResult)

	if !pyResult.Success {
		errMsg := pyResult.Error
		if errMsg == "" {
			errMsg = "unknown error"
		}
		// 「没配图像引擎」是用户照着就能修的失败，单独成类让 handler 给出
		// 可操作的文案。上游错误原文仍不外泄（可能含内部 URL/路径）。
		if pyResult.ErrorCode == "no_image_engine" {
			return nil, ErrNoImageEngine
		}
		// 部分成功：立绘已经画出来了，只是后续阶段（分层/绑定/PSD）失败。
		// 以前这里整条判失败，调用方再回退本地占位图 —— 已经拿到的真图被
		// 一张几何剪影替换，用户既看不到真实原因，也拿不到可用的立绘。
		if partial := partialFailureResponse(g.cfg, req, &pyResult); partial != nil {
			return partial, nil
		}
		return nil, fmt.Errorf("工作流失败: %s (state: %s)", errMsg, pyResult.ErrorState)
	}

	if !imageOK {
		return nil, fmt.Errorf("工作流成功但未找到生成的图片（state: %s）", pyResult.ErrorState)
	}
	return buildWorkflowResponse(g.cfg, req, &pyResult, imagePath), nil
}

// partialFailureResponse 处理「图已生成、后续步骤失败」的半成功结果。
//
// 返回 nil 表示不属于部分成功（没声明 partial、或声明了但磁盘上没有图），
// 调用方应继续按整条失败处理。这条分支存在的唯一目的，是不让已经画好的
// 真图因为分层/绑定失败被占位图替换。
func partialFailureResponse(cfg *config.Config, req models.GenerateRequest,
	py *pythonWorkflowResult) *models.GenerateImageResponse {
	if py == nil || py.Success || !py.Partial {
		return nil
	}
	imagePath, ok := workflowImagePath(cfg, py)
	if !ok {
		return nil
	}
	errMsg := py.Error
	if errMsg == "" {
		errMsg = "unknown error"
	}
	resp := buildWorkflowResponse(cfg, req, py, imagePath)
	resp.Incomplete = true
	resp.IncompleteReason = fmt.Sprintf(
		"立绘已生成，但后续步骤失败（%s）：%s", py.ErrorState, errMsg)
	return resp
}

// workflowImagePath 解析并校验工作流产出的立绘路径。
//
// 优先用顶层的 character_image；老版本只在 steps.generate.path 里留痕，
// 所以两处都认。文件不存在时返回 false —— 让调用方按「没有图」处理，
// 而不是把一个必然 404 的路径塞进响应。
func workflowImagePath(cfg *config.Config, py *pythonWorkflowResult) (string, bool) {
	imagePath := py.CharacterImage
	if imagePath == "" {
		if genStep, ok := py.Steps["generate"].(map[string]interface{}); ok {
			if p, ok := genStep["path"].(string); ok {
				imagePath = p
			}
		}
	}
	if imagePath == "" {
		return "", false
	}
	if !filepath.IsAbs(imagePath) {
		imagePath = filepath.Join(cfg.Output.BaseDir, imagePath)
	}
	if _, err := os.Stat(imagePath); err != nil {
		return "", false
	}
	return imagePath, true
}

// buildWorkflowResponse 把 Python 工作流结果映射成 API 响应。
//
// 角色产物落在 output/characters/<id>/ 下，URL 必须由真实路径推导；
// 「/output/ + basename」会让生成结果在角色模式下直接 404。
func buildWorkflowResponse(cfg *config.Config, req models.GenerateRequest,
	py *pythonWorkflowResult, imagePath string) *models.GenerateImageResponse {
	imageURL := OutputURLFor(cfg, imagePath)
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
		LayersDir:   py.LayersDir,
		OutputDir:   py.OutputDir,
		CharacterID: py.CharacterID,
		Steps:       py.Steps,
	}

	// 上游可能降尺寸（免密通道约 0.6MP 上限）：以 Python 记录的**真实**
	// 像素为准，请求尺寸只作回退 —— 否则界面会把一张 627×940 的图标成
	// 1024×1536，用户以为拿到了原尺寸，实际是放大过的糊图。
	if genStep, ok := py.Steps["generate"].(map[string]interface{}); ok {
		if v, ok := genStep["width"].(float64); ok && v > 0 {
			resp.Width = int(v)
		}
		if v, ok := genStep["height"].(float64); ok && v > 0 {
			resp.Height = int(v)
		}
		if downgraded, ok := genStep["size_downgraded"].(bool); ok {
			resp.SizeDowngraded = downgraded
		}
	}

	// 提取 PSD 路径和 model3.json 路径
	if psdStep, ok := py.Steps["psd"].(map[string]interface{}); ok {
		if p, ok := psdStep["psd_path"].(string); ok {
			resp.PSDPath = p
		}
	}
	if rigStep, ok := py.Steps["rigging"].(map[string]interface{}); ok {
		if p, ok := rigStep["model3_json"].(string); ok {
			resp.Model3JSON = p
		}
	}

	applyWorkflowPrompt(resp, py)
	return resp
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// generateWithLocalGenerator 使用自研本地生成器生成图片。
//
// fallbackReason 说明**为什么**拿到的是占位图（没配图像服务 / 上游失败），
// 它会随响应与历史记录一起落盘：占位图本身是有意为之的离线兜底，
// 但把它当成一次正常成功展示才是缺陷 —— 调用方必须交代原因。
func (g *ImageGenerator) generateWithLocalGenerator(req models.GenerateImageRequest, fallbackReason string) (*models.GenerateImageResponse, error) {
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
		// 但 success=true 不等于「拿到了 AI 立绘」——占位与否由下面这两个字段说。
		Success:        true,
		Fallback:       true,
		FallbackReason: fallbackReason,
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
