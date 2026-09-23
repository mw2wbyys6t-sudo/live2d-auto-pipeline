package models

import "time"

// 通用响应结构
type Response struct {
	Success bool        `json:"success"`
	Message string      `json:"message,omitempty"`
	Data    interface{} `json:"data,omitempty"`
	Error   string      `json:"error,omitempty"`
}

// 图片生成请求
type GenerateImageRequest struct {
	Prompt      string `json:"prompt" binding:"required"`
	Width       int    `json:"width"`
	Height      int    `json:"height"`
	Seed        int    `json:"seed"`
	ModelID     string `json:"model_id,omitempty"`
	NoLive2DOpt bool   `json:"no_live2d_opt,omitempty"`
	Quality     string `json:"quality,omitempty"`
	Steps       int    `json:"steps,omitempty"`
}

// 图片生成响应
type GenerateImageResponse struct {
	ImagePath    string            `json:"image_path"`
	ImageURL     string            `json:"image_url"`
	Seed         int               `json:"seed"`
	Width        int               `json:"width"`
	Height       int               `json:"height"`
	Source       string            `json:"source"`
	Features     map[string]string `json:"features,omitempty"`
	CreatedAt    time.Time         `json:"created_at"`
	// v0.10.1: 完整工作流产物
	LayersDir    string            `json:"layers_dir,omitempty"`
	PSDPath      string            `json:"psd_path,omitempty"`
	Model3JSON   string            `json:"model3_json,omitempty"`
	OutputDir    string            `json:"output_dir,omitempty"`
	CharacterID  string            `json:"character_id,omitempty"`
	Success      bool              `json:"success"`
	Error        string            `json:"error,omitempty"`
	// 各步骤产物路径
	Steps        map[string]interface{} `json:"steps,omitempty"`
}

// PSD分层请求
type PSDLayerRequest struct {
	ImagePath     string `json:"image_path" binding:"required"`
	OutputDir     string `json:"output_dir,omitempty"`
	UseAI         bool   `json:"use_ai"`
	UseSeeThrough bool   `json:"use_see_through"`
}

// PSD分层响应
type PSDLayerResponse struct {
	PlanDir    string    `json:"plan_dir"`
	PSDPath    string    `json:"psd_path"`
	LayerCount int       `json:"layer_count"`
	Layers     []string  `json:"layers"`
	CreatedAt  time.Time `json:"created_at"`
}

// 服务状态
type ServiceStatus struct {
	Name        string    `json:"name"`
	Available   bool      `json:"available"`
	Version     string    `json:"version,omitempty"`
	LastChecked time.Time `json:"last_checked"`
}

// 系统状态
type SystemStatus struct {
	Services []ServiceStatus `json:"services"`
	Version  string          `json:"version"`
	Uptime   string          `json:"uptime"`
}

// See-through 工作流请求
type SeeThroughRequest struct {
	ImagePath  string `json:"image_path" binding:"required"`
	ComfyUIDir string `json:"comfyui_dir,omitempty"`
}

// See-through 工作流响应
type SeeThroughResponse struct {
	TaskID    string    `json:"task_id"`
	Status    string    `json:"status"`
	OutputDir string    `json:"output_dir,omitempty"`
	Message   string    `json:"message"`
	CreatedAt time.Time `json:"created_at"`
}

// 任务状态（带进度跟踪）
type TaskStatus struct {
	TaskID    string    `json:"task_id"`
	Status    string    `json:"status"`
	Progress  int       `json:"progress"`
	Stage     string    `json:"stage,omitempty"`
	Message   string    `json:"message,omitempty"`
	Result    string    `json:"result,omitempty"`
	Error     string    `json:"error,omitempty"`
	UpdatedAt time.Time `json:"updated_at"`
}

// ======================================================================
// v0.10.0: 角色一致性系统相关类型
// ======================================================================

// CharacterCard 角色卡片 - 与 Python CharacterCard 对应
type CharacterCard struct {
	CharacterID string       `json:"character_id"`
	Name        string       `json:"name"`
	CreatedAt   string       `json:"created_at"`
	UpdatedAt   string       `json:"updated_at"`
	Face        FaceParams   `json:"face"`
	Hair        HairParams   `json:"hair"`
	Body        BodyParams   `json:"body"`
	Palette     PaletteParams `json:"palette"`
	Outfit      OutfitParams `json:"outfit"`
	References  RefPaths     `json:"references"`
	Embedding   []float64    `json:"embedding,omitempty"`
	Persona     PersonaInfo  `json:"persona"`
	Style       StyleInfo    `json:"style"`
}

// FaceParams 面部参数
type FaceParams struct {
	Shape          string  `json:"shape"`
	EyeShape       string  `json:"eye_shape"`
	EyeColor       string  `json:"eye_color"`
	EyeSize        float64 `json:"eye_size"`
	EyebrowShape   string  `json:"eyebrow_shape"`
	NoseType       string  `json:"nose_type"`
	MouthType      string  `json:"mouth_type"`
	FaceWidthRatio float64 `json:"face_width_ratio"`
	FaceHeightRatio float64 `json:"face_height_ratio"`
}

// HairParams 头发参数
type HairParams struct {
	Color          string  `json:"color"`
	Style          string  `json:"style"`
	Length         float64 `json:"length"`
	BangsStyle     string  `json:"bangs_style"`
	HighlightsColor string `json:"highlights_color"`
}

// BodyParams 身体参数
type BodyParams struct {
	Type        string  `json:"type"`
	HeightRatio float64 `json:"height_ratio"`
	Proportions string  `json:"proportions"`
}

