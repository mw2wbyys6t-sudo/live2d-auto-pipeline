package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/gin-gonic/gin"

	"live2d-api/services"
)

// newIsolatedOutputHandler 返回一个 output 目录指向独立临时目录的 Handler，
// 用于不依赖 Python/相机的端点契约测试。
func newIsolatedOutputHandler(t *testing.T) *Handler {
	t.Helper()
	h := newTestHandler(t)
	return h
}

// TestLatestGenerationEmptyOutput 无记录且无模型时必须如实回 404。
func TestLatestGenerationEmptyOutput(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h := newIsolatedOutputHandler(t)
	r := gin.New()
	r.GET("/api/generations/latest", h.LatestGeneration)

	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/generations/latest", nil))
	if w.Code != http.StatusNotFound {
		t.Fatalf("空 output 应返回 404，实为 %d", w.Code)
	}
}

// TestLatestGenerationFallbackScan 没有 latest_generation.json 时，
// 应回退扫描 output 下最新的 *.model3.json 并给出 model3_url / model_dir。
func TestLatestGenerationFallbackScan(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h := newIsolatedOutputHandler(t)
	modelDir := filepath.Join(h.cfg.Output.BaseDir, "live2d_exports", "char_test")
	if err := os.MkdirAll(modelDir, 0o755); err != nil {
		t.Fatal(err)
	}
	model3 := filepath.Join(modelDir, "model.model3.json")
	if err := os.WriteFile(model3, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}

	r := gin.New()
	r.GET("/api/generations/latest", h.LatestGeneration)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/generations/latest", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("存在模型时应返回 200，实为 %d: %s", w.Code, w.Body.String())
	}
	var body struct {
		Data map[string]interface{} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("响应不是合法 JSON: %v", err)
	}
	if got, _ := body.Data["model_dir"].(string); got != modelDir {
		t.Errorf("model_dir = %q, 期望 %q", got, modelDir)
	}
	if got, _ := body.Data["model3_url"].(string); got == "" {
		t.Errorf("model3_url 未被派生: %v", body.Data)
	}
}

// TestUploadRejectsNonImage 上传端点必须拒绝白名单以外的扩展名。
func TestUploadRejectsNonImage(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h := newIsolatedOutputHandler(t)
	r := gin.New()
	r.POST("/api/upload", h.UploadImage)

	body := &bytes.Buffer{}
	content := "--BND\r\nContent-Disposition: form-data; name=\"file\"; filename=\"evil.txt\"\r\nContent-Type: text/plain\r\n\r\npayload\r\n--BND--\r\n"
	body.WriteString(content)
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/upload", body)
	req.Header.Set("Content-Type", "multipart/form-data; boundary=BND")
	r.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf(".txt 上传应被拒绝（400），实为 %d", w.Code)
	}
}

// TestUploadAcceptsPng 白名单内的图片应成功落盘并返回 uploads 内的路径。
func TestUploadAcceptsPng(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h := newIsolatedOutputHandler(t)
	r := gin.New()
	r.POST("/api/upload", h.UploadImage)

	png := []byte{0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'}
	body := &bytes.Buffer{}
	body.WriteString("--BND\r\nContent-Disposition: form-data; name=\"file\"; filename=\"hero.png\"\r\nContent-Type: image/png\r\n\r\n")
	body.Write(png)
	body.WriteString("\r\n--BND--\r\n")
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/upload", body)
	req.Header.Set("Content-Type", "multipart/form-data; boundary=BND")
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("png 上传应成功，实为 %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Data struct {
			Path string `json:"path"`
			URL  string `json:"url"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("响应解析失败: %v", err)
	}
	if err := services.ValidateUploadedImagePath(h.cfg, resp.Data.Path); err != nil {
		t.Errorf("返回的 path 未通过 uploads 校验: %v", err)
	}
}

// TestSegmentRequiresImage 无图无记录时分层端点必须明确拒绝。
func TestSegmentRequiresImage(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h := newIsolatedOutputHandler(t)
	r := gin.New()
	r.POST("/api/segment", h.SegmentImage)

	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/api/segment",
		bytes.NewBufferString(`{"method":"kmeans"}`)))
	if w.Code != http.StatusBadRequest {
		t.Fatalf("无源图片应返回 400，实为 %d", w.Code)
	}
}

// TestServeOutputSubdirectory /output/*filepath 应能访问子目录内文件，
// 且目录穿越仍被拒绝。
func TestServeOutputSubdirectory(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h := newIsolatedOutputHandler(t)
	sub := filepath.Join(h.cfg.Output.BaseDir, "layers_1")
	if err := os.MkdirAll(sub, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(sub, "hair.png"), []byte("png"), 0o644); err != nil {
		t.Fatal(err)
	}

	r := gin.New()
	r.GET("/output/*filepath", h.ServeOutput)

	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/output/layers_1/hair.png", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("子目录文件应可访问，实为 %d", w.Code)
	}

	w2 := httptest.NewRecorder()
	r.ServeHTTP(w2, httptest.NewRequest(http.MethodGet, "/output/..%2F..%2Fetc%2Fpasswd", nil))
	if w2.Code == http.StatusOK {
		t.Fatalf("目录穿越不应成功: %d", w2.Code)
	}
}
