package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"live2d-api/config"
	"live2d-api/services"
)

// 当原生面捕二进制不可用时，/tracking/start 应返回 501 并附带提示；
// /tracking/status 始终返回 200；/tracking/stop 在未运行时返回 200。
func TestTrackingEndpointsWithoutBinary(t *testing.T) {
	gin.SetMode(gin.TestMode)

	// 使用一个不存在的二进制路径，确保 IsAvailable() 返回 false。
	// Enabled 显式开启，以测试"二进制不存在"路径（而非"配置禁用"路径）。
	cfg := &config.Config{}
	cfg.NativeTracker.Enabled = true
	cfg.NativeTracker.BinaryPath = "/nonexistent/face_tracker"

	hub := services.NewWSHub()
	go hub.Run()

	h := &Handler{
		cfg:          cfg,
		nativeBridge: services.NewNativeBridge(cfg, hub),
	}

	r := gin.New()
	r.POST("/api/tracking/start", h.StartTracking)
	r.POST("/api/tracking/stop", h.StopTracking)
	r.GET("/api/tracking/status", h.TrackingStatus)

	// /tracking/status — 始终 200
	{
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/api/tracking/status", nil)
		r.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Errorf("status 应返回 200，实为 %d", w.Code)
		}
		var body map[string]interface{}
		json.Unmarshal(w.Body.Bytes(), &body)
		data, _ := body["data"].(map[string]interface{})
		if data["available"] != false {
			t.Errorf("无二进制时 available 应为 false，实为 %v", data["available"])
		}
	}

	// /tracking/start — 二进制不存在 → 501
	{
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, "/api/tracking/start", nil)
		r.ServeHTTP(w, req)
		if w.Code != http.StatusNotImplemented {
			t.Errorf("start 在无二进制时应返回 501，实为 %d", w.Code)
		}
		var body map[string]interface{}
		json.Unmarshal(w.Body.Bytes(), &body)
		if ok, _ := body["success"].(bool); ok {
			t.Error("start 在无二进制时不得报告 success=true")
		}
	}

	// /tracking/stop — 未运行 → 200
	{
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, "/api/tracking/stop", nil)
		r.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Errorf("stop 在未运行时应返回 200，实为 %d", w.Code)
		}
	}
}
