package services

import (
	"os"
	"path/filepath"
	"testing"
)

// 分层脚本开工前就 mkdir(out_dir)，脚本失败时会把空目录留在产物根下，
// 前端「最近分层」列表因此多出点开空白的条目。removeEmptyDir 只清空壳。
func TestRemoveEmptyDir(t *testing.T) {
	root := t.TempDir()

	empty := filepath.Join(root, "layers_empty")
	if err := os.MkdirAll(empty, 0o755); err != nil {
		t.Fatalf("创建空目录失败: %v", err)
	}
	removeEmptyDir(empty)
	if _, err := os.Stat(empty); !os.IsNotExist(err) {
		t.Errorf("空目录应被删除，stat err=%v", err)
	}

	kept := filepath.Join(root, "layers_kept")
	if err := os.MkdirAll(kept, 0o755); err != nil {
		t.Fatalf("创建产物目录失败: %v", err)
	}
	if err := os.WriteFile(filepath.Join(kept, "face.png"), []byte("x"), 0o644); err != nil {
		t.Fatalf("写入产物失败: %v", err)
	}
	removeEmptyDir(kept)
	if _, err := os.Stat(kept); err != nil {
		t.Errorf("非空目录不应被删除: %v", err)
	}
}

// 参数为空或目录不存在时必须静默跳过，不能让清理本身变成新的失败点。
func TestRemoveEmptyDirNoop(t *testing.T) {
	removeEmptyDir("")
	removeEmptyDir(filepath.Join(t.TempDir(), "not-exist"))
}
