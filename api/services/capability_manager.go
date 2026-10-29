package services

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"time"

	"live2d-api/config"
)

// 按需下载的重型能力组白名单（与 core/capabilities.py 的 GROUPS 对齐）。
// 只允许这里列出的 pip spec，避免前端任意注入包名；基础包主链路不依赖这些。
var capabilityPlans = map[string][]string{
	"sam2_gd":   {"torch", "torchvision", "transformers", "segment-anything"},
	"rembg":     {"rembg[cpu]"},
	"mediapipe": {"mediapipe"},
	"asr":       {"faster-whisper"},
	"tts":       {"edge-tts"},
	"diffusion": {"diffusers", "accelerate"},
}

// bridgeCapabilitiesScript 查询可选重型能力的可导入性（与 core.capabilities.report 同构）。
// 把 per-user site-packages（pip --user 目标）也纳入 sys.path：捆绑 embeddable Python
// 默认不读用户站点目录，否则按需安装的包探测不到。
const bridgeCapabilitiesScript = `
import json, sys, site, os
sys.path.insert(0, ".")
for p in (site.getusersitepackages(), site.getuserbase()):
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)
from core.capabilities import report
print(json.dumps(report(), ensure_ascii=False))
`

// capabilityJob 记录一次后台 pip 安装的状态与尾部输出。
type capabilityJob struct {
	Group    string    `json:"group"`
	Status   string    `json:"status"` // running | done | failed
	Started  time.Time `json:"started_at"`
	Finished time.Time `json:"finished_at,omitempty"`
	Tail     string    `json:"tail,omitempty"`
	Error    string    `json:"error,omitempty"`
}

// CapabilityManager 串行化重型能力安装（torch 系体积大，并发无意义且易打爆磁盘/网络）。
type CapabilityManager struct {
	cfg     *config.Config
	mu      sync.Mutex
	jobs    map[string]*capabilityJob
	lastJob string
}

func NewCapabilityManager(cfg *config.Config) *CapabilityManager {
	return &CapabilityManager{cfg: cfg, jobs: map[string]*capabilityJob{}}
}

// Probe 经给定 Python 桥探测各能力组的可导入性（复用 Handler 的 bridge）。
func (cm *CapabilityManager) Probe(ctx context.Context, pb *PythonBridge) (map[string]interface{}, error) {
	res, err := pb.runBridgeTask(ctx, bridgeCapabilitiesScript, nil, 60*time.Second)
	if err != nil {
		return nil, err
	}
	inner, ok := res["result"].(map[string]interface{})
	if !ok {
		inner = res
	}
	return inner, nil
}

// Download 启动某能力组的后台 pip 安装；组名不在白名单则拒绝。
func (cm *CapabilityManager) Download(group string) (*capabilityJob, error) {
	if _, ok := capabilityPlans[group]; !ok {
		return nil, fmt.Errorf("未知能力组: %s", group)
	}
	steps := installSteps(group, runtime.GOOS)

	cm.mu.Lock()
	defer cm.mu.Unlock()
	// 同一组已有运行中任务时直接返回，不重复拉起。
	if existing, ok := cm.jobs[group]; ok && existing.Status == "running" {
		return existing, nil
	}

	job := &capabilityJob{Group: group, Status: "running", Started: time.Now()}
	cm.jobs[group] = job
	cm.lastJob = group

	go cm.runInstall(job, steps)
	return job, nil
}

// gpuRuntimeSpec 是 Windows 基础包随带的分层运行时发行名。
// 它必须与 requirements.desktop.txt 的 win32 条目保持一致（有测试钉住）。
const gpuRuntimeSpec = "onnxruntime-directml>=1.24.4"

// installSteps 给出某能力组在指定平台上的 pip 调用序列（不含解释器路径）。
//
// 为什么 rembg 在 Windows 上要多一步：基础包带的是 onnxruntime-directml，
// 而 rembg[cpu] 钉着 CPU 版 onnxruntime。两个发行名共用同一个 onnxruntime/
// 目录，后装的覆盖先装的，pip 全程不报错 —— 装完 rembg，分层的 GPU 加速就
// 静默消失了。因此 rembg 之后强制重装 DirectML 运行时（--no-deps 只补文件，
// 不动其它依赖）。
func installSteps(group, goos string) [][]string {
	spec, ok := capabilityPlans[group]
	if !ok {
		return nil
	}
	base := []string{"-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check"}
	steps := [][]string{append(append([]string{}, base...), spec...)}
	if goos == "windows" && group == "rembg" {
		steps = append(steps, append(append([]string{}, base...),
			"--force-reinstall", "--no-deps", gpuRuntimeSpec))
	}
	return steps
}

// runInstall 顺序执行该能力组的 pip 步骤，把尾部输出与终态写回 job。
// 任一步失败即终止（不吞错误：GPU 运行时没恢复成功时必须让用户看到）。
func (cm *CapabilityManager) runInstall(job *capabilityJob, steps [][]string) {
	var tail string
	var err error
	for _, args := range steps {
		runErr := cm.runPip(args, &tail)
		if runErr != nil {
			err = runErr
			break
		}
	}

	cm.mu.Lock()
	defer cm.mu.Unlock()
	job.Finished = time.Now()
	job.Tail = tail
	if err != nil {
		job.Status = "failed"
		job.Error = err.Error()
		return
	}
	job.Status = "done"
}

// runPip 执行一次 pip 调用，并把输出尾部累积到 tail（供 job 详情展示）。
func (cm *CapabilityManager) runPip(args []string, tail *string) error {
	cmd := exec.CommandContext(context.Background(), cm.cfg.Python.PythonPath, args...)
	cmd.Dir = cm.cfg.Python.ScriptsDir
	configurePythonProcess(cmd)
	cmd.Cancel = func() error {
		killPythonProcess(cmd)
		return os.ErrProcessDone
	}
	cmd.WaitDelay = 10 * time.Second

	output, err := cmd.CombinedOutput()
	chunk := string(output)
	if len(chunk) > 4000 {
		chunk = chunk[len(chunk)-4000:]
	}
	*tail += chunk
	if err != nil {
		return fmt.Errorf("%w（pip 步骤：%s）", err, strings.Join(args[len(args)-1:], " "))
	}
	return nil
}

// Status 返回某组最近一次任务状态；未下载过返回 nil（前端按探测结果显示）。
func (cm *CapabilityManager) Status(group string) *capabilityJob {
	cm.mu.Lock()
	defer cm.mu.Unlock()
	return cm.jobs[group]
}

// LastJob 返回最近启动的任务组（供轮询简化）。
func (cm *CapabilityManager) LastJob() string {
	cm.mu.Lock()
	defer cm.mu.Unlock()
	return cm.lastJob
}

// MarshalJSON 让 capabilityJob 可直接塞进响应。
func (j *capabilityJob) MarshalJSON() ([]byte, error) {
	type alias capabilityJob
	return json.Marshal((*alias)(j))
}
