package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
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
