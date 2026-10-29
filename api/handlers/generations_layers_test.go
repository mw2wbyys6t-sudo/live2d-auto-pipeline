package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"testing"

	"github.com/gin-gonic/gin"

	"live2d-api/config"
	"live2d-api/models"
	"live2d-api/services"
)

func newHistoryHandler(t *testing.T) *Handler {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Output.BaseDir = t.TempDir()
	return &Handler{cfg: cfg}
}

func recordHistory(t *testing.T, h *Handler, charDir, prompt string) {
	t.Helper()
	if err := os.MkdirAll(charDir, 0o755); err != nil {
		t.Fatalf("创建目录失败: %v", err)
	}
	img := filepath.Join(charDir, "image.png")
	if err := os.WriteFile(img, []byte("png"), 0o644); err != nil {
		t.Fatalf("写入图片失败: %v", err)
	}
	services.RecordLatestGeneration(h.cfg, &models.GenerateImageResponse{
		ImagePath:  img,
		ImageURL:   "/output/image.png",
		OutputDir:  charDir,
		PromptUser: prompt,
		Success:    true,
	})
}

func serveJSON(t *testing.T, h *Handler, route, handler string, target string) (*httptest.ResponseRecorder, map[string]interface{}) {
	t.Helper()
	gin.SetMode(gin.TestMode)
	r := gin.New()
	switch handler {
	case "generations":
		r.GET(route, h.ListGenerations)
	case "layers":
		r.GET(route, h.ListLayers)
	default:
		t.Fatalf("未知 handler %s", handler)
	}
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, target, nil))
	var body map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("响应不是 JSON: %v (%s)", err, w.Body.String())
	}
	return w, body
}

func TestListGenerationsEndpointReturnsHistory(t *testing.T) {
	h := newHistoryHandler(t)
	recordHistory(t, h, filepath.Join(h.cfg.Output.BaseDir, "characters", "c1"), "第一个")
	recordHistory(t, h, filepath.Join(h.cfg.Output.BaseDir, "characters", "c2"), "第二个")

	w, body := serveJSON(t, h, "/api/generations", "generations", "/api/generations")
	if w.Code != http.StatusOK {
		t.Fatalf("状态码 = %d: %s", w.Code, w.Body.String())
	}
	data, _ := body["data"].(map[string]interface{})
	items, _ := data["generations"].([]interface{})
	if len(items) != 2 {
		t.Fatalf("作品数 = %d", len(items))
	}
	first, _ := items[0].(map[string]interface{})
	if got := first["prompt"]; got != "第二个" {
		t.Fatalf("最新作品 prompt = %v", got)
	}
	if got := first["image_url"]; got != "/output/characters/c2/image.png" {
		t.Fatalf("image_url = %v", got)
	}

	w, body = serveJSON(t, h, "/api/generations", "generations", "/api/generations?limit=1")
	if w.Code != http.StatusOK {
		t.Fatalf("limit 请求状态码 = %d", w.Code)
	}
	data, _ = body["data"].(map[string]interface{})
	if items, _ := data["generations"].([]interface{}); len(items) != 1 {
		t.Fatalf("limit=1 返回 %d 条", len(items))
	}
}

func TestListLayersEndpoint(t *testing.T) {
	h := newHistoryHandler(t)
	dir := filepath.Join(h.cfg.Output.BaseDir, "layers_1")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("创建分层目录失败: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "face.png"), []byte("png"), 0o644); err != nil {
		t.Fatalf("写入图层失败: %v", err)
	}

	w, body := serveJSON(t, h, "/api/layers", "layers", "/api/layers?dir="+url.QueryEscape(dir))
	if w.Code != http.StatusOK {
		t.Fatalf("状态码 = %d: %s", w.Code, w.Body.String())
	}
	data, _ := body["data"].(map[string]interface{})
	layers, _ := data["layers"].([]interface{})
	if len(layers) != 1 {
		t.Fatalf("图层数 = %d", len(layers))
	}

	w, _ = serveJSON(t, h, "/api/layers", "layers", "/api/layers")
	if w.Code != http.StatusBadRequest {
		t.Fatalf("缺 dir 应 400，实际 %d", w.Code)
	}
	w, _ = serveJSON(t, h, "/api/layers", "layers", "/api/layers?dir="+url.QueryEscape(t.TempDir()))
	if w.Code != http.StatusBadRequest {
		t.Fatalf("越界目录应 400，实际 %d", w.Code)
	}
	w, _ = serveJSON(t, h, "/api/layers", "layers", "/api/layers?dir="+url.QueryEscape(filepath.Join(h.cfg.Output.BaseDir, "nope")))
	if w.Code != http.StatusNotFound {
		t.Fatalf("不存在目录应 404，实际 %d", w.Code)
	}
}
