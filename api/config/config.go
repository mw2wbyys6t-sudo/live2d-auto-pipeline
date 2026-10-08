package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"time"
)

// defaultPythonPath 返回当前平台最常见的 Python 解释器名。
// Windows 没有 python3 别名（只有 python / py），Unix 反之。
func defaultPythonPath() string {
	if runtime.GOOS == "windows" {
		return "python"
	}
	return "python3"
}

type Config struct {
	Server    ServerConfig    `json:"server"`
	SDWebUI   SDWebUIConfig   `json:"sd_webui"`
	Python    PythonConfig    `json:"python"`
	Output    OutputConfig    `json:"output"`
	ComfyUI   ComfyUIConfig   `json:"comfyui"`
	Cache     CacheConfig     `json:"cache"`
	LLM       LLMConfig       `json:"llm"`
	TTS       TTSConfig       `json:"tts"`
	Character CharacterConfig `json:"character"`
	WebSocket WebSocketConfig `json:"websocket"`
	Redis     RedisConfig     `json:"redis"`
	MediaPipe MediaPipeConfig `json:"mediapipe"`
}

type ServerConfig struct {
	Host               string        `json:"host"`
	Port               int           `json:"port"`
	MaxRequestBodySize int64         `json:"max_request_body_size"`
	MaxHeaderBytes     int           `json:"max_header_bytes"`
	ReadTimeout        time.Duration `json:"read_timeout"`
	WriteTimeout       time.Duration `json:"write_timeout"`
	ReadHeaderTimeout  time.Duration `json:"read_header_timeout"`
	IdleTimeout        time.Duration `json:"idle_timeout"`
	AllowedOrigins     []string      `json:"allowed_origins"`
}

// SDWebUIConfig 本地 SD WebUI 图像生成设置。
//
// 注意：本字段目前仅作配置预留，Go 后端尚未接入 SD WebUI 调用链。
// 图像生成实际由 Python 侧（core/workflow.py + llm_bridge）执行。
type SDWebUIConfig struct {
	BaseURL string `json:"base_url"`
	Timeout int    `json:"timeout"`
	Enabled bool   `json:"enabled"`
}

type PythonConfig struct {
	PythonPath string `json:"python_path"`
	ScriptsDir string `json:"scripts_dir"`
	TimeoutSec int    `json:"timeout_sec"`
	// ExportTimeoutSec 是完整 Live2D 导出（网格 + 图集烘焙 + moc3 编译 + 官方内核
	// 验收）的预算。实测依据 tools/measure_export_duration.py：26 层 x 1024px
	// （27 百万像素）中位 6.6s，12 层 x 2048px（50 百万像素）6.5s。
	ExportTimeoutSec int `json:"export_timeout_sec"`
	// HfCache 指向打包后的 HuggingFace 权重缓存目录（便携版用）。
	// 非空时，python_bridge 在启动 Python 子进程时注入 HF_HOME 与
	// HUGGINGFACE_HUB_CACHE 指向该目录，让 transformers / huggingface_hub
	// 从打包位置读权重，而不是用户目录下的默认缓存。
	// 选此方案（在 Go exec 时注入）而非写注册表或改 Python 启动脚本，
	// 是因为它只影响本程序的子进程，不污染用户全局环境，卸载无需还原。
	HfCache string `json:"hf_cache"`
}

type OutputConfig struct {
	BaseDir     string `json:"base_dir"`
	MaxFileSize int64  `json:"max_file_size"`
}

type ComfyUIConfig struct {
	BaseDir string `json:"base_dir"`
	Enabled bool   `json:"enabled"`
}

type CacheConfig struct {
	Enabled    int `json:"enabled"`
	MaxEntries int `json:"max_entries"`
	MaxSizeMB  int `json:"max_size_mb"`
	TTLSeconds int `json:"ttl_seconds"`
}

// LLMConfig LLM提供商设置
type LLMConfig struct {
	Provider    string  `json:"provider"` // openai, anthropic, ollama, etc.
	APIKey      string  `json:"api_key"`
	BaseURL     string  `json:"base_url"`
	Model       string  `json:"model"`
	MaxTokens   int     `json:"max_tokens"`
	Temperature float64 `json:"temperature"`
}

