package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"live2d-api/config"
	"live2d-api/services"
)

// newGPUDiagnosticsHandler 用真实解释器 + 临时 core/gpu_probe 桩构造 Handler，
// 让诊断页的 GPU 行在任意机器上都有确定输入（NewHandler 会补齐 nativeBridge 等依赖）。
func newGPUDiagnosticsHandler(t *testing.T, pythonPath, scriptsDir string) *Handler {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Python.PythonPath = pythonPath
	cfg.Python.ScriptsDir = scriptsDir
	cfg.Python.TimeoutSec = 60
	cfg.Output.BaseDir = t.TempDir()
	return NewHandler(cfg, services.NewImageGenerator(cfg), services.NewRequestCache(cfg.Cache))
}

func writeGPUProbeStub(t *testing.T, probeJSON string) string {
	t.Helper()
	root := t.TempDir()
	coreDir := filepath.Join(root, "core")
	if err := os.MkdirAll(coreDir, 0o755); err != nil {
		t.Fatalf("创建临时 core 包失败: %v", err)
	}
	// 桩返回体保持 JSON 写法：用 json.loads 解析，避免把 true/false
	// 当 Python 字面量写进源码（NameError: name 'true' is not defined）。
	stub := "import json\n\n\ndef probe_gpu():\n    return json.loads(r'''" + probeJSON + "''')\n"
	if err := os.WriteFile(filepath.Join(coreDir, "gpu_probe.py"), []byte(stub), 0o644); err != nil {
		t.Fatalf("写入 core/gpu_probe 桩失败: %v", err)
	}
	return root
}

