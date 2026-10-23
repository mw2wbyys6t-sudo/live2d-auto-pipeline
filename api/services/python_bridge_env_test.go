package services

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"live2d-api/config"
)

// countingFakePython 生成一个假解释器：每次被执行就往 counter 文件追加一行，
// 并把 stdout 里给定的 JSON 原样打印出来。
//
// 用它来断言「探测只 exec 一次」与「缓存期内不再 exec」——
// 这是 v0.10.9 性能修复的核心契约：旧实现每次调用要跑 3 个子进程，
// /api/health 被秒级轮询时等于持续 fork Python。
func countingFakePython(t *testing.T, stdout string) (exe string, counter string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("用 /bin/sh 脚本模拟解释器；Windows 侧由 handlers 包的健康检查用例覆盖")
	}
	dir := t.TempDir()
	counter = filepath.Join(dir, "calls.txt")
	exe = filepath.Join(dir, "fake-python.sh")
	body := fmt.Sprintf("#!/bin/sh\necho x >> %q\nprintf '%%s' %q\n", counter, stdout)
	if err := os.WriteFile(exe, []byte(body), 0o755); err != nil {
		t.Fatalf("写入假解释器失败: %v", err)
	}
	return exe, counter
}

func execCount(t *testing.T, counter string) int {
	t.Helper()
	data, err := os.ReadFile(counter)
	if os.IsNotExist(err) {
		return 0
	}
	if err != nil {
		t.Fatalf("读取调用计数失败: %v", err)
	}
	return strings.Count(string(data), "x")
}

func newEnvTestBridge(pythonPath string) *PythonBridge {
	return NewPythonBridge(&config.Config{
		Python: config.PythonConfig{PythonPath: pythonPath, TimeoutSec: 5},
	})
}

// 合并 exec：一次探测只允许拉起一个子进程（旧实现是 3 个）。
func TestPythonEnvProbeUsesSingleExec(t *testing.T) {
	exe, counter := countingFakePython(t, `{"version":"3.11.0","missing":[]}`)
	pb := newEnvTestBridge(exe)

	ok, issues := pb.CheckPythonEnvironment()
	if !ok || len(issues) != 0 {
		t.Fatalf("期望环境可用且无 issue，实际 ok=%v issues=%v", ok, issues)
	}
	if n := execCount(t, counter); n != 1 {
		t.Fatalf("一次探测应只 exec 1 次（合并 version + 依赖检查），实际 %d 次", n)
	}
}

// 缓存：TTL 内的重复调用不得再起子进程。
func TestPythonEnvCheckIsCached(t *testing.T) {
	exe, counter := countingFakePython(t, `{"version":"3.11.0","missing":[]}`)
	pb := newEnvTestBridge(exe)

	if ok, _ := pb.CheckPythonEnvironment(); !ok {
		t.Fatal("首次探测应返回可用")
	}
	if ok, _ := pb.CheckPythonEnvironment(); !ok {
		t.Fatal("缓存命中应复用同一结论")
	}
	if n := execCount(t, counter); n != 1 {
		t.Fatalf("缓存期内不应重复 exec，实际 %d 次", n)
	}
}

// 失效：InvalidatePythonEnvironment 之后必须真的重跑。
func TestPythonEnvInvalidateForcesRecheck(t *testing.T) {
	exe, counter := countingFakePython(t, `{"version":"3.11.0","missing":[]}`)
	pb := newEnvTestBridge(exe)

	pb.CheckPythonEnvironment()
	pb.InvalidatePythonEnvironment()
	pb.CheckPythonEnvironment()

	if n := execCount(t, counter); n != 2 {
		t.Fatalf("失效后应再 exec 一次（合计 2），实际 %d 次", n)
	}
}

// 缺失依赖必须仍能逐项报出来 —— 合并 exec 不等于丢信息。
func TestPythonEnvCheckReportsMissingDependency(t *testing.T) {
	exe, _ := countingFakePython(t, `{"version":"3.11.0","missing":["numpy"]}`)
	pb := newEnvTestBridge(exe)

	ok, issues := pb.CheckPythonEnvironment()
	if ok {
		t.Fatal("缺 numpy 时应判定不可用")
	}
	if len(issues) != 1 || !strings.Contains(issues[0], "numpy") {
		t.Fatalf("应报出缺失的 numpy，实际 issues=%v", issues)
	}
}

// 输出不可解析（假解释器只返回退出码）时，退回「退出码 0 即可用」，
// 与合并前的判据一致 —— 否则会把健康状态平白判成 503。
func TestPythonEnvCheckFallsBackWhenOutputUnparsable(t *testing.T) {
	exe, _ := countingFakePython(t, "")
	pb := newEnvTestBridge(exe)

	ok, issues := pb.CheckPythonEnvironment()
	if !ok || len(issues) != 0 {
		t.Fatalf("输出不可解析时应回退为「退出码 0 即可用」，实际 ok=%v issues=%v", ok, issues)
	}
}

// 解释器本身起不来时必须是不可用 + 可读原因。
func TestPythonEnvCheckReportsBrokenInterpreter(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("依赖 /bin/false")
	}
	pb := newEnvTestBridge("/bin/false")

	ok, issues := pb.CheckPythonEnvironment()
	if ok {
		t.Fatal("/bin/false 应判定为不可用")
	}
	if len(issues) == 0 || !strings.Contains(issues[0], "Python 不可用") {
		t.Fatalf("应给出可读的不可用原因，实际 issues=%v", issues)
	}
}
