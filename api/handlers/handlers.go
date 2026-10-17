package handlers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"live2d-api/config"
	"live2d-api/models"
	"live2d-api/services"
)

// Version 是后端的语义版本号，集中定义避免散落多处版本字符串不同步。
// v0.10.3：首次集中化管理，更新时只需改这一处。
const Version = "v0.10.8-go"

type Handler struct {
	cfg            *config.Config
	imageGenerator *services.ImageGenerator
	pythonBridge   *services.PythonBridge
	nativeBridge   *services.NativeBridge
	cache          *services.RequestCache
	charSvc        *services.CharacterService
	chatSvc        *services.ChatService
	wsHub          *services.WSHub
	modelDL        *services.ModelDownloader
	capMgr         *services.CapabilityManager
	startTime      time.Time
}

func NewHandler(cfg *config.Config, imageGenerator *services.ImageGenerator, cache *services.RequestCache) *Handler {
	h := &Handler{
		cfg:            cfg,
		imageGenerator: imageGenerator,
		pythonBridge:   services.NewPythonBridge(cfg),
		cache:          cache,
		charSvc:        services.NewCharacterService(cfg),
		chatSvc:        services.NewChatService(cfg),
		wsHub:          services.NewWSHubWithOrigins(cfg.Server.AllowedOrigins, cfg.Server.Port),
		modelDL:        services.NewModelDownloader(cfg.Python.HfCache),
		capMgr:         services.NewCapabilityManager(cfg),
		startTime:      time.Now(),
	}
	h.nativeBridge = services.NewNativeBridge(cfg, h.wsHub)

	// 启动缓存清理守护进程
	if cache != nil {
		cache.StartCleanupDaemon(5 * time.Minute)
	}

	// 应用配置的最大连接数（WebSocket.MaxConnections 此前声明但无人读取）
	if cfg.WebSocket.MaxConnections > 0 {
		h.wsHub.SetMaxConns(cfg.WebSocket.MaxConnections)
	}

	// 启动 WebSocket hub
	go h.wsHub.Run()

	return h
}

// HealthCheck 健康检查。
//
// v0.10.3: 不再永远返回 200 —— 启动后跑一次 Python 解释器探针，不可用时
// 返回 503 + 中文说明，避免「前端看到正常、点生成立刻崩」的误导体验。
func (h *Handler) HealthCheck(c *gin.Context) {
	pyOK, pyIssues := h.pythonBridge.CheckPythonEnvironment()
	if !pyOK {
		c.JSON(http.StatusServiceUnavailable, models.Response{
			Success: false,
			Error:   "Python 环境不可用：" + strings.Join(pyIssues, "；"),
			Data: map[string]interface{}{
				"version":   Version,
				"uptime":    time.Since(h.startTime).String(),
				"python_ok": false,
				"hint":      "请确认 Python 已安装并配置正确，或运行 python install.py 安装依赖",
			},
		})
		return
	}
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "Live2D API 服务正常运行",
		Data: map[string]interface{}{
			"version":   Version,
			"uptime":    time.Since(h.startTime).String(),
			"python_ok": true,
		},
	})
}

// GetSystemStatus 获取系统状态
func (h *Handler) GetSystemStatus(c *gin.Context) {
	var services []models.ServiceStatus

	// 本地生成器状态
	localAvailable, localMsg := h.imageGenerator.CheckLocalGeneratorStatus()
	services = append(services, models.ServiceStatus{
		Name:        "local_generator",
		Available:   localAvailable,
		Version:     localMsg,
		LastChecked: time.Now(),
	})

	// Python 环境
	pyOK, pyIssues := h.pythonBridge.CheckPythonEnvironment()
	pyStatus := "正常"
	if !pyOK {
		pyStatus = "异常: " + strings.Join(pyIssues, ", ")
	}
	services = append(services, models.ServiceStatus{
		Name:        "python_env",
		Available:   pyOK,
		Version:     pyStatus,
		LastChecked: time.Now(),
	})

	// See-through 状态
	seeThroughOK := h.pythonBridge.CheckSeeThroughInstalled()
	services = append(services, models.ServiceStatus{
		Name:        "see_through",
		Available:   seeThroughOK,
		Version:     "SIGGRAPH 2026",
		LastChecked: time.Now(),
	})

	// 缓存服务状态
	if h.cache != nil {
		_ = h.cache.Stats() // 调用Stats保持接口一致性
		services = append(services, models.ServiceStatus{
			Name:        "request_cache",
			Available:   true,
			Version:     "enabled",
			LastChecked: time.Now(),
		})
	}

	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Data: models.SystemStatus{
			Services: services,
			Version:  Version,
			Uptime:   time.Since(h.startTime).String(),
		},
	})
}

// GenerateImage 生成图片（支持缓存）
func (h *Handler) GenerateImage(c *gin.Context) {
	var req models.GenerateImageRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{
			Success: false,
			Error:   "请求参数错误: " + err.Error(),
		})
		return
	}

	// 尝试从缓存获取
	var result *models.GenerateImageResponse
	var fromCache bool

	if h.cache != nil && req.Seed != 0 {
		result, fromCache = h.cache.Get(req.Prompt, req.Width, req.Height, req.Seed, req.ModelID)
	}

	if !fromCache {
		// 缓存未命中，生成图片
		var err error
		result, err = h.imageGenerator.GenerateImage(req)
		if err != nil {
			h.serverError(c, "图片生成", err)
			return
		}

		// 将结果存入缓存
		if h.cache != nil && req.Seed != 0 {
			h.cache.Set(req.Prompt, req.Width, req.Height, req.Seed, req.ModelID, result)
		}
	}

	response := models.Response{
		Success: true,
		Message: "图片生成成功",
		Data:    result,
	}

	if fromCache {
		response.Message = "图片生成成功（来自缓存）"
		response.Data = map[string]interface{}{
			"result":     result,
			"from_cache": true,
		}
	}

	c.JSON(http.StatusOK, response)
}

