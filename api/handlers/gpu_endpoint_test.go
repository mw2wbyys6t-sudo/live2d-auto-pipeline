package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"live2d-api/config"
	"live2d-api/services"
)

func gpuRequest(t *testing.T, h *Handler) *httptest.ResponseRecorder {
	t.Helper()
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/api/gpu", h.GetGPUStatus)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/gpu", nil))
	return w
}

// 解释器不可用时必须降级：前端自检页要能照常渲染，且响应不得回显
// 可执行文件名或本机路径（否则变成环境信息泄露）。
func TestGetGPUStatusDegradesWithoutLeakingDetails(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Python.PythonPath = "definitely-not-a-python-binary"
	h := &Handler{pythonBridge: services.NewPythonBridge(cfg)}

	w := gpuRequest(t, h)
	if w.Code != http.StatusOK {
		t.Fatalf("探测失败应回 200 降级载荷，实际 %d", w.Code)
	}
	body := w.Body.String()
	if strings.Contains(body, "definitely-not-a-python-binary") || strings.Contains(body, os.TempDir()) {
		t.Fatalf("响应回显了本机环境细节: %s", body)
	}

	var resp struct {
		Success bool                   `json:"success"`
		Data    map[string]interface{} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("响应不是合法 JSON: %v (%s)", err, body)
	}
	if resp.Data["query_ok"] != false {
		t.Errorf("探测失败时 query_ok 应为 false，实际 %v", resp.Data["query_ok"])
	}
	if _, ok := resp.Data["adapters"]; !ok {
		t.Error("降级载荷仍应提供 adapters 键，避免前端渲染项目数跳变")
	}
}

// handlersTestPython 与 services 包同策略：优先 LIVE2D_TEST_PYTHON，
// 否则在 PATH 上找可用解释器（Windows 上 python3 常是 Store 占位符，必须真跑一次）。
func handlersTestPython(t *testing.T) string {
	t.Helper()
	if p := os.Getenv("LIVE2D_TEST_PYTHON"); p != "" {
		return p
	}
	for _, name := range []string{"python3", "python"} {
		p, err := exec.LookPath(name)
		if err != nil {
			continue
		}
		if exec.Command(p, "-c", "").Run() == nil {
			return p
		}
	}
	t.Skip("没有可用的 Python 解释器")
	return ""
}

func TestGetGPUStatusReportsContractWithRealInterpreter(t *testing.T) {
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatalf("解析仓库根失败: %v", err)
	}
	cfg := config.DefaultConfig()
	cfg.Python.PythonPath = handlersTestPython(t)
	cfg.Python.ScriptsDir = root
	cfg.Python.TimeoutSec = 60
	h := &Handler{pythonBridge: services.NewPythonBridge(cfg)}

	w := gpuRequest(t, h)
	if w.Code != http.StatusOK {
		t.Fatalf("应回 200，实际 %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Data map[string]interface{} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("响应不是合法 JSON: %v", err)
	}
	if resp.Data["query_ok"] != true {
		t.Fatalf("真实解释器下 query_ok 应为 true，实际 %v", resp.Data["query_ok"])
	}
	for _, key := range []string{"available", "adapters", "runtime", "recommendation"} {
		if _, ok := resp.Data[key]; !ok {
			t.Errorf("缺少键 %q", key)
		}
	}
	rec, _ := resp.Data["recommendation"].(map[string]interface{})
	if device, _ := rec["device"].(string); device != "cuda" && device != "dml" && device != "cpu" {
		t.Errorf("recommendation.device 非法: %q", device)
	}
}
