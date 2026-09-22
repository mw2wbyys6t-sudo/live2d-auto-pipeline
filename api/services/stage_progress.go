package services

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// ======================================================================
// 构建阶段实时进度
//
// Python 侧（live2d_builder/stage_events.py）把 pipeline 的 log.step 边界写成
// 行分隔 JSON；这里负责逐行读出来、收敛成一条「哪些阶段真的被观察到」的记录，
// 再交给调用方（handlers）转发到 WebSocket Hub。
//
// 为什么不复用 runInlinePythonTimeout：它用 CombinedOutput()，进程跑完才拿到
// 输出，天然做不到实时。这里的实现刻意与它保持同样的返回值与错误语义
// （含 ErrPythonTimeout），这样最终响应契约一个字都不用改。
// ======================================================================

// StageMarkerPrefix 与 Python 侧 stage_events.MARKER 一一对应。
const StageMarkerPrefix = "LIVE2D_STAGE="

// 阶段状态。 absence（从未上报）是一种显式状态，不能靠前端猜。
const (
	// StageStarted：进入该阶段。
	StageStarted string = "started"
	// StageAdvanced：控制流走过了该阶段（进程没在它里面死掉）。
	// 注意：这**不**代表该阶段的产物通过了验收。
	StageAdvanced string = "advanced"
	// StageAdvancedWithErrors：走过了该阶段，但该阶段期间出现过 ERROR 级日志
	// （例如 moc3 编译失败后构建继续往下跑）——绝不能被渲染成成功。
	StageAdvancedWithErrors string = "advanced_with_errors"
	// StageFailed：进程在该阶段里中断/失败。
	StageFailed string = "failed"
	// StageNotReported：该阶段一个事件都没有产生，因此不得显示为已完成。
	StageNotReported string = "not_reported"
	// StageSucceeded：只有官方 Cubism Core 验收通过（runtime_ready=true）才发。
	StageSucceeded string = "succeeded"
	// StageBlocked：构建跑完了，但产物未通过验收/缺验收信息。
	StageBlocked string = "blocked"
)

// StageEvent 是一次构建里单个阶段的一次状态变化。
type StageEvent struct {
	JobID     string `json:"job_id,omitempty"`
	Index     int    `json:"index"`
	Step      int    `json:"step"`
	Total     int    `json:"total"`
	Name      string `json:"name"`
	Status    string `json:"status"`
	Message   string `json:"message,omitempty"`
	Percent   int    `json:"percent"`
	ElapsedMS int64  `json:"elapsed_ms,omitempty"`
	// Verified 表示该状态来自可核验的验收（官方内核 runtime_ready），
	// 阶段边界一律为 false。
	Verified bool `json:"verified"`
}

// ParseStageLine 从一行子进程输出里取出阶段事件；不是事件行时返回 ok=false。
func ParseStageLine(line string) (StageEvent, bool) {
	text := strings.TrimSpace(line)
	if !strings.HasPrefix(text, StageMarkerPrefix) {
		return StageEvent{}, false
	}
	var evt StageEvent
	if err := json.Unmarshal([]byte(strings.TrimPrefix(text, StageMarkerPrefix)), &evt); err != nil {
		return StageEvent{}, false
	}
	if evt.Status == "" {
		return StageEvent{}, false
	}
	return evt, true
}

// StagePercent 阶段百分比：上限 99%，100% 只属于经过验收的终端事件。
func StagePercent(step, total int, entered bool) int {
	if total <= 0 {
		return 0
	}
	done := step
	if entered {
		done = step - 1
	}
	if done < 0 {
		done = 0
	}
	percent := done * 100 / total
	if percent > 99 {
		percent = 99
	}
	return percent
}

// StageTracker 收敛一次构建的阶段事件：转发出去，同时记住哪些阶段真的上报过。
// 并发安全（stdout / stderr 两个读循环会同时喂它）。
type StageTracker struct {
	mu        sync.Mutex
	jobID     string
	onEvent   func(StageEvent)
	startedAt time.Time
	nextIndex int
	open      *StageEvent
	total     int
	finished  bool
	seen      map[int]bool
}

// NewStageTracker 创建跟踪器；onEvent 可为 nil（只要记录、不外发）。
func NewStageTracker(jobID string, onEvent func(StageEvent)) *StageTracker {
	return &StageTracker{
		jobID:     jobID,
		onEvent:   onEvent,
		startedAt: time.Now(),
		seen:      map[int]bool{},
	}
}

