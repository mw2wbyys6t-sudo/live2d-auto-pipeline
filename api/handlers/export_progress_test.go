package handlers

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"testing"

	"live2d-api/config"
	"live2d-api/models"
	"live2d-api/services"
)

// 导出进度的对外形状：进度只是旁路，终端状态必须与响应契约同源。

func testExportReporter() (*exportStageReporter, *[]models.WSMessage) {
	var mu sync.Mutex
	sent := &[]models.WSMessage{}
	reporter := &exportStageReporter{
		jobID: "export_test",
		publish: func(msg models.WSMessage) {
			mu.Lock()
			*sent = append(*sent, msg)
			mu.Unlock()
		},
	}
	reporter.tracker = services.NewStageTracker(reporter.jobID, reporter.onStage)
	return reporter, sent
}

func statusSequence(t *testing.T, sent []models.WSMessage) []string {
	t.Helper()
	out := make([]string, 0, len(sent))
	for _, msg := range sent {
		evt := eventOf(t, msg.Data)
		out = append(out, fmt.Sprintf("%d:%s", evt.Step, evt.Status))
	}
	return out
}

// eventOf 兼容两种形状：内存里直接是 StageEvent，经 Hub 的 JSON 往返后是 map。
func eventOf(t *testing.T, data interface{}) services.StageEvent {
	t.Helper()
	if evt, ok := data.(services.StageEvent); ok {
		return evt
	}
	raw, err := json.Marshal(data)
	if err != nil {
		t.Fatalf("data 无法序列化: %v", err)
	}
	var evt services.StageEvent
	if err := json.Unmarshal(raw, &evt); err != nil {
		t.Fatalf("data 不是阶段事件: %v / %s", err, raw)
	}
	return evt
}

func lastEvent(t *testing.T, sent []models.WSMessage) services.StageEvent {
	t.Helper()
	if len(sent) == 0 {
		t.Fatal("一条进度都没有发出")
	}
	return eventOf(t, sent[len(sent)-1].Data)
}

func countStatus(t *testing.T, sent []models.WSMessage, status string) int {
	t.Helper()
	total := 0
	for _, msg := range sent {
		if eventOf(t, msg.Data).Status == status {
			total++
		}
	}
	return total
}

func readyResult(ready bool, blocker string) map[string]interface{} {
	moc3 := map[string]interface{}{"moc3_written": ready, "runtime_ready": ready}
	if blocker != "" {
		moc3["moc3_blocker"] = blocker
	}
	return map[string]interface{}{"result": map[string]interface{}{
		"success":     true,
		"output_dir":  "/tmp/char",
		"model3_json": "/tmp/char/char.model3.json",
		"moc3":        moc3,
	}}
}

