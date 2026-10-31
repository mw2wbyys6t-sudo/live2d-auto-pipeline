package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/gin-gonic/gin"
)

// GET /api/diagnostics 返回逐条检查（后端恒绿、回环绑定绿），不触发生成。
func TestGetDiagnosticsReportsChecks(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h := newTestHandler(t)
	r := gin.New()
	r.GET("/api/diagnostics", h.GetDiagnostics)

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/diagnostics", nil)
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("diagnostics 应返回 200，实得 %d: %s", w.Code, w.Body.String())
	}
	var body struct {
		Success bool `json:"success"`
		Data    struct {
			Checks []struct {
				Key  string `json:"key"`
				OK   bool   `json:"ok"`
			} `json:"checks"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("响应非 JSON: %v", err)
	}
	if !body.Success {
		t.Fatal("success 应为 true")
	}
	found := map[string]bool{}
	for _, c := range body.Data.Checks {
		found[c.Key] = c.OK
	}
	if !found["backend"] {
		t.Error("后端检查应为绿")
	}
	if !found["bind_loopback"] {
		t.Error("回环绑定检查应为绿（测试默认 127.0.0.1）")
	}
	if _, ok := found["output_dir"]; !ok {
		t.Error("应包含产物目录可写检查")
	}
}

// 自检页的日志目录必须由后端报出，且跟着**可写数据根**走。
//
// 实测（2026-10-30 已安装的 v0.10.10）：页面写死 "%APPDATA%\PygmalionStudio\logs"，
// 而那个目录不存在，真实日志在安装目录下的 app\logs\live2d.log ——
// 用户照着提示找不到日志，反馈问题时也带不上证据。
func TestDiagnosticsReportsLogDir(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h := newTestHandler(t)
	dataDir := filepath.Join(t.TempDir(), "PygmalionStudio")
	h.cfg.DataDir = dataDir

	r := gin.New()
	r.GET("/api/diagnostics", h.GetDiagnostics)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/diagnostics", nil))

	var body struct {
		Data struct {
			LogDir string `json:"log_dir"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("响应非 JSON: %v", err)
	}
	want := filepath.Join(dataDir, "logs")
	if body.Data.LogDir != want {
		t.Errorf("log_dir = %q，应为可写数据根下的 logs（%q）", body.Data.LogDir, want)
	}
}