// Observe 记录一个来自 Python 的阶段事件：新阶段进入时先把上一个开放阶段推进。
func (t *StageTracker) Observe(evt StageEvent) {
	if t == nil {
		return
	}
	t.mu.Lock()
	clone := evt
	clone.Index = 0
	clone.ElapsedMS = time.Since(t.startedAt).Milliseconds()
	if clone.JobID == "" {
		clone.JobID = t.jobID
	}
	if clone.Total > t.total {
		t.total = clone.Total
	}
	pending := make([]StageEvent, 0, 2)

	if clone.Status == StageStarted {
		if open := t.open; open != nil {
			closed := *open
			closed.Status = StageAdvanced
			closed.Percent = StagePercent(closed.Step, closed.Total, false)
			pending = append(pending, closed)
		}
		t.open = &clone
	} else {
		// advanced / failed 之类的收尾事件由 Python 显式给出，不再自动补。
		if t.open != nil && t.open.Step == clone.Step &&
			(clone.Status == StageAdvanced || clone.Status == StageAdvancedWithErrors) {
			t.open = nil
		}
	}
	if clone.Step > 0 {
		t.seen[clone.Step] = true
	}
	pending = append(pending, clone)
	for i := range pending {
		t.nextIndex++
		pending[i].Index = t.nextIndex
	}
	t.mu.Unlock()

	for _, evt := range pending {
		t.publish(evt)
	}
}

// JobID 返回跟踪器的作业标识（供内联脚本回带，用于前端关联同一次导出）。
func (t *StageTracker) JobID() string {
	if t == nil {
		return ""
	}
	return t.jobID
}

// Abort 进程在半路挂了：把仍开放的阶段记为 failed。
// 不发终端事件 —— 终端状态只有掌握响应契约的调用方（handlers）才有权宣布。
func (t *StageTracker) Abort(message string) {
	if t == nil {
		return
	}
	t.mu.Lock()
	open := t.open
	t.open = nil
	var evt *StageEvent
	if open != nil {
		closed := *open
		closed.Status = StageFailed
		closed.Message = message
		closed.ElapsedMS = time.Since(t.startedAt).Milliseconds()
		t.nextIndex++
		closed.Index = t.nextIndex
		evt = &closed
	}
	t.mu.Unlock()
	if evt != nil {
		t.publish(*evt)
	}
}

// Fail 进程没能走完：开放阶段记为 failed，并补发未上报阶段 + 终端失败事件。
func (t *StageTracker) Fail(message string) {
	t.terminal(StageFailed, message, false)
}

// Terminal 收尾：补发从未上报的阶段，再发终端事件。
// verified 只应来自官方 Cubism Core 的 runtime_ready；其余一律 blocked。
// 幂等：一个 job 只有一个终端事件，重复调用被忽略。
func (t *StageTracker) Terminal(status, message string, verified bool) {
	t.terminal(status, message, verified)
}

func (t *StageTracker) terminal(status, message string, verified bool) {
	if t == nil {
		return
	}
	t.mu.Lock()
	if t.finished {
		t.mu.Unlock()
		return
	}
	t.finished = true
	elapsed := time.Since(t.startedAt).Milliseconds()
	queue := make([]StageEvent, 0, t.total+2)

	evt := StageEvent{
		JobID:     t.jobID,
		Total:     t.total,
		Name:      "export",
		Status:    status,
		Message:   message,
		Percent:   percentForTerminal(status, verified),
		ElapsedMS: elapsed,
		Verified:  verified,
	}

	if open := t.open; open != nil {
		// 进程退出码为 0 才算走过了最后一步；失败时就把它记成 failed。
		closed := *open
		closed.Status = StageAdvanced
		closed.Percent = StagePercent(closed.Step, closed.Total, false)
		if status == StageFailed {
			closed.Status = StageFailed
		}
		closed.Message = message
		closed.ElapsedMS = elapsed
		// 阶段边界永远不标 verified：验收只看最终产物的 runtime_ready。
		closed.Verified = false
		t.open = nil
		// 终端事件是作业级判定，Step 保持 0：否则 UI 会给同一步画出两行。
		queue = append(queue, closed)
	}
	queue = append(queue, t.missingStepsLocked(elapsed)...)
	queue = append(queue, evt)
	for i := range queue {
		t.nextIndex++
		queue[i].Index = t.nextIndex
	}
	t.mu.Unlock()

	for i := range queue {
		t.publish(queue[i])
	}
}

func percentForTerminal(status string, verified bool) int {
	if status == StageSucceeded && verified {
		return 100
	}
	return 0
}