// PaletteParams 配色方案
type PaletteParams struct {
	PrimaryColors []string `json:"primary_colors"`
	SkinTone      string   `json:"skin_tone"`
	AccentColor   string   `json:"accent_color"`
}

// OutfitParams 服装
type OutfitParams struct {
	Current  map[string]interface{}   `json:"current"`
	Wardrobe []map[string]interface{} `json:"wardrobe"`
}

// RefPaths 参考图路径
type RefPaths struct {
	Front string `json:"front,omitempty"`
	Side  string `json:"side,omitempty"`
	Back  string `json:"back,omitempty"`
}

// PersonaInfo 角色人设
type PersonaInfo struct {
	Personality string `json:"personality"`
	VoiceStyle  string `json:"voice_style"`
	Backstory   string `json:"backstory"`
}

// StyleInfo 风格约束
type StyleInfo struct {
	Constraints   string `json:"constraints"`
	NegativePrompt string `json:"negative_prompt"`
}

// CharacterRequest 创建/更新角色请求
type CharacterRequest struct {
	Name        string       `json:"name" binding:"required"`
	Face        FaceParams   `json:"face,omitempty"`
	Hair        HairParams   `json:"hair,omitempty"`
	Body        BodyParams   `json:"body,omitempty"`
	Palette     PaletteParams `json:"palette,omitempty"`
	Outfit      OutfitParams `json:"outfit,omitempty"`
	Persona     PersonaInfo  `json:"persona,omitempty"`
	Style       StyleInfo    `json:"style,omitempty"`
	RefImage    string       `json:"ref_image,omitempty"`
}

// CharacterListResponse 角色列表项
type CharacterListResponse struct {
	CharacterID  string  `json:"character_id"`
	Name         string  `json:"name"`
	CreatedAt    string  `json:"created_at"`
	UpdatedAt    string  `json:"updated_at"`
	HasEmbedding bool    `json:"has_embedding"`
	HairColor    string  `json:"hair_color,omitempty"`
	EyeColor     string  `json:"eye_color,omitempty"`
}

// GenerateRequest 增强版生成请求（含角色一致性）
type GenerateRequest struct {
	Prompt        string `json:"prompt" binding:"required"`
	Width         int    `json:"width,omitempty"`
	Height        int    `json:"height,omitempty"`
	Seed          int    `json:"seed,omitempty"`
	ModelID       string `json:"model_id,omitempty"`
	CharacterID   string `json:"character_id,omitempty"`
	UseSemantic   bool   `json:"use_semantic,omitempty"`
	ExportLive2D  bool   `json:"export_live2d,omitempty"`
	UseTracking   bool   `json:"use_tracking,omitempty"`
	NegativePrompt string `json:"negative_prompt,omitempty"`
	DeployDesktop bool   `json:"deploy_desktop,omitempty"`
}

// ChatRequest LLM聊天请求
type ChatRequest struct {
	CharacterID string `json:"character_id,omitempty"`
	Message     string `json:"message" binding:"required"`
	History     []ChatMessage `json:"history,omitempty"`
	Stream      bool   `json:"stream,omitempty"`
	SystemPrompt string `json:"system_prompt,omitempty"`
}

// ChatMessage 单条聊天消息
type ChatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// ChatResponse LLM聊天响应
type ChatResponse struct {
	Reply    string `json:"reply"`
	Emotion  string `json:"emotion,omitempty"`
	Action   string `json:"action,omitempty"`
	Finished bool   `json:"finished"`
}

// ChatStreamChunk SSE流式响应块
type ChatStreamChunk struct {
	Type     string `json:"type"`     // "token", "emotion", "action", "done", "error"
	Content  string `json:"content,omitempty"`
	Emotion  string `json:"emotion,omitempty"`
	Action   string `json:"action,omitempty"`
	Finished bool   `json:"finished,omitempty"`
	Error    string `json:"error,omitempty"`
}

// TrackingRequest 人脸追踪WebSocket请求
type TrackingRequest struct {
	Type      string  `json:"type"`       // "face", "blendshape", "audio"
	Timestamp int64   `json:"timestamp"`
	Data      map[string]float64 `json:"data,omitempty"`
}

// WSMessage WebSocket通用消息
type WSMessage struct {
	Type    string      `json:"type"`    // "progress", "tracking", "chat", "error", "ping"
	TaskID  string      `json:"task_id,omitempty"`
	Stage   string      `json:"stage,omitempty"`
	Progress int        `json:"progress,omitempty"`
	Message string      `json:"message,omitempty"`
	Data    interface{} `json:"data,omitempty"`
	Error   string      `json:"error,omitempty"`
	Time    int64       `json:"time,omitempty"`
}

// ExportModelRequest 导出Live2D模型请求
type ExportModelRequest struct {
	CharacterID string `json:"character_id,omitempty"`
	LayersDir   string `json:"layers_dir,omitempty"`
	OutputDir   string `json:"output_dir,omitempty"`
	ModelDir    string `json:"model_dir,omitempty"` // 已存在的模型目录（预览页直接打包下载）
	Download    bool   `json:"download,omitempty"`  // true 时直接返回 zip 二进制流
	Format      string `json:"format,omitempty"`    // "model3", "runtime"
}

// 导出响应没有固定结构体：Handler 直接把 Python 构建结果（含摊平后的
// runtime_ready / blocker）放进 models.Response.Data。

// ExpressionInfo 表情信息
type ExpressionInfo struct {
	Name      string   `json:"name"`
	ID        string   `json:"id"`
	Params    []string `json:"params"`
	Thumbnail string   `json:"thumbnail,omitempty"`
}
