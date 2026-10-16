package handlers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"live2d-api/models"
	"live2d-api/services"
)

// modelsSvc 返回可选模型下载器（构造时已用 HfCache 初始化；兜底惰性重建）。
func (h *Handler) modelsSvc() *services.ModelDownloader {
	if h.modelDL == nil {
		h.modelDL = services.NewModelDownloader(h.cfg.Python.HfCache)
	}
	return h.modelDL
}

// ListOptionalModels GET /api/models/optional — 重模型清单 + 下载/就绪状态。
func (h *Handler) ListOptionalModels(c *gin.Context) {
	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Data:    gin.H{"components": h.modelsSvc().List()},
	})
}

// StartOptionalModel POST /api/models/optional/download {"name": "..."}
func (h *Handler) StartOptionalModel(c *gin.Context) {
	var req struct {
		Name string `json:"name" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "缺少 name 参数"})
		return
	}
	if err := h.modelsSvc().Start(req.Name); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: err.Error()})
		return
	}
	c.JSON(http.StatusOK, models.Response{Success: true, Message: "下载已开始", Data: h.modelsSvc().Status(req.Name)})
}

// CancelOptionalModel POST /api/models/optional/cancel {"name": "..."}
func (h *Handler) CancelOptionalModel(c *gin.Context) {
	var req struct {
		Name string `json:"name" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{Success: false, Error: "缺少 name 参数"})
		return
	}
	h.modelsSvc().Cancel(req.Name)
	c.JSON(http.StatusOK, models.Response{Success: true, Data: h.modelsSvc().Status(req.Name)})
}