// missingStepsLocked 为「从未上报进度的阶段」补事件。
// 名字只能用 stage_N 占位：Python 从没报过这些阶段，Go 再硬编码一份阶段名表
// 就是凭记忆编内容 —— 真要显示名字，得让 pipeline 自己报上来。
func (t *StageTracker) missingStepsLocked(elapsed int64) []StageEvent {
	if t.total <= 0 {
		return nil
	}
	missing := make([]StageEvent, 0, t.total)
	for step := 1; step <= t.total; step++ {
		if t.seen[step] {
			continue
		}
		// 补报之后这一步就「有过事件」了（事件内容是 not_reported），
		// 再走一遍收尾不会重复刷屏。
		t.seen[step] = true
		missing = append(missing, StageEvent{
			JobID:     t.jobID,
			Index:     0, // 统一由调用方排队时编号
			Step:      step,
			Total:     t.total,
			Name:      fmt.Sprintf("stage_%d", step),
			Status:    StageNotReported,
			Message:   "该阶段没有产生任何进度事件，不能视为已完成",
			Percent:   0,
			ElapsedMS: elapsed,
			Verified:  false,
		})
	}
	return missing
}

// UnreportedSteps 返回至今没产生过任何事件的阶段号（收尾补报之后应为空）。
func (t *StageTracker) UnreportedSteps() []int {
	if t == nil {
		return nil
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	steps := make([]int, 0, t.total)
	for step := 1; step <= t.total; step++ {
		if !t.seen[step] {
			steps = append(steps, step)
		}
	}
	return steps
}

func (t *StageTracker) publish(evt StageEvent) {
	if t == nil || t.onEvent == nil {
		return
	}
	if evt.Index == 0 {
		t.mu.Lock()
		t.nextIndex++
		evt.Index = t.nextIndex
		t.mu.Unlock()
	}
	t.onEvent(evt)
}

// ======================================================================
// 逐行读取子进程输出
// ======================================================================

// scanStageLines 逐行读取一个输出流：阶段事件行交给 tracker，其余留在返回的
// 日志文本里。事件行被剔除，保证「最终 JSON 提取」看到的是没插进东西的原输出。
func scanStageLines(r io.Reader, tracker *StageTracker) string {
	var combined strings.Builder
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(strings.TrimSpace(line), StageMarkerPrefix) {
			if evt, ok := ParseStageLine(line); ok {
				tracker.Observe(evt)
			}
			continue
		}
		combined.WriteString(line)
		combined.WriteByte('\n')
	}
	return combined.String()
}

// runInlinePythonStreaming 是 runInlinePythonTimeout 的实时版：同一份预算语义、
// 同一份返回结构（{"result": ...}）、同一份错误文本，只是多了一个 tracker。
func (pb *PythonBridge) runInlinePythonStreaming(code string,
	timeout time.Duration, tracker *StageTracker) (map[string]interface{}, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, pb.cfg.Python.PythonPath, "-c", code)
	cmd.Dir = pb.cfg.Python.ScriptsDir
	cmd.Env = append(os.Environ(),
		"PYTHONIOENCODING=utf-8",
		"PYTHONPATH="+pb.cfg.Python.ScriptsDir,
		// 缓冲没关就没法实时：默认管道下 stderr 是块缓冲，进度会在进程退出时
		// 一次性涌到，那和只看最终响应没有区别。
		"PYTHONUNBUFFERED=1",
	)

	configurePythonProcess(cmd)

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("Python执行失败: %v", err)
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, fmt.Errorf("Python执行失败: %v", err)
	}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("Python执行失败: %v", err)
	}

	var mu sync.Mutex
	var output strings.Builder
	var wg sync.WaitGroup
	for _, stream := range []io.Reader{stdout, stderr} {
		wg.Add(1)
		go func(r io.Reader) {
			defer wg.Done()
			text := scanStageLines(r, tracker)
			mu.Lock()
			output.WriteString(text)
			mu.Unlock()
		}(stream)
	}

	// cmd.Wait() 会关闭管道，所以必须等两个读循环都结束（官方文档要求）。
	waited := make(chan error, 1)
	go func() { wg.Wait(); waited <- cmd.Wait() }()

	var waitErr error
	select {
	case waitErr = <-waited:
	case <-ctx.Done():
		if cmd.Process != nil {
			killPythonProcess(cmd)
		}
		<-waited
		tracker.Abort(fmt.Sprintf("构建在 %s 预算内没有完成", timeout))
		return nil, fmt.Errorf("%w（%s）: Python执行超时", ErrPythonTimeout, timeout)
	}

	mu.Lock()
	combined := output.String()
	mu.Unlock()

	if waitErr != nil {
		tracker.Abort(fmt.Sprintf("构建中断: %v", waitErr))
		return nil, fmt.Errorf("Python执行失败: %v\n%s", waitErr, sanitizeOutput(combined))
	}

	// 与原实现一致：从后往前找第一行合法 JSON，找不到就是错误。
	lines := strings.Split(strings.TrimSpace(combined), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		var result interface{}
		if json.Unmarshal([]byte(strings.TrimSpace(lines[i])), &result) == nil && result != nil {
			return map[string]interface{}{"result": result}, nil
		}
	}
	tracker.Abort("Python 没有返回有效 JSON 结果")
	return nil, fmt.Errorf("Python did not return a valid JSON result")
}

