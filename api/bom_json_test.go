package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

// Windows 工具（PowerShell Set-Content -Encoding UTF8、记事本另存 UTF-8）会给
// JSON body 加 UTF-8 BOM。Go 的 encoding/json 不认它，直接报
// "invalid character '\ufeff' looking for beginning of value" —— 用户只看到
// 「参数错误」，看不出是编码问题。中间件必须在解析前剥掉 BOM。
func TestValidateRequestMiddlewareStripsUTF8BOM(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(validateRequestMiddleware())
	router.POST("/echo", func(c *gin.Context) {
		var req struct {
			ImagePath string `json:"image_path"`
		}
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}
		c.JSON(http.StatusOK, gin.H{"image_path": req.ImagePath})
	})

	payload := []byte(`{"image_path":"F:\\前沿虚拟偶像\\output\\a.png"}`)
	body := append([]byte{0xEF, 0xBB, 0xBF}, payload...)
	req := httptest.NewRequest(http.MethodPost, "/echo", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "bom-test")
	w := httptest.NewRecorder()

	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("带 BOM 的 JSON 应被正常解析，实际 %d: %s", w.Code, w.Body.String())
	}
	var resp map[string]string
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("响应不是合法 JSON: %v", err)
	}
	if resp["image_path"] != `F:\前沿虚拟偶像\output\a.png` {
		t.Fatalf("路径被 BOM 处理破坏: %q", resp["image_path"])
	}
}

// 没有 BOM 的 JSON 行为不变。
func TestValidateRequestMiddlewareKeepsPlainJSON(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(validateRequestMiddleware())
	router.POST("/echo", func(c *gin.Context) {
		var req struct {
			OK bool `json:"ok"`
		}
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}
		c.JSON(http.StatusOK, gin.H{"ok": req.OK})
	})

	req := httptest.NewRequest(http.MethodPost, "/echo", bytes.NewReader([]byte(`{"ok":true}`)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "bom-test")
	w := httptest.NewRecorder()

	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK || !bytes.Contains(w.Body.Bytes(), []byte(`"ok":true`)) {
		t.Fatalf("普通 JSON 不应受影响，实际 %d: %s", w.Code, w.Body.String())
	}
}

// multipart 上传的 body 绝不能被 BOM 逻辑改写：文件头必须原样交给 handler。
func TestValidateRequestMiddlewareLeavesMultipartAlone(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(validateRequestMiddleware())
	router.POST("/upload", func(c *gin.Context) {
		buf := make([]byte, 3)
		_, _ = c.Request.Body.Read(buf)
		c.JSON(http.StatusOK, gin.H{"head": buf})
	})

	body := append([]byte{0xEF, 0xBB, 0xBF}, []byte("rest-of-file")...)
	req := httptest.NewRequest(http.MethodPost, "/upload", bytes.NewReader(body))
	req.Header.Set("Content-Type", "multipart/form-data; boundary=x")
	req.Header.Set("User-Agent", "bom-test")
	w := httptest.NewRecorder()

	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("multipart 请求应通过，实际 %d", w.Code)
	}
	if !bytes.Contains(w.Body.Bytes(), []byte("77u/")) {
		// gin 把 []byte 序列化成 base64：77u/ 就是 EF BB BF，说明 BOM 原样保留
		t.Fatalf("multipart body 被改写了: %s", w.Body.String())
	}
}
