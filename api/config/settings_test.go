package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSettingsPathPrefersEnvThenDataDir(t *testing.T) {
	explicit := filepath.Join(t.TempDir(), "explicit.json")
	t.Setenv("LIVE2D_SETTINGS_FILE", explicit)
	if got := SettingsPath(&Config{DataDir: "D:/data"}); got != explicit {
		t.Fatalf("显式 LIVE2D_SETTINGS_FILE 应优先，实得 %q", got)
	}

	t.Setenv("LIVE2D_SETTINGS_FILE", "")
	cfg := &Config{DataDir: filepath.Join("D:", "data")}
	if got, want := SettingsPath(cfg), filepath.Join(cfg.DataDir, "settings.json"); got != want {
		t.Fatalf("桌面数据根应优先，got=%q want=%q", got, want)
	}
}

func TestLoadImageSettingsDefaultsOnMissingFile(t *testing.T) {
	settings, err := LoadImageSettings(filepath.Join(t.TempDir(), "missing.json"))
	if err != nil {
		t.Fatalf("缺失文件不应报错: %v", err)
	}
	defaults := DefaultImageSettings()
	if settings.Generation.Width != defaults.Generation.Width ||
		settings.Generation.Height != defaults.Generation.Height {
		t.Fatalf("缺失文件应回退默认画幅: %+v", settings.Generation)
	}
	if !settings.Generation.Normalize {
		t.Fatal("默认应开启立绘规范化")
	}
}

func TestLoadImageSettingsCorruptFallsBackToDefaults(t *testing.T) {
	path := filepath.Join(t.TempDir(), "settings.json")
	if err := os.WriteFile(path, []byte("{broken"), 0o644); err != nil {
		t.Fatal(err)
	}
	settings, err := LoadImageSettings(path)
	if err == nil {
		t.Fatal("损坏的 JSON 应返回错误供调用方记录")
	}
	if settings.Generation.Height != DefaultImageSettings().Generation.Height {
		t.Fatalf("损坏文件应回退默认值: %+v", settings.Generation)
	}
}

func TestSaveImageSettingsIsAtomicAndRoundTrips(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "settings.json")
	settings := DefaultImageSettings()
	settings.Engines.ComfyUIBaseURL = "http://127.0.0.1:9999"
	settings.Generation.Candidates = 2
	if err := SaveImageSettings(path, settings); err != nil {
		t.Fatalf("保存失败: %v", err)
	}
	if _, err := os.Stat(path + ".tmp"); !os.IsNotExist(err) {
		t.Fatal("临时文件应已被 rename 掉")
	}
	loaded, err := LoadImageSettings(path)
	if err != nil {
		t.Fatalf("读取失败: %v", err)
	}
	if loaded.Engines.ComfyUIBaseURL != "http://127.0.0.1:9999" || loaded.Generation.Candidates != 2 {
		t.Fatalf("round-trip 不一致: %+v", loaded)
	}

	data, _ := os.ReadFile(path)
	if !json.Valid(data) {
		t.Fatal("落盘内容必须是合法 JSON")
	}
	if strings.Contains(string(data), "api_key") || strings.Contains(string(data), "secret") {
		t.Fatal("settings.json 不得出现 Key 字段")
	}
}
