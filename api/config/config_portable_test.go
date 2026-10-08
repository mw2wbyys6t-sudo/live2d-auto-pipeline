package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// TestLoadConfig_FlatPortableKeys 验证便携版的扁平配置格式能被正确读取。
//
// 背景：build_portable.py 与 deploy/installer/desktop.iss 都把
// python_path / scripts_dir / hf_cache 写在 JSON 顶层（扁平），而 Config
// 结构体用的是嵌套字段（python.python_path）。在修复前，json.Unmarshal
// 会静默忽略这些顶层键，导致便携配置完全不生效——这是一个隐藏的契约
// 裂缝，本测试就是它的回归闸门。
func TestLoadConfig_FlatPortableKeys(t *testing.T) {
	dir := t.TempDir()
	pyPath := filepath.Join(dir, "runtime", "python", "python.exe")
	scriptsDir := filepath.Join(dir, "app")
	hfCache := filepath.Join(dir, "runtime", "hf-cache")

	// 模拟 build_portable.py 写出的扁平格式（用 json.Marshal 保证转义正确）
	flat := map[string]string{
		"python_path": pyPath,
		"scripts_dir": scriptsDir,
		"hf_cache":    hfCache,
	}
	data, err := json.MarshalIndent(flat, "", "  ")
	if err != nil {
		t.Fatalf("Marshal 失败: %v", err)
	}
	path := filepath.Join(dir, "config.portable.json")
	if err := os.WriteFile(path, data, 0644); err != nil {
		t.Fatalf("写入测试配置失败: %v", err)
	}

	cfg, err := LoadConfig(path)
	if err != nil {
		t.Fatalf("LoadConfig 出错: %v", err)
	}

	if cfg.Python.PythonPath != pyPath {
		t.Errorf("PythonPath 未从扁平键迁移: got %q, want %q", cfg.Python.PythonPath, pyPath)
	}
	if cfg.Python.ScriptsDir != scriptsDir {
		t.Errorf("ScriptsDir 未从扁平键迁移: got %q, want %q", cfg.Python.ScriptsDir, scriptsDir)
	}
	if cfg.Python.HfCache != hfCache {
		t.Errorf("HfCache 未从扁平键迁移: got %q, want %q", cfg.Python.HfCache, hfCache)
	}
}

// TestLoadConfig_NestedKeysStillWork 验证标准嵌套格式仍然有效，
// 扁平兼容层不会破坏既有配置。
func TestLoadConfig_NestedKeysStillWork(t *testing.T) {
	dir := t.TempDir()
	nestedJSON := `{
  "python": {
    "python_path": "/usr/bin/python3",
    "scripts_dir": "/opt/app",
    "hf_cache": "/opt/hf-cache"
  }
}`
	path := filepath.Join(dir, "config.json")
	if err := os.WriteFile(path, []byte(nestedJSON), 0644); err != nil {
		t.Fatalf("写入测试配置失败: %v", err)
	}

	cfg, err := LoadConfig(path)
	if err != nil {
		t.Fatalf("LoadConfig 出错: %v", err)
	}

	if cfg.Python.PythonPath != "/usr/bin/python3" {
		t.Errorf("嵌套 PythonPath 失效: got %q", cfg.Python.PythonPath)
	}
	if cfg.Python.ScriptsDir != "/opt/app" {
		t.Errorf("嵌套 ScriptsDir 失效: got %q", cfg.Python.ScriptsDir)
	}
	if cfg.Python.HfCache != "/opt/hf-cache" {
		t.Errorf("嵌套 HfCache 失效: got %q", cfg.Python.HfCache)
	}
}

// TestLoadConfig_FlatOverridesNested 验证扁平键与嵌套键同时存在时，
// 扁平胜出（portable 语义：显式便携配置应覆盖通用嵌套配置）。
func TestLoadConfig_FlatOverridesNested(t *testing.T) {
	dir := t.TempDir()
	mixedJSON := `{
  "python_path": "/portable/python",
  "python": { "python_path": "/nested/python" }
}`
	path := filepath.Join(dir, "mixed.json")
	if err := os.WriteFile(path, []byte(mixedJSON), 0644); err != nil {
		t.Fatalf("写入测试配置失败: %v", err)
	}

	cfg, err := LoadConfig(path)
	if err != nil {
		t.Fatalf("LoadConfig 出错: %v", err)
	}

	if cfg.Python.PythonPath != "/portable/python" {
		t.Errorf("扁平应胜出: got %q, want /portable/python", cfg.Python.PythonPath)
	}
}
