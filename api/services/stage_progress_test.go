package services

import (
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"live2d-api/config"
)

// 阶段进度通道的核心不变量：只有真的被观察到的阶段才允许出现完成态，
// 而「完成」这个词只留给经过官方内核验收的终端事件。

func markerLine(t *testing.T, evt StageEvent) string {
	t.Helper()
	b, err := json.Marshal(evt)
	if err != nil {
		t.Fatalf("事件无法序列化: %v", err)
	}
	return StageMarkerPrefix + string(b)
}

func TestParseStageLine(t *testing.T) {
	line := markerLine(t, StageEvent{Step: 9, Total: 10, Name: "Compiling moc3",
		Status: StageStarted, Percent: 80})
	evt, ok := ParseStageLine("  " + line + "  ")
	if !ok {
		t.Fatalf("事件行没被认出来: %s", line)
	}
	if evt.Step != 9 || evt.Total != 10 || evt.Status != StageStarted {
		t.Errorf("解析结果不对: %#v", evt)
	}
	for _, bad := range []string{
		"", "[9/10] Compiling moc3", StageMarkerPrefix + "not json",
		StageMarkerPrefix + `{"step":1}`, // 没有 status 的事件不可信
		StageMarkerPrefix + `["array"]`,
	} {
		if _, ok := ParseStageLine(bad); ok {
			t.Errorf("非法事件行被接受了: %q", bad)
		}
	}
}

func TestScanStageLinesFeedsTrackerAndKeepsResultJSON(t *testing.T) {
	var mu sync.Mutex
	var got []StageEvent
	tracker := NewStageTracker("job-1", func(evt StageEvent) {
		mu.Lock()
		got = append(got, evt)
		mu.Unlock()
	})

	output := strings.Join([]string{
		"[INFO] rigging.pipeline: [1/2] Generating meshes",
		markerLine(t, StageEvent{Step: 1, Total: 2, Name: "Generating meshes", Status: StageStarted}),
		"[WARNING] something noisy",
		markerLine(t, StageEvent{Step: 1, Total: 2, Name: "Generating meshes", Status: StageAdvanced}),
		`{"success": true, "mesh_count": 3}`,
	}, "\n")

	logText := scanStageLines(strings.NewReader(output), tracker)
	if strings.Contains(logText, StageMarkerPrefix) {
		t.Errorf("事件行漏进了日志文本: %q", logText)
	}
	if !strings.Contains(logText, "Generating meshes") {
		t.Errorf("普通日志被吃掉了: %q", logText)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(got) != 2 {
		t.Fatalf("应有 2 个阶段事件，实得 %d: %#v", len(got), got)
	}
	if got[0].Status != StageStarted || got[1].Status != StageAdvanced {
		t.Errorf("事件状态序列不对: %s / %s", got[0].Status, got[1].Status)
	}
	for _, evt := range got {
		if evt.JobID != "job-1" {
			t.Errorf("事件未补上 job_id: %#v", evt)
		}
		if evt.Verified {
			t.Errorf("阶段边界不该 verified: %#v", evt)
		}
	}
	// 事件行被剔除后，最终结果 JSON 仍要能被原来的「从后往前找」逻辑取到。
	lines := strings.Split(strings.TrimSpace(logText), "\n")
	var result map[string]interface{}
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &result); err != nil {
		t.Fatalf("最后一行不再是结果 JSON: %v / %q", err, lines[len(lines)-1])
	}
	if result["success"] != true {
		t.Errorf("结果内容变了: %v", result)
	}
}

// collectTracker 返回一个按序记录事件的跟踪器。
func collectTracker(jobID string) (*StageTracker, *[]StageEvent) {
	var mu sync.Mutex
	events := &[]StageEvent{}
	tracker := NewStageTracker(jobID, func(evt StageEvent) {
		mu.Lock()
		*events = append(*events, evt)
		mu.Unlock()
	})
	return tracker, events
}

func filterEvents(events []StageEvent, keep func(StageEvent) bool) []StageEvent {
	matched := make([]StageEvent, 0, len(events))
	for _, evt := range events {
		if keep(evt) {
			matched = append(matched, evt)
		}
	}
	return matched
}

