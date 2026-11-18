package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// ImageSettings 是设置页保存的**非敏感**生图配置。
//
// 敏感 API Key 一律走 Python 侧加密保险库（core/secure_storage.py），
// 绝不写进本文件、也绝不经过 Go 的响应体。这里只有地址、模型名与默认画幅。
type ImageSettings struct {
	Engines         EngineSettings     `json:"engines"`
	Providers       ProviderSettings   `json:"providers"`
	Generation      GenerationSettings `json:"generation"`
	CustomProviders []CustomProvider   `json:"custom_providers,omitempty"`
}

// EngineSettings 本地引擎地址（只允许回环/私网，校验在 services 层）。
type EngineSettings struct {
	ComfyUIBaseURL string `json:"comfyui_base_url"`
	SDWebUIBaseURL string `json:"sd_webui_base_url"`
}

// ProviderSettings 云端上游的非敏感参数。
type ProviderSettings struct {
	OpenAI    OpenAISettings    `json:"openai"`
	Seedream  SeedreamSettings  `json:"seedream"`
	SenseNova SenseNovaSettings `json:"sensenova"`
}

type OpenAISettings struct {
	BaseURL string `json:"base_url"`
	Model   string `json:"model"`
}

type SeedreamSettings struct {
	Version string `json:"version"`
	Size    string `json:"size"`
}

type SenseNovaSettings struct {
	BaseURL string `json:"base_url"`
}

// GenerationSettings 生成默认值；候选数 1 表示关闭多候选择优。
type GenerationSettings struct {
	Width             int    `json:"width"`
	Height            int    `json:"height"`
	Candidates        int    `json:"candidates"`
	PollinationsModel string `json:"pollinations_model"`
	PollinationsSeed  int    `json:"pollinations_seed"`
	Normalize         bool   `json:"normalize"`
}

// CustomProvider 为后续「自训专精模型」预留：名字/地址/模型名可先在设置页登记，
// 真正接入时只需在 Python registry 增加一项。
type CustomProvider struct {
	Name        string `json:"name"`
	DisplayName string `json:"display_name"`
	BaseURL     string `json:"base_url"`
	Model       string `json:"model"`
	KeyEnv      string `json:"key_env,omitempty"`
}

// DefaultImageSettings 返回与 Python 侧默认值一致的初始配置。
func DefaultImageSettings() ImageSettings {
	return ImageSettings{
		Engines: EngineSettings{
			ComfyUIBaseURL: "http://127.0.0.1:8188",
			SDWebUIBaseURL: "http://127.0.0.1:7860",
		},
		Providers: ProviderSettings{
			OpenAI:    OpenAISettings{BaseURL: "https://api.openai.com/v1", Model: "gpt-image-1"},
			Seedream:  SeedreamSettings{Version: "seedream-4.0", Size: "1024x1024"},
			SenseNova: SenseNovaSettings{BaseURL: "https://api.sensenova.cn/v1"},
		},
		Generation: GenerationSettings{
			Width:             1024,
			Height:            1536,
			Candidates:        1,
			PollinationsModel: "flux",
			PollinationsSeed:  0,
			Normalize:         true,
		},
		CustomProviders: []CustomProvider{},
	}
}

// SettingsPath 决定 settings.json 的落点，优先级：
//
//  1. LIVE2D_SETTINGS_FILE（显式覆盖，测试/排障用）
//  2. cfg.DataDir/settings.json（桌面版可写数据根）
//  3. 用户配置目录/PygmalionStudio/settings.json（开发环境兜底）
//
// 绝不写安装目录：装在 Program Files 下不可写，卸载还会连配置一起删。
func SettingsPath(c *Config) string {
	if p := strings.TrimSpace(os.Getenv("LIVE2D_SETTINGS_FILE")); p != "" {
		return p
	}
	if c != nil && strings.TrimSpace(c.DataDir) != "" {
		return filepath.Join(c.DataDir, "settings.json")
	}
	base, err := os.UserConfigDir()
	if err != nil || strings.TrimSpace(base) == "" {
		base = os.TempDir()
	}
	return filepath.Join(base, "PygmalionStudio", "settings.json")
}

// LoadImageSettings 读取设置文件。缺失返回默认值；损坏也回退默认值，
// 并把解析错误交给调用方记录（不带着坏配置继续跑）。
func LoadImageSettings(path string) (ImageSettings, error) {
	settings := DefaultImageSettings()
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return settings, nil
		}
		return settings, err
	}
	if err := json.Unmarshal(data, &settings); err != nil {
		return DefaultImageSettings(), err
	}
	return settings, nil
}

// SaveImageSettings 原子写入（tmp + rename），避免写一半断电留下坏 JSON。
func SaveImageSettings(path string, settings ImageSettings) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}
