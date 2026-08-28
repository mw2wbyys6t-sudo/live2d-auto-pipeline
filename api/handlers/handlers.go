package handlers

import (
	"encoding/json"
	"fmt"
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

type Handler struct {
	cfg            *config.Config
	imageGenerator *services.ImageGenerator
	pythonBridge   *services.PythonBridge
	cache          *services.RequestCache
	charSvc        *services.CharacterService
	chatSvc        *services.ChatService
	chatSessions   *services.ChatSessionStore
	wsHub          *services.WSHub
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
		chatSessions:   services.NewChatSessionStore(),
		wsHub:          services.NewWSHub(),
		startTime:      time.Now(),
	}

	// 启动缓存清理守护进程
	if cache != nil {
		cache.StartCleanupDaemon(5 * time.Minute)
	}

	// 启动 WebSocket hub
	go h.wsHub.Run()

	return h
}

// apiVersion returns the configured API version, falling back to the
// canonical constant when the config value is empty. This is the single
// source of truth for the version string — no hardcoded literals.
func (h *Handler) apiVersion() string {
	if h.cfg != nil && h.cfg.Version != "" {
		return h.cfg.Version
	}
	return config.APIVersion
}

// HealthCheck 健康检查
func (h *Handler) HealthCheck(c *gin.Context) {
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "Live2D API 服务正常运行",
		Data: map[string]interface{}{
			"version": h.apiVersion(),
			"uptime":  time.Since(h.startTime).String(),
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
			Version:  h.apiVersion(),
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
			c.JSON(http.StatusInternalServerError, models.Response{
				Success: false,
				Error:   "图片生成失败: " + err.Error(),
			})
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

// GenerateCharacter v10.0: 增强版生成（角色一致性 + 语义分割 + Live2D导出）
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
		h.wsHub.BroadcastProgress(taskID, "error", 0, err.Error())
		c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: "生成失败: " + err.Error()})
		return
	}
	h.wsHub.BroadcastProgress(taskID, "done", 100, "生成完成！")
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "角色生成成功", Data: result})
}

// Chat 非流式聊天
func (h *Handler) Chat(c *gin.Context) {
	req, ctx, err := h.getChatContext(c)
	if err != nil {
		return
	}
	resp, err := h.chatSvc.Chat(req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: "聊天失败: " + err.Error()})
		return
	}
	// Record the exchange in the shared session (best-effort).
	h.chatSessions.AppendHistory(ctx.SessionID,
		models.ChatMessage{Role: "user", Content: req.Message},
		models.ChatMessage{Role: "assistant", Content: resp.Reply},
	)
	c.JSON(http.StatusOK, models.Response{Success: true, Data: resp})
}