// GenerateCharacter v0.10.0: 增强版生成（角色一致性 + 语义分割 + Live2D导出）
func (h *Handler) GenerateCharacter(c *gin.Context) {
	var req models.GenerateRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	if req.Width <= 0 {
		req.Width = 1024
	}
	if req.Height <= 0 {
		req.Height = 1024
	}
	if !req.UseSemantic {
		req.UseSemantic = true
	}

	taskID := fmt.Sprintf("gen_%d", time.Now().UnixNano())
	h.wsHub.BroadcastProgress(taskID, "starting", 5, "开始生成...")

	if req.CharacterID != "" {
		if prompt, err := h.charSvc.GetGenerationPrompt(req.CharacterID, req.Prompt); err == nil {
			req.Prompt = prompt
		}
		h.wsHub.BroadcastProgress(taskID, "generating", 25, "角色一致性已应用...")
	}

	h.wsHub.BroadcastProgress(taskID, "generating", 40, "正在生成图片...")
	result, err := h.imageGenerator.GenerateWithCharacter(req)
	if err != nil {
		// 异常详情只进日志；推给浏览器/返回给客户端的是固定文案，
		// 避免上游错误原文（可能含内部 URL/路径）经 WS 或 HTTP 外泄。
		log.Printf("[error] 角色生成: %v", err)
		h.wsHub.BroadcastProgress(taskID, "error", 0, "生成服务暂不可用，请稍后重试")
		c.JSON(http.StatusInternalServerError, models.Response{
			Success: false,
			Error:   "角色生成服务暂不可用，请稍后重试",
		})
		return
	}
	h.wsHub.BroadcastProgress(taskID, "done", 100, "生成完成！")
	services.RecordLatestGeneration(h.cfg, result)
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "角色生成成功", Data: result})
}

// Chat 非流式聊天
func (h *Handler) Chat(c *gin.Context) {
	var req models.ChatRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	resp, err := h.chatSvc.Chat(req)
	if err != nil {
		h.serverError(c, "聊天", err)
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Data: resp})
}

// GetModels 获取可用模型列表
func (h *Handler) GetModels(c *gin.Context) {
	availableModels := h.imageGenerator.GetAvailableModels()
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		// 诚实性标注：这份列表是内置示例，切换它不会改变本地生成结果。
		Message: "内置示例模型列表（仅作展示；当前本地生成器不消费模型选择）",
		Data:    availableModels,
	})
}

// CreatePSDPlan 创建 PSD 分层规划
func (h *Handler) CreatePSDPlan(c *gin.Context) {
	var req models.PSDLayerRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{
			Success: false,
			Error:   "请求参数错误: " + err.Error(),
		})
		return
	}

	result, err := h.pythonBridge.CreatePSDPlan(c.Request.Context(), req.ImagePath)
	if err != nil {
		switch {
		case errors.Is(err, services.ErrPathOutsideBase):
			c.JSON(http.StatusBadRequest, models.Response{
				Success: false,
				Error:   err.Error(),
			})
		case errors.Is(err, services.ErrPythonTimeout):
			// CPU 语义分层可能耗时数分钟，超时与 segment 一样走 504 契约
			c.JSON(http.StatusGatewayTimeout, models.Response{Success: false, Error: err.Error()})
		default:
			h.serverError(c, "PSD分层", err)
		}
		return
	}

	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "PSD分层规划创建成功",
		Data:    result,
	})
}

// RunSeeThrough 运行 See-through 工作流
func (h *Handler) RunSeeThrough(c *gin.Context) {
	var req models.SeeThroughRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{
			Success: false,
			Error:   "请求参数错误: " + err.Error(),
		})
		return
	}

	result, err := h.pythonBridge.RunSeeThroughWorkflow(req.ImagePath)
	if err != nil {
		h.serverError(c, "See-through 工作流", err)
		return
	}

	// 诚实性修复：该接口从未真正执行抠图，过去却以 200 + success:true 返回，
	// 让用户以为已处理。现在改为 501，并说明可替代路径。
	c.JSON(http.StatusNotImplemented, models.Response{
		Success: false,
		Error: "See-through 抠图未实现：本版本不会执行背景移除。" +
			"请改用分层页的颜色/语义分层；如已安装 ComfyUI，可由后续版本接入。",
		Data: map[string]interface{}{
			"implemented": false,
			"detail":      result,
		},
	})
}

// GetPythonScripts 获取 Python 脚本列表
func (h *Handler) GetPythonScripts(c *gin.Context) {
	scripts := h.pythonBridge.GetPythonScripts()
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Data:    scripts,
	})
}

// serverError 用于未预期的服务端错误（5xx）：异常详情（可能含绝对路径、
// 上游错误原文等内部信息）只写服务端日志，响应体统一返回固定文案，
// 不向前端/局域网客户端泄露实现细节。业务校验失败（400/404）不要用它。
func (h *Handler) serverError(c *gin.Context, where string, err error) {
	log.Printf("[error] %s: %v", where, err)
	c.JSON(http.StatusInternalServerError, models.Response{
		Success: false,
		Error:   where + "服务暂不可用，请稍后重试",
	})
}

