package handlers

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/gin-gonic/gin"

	"live2d-api/config"
	"live2d-api/models"
	"live2d-api/services"
)

// newTestHandler 创建一个测试用 handler（不依赖外部服务）
func newTestHandler(t *testing.T) *Handler {
	t.Helper()
	return newTestHandlerWithPython(t, "python3")
}

// newTestHandlerWithPython 同上，但显式指定 Python 解释器路径。
//
// HealthCheck 会真的执行这个解释器做探针，所以需要它的用例必须自己决定
// 用哪个解释器，而不是碰运气去读宿主机 PATH 上的 python3。
func newTestHandlerWithPython(t *testing.T, pythonPath string) *Handler {
	t.Helper()
	gin.SetMode(gin.TestMode)

	cfg := &config.Config{
		Server: config.ServerConfig{
			Host:               "127.0.0.1",
			Port:               0,
			MaxRequestBodySize: 10 << 20,
		},
		Output: config.OutputConfig{
			BaseDir: t.TempDir(),
		},
		Python: config.PythonConfig{
			PythonPath: pythonPath,
			ScriptsDir: "",
			TimeoutSec: 5,
		},
		Cache: config.CacheConfig{
			Enabled:    1,
			MaxEntries: 100,
			MaxSizeMB:  10,
			TTLSeconds: 60,
		},
		Character: config.CharacterConfig{
			StorageDir: t.TempDir() + "/chars",
		},
	}
	cache := services.NewRequestCache(cfg.Cache)
	img := services.NewImageGenerator(cfg)
	return NewHandler(cfg, img, cache)
}

// fakePython 生成一个「行为固定」的假 Python 解释器，供 HealthCheck 探针使用。
//
// 为什么需要它：HealthCheck（v0.10.3 起）会真的执行 `python --version` 并
// `import PIL` / `import numpy`。单元测试不该依赖宿主机恰好装了什么——CI 的
// Go 任务就没装这两个库，用真解释器会返回 503，让「断言 200」变成假失败。
// 注入假解释器后，健康与不健康两条分支都能被确定性地测到。
//
//	exitCode == 0 → 模拟「环境完好」；非 0 → 模拟「解释器不可用」
func fakePython(t *testing.T, exitCode int) string {
	t.Helper()
	dir := t.TempDir()

	if runtime.GOOS == "windows" {
		path := filepath.Join(dir, "fake-python.bat")
		if err := os.WriteFile(path, []byte(fmt.Sprintf("@echo off\r\nexit /b %d\r\n", exitCode)), 0o644); err != nil {
			t.Fatalf("写入假解释器失败: %v", err)
		}
		return path
	}

	path := filepath.Join(dir, "fake-python.sh")
	if err := os.WriteFile(path, []byte(fmt.Sprintf("#!/bin/sh\nexit %d\n", exitCode)), 0o755); err != nil {
		t.Fatalf("写入假解释器失败: %v", err)
	}
	return path
}

func TestHealthCheck(t *testing.T) {
	h := newTestHandlerWithPython(t, fakePython(t, 0))
	r := gin.New()
	r.GET("/api/health", h.HealthCheck)

	req := httptest.NewRequest(http.MethodGet, "/api/health", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d (body=%s)", w.Code, w.Body.String())
	}
	var resp models.Response
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if !resp.Success {
		t.Errorf("expected success=true, got %v", resp.Success)
	}
	if resp.Message == "" {
		t.Error("expected non-empty message")
	}
}

// TestHealthCheckReportsBrokenPython 锁住 v0.10.3 的行为：
// Python 探针失败时不再假装健康，而是 503 + 可读的原因。
func TestHealthCheckReportsBrokenPython(t *testing.T) {
	h := newTestHandlerWithPython(t, fakePython(t, 1))
	r := gin.New()
	r.GET("/api/health", h.HealthCheck)

	req := httptest.NewRequest(http.MethodGet, "/api/health", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d (body=%s)", w.Code, w.Body.String())
	}
	var resp models.Response
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if resp.Success {
		t.Error("expected success=false when Python 不可用")
	}
	if resp.Error == "" {
		t.Error("expected non-empty error telling why Python 不可用")
	}
}

// TestHealthCheckWithRealPython 用真机解释器验证「环境完好 → 200」这一真实路径。
//
// 沿用仓库既有的 LIVE2D_TEST_PYTHON 约定：未设置就跳过，不制造假失败。
// CI 的 Go 任务装了 Pillow + numpy 并设置该变量，让这条路径也被真实覆盖。
func TestHealthCheckWithRealPython(t *testing.T) {
	pythonPath := os.Getenv("LIVE2D_TEST_PYTHON")
	if pythonPath == "" {
		t.Skip("需要 LIVE2D_TEST_PYTHON 才能用真机解释器验证健康检查")
	}

	h := newTestHandlerWithPython(t, pythonPath)
	r := gin.New()
	r.GET("/api/health", h.HealthCheck)

	req := httptest.NewRequest(http.MethodGet, "/api/health", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("真机解释器 %s 应报告健康，实际 %d (body=%s)", pythonPath, w.Code, w.Body.String())
	}
}

