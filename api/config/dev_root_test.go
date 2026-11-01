package config

import (
	"os"
	"path/filepath"
	"testing"
)

// 从 api/ 启动（`go run .` / `./live2d-api`）时，项目根必须在 cwd 的祖先里
// 找到。此前只按 "../../" 推算，得到的是仓库的上一级：output 指到仓库外
// （/output/* 全 404），scripts_dir 也错（Python 子进程报 No module named
// 'core'）。
func TestDefaultConfigFindsRootFromAPISubdir(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "core"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "core", "workflow.py"), []byte("# marker\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	apiDir := filepath.Join(root, "api")
	if err := os.MkdirAll(apiDir, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Chdir(apiDir)

	cfg := DefaultConfig()
	if got, want := cfg.Python.ScriptsDir, root; got != want {
		t.Errorf("scripts_dir = %q，期望 %q", got, want)
	}
	if got, want := cfg.Output.BaseDir, filepath.Join(root, "output"); got != want {
		t.Errorf("output.base_dir = %q，期望 %q", got, want)
	}
}