func diagnosticsChecks(t *testing.T, h *Handler) []map[string]interface{} {
	t.Helper()
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/api/diagnostics", h.GetDiagnostics)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/diagnostics", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("diagnostics 应返回 200，实际 %d: %s", w.Code, w.Body.String())
	}
	var body struct {
		Data struct {
			Checks []map[string]interface{} `json:"checks"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("响应非 JSON: %v (%s)", err, w.Body.String())
	}
	return body.Data.Checks
}

func findCheck(checks []map[string]interface{}, key string) map[string]interface{} {
	for _, c := range checks {
		if k, _ := c["key"].(string); k == key {
			return c
		}
	}
	return nil
}

// 有显卡但运行时只有 CPU provider：不能报绿，必须说明「跑在 CPU 上」。
func TestGetDiagnosticsFlagsGPUUnavailableToRuntime(t *testing.T) {
	root := writeGPUProbeStub(t, `{"available": true, "adapters": [{"name": "GameViewer Virtual Display Adapter", "vendor": "OTHER"}, {"name": "NVIDIA GeForce RTX 2060", "vendor": "NVIDIA", "driver_version": "32.0.15.8183", "vram_bytes": null}], "runtime": {"onnxruntime_available": true, "onnxruntime_providers": ["CPUExecutionProvider"], "torch_available": false, "torch_cuda_available": false, "torch_device_name": null}, "recommendation": {"device": "cpu"}}`)
	h := newGPUDiagnosticsHandler(t, handlersTestPython(t), root)

	checks := diagnosticsChecks(t, h)
	gpu := findCheck(checks, "gpu")
	if gpu == nil {
		t.Fatal("诊断页应包含 GPU 检查项")
	}
	if gpu["ok"] != false {
		t.Errorf("硬件在但运行时用不上时 ok 应为 false，实际 %v", gpu["ok"])
	}
	joined := checkIssuesText(gpu)
	if !strings.Contains(joined, "CPU") {
		t.Errorf("issue 应说明当前跑在 CPU 上，实际 %q", joined)
	}
	if !strings.Contains(joined, "RTX 2060") {
		t.Errorf("issue 应点出具体显卡，便于用户判断该装什么，实际 %q", joined)
	}
	// 虚拟显示器（远程桌面/采集卡）不是显卡，写进结论会让人白装运行时。
	if strings.Contains(joined, "GameViewer") {
		t.Errorf("issue 不应把虚拟显示器当成已检测到的显卡，实际 %q", joined)
	}
}

// 运行时可用（推荐 cuda）时应为绿，且 Detail 带上设备与推荐值。
func TestGetDiagnosticsMarksGPUUsableWhenRuntimeSupportsIt(t *testing.T) {
	root := writeGPUProbeStub(t, `{"available": true, "adapters": [{"name": "NVIDIA GeForce RTX 2060", "vendor": "NVIDIA", "driver_version": "32.0.15.8183", "vram_bytes": null}], "runtime": {"onnxruntime_available": true, "onnxruntime_providers": ["CUDAExecutionProvider", "CPUExecutionProvider"], "torch_available": true, "torch_cuda_available": true, "torch_device_name": "NVIDIA GeForce RTX 2060"}, "recommendation": {"device": "cuda"}}`)
	h := newGPUDiagnosticsHandler(t, handlersTestPython(t), root)

	gpu := findCheck(diagnosticsChecks(t, h), "gpu")
	if gpu == nil {
		t.Fatal("诊断页应包含 GPU 检查项")
	}
	if gpu["ok"] != true {
		t.Errorf("运行时可用时 ok 应为 true，实际 %v", gpu["ok"])
	}
	if detail, _ := gpu["detail"].(string); !strings.Contains(detail, "cuda") {
		t.Errorf("detail 应包含推荐设备 cuda，实际 %q", detail)
	}
}

// 探测失败（解释器不可用）不能让诊断页缺行：给出行并说明不可用。
func TestGetDiagnosticsKeepsGPURowWhenProbeFails(t *testing.T) {
	h := newGPUDiagnosticsHandler(t, "definitely-not-a-python-binary", t.TempDir())

	gpu := findCheck(diagnosticsChecks(t, h), "gpu")
	if gpu == nil {
		t.Fatal("探测失败时也应保留 GPU 检查行")
	}
	if gpu["ok"] != false {
		t.Errorf("探测失败应记为不通过，实际 %v", gpu["ok"])
	}
	body := strings.ToLower(checkAllText(gpu))
	if strings.Contains(body, "definitely-not-a-python-binary") || strings.Contains(body, strings.ToLower(os.TempDir())) {
		t.Errorf("诊断行不得回显本机环境细节: %v", gpu)
	}
}

// 诊断页不能吃到 /api/gpu 留下的 30s 旧结论：用户装完运行时再点诊断，
// 看到的必须是新状态。
func TestGetDiagnosticsReprobesGPUFreshlyAfterCachedResult(t *testing.T) {
	python := handlersTestPython(t)
	root := writeGPUProbeStub(t, `{"available": true, "adapters": [{"name": "NVIDIA GeForce RTX 2060", "vendor": "NVIDIA"}], "runtime": {"onnxruntime_available": true, "onnxruntime_providers": ["CPUExecutionProvider"]}, "recommendation": {"device": "cpu"}}`)
	h := newGPUDiagnosticsHandler(t, python, root)

	// 先把结论写进缓存（/api/gpu 走的就是这份缓存）。
	gin.SetMode(gin.TestMode)
	cached := gin.New()
	cached.GET("/api/gpu", h.GetGPUStatus)
	cached.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/api/gpu", nil))

	// 用户这时装好了 CUDA 运行时：桩输出变了。
	writeGPUProbeStubAt(t, root, `{"available": true, "adapters": [{"name": "NVIDIA GeForce RTX 2060", "vendor": "NVIDIA"}], "runtime": {"onnxruntime_available": true, "onnxruntime_providers": ["CUDAExecutionProvider", "CPUExecutionProvider"], "torch_cuda_available": true}, "recommendation": {"device": "cuda"}}`)

	gpu := findCheck(diagnosticsChecks(t, h), "gpu")
	if gpu == nil {
		t.Fatal("诊断页应包含 GPU 检查项")
	}
	if gpu["ok"] != true {
		t.Errorf("诊断页应重探到安装后的新状态（ok=true），实际 %v（detail=%v）", gpu["ok"], gpu["detail"])
	}
}

func writeGPUProbeStubAt(t *testing.T, root, probeJSON string) {
	t.Helper()
	stub := "import json\n\n\ndef probe_gpu():\n    return json.loads(r'''" + probeJSON + "''')\n"
	if err := os.WriteFile(filepath.Join(root, "core", "gpu_probe.py"), []byte(stub), 0o644); err != nil {
		t.Fatalf("改写 core/gpu_probe 桩失败: %v", err)
	}
}

func checkIssuesText(check map[string]interface{}) string {
	issues, _ := check["issues"].([]interface{})
	parts := make([]string, 0, len(issues))
	for _, i := range issues {
		if s, ok := i.(string); ok {
			parts = append(parts, s)
		}
	}
	return strings.Join(parts, " ")
}

func checkAllText(check map[string]interface{}) string {
	parts := []string{checkIssuesText(check)}
	for _, key := range []string{"label", "detail"} {
		if s, ok := check[key].(string); ok {
			parts = append(parts, s)
		}
	}
	return strings.Join(parts, " ")
}
