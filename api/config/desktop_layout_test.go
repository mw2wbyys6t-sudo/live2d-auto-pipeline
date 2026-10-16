package config

// ADR-010 桌面布局单测：资源根发现、解释器/源码/用户目录映射、-config 优先级。

import (
	"os"
	"path/filepath"
	"testing"
)

func fakeResourceRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	// 布局标记：app/core/workflow.py
	if err := os.MkdirAll(filepath.Join(root, "app", "core"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "app", "core", "workflow.py"), []byte("# marker"), 0o644); err != nil {
		t.Fatal(err)
	}
	pyDir := filepath.Join(root, "python")
	if err := os.MkdirAll(pyDir, 0o755); err != nil {
		t.Fatal(err)
	}
	pythonExe := "pythonw.exe"
	if goos := os.Getenv("GOOS"); goos == "linux" {
		pythonExe = "python3"
	}
	if err := os.WriteFile(filepath.Join(pyDir, pythonExe), []byte(""), 0o755); err != nil {
		t.Fatal(err)
	}
	return root
}

func TestResolveDesktopRootFindsMarker(t *testing.T) {
	root := fakeResourceRoot(t)
	if got := resolveDesktopRoot(root); got != root {
		t.Fatalf("resolveDesktopRoot 应返回资源根 %q，实得 %q", root, got)
	}
	// 平铺根下再套一层 resources/ 也应命中父目录
	nested := filepath.Join(root, "resources")
	if err := os.MkdirAll(nested, 0o755); err != nil {
		t.Fatal(err)
	}
	if got := resolveDesktopRoot(filepath.Join(nested, "live2d-api.exe-dir")); got != "" {
		t.Fatalf("无标记目录应返回空，实得 %q", got)
	}
}

func TestApplyDesktopLayoutMapsInterpreterAndUserData(t *testing.T) {
	root := fakeResourceRoot(t)
	data := t.TempDir()
	docs := t.TempDir()
	t.Setenv("LIVE2D_DATA_DIR", data)
	t.Setenv("LIVE2D_DOCS_DIR", docs)

	cfg := DefaultConfig()
	applyDesktopRoot(cfg, root, false)

	if cfg.Python.ScriptsDir != filepath.Join(root, "app") {
		t.Errorf("scripts_dir 应为资源 app/，实得 %q", cfg.Python.ScriptsDir)
	}
	if cfg.Python.HfCache != filepath.Join(data, "hf-cache") {
		t.Errorf("hf_cache 应在用户数据根，实得 %q", cfg.Python.HfCache)
	}
	if cfg.Output.BaseDir != filepath.Join(docs, "output") {
		t.Errorf("output 应在文档目录，实得 %q", cfg.Output.BaseDir)
	}
	if cfg.Character.StorageDir != filepath.Join(data, "characters") {
		t.Errorf("角色库应在用户数据根，实得 %q", cfg.Character.StorageDir)
	}
	if cfg.Python.PythonPath == "" || cfg.Python.PythonPath == "python" {
		t.Errorf("桌面布局必须指向内置解释器，实得 %q", cfg.Python.PythonPath)
	}
}

func TestApplyDesktopLayoutRespectsConfigFileForUserData(t *testing.T) {
	root := fakeResourceRoot(t)
	data := t.TempDir()
	t.Setenv("LIVE2D_DATA_DIR", data)
	t.Setenv("LIVE2D_DOCS_DIR", t.TempDir())

	cfg := DefaultConfig()
	// 模拟 -config 已显式给出 output/characters：本函数不得覆盖。
	customOutput := filepath.Join(t.TempDir(), "custom-output")
	customChars := filepath.Join(t.TempDir(), "custom-chars")
	cfg.Output.BaseDir = customOutput
	cfg.Character.StorageDir = customChars

	applyDesktopRoot(cfg, root, true)

	if cfg.Output.BaseDir != customOutput {
		t.Errorf("configProvided=true 时不得覆盖 output.base_dir，实得 %q", cfg.Output.BaseDir)
	}
	if cfg.Character.StorageDir != customChars {
		t.Errorf("configProvided=true 时不得覆盖 character.storage_dir，实得 %q", cfg.Character.StorageDir)
	}
	// 但程序本体字段（python/hf/tracker）仍应补齐。
	if cfg.Python.HfCache != filepath.Join(data, "hf-cache") {
		t.Errorf("hf_cache 仍应补齐，实得 %q", cfg.Python.HfCache)
	}
}
