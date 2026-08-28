package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

type Config struct {
	Version  string         `json:"version"`
	// RateLimitPerMinute caps requests per client IP per minute. Set to 0 to
	// disable rate limiting entirely. Loopback clients are always exempt so
	// local preview / e2e tooling is never throttled.
	RateLimitPerMinute int `json:"rate_limit_per_minute"`
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

// APIVersion is the canonical API version string. It is the fallback used
// when Config.Version is empty, and keeps the "/v10" contract stable for
// clients that probe /api/health.
const APIVersion = "v10.1-go"

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

// isProjectRoot reports whether dir contains the expected project markers.
func isProjectRoot(dir string) bool {
	if dir == "" || dir == "/" {
		return false
	}
	if _, err := os.Stat(filepath.Join(dir, "core", "workflow.py")); err != nil {
		return false
	}
	if _, err := os.Stat(filepath.Join(dir, "live2d_builder")); err != nil {
		return false
	}
	return true
}

// findProjectRoot locates the project root by walking up from the current
// working directory (and, as a fallback, a set of well-known mount points)
// until a directory containing core/workflow.py is found. This is robust to
// the server being launched from api/, the project root, or /tmp.
func findProjectRoot() string {
	wd, err := os.Getwd()
	if err == nil {
		dir := wd
		for {
			if isProjectRoot(dir) {
				return dir
			}
			parent := filepath.Dir(dir)
			if parent == dir || parent == "/" {
				break
			}
			dir = parent
		}
	}
	for _, candidate := range []string{"/workspace", "/app", "/repo", "/project", "/mnt/tos/workspace"} {
		if isProjectRoot(candidate) {
			return candidate
		}
	}
	// Last resort: the historical two-levels-up guess (works when cwd is
	// api/config).
	if abs, err := filepath.Abs(filepath.Join("..", "..")); err == nil && isProjectRoot(abs) {
		return abs
	}
	return "/workspace"
}

func DefaultConfig() *Config {
	baseDir := findProjectRoot()
	scriptsDir := baseDir

	return &Config{
		Version:            APIVersion,
		RateLimitPerMinute: 600,
		Server: ServerConfig{
			Host:                "0.0.0.0",
			Port:                8080,
			MaxRequestBodySize:  10 * 1024 * 1024,
			MaxHeaderBytes:      1 * 1024 * 1024,
			ReadTimeout:         30 * time.Second,
			WriteTimeout:        180 * time.Second,
			ReadHeaderTimeout:   5 * time.Second,
			IdleTimeout:         120 * time.Second,
			AllowedOrigins:      []string{"*"},
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
		// v10.0: LLM配置
		LLM: LLMConfig{
			Provider:    "ollama",
			APIKey:      "",
			BaseURL:     "http://127.0.0.1:11434",
			Model:       "llama3.1",
			MaxTokens:   2048,
			Temperature: 0.7,
		},
		// v10.0: TTS配置
		TTS: TTSConfig{
			Provider: "edge-tts",
			Voice:    "zh-CN-XiaoxiaoNeural",
			Rate:     "+0%%",
			Enabled:  true,
		},
		// v10.0: 角色存储配置
		Character: CharacterConfig{
			StorageDir:      filepath.Join(baseDir, "assets", "characters"),
			MaxEmbeddingDim: 512,
		},
		// v10.0: WebSocket配置
		WebSocket: WebSocketConfig{
			Enabled:        true,
			MaxConnections: 100,
			PingInterval:   30,
			WriteWait:      10,
			PongWait:       60,
		},
		// v10.0: Redis配置（可选）
		Redis: RedisConfig{
			URL:     "redis://127.0.0.1:6379/0",
			Enabled: false,
			DB:      0,
		},
		// v10.0: MediaPipe配置
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