// GetModels 获取可用模型列表
func (h *Handler) GetModels(c *gin.Context) {
	availableModels := h.imageGenerator.GetAvailableModels()
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "获取模型列表成功",
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

	result, err := h.pythonBridge.CreatePSDPlan(req.ImagePath)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.Response{
			Success: false,
			Error:   "PSD分层失败: " + err.Error(),
		})
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
		c.JSON(http.StatusInternalServerError, models.Response{
			Success: false,
			Error:   "See-through 工作流启动失败: " + err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "See-through 工作流状态",
		Data:    result,
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

// ServeOutput 提供输出文件访问（支持子目录，如模型目录中的 layers/*.png）
func (h *Handler) ServeOutput(c *gin.Context) {
	// Wildcard param is like "/rigged_x/char.model3.json"; strip leading "/".
	rel := strings.TrimPrefix(c.Param("filepath"), "/")
	if rel == "" {
		c.JSON(http.StatusBadRequest, models.Response{
			Success: false,
			Error:   "文件名不能为空",
		})
		return
	}

	// 安全检查：防止目录遍历 / 绝对路径 / 空字节注入
	if filepath.IsAbs(rel) || strings.ContainsRune(rel, 0) ||
		strings.Contains(rel, "..") {
		c.JSON(http.StatusForbidden, models.Response{
			Success: false,
			Error:   "非法的文件路径",
		})
		return
	}
	filePath := filepath.Join(h.cfg.Output.BaseDir, rel)
	if !isPathSafe(filePath, h.cfg.Output.BaseDir) {
		c.JSON(http.StatusForbidden, models.Response{
			Success: false,
			Error:   "非法的文件路径",
		})
		return
	}

	// 检查文件是否存在
	info, err := os.Stat(filePath)
	if os.IsNotExist(err) || info.IsDir() {
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

// isPathSafe 检查路径是否在允许的目录内。
//
// 防御三类攻击：
//  1. 空字节注入（"\x00" 截断路径）——直接拒绝；
//  2. 绝对路径逃逸——任何解析后落在 baseDir 之外的路径都拒绝；
//  3. ".." 目录遍历——通过 filepath.Rel 计算相对路径，拒绝任何以
//     ".." 开头的结果（含 "../x" 跨目录形式）。
func isPathSafe(path, baseDir string) bool {
	// 1. 空字节注入
	if strings.ContainsRune(path, 0) || strings.ContainsRune(baseDir, 0) {
		return false
	}

	absPath, err := filepath.Abs(filepath.Clean(path))
	if err != nil {
		return false
	}
	absBase, err := filepath.Abs(filepath.Clean(baseDir))
	if err != nil {
		return false
	}

	// 2. 绝对路径逃逸 / 3. 目录遍历：统一用相对路径判定 containment。
	rel, err := filepath.Rel(absBase, absPath)
	if err != nil {
		return false
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return false
	}
	// 不同卷（Windows）或其它绝对残留
	if filepath.IsAbs(rel) {
		return false
	}
	return true
}

// GetAPIInfo 获取 API 信息
func (h *Handler) GetAPIInfo(c *gin.Context) {
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Data: map[string]interface{}{
			"name":        "Live2D Master Agent API",
			"version":     h.apiVersion(),
			"description": "AI角色生成、一致性维护、LLM聊天、Live2D导出 API",
			"features": []string{
				"角色一致性系统",
				"语义分割分层",
				"LLM流式聊天(SSE)",
				"WebSocket实时通信",
				"Live2D模型导出",
				"人脸追踪",
				"请求缓存",
				"Gzip压缩",
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
// v10.0: 角色管理 API
// ======================================================================

// ListCharacters 获取角色列表
func (h *Handler) ListCharacters(c *gin.Context) {
	chars, err := h.charSvc.ListCharacters()
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: err.Error()})
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
	card, err := h.charSvc.CreateCharacter(req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: err.Error()})
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
		c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: err.Error()})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "角色已更新", Data: card})
}

// DeleteCharacter 删除角色
func (h *Handler) DeleteCharacter(c *gin.Context) {
	id := c.Param("id")
	if err := h.charSvc.DeleteCharacter(id); err != nil {
		c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: err.Error()})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "角色已删除"})
}

// ======================================================================
// v10.0: LLM 聊天 API (SSE 流式)
// ======================================================================

// ChatStream 流式聊天 (SSE)
func (h *Handler) ChatStream(c *gin.Context) {
	req, ctx, err := h.getChatContext(c)
	if err != nil {
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

	// Stream chat via chat service; accumulate the reply so it can be
	// persisted into the shared chat session. The service emits its own
	// terminal "done" chunk; we only add one if it doesn't.
	var replyBuild strings.Builder
	sentDone := false
	streamErr := h.chatSvc.ChatStream(req, func(chunk services.ChatStreamChunk) {
		if chunk.Type == "token" {
			replyBuild.WriteString(chunk.Content)
		}
		if chunk.Type == "done" {
			sentDone = true
		}
		data, _ := json.Marshal(chunk)
		c.Writer.Write([]byte("data: " + string(data) + "\n\n"))
		flusher.Flush()
	})

	if streamErr != nil {
		errChunk := services.ChatStreamChunk{Type: "error", Error: streamErr.Error()}
		data, _ := json.Marshal(errChunk)
		c.Writer.Write([]byte("data: " + string(data) + "\n\n"))
		flusher.Flush()
	}

	// Persist the exchange into the session (best-effort, never breaks SSE).
	h.chatSessions.AppendHistory(ctx.SessionID,
		models.ChatMessage{Role: "user", Content: req.Message},
		models.ChatMessage{Role: "assistant", Content: replyBuild.String()},
	)

	if !sentDone {
		doneChunk := services.ChatStreamChunk{Type: "done", Finished: true}
		data, _ := json.Marshal(doneChunk)
		c.Writer.Write([]byte("data: " + string(data) + "\n\n"))
		flusher.Flush()
	}
}

// ======================================================================
// v10.0: WebSocket 连接
// ======================================================================

// WSHandle WebSocket 连接处理
func (h *Handler) WSHandle(c *gin.Context) {
	h.wsHub.HandleConnection(c)
}

// ======================================================================
// v10.0: Live2D 模型导出
// ======================================================================

// ExportLive2D 导出 Live2D 模型
//
// 默认返回 JSON 描述（向后兼容）。当请求体 download=true 时，直接将模型目录
// 打包为 zip 并以 application/zip 二进制流返回，供前端"Export Model3"按钮下载。
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
		modelDir, err := h.resolveExportModelDir(req)
		if err != nil {
			c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
			return
		}
		zipBytes, err := zipDirectory(modelDir)
		if err != nil {
			c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: "打包失败: " + err.Error()})
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
	result, err := h.pythonBridge.ExportLive2DModel(req.CharacterID, req.LayersDir, req.OutputDir)
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: err.Error()})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "模型导出成功", Data: result})
}

