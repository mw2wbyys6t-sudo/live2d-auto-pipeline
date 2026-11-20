package main

import (
	"log"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

// 桌面版 spawn 后端时把 stdout/stderr 接到 NUL（desktop/src/lib.rs），
// handlers.serverError 记录的根因只能在落盘文件里取证。这个用例钉住
// 「写进 log 的内容必须出现在 logs/backend.log」。
func TestSetupBackendLogWritesFile(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "logs")
	prevLog := log.Writer()
	prevGin, prevGinErr := gin.DefaultWriter, gin.DefaultErrorWriter
	t.Cleanup(func() {
		log.SetOutput(prevLog)
		gin.DefaultWriter, gin.DefaultErrorWriter = prevGin, prevGinErr
	})

	f, err := setupBackendLog(dir)
	if err != nil {
		t.Fatalf("setupBackendLog 失败: %v", err)
	}
	if f == nil {
		t.Fatal("setupBackendLog 应返回日志文件句柄")
	}

	log.Print("图像分层失败：内置 Python 缺少 numpy")
	if err := f.Close(); err != nil {
		t.Fatalf("关闭日志文件失败: %v", err)
	}

	data, err := os.ReadFile(filepath.Join(dir, "backend.log"))
	if err != nil {
		t.Fatalf("读取 backend.log 失败: %v", err)
	}
	if !strings.Contains(string(data), "缺少 numpy") {
		t.Errorf("backend.log 未写入日志内容，实际内容: %q", string(data))
	}
}

func TestSetupBackendLogSkipsEmptyDir(t *testing.T) {
	f, err := setupBackendLog("")
	if err != nil {
		t.Fatalf("空目录不应报错: %v", err)
	}
	if f != nil {
		_ = f.Close()
		t.Fatal("空目录不应创建日志文件")
	}
}
