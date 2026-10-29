package services

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"live2d-api/config"
)

// newRepoRootGPUBridge 用真实解释器 + 仓库根作为 ScriptsDir，
// 让 Go → Python 的 GPU 探测走完整链路（判定逻辑只在 core.gpu_probe 一份）。
func newRepoRootGPUBridge(t *testing.T) *PythonBridge {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatalf("解析仓库根失败: %v", err)
	}
	cfg := config.DefaultConfig()
	cfg.Python.PythonPath = testPythonPath(t)
	cfg.Python.ScriptsDir = root
	cfg.Python.TimeoutSec = 60
	return NewPythonBridge(cfg)
}

// 契约形状：换后端时自检页显示的项目数不能跳变，所以两边必须给同一组键。
func TestProbeGPUReportsStableContract(t *testing.T) {
	pb := newRepoRootGPUBridge(t)

	gpu, err := pb.ProbeGPU(context.Background())
	if err != nil {
		t.Fatalf("GPU 探测应成功，实际失败: %v", err)
	}
	for _, key := range []string{"available", "adapters", "runtime", "recommendation"} {
		if _, ok := gpu[key]; !ok {
			t.Errorf("返回结果缺少键 %q", key)
		}
	}
	if _, ok := gpu["available"].(bool); !ok {
		t.Errorf("available 应是 bool，实际是 %T", gpu["available"])
	}

	rec, ok := gpu["recommendation"].(map[string]interface{})
	if !ok {
		t.Fatalf("recommendation 应是对象，实际是 %T", gpu["recommendation"])
	}
	switch device, _ := rec["device"].(string); device {
	case "cuda", "dml", "cpu":
	default:
		t.Errorf("recommendation.device 应是 cuda/dml/cpu 之一，实际 %q", device)
	}
}

// stubGPUProbeBridge 造一个临时仓库根：core/gpu_probe 是被计数的桩函数。
// 用真实解释器而不是 /bin/sh 假解释器，Windows 与 Linux 上都能跑。
func stubGPUProbeBridge(t *testing.T) (*PythonBridge, string) {
	t.Helper()
	root := t.TempDir()
	coreDir := filepath.Join(root, "core")
	if err := os.MkdirAll(coreDir, 0o755); err != nil {
		t.Fatalf("创建临时 core 包失败: %v", err)
	}
	counter := filepath.Join(root, "calls.txt")
	stub := "import io\n" +
		"def probe_gpu():\n" +
		"    with io.open(" + strconv.Quote(counter) + ", \"a\", encoding=\"utf-8\") as f:\n" +
		"        f.write(\"x\\n\")\n" +
		"    return {\"available\": True, \"adapters\": [], \"runtime\": {}, \"recommendation\": {\"device\": \"cpu\"}}\n"
	if err := os.WriteFile(filepath.Join(coreDir, "gpu_probe.py"), []byte(stub), 0o644); err != nil {
		t.Fatalf("写入 core/gpu_probe 桩失败: %v", err)
	}

	cfg := config.DefaultConfig()
	cfg.Python.PythonPath = testPythonPath(t)
	cfg.Python.ScriptsDir = root
	cfg.Python.TimeoutSec = 60
	return NewPythonBridge(cfg), counter
}

func stubExecCount(t *testing.T, counter string) int {
	t.Helper()
	data, err := os.ReadFile(counter)
	if err != nil {
		t.Fatalf("桩未被执行（counter 缺失）: %v", err)
	}
	return strings.Count(string(data), "x")
}

// 探测要起子进程并跑 WMI，TTL 内的重复调用必须命中缓存。
func TestProbeGPUUsesSingleExecWithinTTL(t *testing.T) {
	pb, counter := stubGPUProbeBridge(t)

	if _, err := pb.ProbeGPU(context.Background()); err != nil {
		t.Fatalf("首次探测应成功: %v", err)
	}
	if _, err := pb.ProbeGPU(context.Background()); err != nil {
		t.Fatalf("缓存命中应成功: %v", err)
	}
	if n := stubExecCount(t, counter); n != 1 {
		t.Fatalf("TTL 内重复调用应只 exec 1 次，实际 %d 次", n)
	}
}

// 用户装完 CUDA/DirectML 后不能还看到旧结论：显式失效必须立刻重探。
func TestProbeGPURechecksAfterExplicitInvalidation(t *testing.T) {
	pb, counter := stubGPUProbeBridge(t)

	if _, err := pb.ProbeGPU(context.Background()); err != nil {
		t.Fatalf("首次探测应成功: %v", err)
	}
	pb.InvalidateGPUProbe()
	if _, err := pb.ProbeGPU(context.Background()); err != nil {
		t.Fatalf("失效后应重新探测: %v", err)
	}
	if n := stubExecCount(t, counter); n != 2 {
		t.Fatalf("显式失效后应重跑一次，实际 exec %d 次", n)
	}
}