func TestGetAPIInfo(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.GET("/api/info", h.GetAPIInfo)

	req := httptest.NewRequest(http.MethodGet, "/api/info", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var resp models.Response
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if !resp.Success {
		t.Error("expected success=true")
	}
}

func TestGetModels(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.GET("/api/models", h.GetModels)

	req := httptest.NewRequest(http.MethodGet, "/api/models", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var resp models.Response
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if !resp.Success {
		t.Error("expected success=true")
	}
}

func TestGetExpressions(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.GET("/api/expressions", h.GetExpressions)

	req := httptest.NewRequest(http.MethodGet, "/api/expressions", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestListCharacters_Empty(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.GET("/api/characters", h.ListCharacters)

	req := httptest.NewRequest(http.MethodGet, "/api/characters", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d (body: %s)", w.Code, w.Body.String())
	}
	var resp models.Response
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if !resp.Success {
		t.Error("expected success=true")
	}
}

func TestCreateCharacter_Valid(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.POST("/api/characters", h.CreateCharacter)

	body := `{"name":"测试角色","prompt":"a cute anime girl","description":"单元测试"}`
	req := httptest.NewRequest(http.MethodPost, "/api/characters", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusCreated && w.Code != http.StatusOK {
		t.Fatalf("expected 200/201, got %d (body: %s)", w.Code, w.Body.String())
	}
	var resp models.Response
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if !resp.Success {
		t.Errorf("expected success=true, body: %s", w.Body.String())
	}
}

func TestCreateCharacter_MissingName(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.POST("/api/characters", h.CreateCharacter)

	body := `{"prompt":"a cute anime girl"}`
	req := httptest.NewRequest(http.MethodPost, "/api/characters", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing name, got %d", w.Code)
	}
}

func TestCreateCharacter_InvalidJSON(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.POST("/api/characters", h.CreateCharacter)

	body := `{invalid json`
	req := httptest.NewRequest(http.MethodPost, "/api/characters", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d", w.Code)
	}
}

func TestGetCharacter_NotFound(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.GET("/api/characters/:id", h.GetCharacter)

	req := httptest.NewRequest(http.MethodGet, "/api/characters/nonexistent", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestUpdateCharacter_NotFound(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.PUT("/api/characters/:id", h.UpdateCharacter)

	body := `{"name":"updated","description":"new"}`
	req := httptest.NewRequest(http.MethodPut, "/api/characters/nonexistent", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusNotFound && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 404/500 for nonexistent, got %d", w.Code)
	}
}

func TestDeleteCharacter_NotFound(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.DELETE("/api/characters/:id", h.DeleteCharacter)

	req := httptest.NewRequest(http.MethodDelete, "/api/characters/nonexistent", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError && w.Code != http.StatusNotFound {
		t.Errorf("expected 404/500 for nonexistent, got %d", w.Code)
	}
}

func TestGenerateImage_MissingPrompt(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.POST("/api/generate", h.GenerateImage)

	body := `{"width":512,"height":512}`
	req := httptest.NewRequest(http.MethodPost, "/api/generate", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing prompt, got %d", w.Code)
	}
}

func TestGetPythonScripts(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.GET("/api/scripts", h.GetPythonScripts)

	req := httptest.NewRequest(http.MethodGet, "/api/scripts", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var resp models.Response
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if !resp.Success {
		t.Error("expected success=true")
	}
}

func TestCacheStats(t *testing.T) {
	h := newTestHandler(t)
	r := gin.New()
	r.GET("/api/cache/stats", h.GetCacheStats)

	req := httptest.NewRequest(http.MethodGet, "/api/cache/stats", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

// TestResponseShape 验证 Response 结构体的 JSON 字段名（保证前端能正确解析）
func TestResponseShape(t *testing.T) {
	r := models.Response{Success: true, Message: "test", Data: "x"}
	b, _ := json.Marshal(r)
	got := string(b)
	for _, k := range []string{`"success"`, `"message"`, `"data"`} {
		if !contains(got, k) {
			t.Errorf("expected key %s in JSON: %s", k, got)
		}
	}
}

func contains(s, sub string) bool {
	return bytes.Contains([]byte(s), []byte(sub))
}