// exportStagePythonCode 生成带阶段事件外发的导出脚本。
//
// 与 PythonBridge.ExportLive2DModel 的内联脚本保持同一套构建调用与回传字段，
// 只多出两行进度挂钩（attach / close_open）——进度是旁路，不参与响应契约。
// 之所以要独立一份：runInlinePythonTimeout 是阻塞的，塞不进逐行读取。
func exportStagePythonCode(scriptsDir, layersDir, outputDir, characterID, jobID string) string {
	return fmt.Sprintf(`
import sys, json, os, glob
sys.path.insert(0, %q)
from pathlib import Path
from PIL import Image
from collections import OrderedDict
from live2d_builder import stage_events
from live2d_builder.pipeline import Live2DBuilder

stage_events.attach(job_id=%q)

layers_dir = %q
out_dir = %q
Path(out_dir).mkdir(parents=True, exist_ok=True)

layers = OrderedDict()
for p in sorted(glob.glob(os.path.join(layers_dir, "*.png"))):
    name = os.path.splitext(os.path.basename(p))[0]
    layers[name] = Image.open(p).convert("RGBA")

if not layers:
    print(json.dumps({"success": False, "message":
                      "图层目录内没有可用 PNG: " + layers_dir}, ensure_ascii=False))
    raise SystemExit(0)

# 必须走完整构建：只有它生成网格、编译 .moc3 并用官方 Cubism Core 验收。
# 早期版本直接调用 Model3Exporter.export(meshes={})，产物里根本没有 moc3。
builder = Live2DBuilder(output_dir=out_dir, character_name=%q)
result = builder.build(layers)

# build() 正常返回才算走完最后一步；异常路径由 Go 侧记为 failed。
stage_events.close_open(stage_events.STATUS_ADVANCED)

# 只回传路径与状态；网格 / 骨骼等中间数据可达数 MB，不进 API 响应。
keys = ("output_dir", "model3_json", "moc3_ref", "textures", "texture_files",
        "physics", "expressions", "mesh_data", "guide", "validation",
        "compatibility", "mesh_guide", "build_meta", "elapsed_seconds", "moc3")
summary = {k: result.get(k) for k in keys if k in result}
summary["success"] = True
summary["mesh_count"] = len(result.get("meshes") or {})
print(json.dumps(summary, ensure_ascii=False, default=str))
`, scriptsDir, jobID, layersDir, outputDir, characterID)
}

// ExportLive2DModelTracked 导出 Live2D 模型，并把 log.step 边界作为阶段事件
// 实时交给 tracker。tracker 为 nil 时完全退回 ExportLive2DModel 原路径
// （同一份脚本、同一个执行函数），保证未启用进度通道时零行为差异。
func (pb *PythonBridge) ExportLive2DModelTracked(characterID, layersDir, outputDir string,
	tracker *StageTracker) (map[string]interface{}, error) {
	if tracker == nil {
		return pb.ExportLive2DModel(characterID, layersDir, outputDir)
	}
	if layersDir == "" {
		suffix := characterID
		if len(suffix) > 8 {
			suffix = suffix[:8]
		}
		layersDir = filepath.Join(pb.cfg.Output.BaseDir, "layers_"+suffix)
	}
	if outputDir == "" {
		outputDir = filepath.Join(pb.cfg.Output.BaseDir, "live2d_exports", characterID)
	}
	pyCode := exportStagePythonCode(pb.cfg.Python.ScriptsDir, layersDir, outputDir,
		characterID, tracker.JobID())
	// 与完整导出共用同一预算（实测规模见 tools/measure_export_duration.py）。
	return pb.runInlinePythonStreaming(pyCode, pb.cfg.GetExportTimeout(), tracker)
}
