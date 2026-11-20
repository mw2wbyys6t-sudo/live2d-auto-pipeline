package services

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"live2d-api/config"
)

// 设置页写入校验的错误哨兵：handler 据此回 400 / 429，而不是笼统的 500。
var (
	ErrInvalidSettings = errors.New("设置参数不合法")
	ErrProbeBusy       = errors.New("已有连接测试在进行中")
	ErrProbeTooSoon    = errors.New("连接测试过于频繁")
)

// validCanvasPresets 是允许的画幅组合（与前端 catalog.ts 的 CANVAS_PRESETS 对齐）。
var validCanvasPresets = map[[2]int]struct{}{
	{1024, 1536}: {},
	{1024, 1024}: {},
	{1536, 1024}: {},
	{2048, 2048}: {},
}

// SettingsUpdate 是 PUT /api/settings 的请求体。指针字段为 nil 表示"保持不变"。
type SettingsUpdate struct {
	Secrets         map[string]string          `json:"secrets"`
	Engines         *config.EngineSettings     `json:"engines"`
	Providers       *config.ProviderSettings   `json:"providers"`
	Generation      *config.GenerationSettings `json:"generation"`
	CustomProviders *[]config.CustomProvider   `json:"custom_providers"`
}

// SettingsService 持有 settings.json 的内存副本，负责校验、原子落盘，
// 以及把 API Key 写入 Python 加密保险库（Go 本身不保存 Key）。
type SettingsService struct {
	cfg    *config.Config
	bridge *PythonBridge

	mu      sync.RWMutex
	current config.ImageSettings
	path    string

	probeMu   sync.Mutex
	lastProbe map[string]time.Time

	// 上游状态查询要起 Python（实测 ~6s）；设置页首屏/保存都会读它，
	// 短缓存避免每次刷新都等一遍。保存 Key / 探针后立即失效。
	detailMu   sync.Mutex
	detail     []map[string]interface{}
	detailAt   time.Time
	detailVault bool
}

func NewSettingsService(cfg *config.Config, bridge *PythonBridge) *SettingsService {
	s := &SettingsService{
		cfg:       cfg,
		bridge:    bridge,
		path:      config.SettingsPath(cfg),
		lastProbe: map[string]time.Time{},
	}
	loaded, err := config.LoadImageSettings(s.path)
	if err != nil {
		// 坏文件不阻塞启动：回退默认值并记日志，用户可在设置页重新保存覆盖。
		log.Printf("[warn] 设置文件读取失败，已回退默认值: %v", err)
	}
	s.current = loaded
	return s
}

// Path 返回 settings.json 的实际路径（诊断/测试用）。
func (s *SettingsService) Path() string { return s.path }

// Get 返回当前设置的副本。
func (s *SettingsService) Get() config.ImageSettings {
	s.mu.RLock()
	defer s.mu.RUnlock()
	settings := s.current
	settings.CustomProviders = append([]config.CustomProvider(nil), s.current.CustomProviders...)
	return settings
}

// Update 应用一次设置变更：先写 Key（保险库），再落盘非敏感项。
// 返回保存后的设置与 warnings（部分 Key 保存失败的说明）。
func (s *SettingsService) Update(req SettingsUpdate) (config.ImageSettings, []string, error) {
	merged, err := s.merge(req)
	if err != nil {
		return config.ImageSettings{}, nil, err
	}

	warnings := make([]string, 0, len(req.Secrets))
	for provider, value := range req.Secrets {
		provider = strings.TrimSpace(provider)
		value = strings.TrimSpace(value)
		if provider == "" {
			continue
		}
		if err := s.applySecret(provider, value); err != nil {
			// 不回传 Key，也不回传上游错误原文，只说明哪个 provider 失败。
			warnings = append(warnings, fmt.Sprintf("%s 的 Key 未保存：保险库不可用或写入失败", provider))
			log.Printf("[error] Key 保存失败 provider=%s: %v", provider, err)
		}
	}

	if err := config.SaveImageSettings(s.path, merged); err != nil {
		return config.ImageSettings{}, warnings, fmt.Errorf("保存设置失败: %w", err)
	}
	s.mu.Lock()
	s.current = merged
	s.mu.Unlock()
	// Key / 地址可能刚变，下一次读状态必须重新探测。
	s.invalidateProviderDetail()
	return s.Get(), warnings, nil
}

