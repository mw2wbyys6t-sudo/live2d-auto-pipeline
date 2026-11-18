package services

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"live2d-api/config"
)

func newTestSettingsService(t *testing.T) (*SettingsService, string) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("LIVE2D_SETTINGS_FILE", filepath.Join(dir, "settings.json"))
	cfg := &config.Config{Output: config.OutputConfig{BaseDir: dir}}
	return NewSettingsService(cfg, nil), dir
}

func TestUpdateRejectsPublicLocalEngineAddress(t *testing.T) {
	svc, _ := newTestSettingsService(t)
	engines := svc.Get().Engines
	engines.ComfyUIBaseURL = "http://example.com:8188"
	_, _, err := svc.Update(SettingsUpdate{Engines: &engines})
	if !errors.Is(err, ErrInvalidSettings) {
		t.Fatalf("公网本地引擎地址应被拒绝，got %v", err)
	}
}

func TestUpdateRejectsUnsupportedCanvasAndCandidates(t *testing.T) {
	svc, _ := newTestSettingsService(t)

	gen := svc.Get().Generation
	gen.Width, gen.Height = 1000, 1000
	if _, _, err := svc.Update(SettingsUpdate{Generation: &gen}); !errors.Is(err, ErrInvalidSettings) {
		t.Fatalf("非预设画幅应被拒绝，got %v", err)
	}

	gen = svc.Get().Generation
	gen.Candidates = 9
	if _, _, err := svc.Update(SettingsUpdate{Generation: &gen}); !errors.Is(err, ErrInvalidSettings) {
		t.Fatalf("候选数越界应被拒绝，got %v", err)
	}
}

func TestUpdatePersistsNonSecretSettingsWithoutLeakingKeys(t *testing.T) {
	svc, dir := newTestSettingsService(t)
	gen := svc.Get().Generation
	gen.Candidates = 2
	gen.Width, gen.Height = 1024, 1536
	updated, _, err := svc.Update(SettingsUpdate{Generation: &gen})
	if err != nil {
		t.Fatalf("保存失败: %v", err)
	}
	if updated.Generation.Candidates != 2 {
		t.Fatalf("候选数未生效: %+v", updated.Generation)
	}

	data, err := os.ReadFile(filepath.Join(dir, "settings.json"))
	if err != nil {
		t.Fatalf("读取落盘文件失败: %v", err)
	}
	if strings.Contains(string(data), "API_KEY") || strings.Contains(string(data), "sk-") {
		t.Fatal("settings.json 不得包含任何 Key 字样")
	}
	var decoded config.ImageSettings
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("落盘不是合法设置 JSON: %v", err)
	}
}

func TestUpdateRejectsUnknownSecretName(t *testing.T) {
	svc, _ := newTestSettingsService(t)
	_, warnings, err := svc.Update(SettingsUpdate{Secrets: map[string]string{"not-a-key": "value"}})
	if err != nil {
		t.Fatalf("未知 Key 名称不应让整个保存失败: %v", err)
	}
	if len(warnings) != 1 {
		t.Fatalf("应给出一条告警，got %v", warnings)
	}
}

func TestTestProviderRejectsBusyAndMissingName(t *testing.T) {
	svc, _ := newTestSettingsService(t)
	if _, err := svc.TestProvider(context.Background(), ""); !errors.Is(err, ErrInvalidSettings) {
		t.Fatalf("空 provider 应报参数错误，got %v", err)
	}
	// 手动占住全局锁，验证并发保护（不会走到 bridge）。
	svc.probeMu.Lock()
	defer svc.probeMu.Unlock()
	if _, err := svc.TestProvider(context.Background(), "pollinations"); !errors.Is(err, ErrProbeBusy) {
		t.Fatalf("并发测试应返回 busy，got %v", err)
	}
}
