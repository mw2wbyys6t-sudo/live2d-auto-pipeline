package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"

	"live2d-api/config"
)

// 设置接口的安全底线：响应里只能有 Key 的布尔状态，绝不能出现 Key 原文。
func TestSettingsNeverEchoesSecrets(t *testing.T) {
	gin.SetMode(gin.TestMode)
	dir := t.TempDir()
	t.Setenv("LIVE2D_SETTINGS_FILE", filepath.Join(dir, "settings.json"))

	cfg := config.DefaultConfig()
	cfg.Output.BaseDir = dir
	cfg.DataDir = dir
	// 用一个不存在的解释器：Key 写入会失败并进 warnings，但不影响本次断言
	// （我们要证明的是"失败路径也不回显 Key"）。
	cfg.Python.PythonPath = "definitely-not-a-python-binary"

	h := NewHandler(cfg, nil, nil)
	router := gin.New()
	router.GET("/api/settings", h.GetSettings)
	router.PUT("/api/settings", h.UpdateSettings)

	canary := "sk-canary-do-not-echo-123456"
	body, _ := json.Marshal(map[string]interface{}{
		"secrets": map[string]string{"openai": canary},
	})
	req := httptest.NewRequest(http.MethodPut, "/api/settings", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("PUT 状态码 = %d，body=%s", w.Code, w.Body.String())
	}
	if strings.Contains(w.Body.String(), canary) {
		t.Fatal("PUT 响应回显了 Key")
	}

	req = httptest.NewRequest(http.MethodGet, "/api/settings", nil)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("GET 状态码 = %d，body=%s", w.Code, w.Body.String())
	}
	if strings.Contains(w.Body.String(), canary) {
		t.Fatal("GET 响应回显了 Key")
	}
	var payload struct {
		Data struct {
			Keys           map[string]bool `json:"keys"`
			VaultAvailable bool            `json:"vault_available"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &payload); err != nil {
		t.Fatalf("响应不是合法 JSON: %v", err)
	}
	if payload.Data.Keys["openai"] {
		t.Fatal("Key 写入失败时不应报告 openai 已配置")
	}
}