func (s *SettingsService) applySecret(provider, value string) error {
	switch provider {
	case "ark", "seedream", "sensenova", "openai":
	default:
		return fmt.Errorf("%w: 未知的 Key 名称 %q", ErrInvalidSettings, provider)
	}
	if value != "" {
		if len(value) < 4 || len(value) > 512 {
			return fmt.Errorf("%w: Key 长度应在 4–512 之间", ErrInvalidSettings)
		}
		for _, r := range value {
			if r < 0x20 || r == 0x7f {
				return fmt.Errorf("%w: Key 含控制字符", ErrInvalidSettings)
			}
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if value == "" {
		return s.bridge.DeleteProviderKey(ctx, provider)
	}
	return s.bridge.SaveProviderKey(ctx, provider, value)
}

func (s *SettingsService) merge(req SettingsUpdate) (config.ImageSettings, error) {
	s.mu.RLock()
	merged := s.current
	merged.CustomProviders = append([]config.CustomProvider(nil), s.current.CustomProviders...)
	s.mu.RUnlock()

	if req.Engines != nil {
		merged.Engines = *req.Engines
	}
	if req.Providers != nil {
		merged.Providers = *req.Providers
	}
	if req.Generation != nil {
		merged.Generation = *req.Generation
	}
	if req.CustomProviders != nil {
		merged.CustomProviders = append([]config.CustomProvider(nil), (*req.CustomProviders)...)
	}
	if err := s.validate(&merged); err != nil {
		return config.ImageSettings{}, err
	}
	return merged, nil
}

func (s *SettingsService) validate(settings *config.ImageSettings) error {
	if err := validateHTTPURL(settings.Engines.ComfyUIBaseURL, true); err != nil {
		return fmt.Errorf("%w: ComfyUI 地址 %v", ErrInvalidSettings, err)
	}
	if err := validateHTTPURL(settings.Engines.SDWebUIBaseURL, true); err != nil {
		return fmt.Errorf("%w: SD WebUI 地址 %v", ErrInvalidSettings, err)
	}
	if err := validateHTTPURL(settings.Providers.OpenAI.BaseURL, false); err != nil {
		return fmt.Errorf("%w: OpenAI 兼容端点 %v", ErrInvalidSettings, err)
	}
	if err := validateHTTPURL(settings.Providers.SenseNova.BaseURL, false); err != nil {
		return fmt.Errorf("%w: SenseNova 端点 %v", ErrInvalidSettings, err)
	}
	if strings.TrimSpace(settings.Providers.OpenAI.Model) == "" {
		return fmt.Errorf("%w: OpenAI 兼容端点必须填写模型名", ErrInvalidSettings)
	}
	if len(settings.Providers.OpenAI.Model) > 120 {
		return fmt.Errorf("%w: 模型名过长", ErrInvalidSettings)
	}

	gen := &settings.Generation
	if _, ok := validCanvasPresets[[2]int{gen.Width, gen.Height}]; !ok {
		return fmt.Errorf("%w: 画幅必须是 1024×1536 / 1024×1024 / 1536×1024 / 2048×2048", ErrInvalidSettings)
	}
	if gen.Candidates < 1 || gen.Candidates > 4 {
		return fmt.Errorf("%w: 候选数应在 1–4 之间", ErrInvalidSettings)
	}
	if !pollinationsModelPattern.MatchString(strings.TrimSpace(gen.PollinationsModel)) {
		return fmt.Errorf("%w: pollinations 模型名只能包含字母、数字、点、下划线与短横线", ErrInvalidSettings)
	}
	if gen.PollinationsSeed < 0 {
		return fmt.Errorf("%w: 随机种子不能为负数", ErrInvalidSettings)
	}
	return nil
}

// validateHTTPURL 校验地址协议/长度；mustLocal 时要求回环或私网地址
// （与 Python 侧 ensure_local_endpoint 的口径一致，防止把本地引擎指到公网）。
func validateHTTPURL(raw string, mustLocal bool) error {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return fmt.Errorf("不能为空")
	}
	if len(raw) > 300 {
		return fmt.Errorf("过长")
	}
	for _, r := range raw {
		if r < 0x20 || r == 0x7f {
			return fmt.Errorf("含控制字符")
		}
	}
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" {
		return fmt.Errorf("不是合法地址")
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("只支持 http/https")
	}
	if mustLocal && !isLocalHostname(u.Hostname()) {
		return fmt.Errorf("只允许本机或局域网地址")
	}
	return nil
}

