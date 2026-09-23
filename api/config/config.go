package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

type Config struct {
	Server   ServerConfig   `json:"server"`
	SDWebUI  SDWebUIConfig  `json:"sd_webui"`
	Python   PythonConfig   `json:"python"`
	Output   OutputConfig   `json:"output"`
	ComfyUI  ComfyUIConfig  `json:"comfyui"`
	Cache    CacheConfig    `json:"cache"`
	LLM      LLMConfig      `json:"llm"`
	TTS      TTSConfig      `json:"tts"`
	Character CharacterConfig `json:"character"`
	WebSocket WebSocketConfig `json:"websocket"`
	Redis    RedisConfig    `json:"redis"`
	MediaPipe MediaPipeConfig `json:"mediapipe"`
}

type ServerConfig struct {
	Host                string        `json:"host"`
	Port                int           `json:"port"`
	MaxRequestBodySize  int64         `json:"max_request_body_size"`
	MaxHeaderBytes      int           `json:"max_header_bytes"`
	ReadTimeout         time.Duration `json:"read_timeout"`
	WriteTimeout        time.Duration `json:"write_timeout"`
	ReadHeaderTimeout   time.Duration `json:"read_header_timeout"`
	IdleTimeout         time.Duration `json:"idle_timeout"`
	AllowedOrigins      []string      `json:"allowed_origins"`
}

type SDWebUIConfig struct {
	BaseURL      string `json:"base_url"`
	Timeout      int    `json:"timeout"`
	Enabled      bool   `json:"enabled"`
}

type PythonConfig struct {
	PythonPath   string `json:"python_path"`
	ScriptsDir   string `json:"scripts_dir"`
	TimeoutSec   int    `json:"timeout_sec"`
	// ExportTimeoutSec 是完整 Live2D 导出（网格 + 图集烘焙 + moc3 编译 + 官方内核
	// 验收）的预算。实测依据 tools/measure_export_duration.py：26 层 x 1024px
	// （27 百万像素）中位 6.6s，12 层 x 2048px（50 百万像素）6.5s。
	ExportTimeoutSec int `json:"export_timeout_sec"`
}

type OutputConfig struct {
	BaseDir      string `json:"base_dir"`
	MaxFileSize  int64  `json:"max_file_size"`
}

type ComfyUIConfig struct {
	BaseDir      string `json:"base_dir"`
	Enabled      bool   `json:"enabled"`
}

type CacheConfig struct {
	Enabled     int `json:"enabled"`
	MaxEntries  int `json:"max_entries"`
	MaxSizeMB   int `json:"max_size_mb"`
	TTLSeconds  int `json:"ttl_seconds"`
}

// LLMConfig LLM提供商设置
type LLMConfig struct {
	Provider string `json:"provider"` // openai, anthropic, ollama, etc.
	APIKey   string `json:"api_key"`
	BaseURL  string `json:"base_url"`
	Model    string `json:"model"`
	MaxTokens int   `json:"max_tokens"`
	Temperature float64 `json:"temperature"`
}

// TTSConfig 语音合成设置
type TTSConfig struct {
	Provider string `json:"provider"` // edge-tts, azure, etc.
	Voice    string `json:"voice"`
	Rate     string `json:"rate"`
	Enabled  bool   `json:"enabled"`
}

// CharacterConfig 角色存储设置
type CharacterConfig struct {
	StorageDir string `json:"storage_dir"`
	MaxEmbeddingDim int `json:"max_embedding_dim"`
}

// WebSocketConfig WebSocket设置
type WebSocketConfig struct {
	Enabled       bool   `json:"enabled"`
	MaxConnections int   `json:"max_connections"`
	PingInterval  int    `json:"ping_interval_sec"`
	WriteWait     int    `json:"write_wait_sec"`
	PongWait      int    `json:"pong_wait_sec"`
}

// RedisConfig Redis设置（可选，用于任务队列）
type RedisConfig struct {
	URL      string `json:"url"`
	Enabled  bool   `json:"enabled"`
	DB       int    `json:"db"`
	Password string `json:"password"`
}

// MediaPipeConfig MediaPipe人脸追踪设置
type MediaPipeConfig struct {
	Enabled       bool    `json:"enabled"`
	ModelComplexity int   `json:"model_complexity"`
	MinDetectionConfidence float64 `json:"min_detection_confidence"`
	MinTrackingConfidence float64  `json:"min_tracking_confidence"`
}