func TestExportReporterTerminalMatchesResponseContract(t *testing.T) {
	tests := []struct {
		name         string
		result       map[string]interface{}
		err          error
		wantStatus   string
		wantMsgIn    string
		wantVerified bool
	}{
		{
			name: "官方内核验收通过", result: readyResult(true, ""),
			wantStatus: "succeeded", wantVerified: true,
		},
		{
			name: "moc3 被内核拒绝", result: readyResult(false, "官方内核拒绝: inconsistent"),
			wantStatus: "blocked", wantMsgIn: "官方内核拒绝",
		},
		{
			name: "缺验收信息", result: map[string]interface{}{"result": map[string]interface{}{
				"success": true,
			}},
			wantStatus: "blocked", wantMsgIn: "未经官方 Cubism Core 验收",
		},
		{
			name: "构建失败", result: nil, err: fmt.Errorf("Python执行失败: boom"),
			wantStatus: "failed", wantMsgIn: "boom",
		},
		{
			name: "超时", result: nil,
			err:        fmt.Errorf("%w（2m30s）: Python执行超时", services.ErrPythonTimeout),
			wantStatus: "failed", wantMsgIn: "Python执行超时",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			reporter, sent := testExportReporter()
			// Python 只报到了第 9 步进入与推进、第 10 步进入（无收尾）。
			reporter.tracker.Observe(services.StageEvent{Step: 9, Total: 10,
				Name: "Compiling moc3", Status: services.StageStarted, Percent: 80})
			reporter.tracker.Observe(services.StageEvent{Step: 9, Total: 10,
				Name: "Compiling moc3", Status: services.StageAdvanced, Percent: 90})
			reporter.tracker.Observe(services.StageEvent{Step: 10, Total: 10,
				Name: "Validating model", Status: services.StageStarted, Percent: 90})
			reporter.finish(tc.result, tc.err)

			evt := lastEvent(t, *sent)
			if evt.Status != tc.wantStatus {
				t.Errorf("终端状态 = %s，期望 %s（%v）", evt.Status, tc.wantStatus,
					statusSequence(t, *sent))
			}
			if evt.Verified != tc.wantVerified {
				t.Errorf("verified = %v，期望 %v", evt.Verified, tc.wantVerified)
			}
			if evt.Percent == 100 && !tc.wantVerified {
				t.Errorf("未验收却给了 100%%: %#v", evt)
			}
			if tc.wantMsgIn != "" && !strings.Contains(evt.Message, tc.wantMsgIn) {
				t.Errorf("终端消息 %q 未包含 %q", evt.Message, tc.wantMsgIn)
			}
			for _, state := range statusSequence(t, *sent) {
				if strings.HasSuffix(state, ":succeeded") && !tc.wantVerified {
					t.Errorf("未验收却上报了 succeeded: %v", statusSequence(t, *sent))
				}
			}
			// 第 9 步的编译结果由 moc3_blocker 说话，阶段边界永远是 advanced/started。
			if got := countStatus(t, *sent, services.StageSucceeded); got > 1 {
				t.Errorf("succeeded 只能出现在终端事件，实得 %d 条", got)
			}
		})
	}
}

func TestExportReporterFillsUnreportedStages(t *testing.T) {
	reporter, sent := testExportReporter()
	reporter.tracker.Observe(services.StageEvent{Step: 1, Total: 10,
		Name: "Generating meshes", Status: services.StageStarted})
	reporter.tracker.Observe(services.StageEvent{Step: 1, Total: 10,
		Name: "Generating meshes", Status: services.StageAdvanced})
	reporter.finish(readyResult(true, ""), nil)

	sequence := statusSequence(t, *sent)
	// 只有第 1 步上报过：2..10 都要补 not_reported，一个都不能少。
	if got := countStatus(t, *sent, services.StageNotReported); got != 9 {
		t.Errorf("应补报 9 个未上报阶段，实得 %d: %v", got, sequence)
	}
	for _, state := range sequence {
		if state == "2:advanced" || state == "2:succeeded" {
			t.Errorf("第 2 步从未上报却被判完成: %v", sequence)
		}
	}
	if sequence[len(sequence)-1] != "0:succeeded" {
		t.Errorf("终端事件必须是最后一条: %v", sequence)
	}
}

func TestExportReporterFailureMarksOpenStageFailed(t *testing.T) {
	reporter, sent := testExportReporter()
	reporter.tracker.Observe(services.StageEvent{Step: 4, Total: 10,
		Name: "Setting up deformers", Status: services.StageStarted})
	reporter.tracker.Abort("构建中断: exit status 1") // 桥接层在中断时做的事
	reporter.finish(nil, fmt.Errorf("Python执行失败: exit status 1"))

	sequence := statusSequence(t, *sent)
	if sequence[1] != "4:failed" {
		t.Errorf("中断的阶段应记为 failed: %v", sequence)
	}
	for _, state := range sequence {
		if strings.HasSuffix(state, ":advanced") {
			t.Errorf("失败导出里出现了 advanced: %v", sequence)
		}
	}
	if got := countStatus(t, *sent, services.StageNotReported); got != 9 {
		t.Errorf("失败时其余 1..3、5..10 共 9 步要补报，实得 %d: %v", got, sequence)
	}
}

