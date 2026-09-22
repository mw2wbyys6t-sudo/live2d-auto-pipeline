package handlers

import (
	"fmt"
	"time"

	"live2d-api/models"
	"live2d-api/services"
)

// ======================================================================
// 导出/构建阶段的实时进度上报
//
// Python 的 log.step 边界 → services.StageTracker → 这里 → WebSocket Hub。
// 响应契约（runtime_ready / blocker / 状态码）完全不变：进度只是旁路，
// 由 handlers.go 的调用点换成 exportLive2DModelTracked 顺带发出。
// ======================================================================

// exportStageReporter 把一次导出的阶段事件发到 Hub。
// publish 做成字段，是为了让单测在不起 Hub 的情况下观察发出的消息。
type exportStageReporter struct {
	jobID   string
	tracker *services.StageTracker
	publish func(models.WSMessage)
}

// newExportStageReporter 为一次导出建跟踪器。Hub 未启用（wsHub == nil，
// 例如纯单测）时返回 nil，调用方原样退回阻塞路径。
func (h *Handler) newExportStageReporter() *exportStageReporter {
	if h == nil || h.wsHub == nil {
		return nil
	}
	hub := h.wsHub
	reporter := &exportStageReporter{
		jobID:   fmt.Sprintf("export_%d", time.Now().UnixNano()),
		publish: func(msg models.WSMessage) { hub.Publish(msg) },
	}
	reporter.tracker = services.NewStageTracker(reporter.jobID, reporter.onStage)
	return reporter
}

func (r *exportStageReporter) onStage(evt services.StageEvent) {
	if r == nil || r.publish == nil {
		return
	}
	// 复用前端已认识的 "progress" 类型；结构化细节放 data，
	// 老客户端只读 stage/progress/message 也能渲染。
	r.publish(models.WSMessage{
		Type:     "progress",
		TaskID:   r.jobID,
		Stage:    evt.Name,
		Progress: evt.Percent,
		Message:  stageMessage(evt),
		Data:     evt,
		Time:     time.Now().UnixMilli(),
	})
}

// stageMessage 把阶段状态写成一句人话；状态词原样保留，不做美化式推断。
func stageMessage(evt services.StageEvent) string {
	prefix := fmt.Sprintf("[%d/%d] %s (%s)", evt.Step, evt.Total, evt.Name, evt.Status)
	if evt.Message == "" {
		return prefix
	}
	return prefix + " — " + evt.Message
}

// exportLive2DModelTracked 跑一次完整导出并实时上报阶段。
//
// 返回值与 pythonBridge.ExportLive2DModel 一致（含错误类型），因此
// exportFailureResponse / exportResponseData 的行为不受影响。
func (h *Handler) exportLive2DModelTracked(characterID, layersDir,
	outputDir string) (map[string]interface{}, error) {
	reporter := h.newExportStageReporter()
	if reporter == nil {
		return h.pythonBridge.ExportLive2DModel(characterID, layersDir, outputDir)
	}
	result, err := h.pythonBridge.ExportLive2DModelTracked(characterID, layersDir,
		outputDir, reporter.tracker)
	reporter.finish(result, err)
	return result, err
}

// finish 收尾：终端状态只在官方 Cubism Core 真的验收通过时才报 succeeded。
// 其余情形（构建失败 / 产物未验收 / 缺验收信息）分别报 failed / blocked，
// 并为从未上报进度的阶段补 not_reported。
func (r *exportStageReporter) finish(result map[string]interface{}, err error) {
	if r == nil || r.tracker == nil {
		return
	}
	if err != nil {
		r.tracker.Terminal(services.StageFailed, err.Error(), false)
		return
	}
	inner := unwrapPythonResult(result)
	moc3, _ := inner["moc3"].(map[string]interface{})
	// 与 exportResponseData 共用判定函数，进度和响应不会各说各话。
	ready, blocker := moc3Readiness(inner, moc3)
	if ready {
		r.tracker.Terminal(services.StageSucceeded, "模型已通过官方 Cubism Core 验收", true)
		return
	}
	if blocker == "" {
		blocker = "导出未产生可验收的模型"
	}
	r.tracker.Terminal(services.StageBlocked, blocker, false)
}
