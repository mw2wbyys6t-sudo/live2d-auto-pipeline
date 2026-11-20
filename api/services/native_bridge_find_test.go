package services

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"live2d-api/config"
)

// FindBinary 的搜索顺序决定最终跑哪份面捕实现：C++（478 点 + 虹膜）优先，
// Rust 版兜底。这里用临时目录搭出两种开发布局，验证优先级。
func TestFindBinaryPrefersCppBuildOverRustRelease(t *testing.T) {
	exe := "face_tracker"
	if runtime.GOOS == "windows" {
		exe = "face_tracker.exe"
	}
	root := t.TempDir()
	cppDir := filepath.Join(root, "native", "face_tracker_cpp", "build")
	rustDir := filepath.Join(root, "native", "face_tracker", "target", "release")
	for _, d := range []string{cppDir, rustDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatalf("mkdir: %v", err)
		}
		if err := os.WriteFile(filepath.Join(d, exe), []byte("stub"), 0o644); err != nil {
			t.Fatalf("write stub: %v", err)
		}
	}

	cfg := &config.Config{}
	cfg.Python.ScriptsDir = root
	nb := NewNativeBridge(cfg, nil)

	got := nb.FindBinary()
	if got != filepath.Join(cppDir, exe) {
		t.Fatalf("应优先 C++ 构建产物，实得 %s", got)
	}

	// 删掉 C++ 产物后回退 Rust release
	if err := os.Remove(filepath.Join(cppDir, exe)); err != nil {
		t.Fatalf("remove: %v", err)
	}
	if got := nb.FindBinary(); got != filepath.Join(rustDir, exe) {
		t.Fatalf("删掉 C++ 产物后应回退 Rust 版，实得 %s", got)
	}
}

// 配置里显式给出的 BinaryPath 优先级最高（部署布局/自定义安装位置）。
func TestFindBinaryHonoursConfiguredPath(t *testing.T) {
	root := t.TempDir()
	custom := filepath.Join(root, "custom_tracker.exe")
	if err := os.WriteFile(custom, []byte("stub"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	cfg := &config.Config{}
	cfg.Python.ScriptsDir = root
	cfg.NativeTracker.BinaryPath = custom
	if got := NewNativeBridge(cfg, nil).FindBinary(); got != custom {
		t.Fatalf("应使用配置路径，实得 %s", got)
	}
}
