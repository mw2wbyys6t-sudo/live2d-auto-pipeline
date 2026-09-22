package handlers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"

	"live2d-api/config"
	"live2d-api/services"
)

// 导出超时必须是 504 + 预算说明，不能伪装成 500，也不能被当成模型坏了。

func newExportTestHandler() *Handler {
	cfg := config.DefaultConfig()
	return &Handler{cfg: cfg}
}

func respondExportFailure(t *testing.T, err error) (int, map[string]interface{}) {
	t.Helper()
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(recorder)
	newExportTestHandler().exportFailureResponse(c, err)

	var body map[string]interface{}
	if decodeErr := json.Unmarshal(recorder.Body.Bytes(), &body); decodeErr != nil {
		t.Fatalf("响应不是 JSON: %v / %s", decodeErr, recorder.Body.String())
	}
	return recorder.Code, body
}

func TestExportTimeoutBecomes504WithBudget(t *testing.T) {
	code, body := respondExportFailure(t,
		fmt.Errorf("%w（2m30s）: Python执行超时", services.ErrPythonTimeout))
	if code != http.StatusGatewayTimeout {
		t.Fatalf("超时应回 504，实得 %d (%v)", code, body)
	}
	if body["success"] != false {
		t.Errorf("超时不得标 success=true: %v", body["success"])
	}
	data, _ := body["data"].(map[string]interface{})
	if data == nil {
		t.Fatalf("缺少 data 字段: %v", body)
	}
	if budget, _ := data["timeout_budget_seconds"].(float64); budget <= 0 {
		t.Errorf("应回传超时预算，实得 %v", data["timeout_budget_seconds"])
	}
	if blocker, _ := data["blocker"].(string); blocker == "" {
		t.Errorf("应给出可读 blocker")
	}
}

func TestOtherExportErrorsStay500(t *testing.T) {
	code, body := respondExportFailure(t, fmt.Errorf("Python执行失败: boom"))
	if code != http.StatusInternalServerError {
		t.Fatalf("非超时应回 500，实得 %d (%v)", code, body)
	}
}

func TestExportTimeoutBudgetFollowsConfig(t *testing.T) {
	cfg := config.DefaultConfig()
	if got := cfg.GetExportTimeout(); got != 150*time.Second {
		t.Errorf("默认预算应为 150s，实得 %v", got)
	}
	// 超过 HTTP 写超时的预算没有意义：连接会先被服务器掐断，客户端拿不到我们的错误
	cfg.Python.ExportTimeoutSec = 600
	if got := cfg.GetExportTimeout(); got >= cfg.Server.WriteTimeout {
		t.Errorf("预算 %v 必须被压到写超时 %v 之下", got, cfg.Server.WriteTimeout)
	}
}
