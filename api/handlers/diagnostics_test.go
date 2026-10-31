package handlers

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
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
				Key string `json:"key"`
				OK  bool   `json:"ok"`
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

// 图像上游那一行的三种状态。
//
// 关键是"只有免密通道"必须判不通过：实测装机版九项全绿、页面写着
// 「环境就绪，可以开始生成」，而唯一可用的 pollinations 每次 402，
// 用户拿到的是一张几何占位图。绿灯的承诺兑现不了，比红灯更糟。
func TestImageProviderCheckStates(t *testing.T) {
	freeOnly := []map[string]interface{}{
		{"name": "pollinations", "requires_key": false},
		{"name": "local_placeholder", "requires_key": false},
	}
	withKey := []map[string]interface{}{
		{"name": "pollinations", "requires_key": false},
		{"name": "seedream", "requires_key": true},
	}

	cases := []struct {
		name      string
		available []map[string]interface{}
		err       error
		wantOK    bool
		wantIn    string
	}{
		{"只有免密通道", freeOnly, nil, false, "免密"},
		{"已配置密钥上游", withKey, nil, true, "1 个已配置"},
		{"查询失败", nil, errors.New("python down"), false, "查不到"},
		{"空列表", []map[string]interface{}{}, nil, false, "查不到"},
	}
	for _, c := range cases {
		got := imageProviderCheck(c.available, c.err)
		if got.OK != c.wantOK {
			t.Errorf("%s: OK = %v，应为 %v", c.name, got.OK, c.wantOK)
		}
		if !strings.Contains(got.Detail, c.wantIn) && !strings.Contains(joined(got.Issues), c.wantIn) {
			t.Errorf("%s: 说明里应含 %q，实得 Detail=%q Issues=%v", c.name, c.wantIn, got.Detail, got.Issues)
		}
	}
}

func joined(items []string) string { return strings.Join(items, " ") }

// 通过的资源行不该再挂失败解释 —— 实测装机页上两行绿勾配着
// 「缺失时回退肤色检测（精度更低）」，读起来像报错。
func TestAssetCheckWordingFollowsState(t *testing.T) {
	ok := assetCheck("tracker_model", "YuNet 人脸模型", true)
	if !ok.OK || strings.Contains(ok.Detail, "缺失") {
		t.Errorf("通过时不该出现缺失类措辞: OK=%v Detail=%q", ok.OK, ok.Detail)
	}
	if len(ok.Issues) != 0 {
		t.Errorf("通过时不该有 Issue，实得 %v", ok.Issues)
	}

	missing := assetCheck("tracker_model", "YuNet 人脸模型", false)
	if missing.OK {
		t.Error("缺失时必须判不通过")
	}
	if len(missing.Issues) == 0 || !strings.Contains(missing.Issues[0], "回退") {
		t.Errorf("缺失时要讲清后果，实得 %v", missing.Issues)
	}
}