// ServeOutput 提供输出文件访问（支持子目录，如 /output/layers_123/hair.png）
func (h *Handler) ServeOutput(c *gin.Context) {
	// 路由从 /output/:filename 升级为 /output/*filepath 后，
	// filepath 形如 "/layers_123/hair.png"；同时兼容旧的单段参数名。
	filename := c.Param("filepath")
	if filename == "" {
		filename = c.Param("filename")
	}
	filename = strings.TrimPrefix(filename, "/")
	if filename == "" {
		c.JSON(http.StatusBadRequest, models.Response{
			Success: false,
			Error:   "文件名不能为空",
		})
		return
	}

	// 安全检查：防止目录遍历
	filePath := filepath.Join(h.cfg.Output.BaseDir, filename)
	if !isPathSafe(filePath, h.cfg.Output.BaseDir) {
		c.JSON(http.StatusForbidden, models.Response{
			Success: false,
			Error:   "非法的文件路径",
		})
		return
	}

	// 检查文件是否存在 & 拒绝目录请求（防目录列举泄露产物清单）
	info, err := os.Stat(filePath)
	if err != nil {
		c.JSON(http.StatusNotFound, models.Response{
			Success: false,
			Error:   "文件不存在",
		})
		return
	}
	if info.IsDir() {
		// gin 的 c.File 对目录会返回 http.ServeFile 的 HTML 目录列举，
		// 泄露 output 下全部产物（生成的图片、分层目录、导出模型等）。
		// 面捕/预览只需具体文件，目录一律按「不存在」处理。
		c.JSON(http.StatusNotFound, models.Response{
			Success: false,
			Error:   "文件不存在",
		})
		return
	}

	// 添加缓存控制头
	c.Header("Cache-Control", "public, max-age=3600")
	c.File(filePath)
}

// isPathSafe 检查路径是否在允许的目录内
func isPathSafe(path, baseDir string) bool {
	absPath, err := filepath.Abs(path)
	if err != nil {
		return false
	}
	absBase, err := filepath.Abs(baseDir)
	if err != nil {
		return false
	}

	rel, err := filepath.Rel(absBase, absPath)
	if err != nil {
		return false
	}

	return !strings.HasPrefix(rel, "..") && rel != ".."
}

// ======================================================================
// 跨页面流水线接力：最近生成 / 分层 / 上传 / 角色参考图与 embedding
// ======================================================================

// LatestGeneration 返回最近一次成功生成的产物，供前端跨页面接力
// （生成 → 分层 → 导出 → 桌宠）。与 Python 后端 /api/generations/latest 同契约。
func (h *Handler) LatestGeneration(c *gin.Context) {
	record, err := services.GetLatestGeneration(h.cfg)
	if err != nil {
		c.JSON(http.StatusNotFound, models.Response{Success: false, Error: err.Error()})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Data: record})
}

// UploadImage 接收一张图片（multipart 字段名 file），保存到 output/uploads/
// 并返回 {path, url}。path 供 /api/segment 与参考图端点使用。
func (h *Handler) UploadImage(c *gin.Context) {
	fh, err := c.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "缺少上传文件字段 file"})
		return
	}
	path, url, err := services.SaveUpload(h.cfg, fh)
	if err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "上传成功", Data: map[string]string{"path": path, "url": url}})
}

// SegmentImage 对图片运行真实分层并写出 PSD。
// body: {"image_path": "...", "method": "semantic"|"kmeans"}；image_path 为空时
// 回退为最近一次生成图（与 Python 后端 /api/segment 行为一致）。
func (h *Handler) SegmentImage(c *gin.Context) {
	var req struct {
		ImagePath string `json:"image_path"`
		Method    string `json:"method"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	imagePath := req.ImagePath
	if imagePath == "" {
		record, err := services.GetLatestGeneration(h.cfg)
		if err != nil {
			c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "未指定 image_path 且没有可用的最近生成图，请先上传图片或生成角色"})
			return
		}
		imagePath, _ = record["image_path"].(string)
		if imagePath == "" {
			c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "最近生成记录中没有源图片路径，请上传图片"})
			return
		}
	}
	// 相对路径按输出根解析：前端传来的合法源图（上传图/生成图）都在输出根内，
	// 桥接层随后还会做 validateWithinBase 归属校验，根之外的绝对路径一律拒绝。
	if !filepath.IsAbs(imagePath) {
		imagePath = filepath.Join(h.cfg.Output.BaseDir, imagePath)
	}

	result, err := h.pythonBridge.SegmentImage(c.Request.Context(), imagePath, req.Method)
	if err != nil {
		switch {
		case errors.Is(err, services.ErrPathOutsideBase):
			// 业务校验：文案不含路径细节，可直接回传
			c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
		case errors.Is(err, services.ErrPythonTimeout):
			// 超时：文案只含预算提示，不含路径，保持既有 504 契约
			c.JSON(http.StatusGatewayTimeout, models.Response{Success: false, Error: err.Error()})
		default:
			h.serverError(c, "图像分层", err)
		}
		return
	}

	// 补充前端可直接使用的 URL 字段
	layers, _ := result["layers"].([]interface{})
	outLayers := make([]map[string]interface{}, 0, len(layers))
	for _, l := range layers {
		lm, ok := l.(map[string]interface{})
		if !ok {
			continue
		}
		p, _ := lm["path"].(string)
		lm["url"] = services.OutputURLFor(h.cfg, p)
		outLayers = append(outLayers, lm)
	}
	result["layers"] = outLayers
	result["layer_count"] = len(outLayers)
	if psdPath, _ := result["psd_path"].(string); psdPath != "" {
		result["psd_url"] = services.OutputURLFor(h.cfg, psdPath)
	}
	if cp, _ := result["composite_preview"].(string); cp != "" {
		result["composite_preview_url"] = services.OutputURLFor(h.cfg, cp)
	}
	result["source_image_url"] = services.OutputURLFor(h.cfg, imagePath)
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "分层完成", Data: result})
}

// AddCharacterReference 为角色登记某个视角的参考图（先经 /api/upload 上传），
// 并尽力触发 Python 侧 embedding 提取；提取失败不影响路径落卡。
func (h *Handler) AddCharacterReference(c *gin.Context) {
	characterID := c.Param("id")
	var req struct {
		ImagePath string `json:"image_path" binding:"required"`
		View      string `json:"view"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	if req.View == "" {
		req.View = "front"
	}
	if _, err := h.charSvc.GetCharacter(characterID); err != nil {
		c.JSON(http.StatusNotFound, models.Response{Success: false, Error: err.Error()})
		return
	}
	if err := services.ValidateUploadedImagePath(h.cfg, req.ImagePath); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
		return
	}

	card, err := h.charSvc.UpdateReference(characterID, req.View, req.ImagePath)
	if err != nil {
		h.serverError(c, "登记参考图", err)
		return
	}
	// embedding 提取尽力而为：失败时参考图路径已在卡上，前端可稍后重算
	embedErr := h.pythonBridge.AddReferenceImage(c.Request.Context(), characterID, req.ImagePath, req.View)
	data := gin.H{"card": card, "embedding_extracted": embedErr == nil}
	if embedErr != nil {
		data["embedding_note"] = "参考图已登记，但 embedding 提取失败（可稍后重算）"
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "参考图已登记", Data: data})
}

