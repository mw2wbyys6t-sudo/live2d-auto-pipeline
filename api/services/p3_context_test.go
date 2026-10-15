package services

// P3-H4：请求取消/超时必须杀死整个 Python 进程组（含孙子进程）。
// Go 自带的 CommandContext 只杀直接子进程；workflow.py 再派生的子进程
// 会带着继承的 stdout 管道继续跑，导致 CombinedOutput/Wait 永不返回——
// 表现为客户端断连后 GPU/CPU 任务空转、goroutine 泄漏。

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"testing"
	"time"

	"live2d-api/config"
)

func newP3Bridge(t *testing.T) *PythonBridge {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Python.PythonPath = testPythonPath(t)
	cfg.Python.ScriptsDir = t.TempDir()
	cfg.Python.TimeoutSec = 30
	return NewPythonBridge(cfg)
}

// testPythonPath 返回一个真实可用的解释器路径。
//
// 这些用例必须真的起子进程（验的是超时包装与整个进程组的终止），所以硬编码
// "python3" 不行：Windows 上通常只有 "python"，脚本会以 exit 9009（命令未找到）
// 立即失败——超时用例因此假通过、正常路径用例假失败。约定与 CI / handlers 一致：
// 优先 LIVE2D_TEST_PYTHON，其次在 PATH 上找 python3 / python，都没有才跳过。
func testPythonPath(t *testing.T) string {
	t.Helper()
	if p := os.Getenv("LIVE2D_TEST_PYTHON"); p != "" {
		return p
	}
	// Windows 的 PATH 里 WindowsApps\python3.exe 是 Microsoft Store 的
	// App Execution Alias 占位符：LookPath 能找到它，但执行只会返回 9009。
	// 因此必须真的跑一次探针，确认解释器可用才采用。
	for _, name := range []string{"python3", "python"} {
		p, err := exec.LookPath(name)
		if err != nil {
			continue
		}
		if exec.Command(p, "-c", "").Run() == nil {
			return p
		}
	}
	t.Skip("需要 LIVE2D_TEST_PYTHON，或 PATH 上有可用的 python3/python 才能跑真实解释器用例")
	return ""
}

// Python 派一个后台 sleep（继承 stdout 管道），自己再睡 30 秒。
// 孙子进程用 sys.executable 起，不要用 ['sleep','30'] —— Windows 没有 sleep 命令，
// Popen 会直接抛 FileNotFoundError，用例就退化成了「进程立即退出」的假通过。
const groupHogCode = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); time.sleep(30)"

func TestP3CancelKillsWholeProcessGroup(t *testing.T) {
	pb := newP3Bridge(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := pb.executePythonScript(ctx, []string{"-c", groupHogCode}, 30*time.Second)
		done <- err
	}()

	time.Sleep(500 * time.Millisecond)
	cancel()

	select {
	case <-done:
		// 进程组（含后台 sleep）被杀，管道关闭，调用及时返回
	case <-time.After(5 * time.Second):
		t.Fatal("ctx 取消后 5 秒内调用未返回：孙子进程未随进程组一起被杀")
	}
}

func TestP3TimeoutReturnsSentinel(t *testing.T) {
	pb := newP3Bridge(t)
	_, err := pb.executePythonScript(
		context.Background(), []string{"-c", "import time; time.sleep(30)"}, 200*time.Millisecond,
	)
	if err == nil {
		t.Fatal("超时必须返回错误")
	}
	if !errors.Is(err, ErrPythonTimeout) {
		t.Fatalf("超时错误必须包装 ErrPythonTimeout，实得 %v", err)
	}
}

func TestP3ParentCancelWinsOverTimeout(t *testing.T) {
	pb := newP3Bridge(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := pb.executePythonScript(ctx, []string{"-c", groupHogCode}, 30*time.Second)
		done <- err
	}()
	time.Sleep(300 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("父 ctx 取消未能终止任务")
	}
}

// M3：runBridgeTask 是 handler（segment/PSD 导入/embedding/export）的统一
// 入口，它必须原样透传 ErrPythonTimeout，504 契约才走得到；此前超时在这里
// 被吞成普通错误，handler 只能误报 500。
func TestP3BridgeTaskTimeoutWrapsSentinel(t *testing.T) {
	pb := newP3Bridge(t)
	const sleepScript = "import time; time.sleep(30)\n"
	_, err := pb.runBridgeTask(context.Background(), sleepScript, nil, 200*time.Millisecond)
	if !errors.Is(err, ErrPythonTimeout) {
		t.Fatalf("runBridgeTask 超时必须透传 ErrPythonTimeout，实得 %v", err)
	}
}

// M3 正向伴侣：预算充足时桥接任务正常返回 JSON，错误处理改动不误伤正常路径。
func TestP3BridgeTaskLegitimatePassThrough(t *testing.T) {
	pb := newP3Bridge(t)
	const okScript = "import sys, json; print(json.dumps({'ok': True}))\n"
	res, err := pb.runBridgeTask(context.Background(), okScript, nil, 30*time.Second)
	if err != nil {
		t.Fatalf("正常桥接任务不应失败: %v", err)
	}
	inner, ok := res["result"].(map[string]interface{})
	if !ok || inner["ok"] != true {
		t.Fatalf("桥接任务结果异常: %#v", res)
	}
}