func DefaultConfig() *Config {
	// Resolve project root. When this file lives at api/config/config.go the
	// `..` `..` trick works, but if the server is launched from a different
	// cwd (e.g. /workspace itself) `filepath.Abs` collapses to "/" and
	// every script resolution breaks. Fall back to the actual cwd, then
	// verify the expected layout markers exist.
	baseDir := ""
	if abs, err := filepath.Abs(filepath.Join("..", "..")); err == nil {
		baseDir = abs
	}
	if baseDir == "" || baseDir == "/" {
		if wd, err := os.Getwd(); err == nil {
			baseDir = wd
		}
	}
	// If the cwd is not the project root (e.g. someone launched the binary
	// from inside /tmp), try to find the project root by looking for the
	// core/ and live2d_builder/ markers.
	if baseDir != "" {
		if _, err := os.Stat(filepath.Join(baseDir, "core", "workflow.py")); err != nil {
			for _, candidate := range []string{"/workspace", "/app", "/repo", "/project"} {
				if _, err := os.Stat(filepath.Join(candidate, "core", "workflow.py")); err == nil {
					baseDir = candidate
					break
				}
			}
		}
	}
	if baseDir == "" {
		baseDir = "/workspace"
	}
	scriptsDir := baseDir

	return &Config{
		Server: ServerConfig{
			Host:                "0.0.0.0",
			Port:                8080,
			MaxRequestBodySize:  10 * 1024 * 1024,
			MaxHeaderBytes:      1 * 1024 * 1024,
			ReadTimeout:         30 * time.Second,
			WriteTimeout:        180 * time.Second,
			ReadHeaderTimeout:   5 * time.Second,
			IdleTimeout:         120 * time.Second,
			// CORS 白名单：默认只放行本地工作台。空列表 = 不启用跨域
			//（同源请求本就不带 Origin，走 Next.js rewrites 代理时不受影响）。
			// 绝不可默认 "*"：它会与 Allow-Credentials:true 形成任意站点携带凭据的漏洞。
			AllowedOrigins: []string{"http://localhost:3000", "http://127.0.0.1:3000"},
		},
		SDWebUI: SDWebUIConfig{
			BaseURL: "http://127.0.0.1:7860",
			Timeout: 300,
			Enabled: true,
		},
		Python: PythonConfig{
			PythonPath: "python3",
			ScriptsDir: scriptsDir,
			TimeoutSec: 120,
			// 实测最慢 6.6s（50 百万像素），150s 留 20 倍以上余量，
			// 且刻意小于 Server.WriteTimeout（180s），见 GetExportTimeout。
			ExportTimeoutSec: 150,
		},
		Output: OutputConfig{
			BaseDir:     filepath.Join(baseDir, "output"),
			MaxFileSize: 50 * 1024 * 1024,
		},
		ComfyUI: ComfyUIConfig{
			BaseDir: filepath.Join(baseDir, "comfyui"),
			Enabled: false,
		},
		Cache: CacheConfig{
			Enabled:    1,
			MaxEntries: 100,
			MaxSizeMB:  100,
			TTLSeconds: 3600,
		},
		// v0.10.0: LLM配置
		LLM: LLMConfig{
			Provider:    "ollama",
			APIKey:      "",
			BaseURL:     "http://127.0.0.1:11434",
			Model:       "llama3.1",
			MaxTokens:   2048,
			Temperature: 0.7,
		},
		// v0.10.0: TTS配置
		TTS: TTSConfig{
			Provider: "edge-tts",
			Voice:    "zh-CN-XiaoxiaoNeural",
			Rate:     "+0%%",
			Enabled:  true,
		},
		// v0.10.0: 角色存储配置
		Character: CharacterConfig{
			StorageDir:      filepath.Join(baseDir, "assets", "characters"),
			MaxEmbeddingDim: 512,
		},
		// v0.10.0: WebSocket配置
		WebSocket: WebSocketConfig{
			Enabled:        true,
			MaxConnections: 100,
			PingInterval:   30,
			WriteWait:      10,
			PongWait:       60,
		},
		// v0.10.0: Redis配置（可选）
		Redis: RedisConfig{
			URL:     "redis://127.0.0.1:6379/0",
			Enabled: false,
			DB:      0,
		},
		// v0.10.0: MediaPipe配置
		MediaPipe: MediaPipeConfig{
			Enabled:                true,
			ModelComplexity:        1,
			MinDetectionConfidence: 0.5,
			MinTrackingConfidence:  0.5,
		},
	}
}

func LoadConfig(path string) (*Config, error) {
	cfg := DefaultConfig()

	if path == "" {
		return cfg, nil
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return cfg, nil // File not found is OK, use defaults
	}

	if err := json.Unmarshal(data, cfg); err != nil {
		return cfg, err
	}

	return cfg, nil
}

// GetPythonTimeout returns the Python timeout as time.Duration
func (c *Config) GetPythonTimeout() time.Duration {
	secs := c.Python.TimeoutSec
	if secs <= 0 {
		secs = 120
	}
	return time.Duration(secs) * time.Second
}

// GetExportTimeout returns the budget for a full Live2D export.
//
// 它必须小于 Server.WriteTimeout：否则 Python 还在跑，HTTP 连接已经先被
// 掐断，客户端拿到的是无说明的死连接，而不是我们的 504。
func (c *Config) GetExportTimeout() time.Duration {
	secs := c.Python.ExportTimeoutSec
	if secs <= 0 {
		secs = 150
	}
	timeout := time.Duration(secs) * time.Second
	if write := c.Server.WriteTimeout; write > 0 && timeout >= write {
		return write - write/10
	}
	return timeout
}