// RecomputeCharacterEmbedding 为角色重新提取视觉 embedding。
func (h *Handler) RecomputeCharacterEmbedding(c *gin.Context) {
	characterID := c.Param("id")
	if _, err := h.charSvc.GetCharacter(characterID); err != nil {
		c.JSON(http.StatusNotFound, models.Response{Success: false, Error: err.Error()})
		return
	}
	result, err := h.pythonBridge.RecomputeCharacterEmbedding(c.Request.Context(), characterID)
	if err != nil {
		if errors.Is(err, services.ErrPythonTimeout) {
			c.JSON(http.StatusGatewayTimeout, models.Response{Success: false, Error: err.Error()})
			return
		}
		h.serverError(c, "embedding 重算", err)
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "embedding 已更新", Data: result})
}

// ======================================================================
// 上游生成服务状态 / 外部素材导入（PSD + 多 PNG）
// ======================================================================

// ListImageProviders 报告图像生成的上游路由状态：哪些 provider 已配置可用、
// 哪些已注册但缺 Key。前端生成页据此展示可选项，避免选中后才发现没 Key。
func (h *Handler) ListImageProviders(c *gin.Context) {
	available, registered, err := h.pythonBridge.ListImageProviders(c.Request.Context())
	if err != nil {
		// 查询失败不阻塞前端：给前端固定的业务状态，错误详情（可能含本机
		// 路径/上游原文）只记日志，不随响应体下发。
		log.Printf("[error] 上游状态查询: %v", err)
		c.JSON(http.StatusOK, models.Response{
			Success: true,
			Message: "上游状态暂不可用",
			Data:    map[string]interface{}{"available": []map[string]interface{}{}, "registered": []string{}, "query_ok": false},
		})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Data: map[string]interface{}{
		"available":  available,
		"registered": registered,
		"query_ok":   true,
	}})
}

// ListCapabilities 返回可选重型能力的可导入性 + 最近一次下载任务状态。
func (h *Handler) ListCapabilities(c *gin.Context) {
	probe, err := h.capMgr.Probe(c.Request.Context(), h.pythonBridge)
	if err != nil {
		log.Printf("[error] 能力探测: %v", err)
		probe = map[string]interface{}{"capabilities": []interface{}{}, "note": "探测暂不可用"}
	}
	if groups, ok := probe["capabilities"].([]interface{}); ok {
		for _, g := range groups {
			if m, ok := g.(map[string]interface{}); ok {
				if name, ok := m["group"].(string); ok {
					if job := h.capMgr.Status(name); job != nil {
						m["job_status"] = job.Status
						m["job_tail"] = job.Tail
						m["job_error"] = job.Error
					}
				}
			}
		}
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Data: probe})
}

