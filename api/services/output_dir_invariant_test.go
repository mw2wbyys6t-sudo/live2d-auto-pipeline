package services

import (
	"path/filepath"
	"strings"
	"testing"

	"live2d-api/config"
)

// 桌面版把脚本装在只读的 app/、把产物写在文档目录，两者不同。
// 产物目录必须是 /output 静态路由的服务根目录，否则生成成功但前端全部 404。
func TestWorkflowOutputDirStaysUnderServedRoot(t *testing.T) {
	scriptsDir := t.TempDir()
	servedRoot := t.TempDir()

	cfg := &config.Config{}
	cfg.Python.ScriptsDir = scriptsDir
	cfg.Output.BaseDir = servedRoot

	if got := workflowOutputDir(cfg, ""); got != servedRoot {
		t.Fatalf("无角色时产物目录 = %q，应为服务根目录 %q", got, servedRoot)
	}

	got := workflowOutputDir(cfg, "char_1")
	want := filepath.Join(servedRoot, "characters", "char_1")
	if got != want {
		t.Fatalf("带角色产物目录 = %q，应为 %q", got, want)
	}

	rel, err := filepath.Rel(servedRoot, got)
	if err != nil || rel == ".." || strings.HasPrefix(rel, "..") {
		t.Fatalf("产物目录 %q 越出服务根目录 %q", got, servedRoot)
	}

	if strings.HasPrefix(got, scriptsDir) {
		t.Fatalf("产物目录 %q 不应落在脚本/安装目录 %q", got, scriptsDir)
	}
}