func TestStageTrackerReportsUnseenStepsAsNotReported(t *testing.T) {
	tracker, events := collectTracker("job-2")
	tracker.Observe(StageEvent{Step: 1, Total: 10, Name: "Generating meshes", Status: StageStarted})
	tracker.Observe(StageEvent{Step: 1, Total: 10, Name: "Generating meshes", Status: StageAdvanced})
	tracker.Observe(StageEvent{Step: 2, Total: 10, Name: "Laying out UVs", Status: StageStarted})
	tracker.Terminal(StageSucceeded, "已通过验收", true)

	missing := filterEvents(*events, func(e StageEvent) bool { return e.Status == StageNotReported })
	if len(missing) != 8 {
		t.Fatalf("3..10 共 8 个阶段没上报，实得 %d 条: %#v", len(missing), missing)
	}
	for i, evt := range missing {
		if evt.Step != i+3 {
			t.Errorf("补报阶段号不对: %d，期望 %d", evt.Step, i+3)
		}
		if evt.Name == "" || evt.Message == "" {
			t.Errorf("补报阶段缺少可读信息: %#v", evt)
		}
		if evt.Verified {
			t.Errorf("没上报过的阶段绝不能 verified=true: %#v", evt)
		}
	}
	if unreported := tracker.UnreportedSteps(); len(unreported) != 0 {
		t.Errorf("补报之后 UnreportedSteps 应清空，实得 %v", unreported)
	}
}

func TestStageTrackerAdvancedFollowsStartedInOrder(t *testing.T) {
	tracker, events := collectTracker("job-3")
	// Python 只在下一个步骤出现时才推进上一个步骤。
	tracker.Observe(StageEvent{Step: 1, Total: 3, Name: "one", Status: StageStarted})
	tracker.Observe(StageEvent{Step: 2, Total: 3, Name: "two", Status: StageStarted})
	tracker.Observe(StageEvent{Step: 3, Total: 3, Name: "three", Status: StageStarted})
	tracker.Terminal(StageSucceeded, "ok", true)

	statuses := make([]string, 0, len(*events))
	for _, evt := range *events {
		statuses = append(statuses, fmt.Sprintf("%d:%s", evt.Step, evt.Status))
	}
	want := []string{"1:started", "1:advanced", "2:started", "2:advanced", "3:started",
		"3:advanced", "0:succeeded"}
	if strings.Join(statuses, " ") != strings.Join(want, " ") {
		t.Errorf("事件序列不对\n实得: %v\n期望: %v", statuses, want)
	}
	for i := 1; i < len(*events); i++ {
		if (*events)[i].Index <= (*events)[i-1].Index {
			t.Errorf("事件序号没有单调递增: %#v", *events)
			break
		}
	}
	last := (*events)[len(*events)-1]
	if last.Percent != 100 || !last.Verified {
		t.Errorf("验收通过的终端事件应 100%% 且 verified: %#v", last)
	}
	for _, evt := range (*events)[:len(*events)-1] {
		if evt.Percent >= 100 {
			t.Errorf("阶段边界不得到 100%%: %#v", evt)
		}
		if evt.Verified {
			t.Errorf("阶段边界不得 verified=true: %#v", evt)
		}
	}
}

func TestStageTrackerBlockedNeverClaimsSuccess(t *testing.T) {
	tracker, events := collectTracker("job-4")
	tracker.Observe(StageEvent{Step: 9, Total: 10, Name: "Compiling moc3", Status: StageStarted})
	tracker.Terminal(StageBlocked, "变形器未编译，产物仅含静态几何", false)

	for _, evt := range *events {
		if evt.Status == StageSucceeded {
			t.Fatalf("未验收却出现了 succeeded: %#v", evt)
		}
		if evt.Percent == 100 {
			t.Errorf("未验收却出现 100%%: %#v", evt)
		}
	}
	terminal := (*events)[len(*events)-1]
	if terminal.Status != StageBlocked || terminal.Step != 0 ||
		!strings.Contains(terminal.Message, "变形器未编译") {
		t.Errorf("终端事件不对: %#v", terminal)
	}
	// 第 9 步「走过了」不等于「编译成功」：advanced 必须 verified=false，
	// 判定权在终端 blocked 事件（blocker 原文透传）。
	closed := filterEvents(*events, func(e StageEvent) bool {
		return e.Step == 9 && e.Status == StageAdvanced
	})
	if len(closed) != 1 || closed[0].Verified {
		t.Errorf("走过的阶段不得带 verified: %#v", *events)
	}
}

