package handlers

// P2-4：未预期 5xx 不得把异常原文（含服务器绝对路径）回传客户端；
// 请求方错误（400）保留可读、无路径细节的业务提示。

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func psdPlanRequest(t *testing.T, h *Handler, imagePath string) *httptest.ResponseRecorder {
	t.Helper()
	r := gin.New()
	r.POST("/api/psd-plan", h.CreatePSDPlan)
	// 用 json.Marshal 构造请求体，不要手拼：Windows 路径含反斜杠
	// （C:\Users\...），直接拼进 JSON 字面量会产生非法转义（`\U`）。
	raw, err := json.Marshal(map[string]string{"image_path": imagePath})
	if err != nil {
		t.Fatalf("构造请求体失败: %v", err)
	}
	body := strings.NewReader(string(raw))
	req := httptest.NewRequest(http.MethodPost, "/api/psd-plan", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

// 攻击向量：输出根之外的绝对路径 → 400，且响应不得回显该路径
// （否则变成文件存在性 oracle）。
func TestPSDPlan_PathOutsideBaseIs400WithoutPathEcho(t *testing.T) {
	h := newTestHandler(t)
	outside := filepath.Join(t.TempDir(), "secret.png")
	if err := os.WriteFile(outside, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	w := psdPlanRequest(t, h, outside)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("根外路径应 400，实得 %d: %s", w.Code, w.Body.String())
	}
	resp := w.Body.String()
	if !strings.Contains(resp, "输出目录") {
		t.Errorf("应给出可读的归属校验提示，实得 %s", resp)
	}
	if strings.Contains(resp, outside) || strings.Contains(resp, "secret.png") {
		t.Errorf("响应不得回显被拒绝的路径/文件名: %s", resp)
	}
}

// 未预期内部错误（此处为根内但文件缺失）→ 固定 500 文案，
// 不泄露内部绝对路径或实现细节。
func TestPSDPlan_InternalFailureReturnsGenericMessage(t *testing.T) {
	h := newTestHandler(t)
	missing := filepath.Join(h.cfg.Output.BaseDir, "uploads", "nope.png")
	w := psdPlanRequest(t, h, missing)
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("内部失败应 500，实得 %d: %s", w.Code, w.Body.String())
	}
	resp := w.Body.String()
	if !strings.Contains(resp, "请稍后重试") {
		t.Errorf("500 应返回固定兜底文案，实得 %s", resp)
	}
	if strings.Contains(resp, missing) {
		t.Errorf("500 不得回显内部路径: %s", resp)
	}
}
