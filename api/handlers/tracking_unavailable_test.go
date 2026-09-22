package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

// 前端的 /preview 依赖 /api/tracking/start|stop；在 Go 后端它们必须给出
// **明确的 501 + 指引**，而不是 404 —— 否则调用方会误以为是自己路由写错了。
func TestTrackingEndpointsReportNotImplemented(t *testing.T) {
	gin.SetMode(gin.TestMode)
	h := &Handler{}
	r := gin.New()
	r.POST("/api/tracking/start", h.TrackingUnavailable)
	r.POST("/api/tracking/stop", h.TrackingUnavailable)

	for _, path := range []string{"/api/tracking/start", "/api/tracking/stop"} {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, path, nil)
		r.ServeHTTP(w, req)

		if w.Code != http.StatusNotImplemented {
			t.Errorf("%s 应返回 501，实为 %d", path, w.Code)
		}
		var body map[string]interface{}
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
			t.Fatalf("%s 响应不是合法 JSON: %v", path, err)
		}
		if ok, _ := body["success"].(bool); ok {
			t.Errorf("%s 不得报告 success=true", path)
		}
		data, _ := body["data"].(map[string]interface{})
		if data == nil || data["available"] != false {
			t.Errorf("%s 不得声明 available=true，实为 %v", path, data)
		}
	}
}