// TTSConfig 语音合成设置。
//
// 注意：Go 后端本身不执行语音合成。TTS 由浏览器端（window.speechSynthesis）
// 和 Python 侧（llm_bridge/tts/）各自实现。本字段供未来 Go 端 TTS 代理预留。
type TTSConfig struct {
	Provider string `json:"provider"` // edge-tts, azure, etc.
	Voice    string `json:"voice"`
	Rate     string `json:"rate"`
	Enabled  bool   `json:"enabled"`
}

// CharacterConfig 角色存储设置
type CharacterConfig struct {
	StorageDir      string `json:"storage_dir"`
	MaxEmbeddingDim int    `json:"max_embedding_dim"`
}

// WebSocketConfig WebSocket设置
type WebSocketConfig struct {
	Enabled        bool `json:"enabled"`
	MaxConnections int  `json:"max_connections"`
	PingInterval   int  `json:"ping_interval_sec"`
	WriteWait      int  `json:"write_wait_sec"`
	PongWait       int  `json:"pong_wait_sec"`
}

// RedisConfig Redis设置（可选，用于任务队列）。
//
// 注意：Go 后端当前使用内存存储请求计数（见 main.go），Redis 未接入。
// 本字段供未来分布式部署预留，当前默认 Enabled=false。
type RedisConfig struct {
	URL      string `json:"url"`
	Enabled  bool   `json:"enabled"`
	DB       int    `json:"db"`
	Password string `json:"password"`
}

// MediaPipeConfig MediaPipe人脸追踪设置。
//
// 注意：Go 后端不执行面捕（/api/tracking/* 返回 501）。
// 面捕由浏览器端（web/lib/face-tracker.ts，MediaPipe WASM）和 Python 侧
//（drivers/face_tracker/）各自实现。本字段供未来 Go 端面捕代理预留。
type MediaPipeConfig struct {
	Enabled                bool    `json:"enabled"`
	ModelComplexity        int     `json:"model_complexity"`
	MinDetectionConfidence float64 `json:"min_detection_confidence"`
	MinTrackingConfidence  float64 `json:"min_tracking_confidence"`
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
			// 桌面版：exe 可能被双击于任意目录（cwd = exe 所在目录）。
			// 优先从 exe 自身位置向上找项目根（推荐把 exe 放在项目根目录，
			// 或项目根的 dist/ 子目录），再退回容器约定路径。
			candidates := []string{}
			if exe, err := os.Executable(); err == nil {
				exeDir := filepath.Dir(exe)
				candidates = append(candidates, exeDir, filepath.Dir(exeDir), filepath.Dir(filepath.Dir(exeDir)))
			}
			candidates = append(candidates, "/workspace", "/app", "/repo", "/project")
			for _, candidate := range candidates {
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
			Host:               "0.0.0.0",
			Port:               8080,
			MaxRequestBodySize: 10 * 1024 * 1024,
			MaxHeaderBytes:     1 * 1024 * 1024,
			ReadTimeout:        30 * time.Second,
			WriteTimeout:       180 * time.Second,
			ReadHeaderTimeout:  5 * time.Second,
			IdleTimeout:        120 * time.Second,
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
			// 桌面版默认解释器随平台自适应：Windows 无 python3 别名。
			// 可执行文件发现兜底见 validateExecutablePath（services）。
			PythonPath: defaultPythonPath(),
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

	// 兼容便携版的扁平配置格式：build_portable.py 与 desktop.iss 都把
	// python_path / scripts_dir / hf_cache 写在 JSON 顶层（而不是嵌在
	// python.{...} 下）。json.Unmarshal 默认会静默忽略这些顶层键，导致
	// portable 配置完全不生效。这里做一次扁平→嵌套的迁移：扁平键存在
	// 即覆盖嵌套字段——portable 配置本来就是"覆盖默认"语义，二者同时
	// 出现在同一份 JSON 里属于误配，让扁平（显式便携）胜出更安全。
	var flat struct {
		PythonPath string `json:"python_path"`
		ScriptsDir string `json:"scripts_dir"`
		HfCache    string `json:"hf_cache"`
	}
	if err := json.Unmarshal(data, &flat); err == nil {
		if flat.PythonPath != "" {
			cfg.Python.PythonPath = flat.PythonPath
		}
		if flat.ScriptsDir != "" {
			cfg.Python.ScriptsDir = flat.ScriptsDir
		}
		if flat.HfCache != "" {
			cfg.Python.HfCache = flat.HfCache
		}
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