func TestExportProgressWireShape(t *testing.T) {
	reporter, sent := testExportReporter()
	reporter.tracker.Observe(services.StageEvent{Step: 3, Total: 10,
		Name: "Building bone hierarchy", Status: services.StageStarted, Percent: 20})
	if len(*sent) != 1 {
		t.Fatalf("应发出 1 条消息，实得 %d", len(*sent))
	}
	msg := (*sent)[0]
	if msg.Type != "progress" || msg.TaskID != "export_test" {
		t.Errorf("type/task_id 不对: %#v", msg)
	}
	if msg.Stage != "Building bone hierarchy" || msg.Time == 0 {
		t.Errorf("stage/time 不对: %#v", msg)
	}
	if !strings.Contains(msg.Message, "[3/10]") || !strings.Contains(msg.Message, "started") {
		t.Errorf("message 应带阶段号与状态词: %q", msg.Message)
	}
	raw, err := json.Marshal(msg)
	if err != nil {
		t.Fatalf("WS 消息无法序列化: %v", err)
	}
	var wire map[string]interface{}
	if err := json.Unmarshal(raw, &wire); err != nil {
		t.Fatalf("WS 消息反序列化失败: %v", err)
	}
	for _, key := range []string{"type", "task_id", "stage", "progress", "message", "data"} {
		if _, ok := wire[key]; !ok {
			t.Errorf("前端读到的字段 %s 缺失: %s", key, raw)
		}
	}
	data, _ := wire["data"].(map[string]interface{})
	for _, key := range []string{"job_id", "step", "total", "status", "verified", "percent"} {
		if _, ok := data[key]; !ok {
			t.Errorf("data.%s 缺失: %s", key, raw)
		}
	}
}

func TestProgressDisabledWithoutHub(t *testing.T) {
	if (&Handler{}).newExportStageReporter() != nil {
		t.Error("没有 Hub 时不该建进度通道")
	}
	if (&Handler{cfg: config.DefaultConfig(), wsHub: services.NewWSHub()}).
		newExportStageReporter() == nil {
		t.Error("有 Hub 时应启用进度通道")
	}
}

// TestExportWithoutHubKeepsLegacyPath：wsHub 为 nil（现有单测/内嵌用法）时
// 必须走原来的阻塞导出，行为与响应契约一字不变。
func TestExportWithoutHubKeepsLegacyPath(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Python.PythonPath = "definitely-not-a-python-binary"
	h := &Handler{cfg: cfg, pythonBridge: services.NewPythonBridge(cfg)}

	result, err := h.exportLive2DModelTracked("char", t.TempDir(), t.TempDir())
	if err == nil {
		t.Fatalf("不存在的解释器必须报错，实得 %v", result)
	}
	if result != nil {
		t.Errorf("失败时不该有结果: %v", result)
	}
	if !strings.Contains(err.Error(), "Python执行失败") {
		t.Errorf("错误文本应与旧路径一致，实得 %v", err)
	}
}

// TestExportWithHubStillReturnsSameContract：启用进度通道后，导出返回值
// 仍是同一个 map / 同一个错误，runtime_ready 与 blocker 由原逻辑算出。
func TestExportWithHubStillReturnsSameContract(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Python.PythonPath = "definitely-not-a-python-binary"
	hub := services.NewWSHub() // 不 Run()：顺带验证无人消费时不会卡住导出
	h := &Handler{cfg: cfg, pythonBridge: services.NewPythonBridge(cfg), wsHub: hub}

	result, err := h.exportLive2DModelTracked("char", t.TempDir(), t.TempDir())
	if err == nil || result != nil {
		t.Fatalf("失败情形要与旧路径一致，实得 result=%v err=%v", result, err)
	}
	if !strings.Contains(err.Error(), "Python执行失败") {
		t.Errorf("错误文本应与旧路径一致，实得 %v", err)
	}
	data := exportResponseData(map[string]interface{}{"result": map[string]interface{}{
		"success": true,
	}})
	if ready, _ := data["runtime_ready"].(bool); ready {
		t.Error("缺 moc3 信息时不得报告就绪")
	}
}