func isLocalHostname(host string) bool {
	host = strings.ToLower(strings.TrimSpace(host))
	if host == "localhost" || host == "::1" || host == "[::1]" {
		return true
	}
	if strings.HasPrefix(host, "127.") {
		return true
	}
	if strings.HasPrefix(host, "10.") || strings.HasPrefix(host, "192.168.") {
		return true
	}
	if strings.HasPrefix(host, "172.") {
		parts := strings.Split(host, ".")
		if len(parts) >= 2 {
			octet := 0
			if _, err := fmt.Sscanf(parts[1], "%d", &octet); err == nil && octet >= 16 && octet <= 31 {
				return true
			}
		}
	}
	return false
}

// providerDetailTTL 是上游状态在内存里的有效期：查询要起 Python（~6s），
// 15 秒内重复打开设置页直接复用；保存/探针会立即失效。
const providerDetailTTL = 15 * time.Second

func (s *SettingsService) invalidateProviderDetail() {
	s.detailMu.Lock()
	s.detail = nil
	s.detailAt = time.Time{}
	s.detailVault = false
	s.detailMu.Unlock()
}

// ProviderDetail 返回 Python 侧每个上游的运行时状态（可用性/是否本地/测试尺寸）
// 以及加密保险库是否可用；查询失败不阻塞设置页。
func (s *SettingsService) ProviderDetail(ctx context.Context) ([]map[string]interface{}, bool, error) {
	s.detailMu.Lock()
	if s.detail != nil && time.Since(s.detailAt) < providerDetailTTL {
		cached := append([]map[string]interface{}(nil), s.detail...)
		vault := s.detailVault
		s.detailMu.Unlock()
		return cached, vault, nil
	}
	s.detailMu.Unlock()

	detail, vault, err := s.bridge.ProviderDetail(ctx)
	if err != nil {
		return nil, false, err
	}
	s.detailMu.Lock()
	s.detail = detail
	s.detailAt = time.Now()
	s.detailVault = vault
	s.detailMu.Unlock()
	return detail, vault, nil
}

// TestProvider 执行一次真实出图测试。全局串行 + 每个上游 5 秒最小间隔，
// 避免用户连点把额度刷光。
func (s *SettingsService) TestProvider(ctx context.Context, provider string) (map[string]interface{}, error) {
	provider = strings.TrimSpace(provider)
	if provider == "" {
		return nil, fmt.Errorf("%w: 缺少 provider", ErrInvalidSettings)
	}
	if !s.probeMu.TryLock() {
		return nil, ErrProbeBusy
	}
	defer s.probeMu.Unlock()

	s.mu.Lock()
	last := s.lastProbe[provider]
	s.mu.Unlock()
	if !last.IsZero() && time.Since(last) < 5*time.Second {
		return nil, ErrProbeTooSoon
	}

	outDir := filepath.Join(s.cfg.Output.BaseDir, "probes")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return nil, fmt.Errorf("创建测试目录失败: %w", err)
	}
	result, err := s.bridge.ProbeProvider(ctx, provider, outDir)
	if err != nil {
		return nil, err
	}
	s.mu.Lock()
	s.lastProbe[provider] = time.Now()
	s.mu.Unlock()
	// 本地引擎刚被启动/关闭时，设置页的状态徽标要跟着变。
	s.invalidateProviderDetail()
	return result, nil
}
