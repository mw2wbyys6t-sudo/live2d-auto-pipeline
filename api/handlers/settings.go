package handlers

import (
	"context"
	"errors"
	"net/http"

	"github.com/gin-gonic/gin"

	"live2d-api/models"
	"live2d-api/services"
)

// settingsPayload 把设置与上游运行时状态拼成设置页可直接渲染的结构。
// 注意：keys 只有布尔，永不包含 Key 内容或掩码。
func (h *Handler) settingsPayload(ctx context.Context) gin.H {
	settings := h.settings.Get()
	detail, vault, err := h.settings.ProviderDetail(ctx)
	if err != nil {
		detail = []map[string]interface{}{}
	}
	keys := map[string]bool{"ark": false, "seedream": false, "sensenova": false, "openai": false}
	for _, item := range detail {
		name, _ := item["name"].(string)
		available, _ := item["available"].(bool)
		requiresKey, _ := item["requires_key"].(bool)
		if !requiresKey {
			continue
		}
		switch name {
		case "seedream":
			// Seedream provider 读的是 ARK_API_KEY，两个名字指向同一把 Key。
			keys["seedream"] = available
			keys["ark"] = available
		case "sensenova":
			keys["sensenova"] = available
		case "openai":
			keys["openai"] = available
		}
	}
	return gin.H{
		"engines":          settings.Engines,
		"providers":        settings.Providers,
		"generation":       settings.Generation,
		"custom_providers": settings.CustomProviders,
		"keys":             keys,
		"vault_available":  vault,
		"provider_status":  detail,
	}
}

// GetSettings 返回设置页需要的全部非敏感配置与 Key 配置状态。
func (h *Handler) GetSettings(c *gin.Context) {
	c.JSON(http.StatusOK, models.Response{Success: true, Data: h.settingsPayload(c.Request.Context())})
}

// UpdateSettings 保存设置：Key 走加密保险库，其余写 settings.json。
func (h *Handler) UpdateSettings(c *gin.Context) {
	var req services.SettingsUpdate
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	_, warnings, err := h.settings.Update(req)
	if err != nil {
		if errors.Is(err, services.ErrInvalidSettings) {
			// 校验类错误文案是固定文案，可直接回传；不含 Key 与路径。
			c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
			return
		}
		h.serverError(c, "保存设置", err)
		return
	}
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Message: "设置已保存",
		Data: gin.H{
			"settings": h.settingsPayload(c.Request.Context()),
			"warnings": warnings,
		},
	})
}

// TestProvider 对单个上游做一次真实出图测试（会消耗一次额度）。
func (h *Handler) TestProvider(c *gin.Context) {
	var req struct {
		Provider string `json:"provider"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "参数错误: " + err.Error()})
		return
	}
	result, err := h.settings.TestProvider(c.Request.Context(), req.Provider)
	switch {
	case err == nil:
		c.JSON(http.StatusOK, models.Response{Success: true, Data: result})
	case errors.Is(err, services.ErrInvalidSettings):
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
	case errors.Is(err, services.ErrProbeBusy), errors.Is(err, services.ErrProbeTooSoon):
		c.JSON(http.StatusTooManyRequests, models.Response{Success: false, Error: err.Error()})
	default:
		h.serverError(c, "连接测试", err)
	}
}