func TestStageTrackerAbortMarksOpenStepFailedWithoutTerminal(t *testing.T) {
	tracker, events := collectTracker("job-5")
	tracker.Observe(StageEvent{Step: 4, Total: 10, Name: "Setting up deformers", Status: StageStarted})
	tracker.Abort("构建中断: exit status 1")

	if len(*events) != 2 {
		t.Fatalf("Abort 只该收尾开放阶段、不该发终端事件，实得 %#v", *events)
	}
	last := (*events)[len(*events)-1]
	if last.Status != StageFailed || last.Step != 4 {
		t.Errorf("开放阶段应记为 failed: %#v", last)
	}
	if last.Verified {
		t.Errorf("失败阶段不得 verified")
	}
	for _, evt := range *events {
		if evt.Name == "export" {
			t.Errorf("Abort 不该发终端事件: %#v", evt)
		}
	}
}

func TestStageTrackerTerminalIsEmittedOnce(t *testing.T) {
	tracker, events := collectTracker("job-6")
	tracker.Observe(StageEvent{Step: 1, Total: 1, Name: "only", Status: StageStarted})
	tracker.Terminal(StageSucceeded, "ok", true)
	tracker.Terminal(StageFailed, "重复调用", false)

	terminals := filterEvents(*events, func(e StageEvent) bool { return e.Name == "export" })
	if len(terminals) != 1 || terminals[0].Status != StageSucceeded {
		t.Errorf("终端事件应只有一条且保留首次判定: %#v", terminals)
	}
}

func TestStageTrackerIsNilSafe(t *testing.T) {
	var tracker *StageTracker
	tracker.Observe(StageEvent{Step: 1, Total: 2, Status: StageStarted})
	tracker.Abort("x")
	tracker.Terminal(StageSucceeded, "x", true)
	if got := tracker.JobID(); got != "" {
		t.Errorf("空跟踪器的 JobID 应为空，实得 %q", got)
	}
	text := "plain\n" + markerLine(t, StageEvent{Step: 1, Total: 2, Status: StageStarted}) + "\n"
	if got := scanStageLines(strings.NewReader(text), nil); strings.Contains(got, StageMarkerPrefix) {
		t.Errorf("无跟踪器时事件行仍要被剔除，实得 %q", got)
	} else if !strings.Contains(got, "plain") {
		t.Errorf("无跟踪器时日志文本要原样保留，实得 %q", got)
	}
}

func TestStagePercentCapsAt99ForStageBoundaries(t *testing.T) {
	if got := StagePercent(10, 10, false); got != 99 {
		t.Errorf("阶段边界上限应为 99%%，实得 %d", got)
	}
	if got := StagePercent(1, 10, true); got != 0 {
		t.Errorf("第一步进入应为 0%%，实得 %d", got)
	}
	if got := StagePercent(3, 4, true); got != 50 {
		t.Errorf("进入第 3/4 步应为 50%%，实得 %d", got)
	}
	if got := StagePercent(2, 0, true); got != 0 {
		t.Errorf("total<=0 应给 0%%，实得 %d", got)
	}
}

// ---------------------------------------------------------------------
// 内联脚本：与 ExportLive2DModel 保持同一构建调用（防漂移）
// ---------------------------------------------------------------------

// snippetKeysTuple 取出脚本里的 keys = (...) 字段清单，用于逐字比对。
func snippetKeysTuple(text string) string {
	start := strings.Index(text, "keys = (")
	if start < 0 {
		return ""
	}
	end := strings.Index(text[start:], "\nsummary[")
	if end < 0 {
		return ""
	}
	return strings.Join(strings.Fields(text[start:start+end]), " ")
}

func TestExportStagePythonCodeSharesBuildCallWithLegacyPath(t *testing.T) {
	legacyRaw, err := os.ReadFile("python_bridge.go")
	if err != nil {
		t.Fatalf("读不到旧桥接脚本，比对失去意义: %v", err)
	}
	code := exportStagePythonCode("/root", "/root/layers", "/root/out", "char", "job-9")

	// 回传给 API 的字段清单必须完全一致，否则两条路径的响应契约会悄悄分叉。
	legacyKeys := snippetKeysTuple(string(legacyRaw))
	trackedKeys := snippetKeysTuple(code)
	if legacyKeys == "" {
		t.Fatal("旧脚本里找不到 keys 清单，比对已过期")
	}
	if legacyKeys != trackedKeys {
		t.Errorf("keys 清单分叉\n旧: %s\n新: %s", legacyKeys, trackedKeys)
	}

	for _, frag := range []string{
		`from live2d_builder.pipeline import Live2DBuilder`,
		`result = builder.build(layers)`,
		`summary["success"] = True`,
		`summary["mesh_count"] = len(result.get("meshes") or {})`,
		`print(json.dumps(summary, ensure_ascii=False, default=str))`,
	} {
		if !strings.Contains(string(legacyRaw), frag) {
			t.Fatalf("片段清单过期，旧脚本已无: %s", frag)
		}
		if !strings.Contains(code, frag) {
			t.Errorf("带进度脚本缺少与旧路径共享的片段: %s", frag)
		}
	}
	if !strings.Contains(code, `Live2DBuilder(output_dir=out_dir, character_name="char")`) {
		t.Errorf("构建调用参数不对: %s", code)
	}
	// 进度挂钩是旁路：attach + 成功路径上的 close_open。
	for _, want := range []string{`stage_events.attach(job_id="job-9")`,
		"stage_events.close_open("} {
		if !strings.Contains(code, want) {
			t.Errorf("脚本缺少进度挂钩 %s", want)
		}
	}
}