// DownloadCapability 启动某重型能力组的后台 pip 安装（白名单校验）。
func (h *Handler) DownloadCapability(c *gin.Context) {
	var req struct {
		Group string `json:"group" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	job, err := h.capMgr.Download(req.Group)
	if err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "下载任务已启动", Data: job})
}

// CapabilityStatus 查询某组最近一次下载任务状态（轮询用）。
func (h *Handler) CapabilityStatus(c *gin.Context) {
	group := c.Param("group")
	job := h.capMgr.Status(group)
	if job == nil {
		c.JSON(http.StatusOK, models.Response{Success: true, Data: map[string]interface{}{"group": group, "status": "idle"}})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Data: job})
}

// ListExportedModels 列出 output 下全部已导出的 Live2D 模型（新→旧），
// 供预览页的模型选择列表使用。
func (h *Handler) ListExportedModels(c *gin.Context) {
	modelList := services.ListExportedModels(h.cfg)
	c.JSON(http.StatusOK, models.Response{Success: true, Data: map[string]interface{}{
		"models": modelList,
		"count":  len(modelList),
	}})
}

// ImportPSD 接收外部 PSD 文件，提取全部像素图层为整幅画布 PNG，
// 返回图层列表 + 图层目录，供分层工作台与后续导出直接使用。
func (h *Handler) ImportPSD(c *gin.Context) {
	fh, err := c.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "缺少上传文件字段 file"})
		return
	}
	if ext := strings.ToLower(filepath.Ext(fh.Filename)); ext != ".psd" {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "仅支持 .psd 文件"})
		return
	}
	if fh.Size > 500<<20 {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "PSD 文件超过 500MB 限制"})
		return
	}
	// 先落到 uploads，再交给 psd-tools 解析
	path, err := services.SavePSD(h.cfg, fh)
	if err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
		return
	}
	result, err := h.pythonBridge.ImportPSDLayers(c.Request.Context(), path)
	if err != nil {
		if errors.Is(err, services.ErrPythonTimeout) {
			c.JSON(http.StatusGatewayTimeout, models.Response{Success: false, Error: err.Error()})
			return
		}
		h.serverError(c, "PSD 图层导入", err)
		return
	}
	layers, _ := result["layers"].([]interface{})
	for _, l := range layers {
		if lm, ok := l.(map[string]interface{}); ok {
			if p, _ := lm["path"].(string); p != "" {
				lm["url"] = services.OutputURLFor(h.cfg, p)
			}
		}
	}
	result["psd_url"] = services.OutputURLFor(h.cfg, path)
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "PSD 导入完成", Data: result})
}

// ImportPNGs 接收多张 PNG（multipart 字段 files，可多选），按提交顺序
// 组装成一个图层集目录，供分层工作台与后续导出使用。
func (h *Handler) ImportPNGs(c *gin.Context) {
	form, err := c.MultipartForm()
	if err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "解析 multipart 失败: " + err.Error()})
		return
	}
	files := form.File["files"]
	if len(files) == 0 {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "缺少上传文件字段 files"})
		return
	}
	dir, layers, err := services.ImportPNGSet(h.cfg, files)
	if err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
		return
	}
	for _, l := range layers {
		if p, ok := l["path"].(string); ok {
			l["url"] = services.OutputURLFor(h.cfg, p)
		}
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "PNG 导入完成", Data: map[string]interface{}{
		"layers_dir":  dir,
		"layers":      layers,
		"layer_count": len(layers),
	}})
}

// GetAPIInfo 获取 API 信息
func (h *Handler) GetAPIInfo(c *gin.Context) {
	// 能力清单按实际探测生成：不再固定宣称「人脸追踪 / 请求缓存」可用。
	features := []string{
		"角色一致性系统",
		"语义分割分层",
		"LLM流式聊天(SSE)",
		"WebSocket实时通信",
		"Live2D模型导出",
		"Gzip压缩",
	}
	if h.nativeBridge != nil && h.nativeBridge.IsAvailable() {
		features = append(features, "原生人脸追踪")
	}
	if h.cache != nil {
		features = append(features, "请求缓存")
	}

	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Data: map[string]interface{}{
			"name":        "Live2D Master Agent API",
			"version":     Version,
			"description": "AI角色生成、一致性维护、LLM聊天、Live2D导出 API",
			"features":    features,
			"notes": []string{
				"See-through 抠图未实现：POST /api/see-through 返回 501",
				"/api/models 与 /api/expressions 是内置示例列表，不代表当前加载的模型",
			},
			"endpoints": []map[string]string{
				{"method": "GET", "path": "/api/health", "desc": "健康检查"},
				{"method": "GET", "path": "/api/status", "desc": "系统状态"},
				{"method": "GET", "path": "/api/info", "desc": "API信息"},
				{"method": "GET", "path": "/api/models", "desc": "可用模型列表"},
				{"method": "POST", "path": "/api/generate", "desc": "生成图片（兼容旧版）"},
				{"method": "POST", "path": "/api/generate/character", "desc": "生成角色（含一致性）"},
				{"method": "GET", "path": "/api/characters", "desc": "角色列表"},
				{"method": "POST", "path": "/api/characters", "desc": "创建角色"},
				{"method": "GET", "path": "/api/characters/:id", "desc": "获取角色详情"},
				{"method": "PUT", "path": "/api/characters/:id", "desc": "更新角色"},
				{"method": "DELETE", "path": "/api/characters/:id", "desc": "删除角色"},
				{"method": "POST", "path": "/api/chat", "desc": "LLM聊天"},
				{"method": "POST", "path": "/api/chat/stream", "desc": "LLM聊天(SSE流式)"},
				{"method": "GET", "path": "/api/ws", "desc": "WebSocket连接"},
				{"method": "POST", "path": "/api/export/live2d", "desc": "导出Live2D模型"},
				{"method": "GET", "path": "/api/expressions", "desc": "表情列表"},
				{"method": "POST", "path": "/api/psd-plan", "desc": "PSD分层规划"},
				{"method": "POST", "path": "/api/see-through", "desc": "See-through工作流"},
			},
		},
	})
}

// GetCacheStats 获取缓存统计
func (h *Handler) GetCacheStats(c *gin.Context) {
	if h.cache == nil {
		c.JSON(http.StatusOK, models.Response{
			Success: true,
			Data: map[string]interface{}{
				"enabled": false,
				"message": "缓存服务未启用",
			},
		})
		return
	}

	stats := h.cache.Stats()
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Data:    stats,
	})
}

// ClearCache 清除缓存
func (h *Handler) ClearCache(c *gin.Context) {
	if h.cache == nil {
		c.JSON(http.StatusOK, models.Response{
			Success: true,
			Message: "缓存服务未启用",
		})
		return
	}

	h.cache.Clear()
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "缓存已清除",
	})
}

// ======================================================================
// v0.10.0: 角色管理 API
// ======================================================================

// ListCharacters 获取角色列表
func (h *Handler) ListCharacters(c *gin.Context) {
	chars, err := h.charSvc.ListCharacters()
	if err != nil {
		h.serverError(c, "角色列表读取", err)
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Data: chars})
}

// CreateCharacter 创建角色
func (h *Handler) CreateCharacter(c *gin.Context) {
	var req models.CharacterRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	// ref_image 必须来自 /api/upload 产物（output/uploads 内），与
	// AddCharacterReference 入口同一道防线；否则会把任意服务端可读文件
	// 经 Python 复制进 assets（本地文件访问控制缺口）。
	if req.RefImage != "" {
		if err := services.ValidateUploadedImagePath(h.cfg, req.RefImage); err != nil {
			c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
			return
		}
	}
	card, err := h.charSvc.CreateCharacter(req)
	if err != nil {
		h.serverError(c, "角色创建", err)
		return
	}
	c.JSON(http.StatusCreated, models.Response{Success: true, Message: "角色创建成功", Data: card})
}

// GetCharacter 获取角色详情
func (h *Handler) GetCharacter(c *gin.Context) {
	id := c.Param("id")
	card, err := h.charSvc.GetCharacter(id)
	if err != nil {
		c.JSON(http.StatusNotFound, models.Response{Success: false, Error: "角色不存在"})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Data: card})
}

// UpdateCharacter 更新角色
func (h *Handler) UpdateCharacter(c *gin.Context) {
	id := c.Param("id")
	var req models.CharacterRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	card, err := h.charSvc.UpdateCharacter(id, req)
	if err != nil {
		h.serverError(c, "角色更新", err)
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "角色已更新", Data: card})
}

// DeleteCharacter 删除角色
func (h *Handler) DeleteCharacter(c *gin.Context) {
	id := c.Param("id")
	if err := h.charSvc.DeleteCharacter(id); err != nil {
		h.serverError(c, "角色删除", err)
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "角色已删除"})
}

// ======================================================================
// v0.10.0: LLM 聊天 API (SSE 流式)
// ======================================================================

// ChatStream 流式聊天 (SSE)
func (h *Handler) ChatStream(c *gin.Context) {
	var req models.ChatRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}

	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("Connection", "keep-alive")
	c.Header("X-Accel-Buffering", "no")

	c.Status(http.StatusOK)

	flusher, ok := c.Writer.(http.Flusher)
	if !ok {
		c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: "streaming not supported"})
		return
	}

	// Stream chat via chat service
	err := h.chatSvc.ChatStream(req, func(chunk services.ChatStreamChunk) {
		data, _ := json.Marshal(chunk)
		c.Writer.Write([]byte("data: " + string(data) + "\n\n"))
		flusher.Flush()
	})

	if err != nil {
		// 流已开始无法改状态码：异常详情只进日志，推给浏览器固定文案。
		log.Printf("[error] 流式聊天: %v", err)
		errChunk := services.ChatStreamChunk{Type: "error", Error: "聊天服务暂不可用，请稍后重试"}
		data, _ := json.Marshal(errChunk)
		c.Writer.Write([]byte("data: " + string(data) + "\n\n"))
		flusher.Flush()
	}

	doneChunk := services.ChatStreamChunk{Type: "done", Finished: true}
	data, _ := json.Marshal(doneChunk)
	c.Writer.Write([]byte("data: " + string(data) + "\n\n"))
	flusher.Flush()
}

// ======================================================================
// v0.10.0: WebSocket 连接
// ======================================================================

// WSHandle WebSocket 连接处理
func (h *Handler) WSHandle(c *gin.Context) {
	h.wsHub.HandleConnection(c)
}

// ======================================================================
// v0.10.0: Live2D 模型导出
// ======================================================================

// exportFailureResponse 把导出失败映射成对外状态码。
//
// 超时不是模型的错，也不该伪装成 500：回 504 并带上预算，调用方才能区分
// 「素材太大要改异步」和「构建真的坏了」。
func (h *Handler) exportFailureResponse(c *gin.Context, err error) {
	if errors.Is(err, services.ErrPythonTimeout) {
		c.JSON(http.StatusGatewayTimeout, models.Response{
			Success: false,
			Error:   err.Error(),
			Data: map[string]interface{}{
				"blocker": fmt.Sprintf("导出在 %s 预算内没有完成",
					h.exportTimeoutBudget()),
				"timeout_budget_seconds": int(h.exportTimeoutBudget().Seconds()),
			},
		})
		return
	}
	// 默认 500：导出构建错误原文可能含临时目录/内部路径，只记日志，
	// 客户端拿固定文案（504 超时分支在上面保留含预算的业务提示）。
	log.Printf("[error] Live2D 导出: %v", err)
	c.JSON(http.StatusInternalServerError, models.Response{
		Success: false, Error: "导出服务暂不可用，请稍后重试",
	})
}

// exportTimeoutBudget 返回当前配置的完整导出预算。
func (h *Handler) exportTimeoutBudget() time.Duration {
	if h.cfg == nil {
		return 150 * time.Second
	}
	return h.cfg.GetExportTimeout()
}

// ExportLive2D 导出 Live2D 模型
func (h *Handler) ExportLive2D(c *gin.Context) {
	var req models.ExportModelRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}

	if req.CharacterID == "" {
		req.CharacterID = "character"
	}

	// Zip download mode (used by the web preview's Export button).
	if req.Download {
		modelDir, err := h.resolveExportModelDir(c.Request.Context(), req)
		if err != nil {
			if errors.Is(err, services.ErrPythonTimeout) {
				h.exportFailureResponse(c, err)
			} else {
				c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
			}
			return
		}
		zipBytes, err := zipDirectory(modelDir)
		if err != nil {
			h.serverError(c, "模型打包", err)
			return
		}
		if len(zipBytes) == 0 {
			c.JSON(http.StatusNotFound, models.Response{Success: false, Error: "模型目录为空或不存在"})
			return
		}
		c.Header("Content-Disposition",
			"attachment; filename=live2d_model.zip")
		c.Data(http.StatusOK, "application/zip", zipBytes)
		return
	}

	// JSON mode (backward compatible).
	result, err := h.exportLive2DModelTracked(c.Request.Context(), req.CharacterID, req.LayersDir, req.OutputDir)
	if err != nil {
		h.exportFailureResponse(c, err)
		return
	}
	data := exportResponseData(result)
	if ok, _ := data["success"].(bool); !ok {
		message, _ := data["message"].(string)
		if message == "" {
			message = "导出未产生模型"
		}
		c.JSON(http.StatusUnprocessableEntity, models.Response{
			Success: false, Error: message, Data: data,
		})
		return
	}
	// 导出成功：把模型产物并入最近生成记录 —— 这是「导出 → 预览 / 桌宠」
	// 的交接点，预览页自动加载与桌宠部署的 model_dir 都来自这份记录。
	model3JSON, _ := data["model3_json"].(string)
	if model3JSON == "" {
		if m, ok := data["model_dir"].(string); ok {
			model3JSON = filepath.Join(m, filepath.Base(m)+".model3.json")
		}
	}
	outputDir, _ := data["output_dir"].(string)
	if outputDir == "" && model3JSON != "" {
		outputDir = filepath.Dir(model3JSON)
	}
	runtimeReady, _ := data["runtime_ready"].(bool)
	services.RecordLatestExport(h.cfg, model3JSON, outputDir, runtimeReady)
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "模型导出成功", Data: data})
}

// unwrapPythonResult 摊平 runInlinePython 的 {"result": ...} 包装。
//
// 桥接层总是把 Python 返回的字典塞在 "result" 键下，直接读顶层键的代码
// 因此永远读不到值（导出下载模式就是这样丢掉 output_dir 的）。
func unwrapPythonResult(raw map[string]interface{}) map[string]interface{} {
	if inner, ok := raw["result"].(map[string]interface{}); ok {
		return inner
	}
	return raw
}

// exportResponseData 把 Python 的导出结果整理成对外 data 字段。
//
// moc3 的编译 / 验收状态摊平成前端直接读取的 runtime_ready 与 blocker；
// 任何缺字段的情况一律按「未就绪」处理，不默认成功。
func exportResponseData(raw map[string]interface{}) map[string]interface{} {
	inner := unwrapPythonResult(raw)
	data := make(map[string]interface{}, len(inner)+3)
	for key, value := range inner {
		if key != "moc3" {
			data[key] = value
		}
	}
	moc3, _ := inner["moc3"].(map[string]interface{})
	data["moc3"] = moc3
	data["runtime_ready"], data["blocker"] = moc3Readiness(inner, moc3)
	return data
}

func moc3Readiness(inner, moc3 map[string]interface{}) (bool, string) {
	if ready, _ := moc3["runtime_ready"].(bool); ready {
		return true, ""
	}
	for _, candidate := range []string{
		stringField(moc3, "moc3_blocker"),
		stringField(inner, "message"),
	} {
		if candidate != "" {
			return false, candidate
		}
	}
	return false, "导出结果缺少 moc3 就绪信息：未经官方 Cubism Core 验收，不能视为可部署"
}

func stringField(values map[string]interface{}, key string) string {
	if text, ok := values[key].(string); ok {
		return text
	}
	return ""
}

// resolveExportModelDir determines the on-disk model directory to zip.
//
// Precedence:
//  1. An explicit model_dir (resolved under Output.BaseDir, traversal-safe).
//  2. A fresh export via the Python bridge; its output_dir is used.
func (h *Handler) resolveExportModelDir(ctx context.Context, req models.ExportModelRequest) (string, error) {
	if req.ModelDir != "" {
		candidate := req.ModelDir
		if !filepath.IsAbs(candidate) {
			candidate = filepath.Join(h.cfg.Output.BaseDir, candidate)
		}
		candidate = filepath.Clean(candidate)
		if !isPathSafe(candidate, h.cfg.Output.BaseDir) {
			return "", fmt.Errorf("非法的模型目录")
		}
		info, err := os.Stat(candidate)
		if err != nil || !info.IsDir() {
			return "", fmt.Errorf("模型目录不存在: %s", req.ModelDir)
		}
		return candidate, nil
	}

	result, err := h.exportLive2DModelTracked(ctx, req.CharacterID, req.LayersDir, req.OutputDir)
	if err != nil {
		return "", err
	}
	inner := unwrapPythonResult(result)
	if d := stringField(inner, "output_dir"); d != "" {
		return d, nil
	}
	if d := stringField(inner, "model3_json"); d != "" {
		return filepath.Dir(d), nil
	}
	return "", fmt.Errorf("导出未产生模型目录")
}

// ExportPSD 导出 PSD 分层文件
func (h *Handler) ExportPSD(c *gin.Context) {
	var req models.PSDLayerRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	result, err := h.pythonBridge.CreatePSDPlan(c.Request.Context(), req.ImagePath)
	if err != nil {
		switch {
		case errors.Is(err, services.ErrPathOutsideBase):
			c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
		case errors.Is(err, services.ErrPythonTimeout):
			c.JSON(http.StatusGatewayTimeout, models.Response{Success: false, Error: err.Error()})
		default:
			h.serverError(c, "PSD导出", err)
		}
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "PSD导出成功", Data: result})
}

// StartTracking 启动原生面捕。
//
// 如果 Rust face_tracker 已编译且可用，启动子进程面捕；
// 否则回退到显式 501（同原 TrackingUnavailable 语义）。
func (h *Handler) StartTracking(c *gin.Context) {
	// 配置级 kill-switch：native_tracker.enabled=false 时禁用面捕
	if !h.cfg.NativeTracker.Enabled {
		c.JSON(http.StatusNotImplemented, models.Response{
			Success: false,
			Error:   "原生面捕已在配置中禁用（native_tracker.enabled=false）",
		})
		return
	}

	if !h.nativeBridge.IsAvailable() {
		c.JSON(http.StatusNotImplemented, models.Response{
			Success: false,
			Error:   "原生面捕未编译。请运行：cd native/face_tracker && cargo build --release",
			Data: map[string]interface{}{
				"backend":   "go",
				"available": false,
				"hint":      "cd native/face_tracker && cargo build --release",
			},
		})
		return
	}

	if h.nativeBridge.IsRunning() {
		c.JSON(http.StatusOK, models.Response{
			Success: true,
			Message: "面捕已在运行",
		})
		return
	}

	if err := h.nativeBridge.StartTracking(); err != nil {
		c.JSON(http.StatusInternalServerError, models.Response{
			Success: false,
			Error:   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "原生面捕已启动",
		Data: map[string]interface{}{
			"source":    "native",
			"camera_id": h.cfg.NativeTracker.CameraID,
			"width":     h.cfg.NativeTracker.Width,
			"height":    h.cfg.NativeTracker.Height,
			"fps":       h.cfg.NativeTracker.FPS,
		},
	})
}

// StopTracking 停止原生面捕。
func (h *Handler) StopTracking(c *gin.Context) {
	if !h.nativeBridge.IsRunning() {
		c.JSON(http.StatusOK, models.Response{
			Success: true,
			Message: "面捕未在运行",
		})
		return
	}

	if err := h.nativeBridge.StopTracking(); err != nil {
		c.JSON(http.StatusInternalServerError, models.Response{
			Success: false,
			Error:   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "面捕已停止",
	})
}

// TrackingStatus 返回面捕状态。
func (h *Handler) TrackingStatus(c *gin.Context) {
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Data: map[string]interface{}{
			"running":   h.nativeBridge.IsRunning(),
			"available": h.nativeBridge.IsAvailable(),
			"enabled":   h.cfg.NativeTracker.Enabled,
			"source":    "native",
			"camera_id": h.cfg.NativeTracker.CameraID,
			"smoothing": h.cfg.NativeTracker.Smoothing,
			"deadzone":  h.cfg.NativeTracker.Deadzone,
		},
	})
}

// ShutdownNativeBridge 彻底关闭面捕子进程（供 main.go 退出信号处理调用）。
func (h *Handler) ShutdownNativeBridge() {
	h.nativeBridge.Shutdown()
}

// DeployDesktop verifies model pixels, then waits for the native pet first frame.
func (h *Handler) DeployDesktop(c *gin.Context) {
	var req models.ExportModelRequest
	if err := c.ShouldBindJSON(&req); err != nil || req.ModelDir == "" {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Message: "需要提供真实导出模型的 model_dir"})
		return
	}
	result, err := h.pythonBridge.DeployDesktopModel(
		c.Request.Context(), req.ModelDir, h.petTrackingAttach(),
	)
	if err != nil {
		c.JSON(http.StatusUnprocessableEntity, models.Response{Success: false, Message: err.Error(), Data: map[string]interface{}{"deployed": false, "runtime_verified": false}})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "桌宠已完成真实模型验收并绘制首帧；桌面透明外观仍需视觉确认", Data: result})
}

// petTrackingAttach 返回桌宠参数源接入函数：把原生面捕的每帧参数以 JSON Lines
// 写进桌宠 stdin，让桌宠跟随真实表情与头部姿态。
//
// 面捕不可用（二进制缺失/配置禁用/无摄像头/启动失败）时静默降级——桌宠退回
// 自带待机动画；若本次部署启动了面捕，则桌宠退出时一并关闭，避免摄像头常亮。
func (h *Handler) petTrackingAttach() func(io.Writer) func() {
	if h.nativeBridge == nil {
		return nil
	}
	return func(stdin io.Writer) func() {
		if !h.nativeBridge.IsAvailable() {
			log.Printf("[deploy] 未找到原生面捕二进制，桌宠将使用自带待机动画")
			return func() {}
		}
		owned := !h.nativeBridge.IsRunning()
		if owned {
			if err := h.nativeBridge.StartTracking(); err != nil {
				log.Printf("[deploy] 原生面捕启动失败，桌宠将使用自带待机动画: %v", err)
				return func() {}
			}
		}
		encoder := json.NewEncoder(stdin)
		h.nativeBridge.SetFrameSink(func(params map[string]float64) {
			// 编码失败（桌宠已退出/管道关闭）无需处理：下一帧或拆除逻辑兜底。
			_ = encoder.Encode(map[string]interface{}{"live2d_params": params})
		})
		log.Printf("[deploy] 桌宠已接入原生面捕（本次启动=%v）", owned)
		return func() {
			h.nativeBridge.SetFrameSink(nil)
			if owned {
				_ = h.nativeBridge.StopTracking()
			}
		}
	}
}

func (h *Handler) DesktopStatus(c *gin.Context) {
	c.JSON(http.StatusOK, models.Response{Success: true, Data: services.DesktopDeploymentStatus()})
}

// WSProgress WebSocket 进度推送端点（/ws/progress 的兼容别名）。
func (h *Handler) WSProgress(c *gin.Context) {
	h.wsHub.HandleConnection(c)
}

// GetExpressions 获取可用表情列表
func (h *Handler) GetExpressions(c *gin.Context) {
	expressions := []models.ExpressionInfo{
		{Name: "neutral", ID: "neutral", Params: []string{}},
		{Name: "smile", ID: "smile", Params: []string{"ParamMouthForm"}},
		{Name: "happy", ID: "happy", Params: []string{"ParamMouthForm", "ParamCheek"}},
		{Name: "angry", ID: "angry", Params: []string{"ParamBrowLY", "ParamBrowRY"}},
		{Name: "sad", ID: "sad", Params: []string{"ParamBrowLY", "ParamBrowRY", "ParamEyeLOpen"}},
		{Name: "surprised", ID: "surprised", Params: []string{"ParamEyeLOpen", "ParamMouthOpenY"}},
		{Name: "shy", ID: "shy", Params: []string{"ParamCheek"}},
		{Name: "wink_left", ID: "wink_l", Params: []string{"ParamEyeLOpen"}},
		{Name: "wink_right", ID: "wink_r", Params: []string{"ParamEyeROpen"}},
		{Name: "blink", ID: "blink", Params: []string{"ParamEyeLOpen", "ParamEyeROpen"}},
	}
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		// 诚实性标注：该列表与当前加载的模型无关，是内置示例。
		Message: "内置示例表情列表（不随当前加载模型变化）",
		Data:    expressions,
	})
}
