package handlers

// P3-1（H1）：POST /api/characters 的 ref_image 与 /api/characters/:id/reference
// 必须共用同一道 uploads 目录归属校验，拒绝任意服务器路径（防任意文件复制）。

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

func createCharacterRequest(t *testing.T, h *Handler, body string) *httptest.ResponseRecorder {
	t.Helper()
	r := gin.New()
	r.POST("/api/characters", h.CreateCharacter)
	req := httptest.NewRequest(http.MethodPost, "/api/characters", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

// characterBody 用 json.Marshal 构造请求体，不要手拼：Windows 路径含反斜杠
// （C:\Users\...），直接拼进 JSON 字面量会产生非法转义（`\U`）。
func characterBody(t *testing.T, name, refImage string) string {
	t.Helper()
	raw, err := json.Marshal(map[string]string{"name": name, "ref_image": refImage})
	if err != nil {
		t.Fatalf("构造请求体失败: %v", err)
	}
	return string(raw)
}

func TestCreateCharacter_RejectsRefImageOutsideUploads(t *testing.T) {
	h := newTestHandler(t)
	outside := filepath.Join(t.TempDir(), "secret.png")
	if err := os.WriteFile(outside, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	w := createCharacterRequest(t, h, characterBody(t, "小坏", outside))
	if w.Code != http.StatusBadRequest {
		t.Fatalf("uploads 之外的 ref_image 必须 400，实得 %d: %s", w.Code, w.Body.String())
	}
	resp := w.Body.String()
	if strings.Contains(resp, outside) || strings.Contains(resp, "secret.png") {
		t.Errorf("响应不得回显被拒路径: %s", resp)
	}
}

func TestCreateCharacter_AllowsRefImageInsideUploads(t *testing.T) {
	h := newTestHandler(t)
	uploads := filepath.Join(h.cfg.Output.BaseDir, "uploads")
	if err := os.MkdirAll(uploads, 0o755); err != nil {
		t.Fatal(err)
	}
	ok := filepath.Join(uploads, "front.png")
	if err := os.WriteFile(ok, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	w := createCharacterRequest(t, h, characterBody(t, "小好", ok))
	if w.Code != http.StatusCreated {
		t.Fatalf("uploads 内的合法 ref_image 应放行（201），实得 %d: %s", w.Code, w.Body.String())
	}
}