// ---------------------------------------------------------------------
// 真链路：Python 产出事件 -> Go 逐行读取。缺环境时跳过，不伪装成跑过。
// ---------------------------------------------------------------------

func realBridge(t *testing.T) *PythonBridge {
	t.Helper()
	python, root := os.Getenv("LIVE2D_TEST_PYTHON"), os.Getenv("LIVE2D_TEST_ROOT")
	if python == "" || root == "" {
		t.Skip("需要 LIVE2D_TEST_PYTHON / LIVE2D_TEST_ROOT 才能跑真实 Python")
	}
	return NewPythonBridge(&config.Config{
		Python: config.PythonConfig{
			PythonPath: python, ScriptsDir: root,
			TimeoutSec: 120, ExportTimeoutSec: 300,
		},
		Output: config.OutputConfig{BaseDir: filepath.Join(root, "output")},
	})
}

// TestStreamingRunsRealPythonAndPublishesStages 验证跨语言线格式真的对得上：
// Python 的 log.step → 事件行 → Go 扫描器 → StageTracker。
func TestStreamingRunsRealPythonAndPublishesStages(t *testing.T) {
	pb := realBridge(t)
	root := pb.cfg.Python.ScriptsDir

	tracker, events := collectTracker("go-real")
	code := fmt.Sprintf(`
import sys, json
sys.path.insert(0, %q)
from core.logger import get_logger
from live2d_builder import stage_events
stage_events.attach(job_id="go-real")
log = get_logger("rigging.gotest")
log.step(1, 3, "alpha")
log.step(2, 3, "beta")
log.step(3, 3, "gamma")
stage_events.close_open(stage_events.STATUS_ADVANCED)
print(json.dumps({"ok": True}))
`, root)

	result, err := pb.runInlinePythonStreaming(code, 90*time.Second, tracker)
	if err != nil {
		t.Fatalf("真实 Python 执行失败: %v", err)
	}
	inner, _ := result["result"].(map[string]interface{})
	if inner["ok"] != true {
		t.Fatalf("结果 JSON 丢了: %v", result)
	}
	tracker.Terminal(StageSucceeded, "ok", true)

	for step, want := range map[int]string{1: "alpha", 2: "beta", 3: "gamma"} {
		found := filterEvents(*events, func(e StageEvent) bool {
			return e.Step == step && e.Name == want
		})
		if len(found) == 0 {
			t.Errorf("第 %d 步（%s）没有实时上报: %#v", step, want, *events)
		}
	}
	if len(*events) == 0 {
		t.Fatal("一个事件都没有")
	}
	terminal := (*events)[len(*events)-1]
	if terminal.Status != StageSucceeded || terminal.Percent != 100 {
		t.Errorf("终端事件不对: %#v", terminal)
	}
	for _, evt := range filterEvents(*events, func(e StageEvent) bool {
		return e.Status == StageNotReported
	}) {
		t.Errorf("三步都上报过了，不该有 not_reported: %#v", evt)
	}
	// 第 3 步的收尾必须来自 Python 的 close_open，而不是 Go 自己脑补。
	closed := filterEvents(*events, func(e StageEvent) bool {
		return e.Step == 3 && e.Status == StageAdvanced
	})
	if len(closed) != 1 {
		t.Errorf("第 3 步应由 close_open 收尾: %#v", *events)
	}
}