// resolveExportModelDir determines the on-disk model directory to zip.
//
// Precedence:
//  1. An explicit model_dir (resolved under Output.BaseDir, traversal-safe).
//  2. A fresh export via the Python bridge; its output_dir is used.
func (h *Handler) resolveExportModelDir(req models.ExportModelRequest) (string, error) {
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

	result, err := h.pythonBridge.ExportLive2DModel(req.CharacterID, req.LayersDir, req.OutputDir)
	if err != nil {
		return "", err
	}
	if d, ok := result["output_dir"].(string); ok && d != "" {
		return d, nil
	}
	if d, ok := result["model3_json"].(string); ok && d != "" {
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
	result, err := h.pythonBridge.CreatePSDPlan(req.ImagePath)
	if err != nil {
		// Fail-open: return a structured error rather than crashing.
		c.JSON(http.StatusInternalServerError, models.Response{Success: false, Error: "PSD导出失败: " + err.Error()})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "PSD导出成功", Data: result})
}

// ExportSpine 导出 Spine 格式（占位/兼容端点）。
//
// 本平台以 Live2D Cubism4 为一等导出目标；Spine 导出作为兼容端点返回
// 可用的 model3 产物路径，调用方可据此自行转换，而不会收到 404。
func (h *Handler) ExportSpine(c *gin.Context) {
	var req models.ExportModelRequest
	_ = c.ShouldBindJSON(&req)
	if req.CharacterID == "" {
		req.CharacterID = "character"
	}
	result, err := h.pythonBridge.ExportLive2DModel(req.CharacterID, req.LayersDir, req.OutputDir)
	if err != nil {
		c.JSON(http.StatusOK, models.Response{
			Success: true,
			Message: "Spine 导出暂未启用；已返回 Live2D model3 产物",
			Data:    map[string]interface{}{"format": "spine", "available": false, "live2d": result},
		})
		return
	}
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "Spine 导出暂未启用；已返回 Live2D model3 产物",
		Data:    map[string]interface{}{"format": "spine", "available": false, "live2d": result},
	})
}

// DeployDesktop 部署桌宠包（兼容端点，fail-open）。
func (h *Handler) DeployDesktop(c *gin.Context) {
	var req models.ExportModelRequest
	_ = c.ShouldBindJSON(&req)
	if req.CharacterID == "" {
		req.CharacterID = "character"
	}
	// Best-effort: ensure a model exists, then return a launch descriptor.
	result, err := h.pythonBridge.ExportLive2DModel(req.CharacterID, req.LayersDir, req.OutputDir)
	modelDir := ""
	if err == nil {
		if d, ok := result["output_dir"].(string); ok {
			modelDir = d
		}
	}
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "桌宠部署描述已生成（运行 drivers/desktop_pet 启动）",
		Data: map[string]interface{}{
			"deployed":  err == nil,
			"model_dir": modelDir,
			"launch":    "python -m drivers.desktop_pet.runner",
		},
	})
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
	c.JSON(http.StatusOK, models.Response{Success: true, Data: expressions})
}