func TestStreamingFailureAndTimeoutMatchLegacyErrors(t *testing.T) {
	pb := realBridge(t)
	root := pb.cfg.Python.ScriptsDir

	t.Run("异常退出", func(t *testing.T) {
		tracker, events := collectTracker("boom")
		_, err := pb.runInlinePythonStreaming(fmt.Sprintf(`
import sys
sys.path.insert(0, %q)
from core.logger import get_logger
from live2d_builder import stage_events
stage_events.attach(job_id="boom")
log = get_logger("rigging.gotest")
log.step(1, 2, "alpha")
log.step(2, 2, "beta")
raise RuntimeError("kaboom")
`, root), 60*time.Second, tracker)
		if err == nil || !strings.HasPrefix(err.Error(), "Python执行失败") {
			t.Fatalf("错误文本要与旧路径一致，实得 %v", err)
		}
		if !strings.Contains(err.Error(), "kaboom") {
			t.Errorf("失败输出被吞了: %v", err)
		}
		failed := filterEvents(*events, func(e StageEvent) bool {
			return e.Status == StageFailed
		})
		if len(failed) != 1 || failed[0].Step != 2 {
			t.Errorf("中断时应把开放阶段记为 failed: %#v", *events)
		}
	})

	t.Run("超时报 ErrPythonTimeout", func(t *testing.T) {
		tracker, _ := collectTracker("slow")
		_, err := pb.runInlinePythonStreaming("import time; time.sleep(30)",
			1500*time.Millisecond, tracker)
		if !errors.Is(err, ErrPythonTimeout) {
			t.Fatalf("超时必须包装 ErrPythonTimeout，实得 %v", err)
		}
		if !strings.Contains(err.Error(), "Python执行超时") {
			t.Errorf("超时文本要与旧路径一致，实得 %v", err)
		}
	})
}

// TestRealExportScriptStreamsStagesWithoutBreakingResult 跑**真实导出脚本**
// （exportStagePythonCode，即 /api/export/live2d 实际执行的那份程序）走流式通道。
//
// 上面的合成脚本只证明线格式对得上；这里要证明的是产品路径本身：
//  1. 真构建的 log.step 边界确实变成事件；
//  2. 进度走 stderr，stdout 最后一行的结果 JSON 不被污染；
//  3. 结果里仍带着官方内核的验收结论（runtime_ready），进度旁路没有把契约弄丢。
func TestRealExportScriptStreamsStagesWithoutBreakingResult(t *testing.T) {
	pb := realBridge(t)
	root := pb.cfg.Python.ScriptsDir

	layers := t.TempDir()
	for _, pair := range [][2]string{{"hair_front", "0x3a"}, {"body", "0xc8"}} {
		writeTestLayerPNG(t, filepath.Join(layers, pair[0]+".png"))
	}
	out := t.TempDir()

	tracker, events := collectTracker("real-export")
	code := exportStagePythonCode(root, layers, out, "goptr", "real-export")
	result, err := pb.runInlinePythonStreaming(code,
		time.Duration(pb.cfg.Python.ExportTimeoutSec)*time.Second, tracker)
	if err != nil {
		t.Fatalf("真实导出在流式通道里失败: %v", err)
	}

	inner, ok := result["result"].(map[string]interface{})
	if !ok {
		t.Fatalf("结果 JSON 丢失或被污染: %T %#v", result["result"], result)
	}
	if inner["success"] != true {
		t.Errorf("导出脚本自报未成功: %#v", inner)
	}
	// 结果里必须还带着 moc3 验收信息，进度通道不能把它挤掉
	if _, present := inner["moc3"]; !present {
		t.Errorf("结果里没有 moc3 验收块，响应契约会退化: %#v", inner)
	}

	steps := map[int]bool{}
	for _, evt := range *events {
		if evt.Status == StageStarted {
			steps[evt.Step] = true
		}
	}
	if len(steps) < 5 {
		t.Errorf("真实构建有 10 个阶段，只观察到 %d 个 started 事件: %#v",
			len(steps), *events)
	}
	// 任何一条阶段事件都不许自带验收结论：那是内核的事
	for _, evt := range *events {
		if evt.Status == StageSucceeded && !evt.Verified {
			t.Errorf("succeeded 却 verified=false，等于假成功: %#v", evt)
		}
	}
}

func writeTestLayerPNG(t *testing.T, path string) {
	t.Helper()
	img := image.NewNRGBA(image.Rect(0, 0, 32, 32))
	for y := 8; y < 24; y++ {
		for x := 8; x < 24; x++ {
			img.SetNRGBA(x, y, color.NRGBA{R: 200, A: 255})
		}
	}
	file, err := os.Create(path)
	if err != nil {
		t.Fatalf("建测试图层失败: %v", err)
	}
	defer file.Close()
	if err := png.Encode(file, img); err != nil {
		t.Fatalf("写测试图层失败: %v", err)
	}
}
