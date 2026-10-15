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
	"runtime"
	"strings"
	"sync"
	"time"
)

// Serialize deploys, including verification, and reuse a running local pet.
var desktopLaunchMu sync.Mutex
var desktopProcess *exec.Cmd
var desktopDone chan struct{}
var desktopResult map[string]interface{}

func copyDesktopResult(src map[string]interface{}) map[string]interface{} {
	dst := make(map[string]interface{}, len(src))
	for k, v := range src {
		dst[k] = v
	}
	return dst
}

// DeployDesktopModel 验收模型后拉起原生桌宠窗口。
//
// attach 可选：桌宠首帧就绪后回调一次，参数是其 stdin 写入端，返回值在桌宠
// 退出时调用。Go 借此把原生面捕参数以 JSON Lines 推给桌宠（无面捕时静默）。
func (pb *PythonBridge) DeployDesktopModel(ctx context.Context, modelDir string, attach func(stdin io.Writer) (detach func())) (map[string]interface{}, error) {
	desktopLaunchMu.Lock()
	defer desktopLaunchMu.Unlock()
	if runtime.GOOS != "windows" {
		return nil, fmt.Errorf("原生桌宠部署目前仅支持 Windows 交互桌面")
	}
	if desktopProcess != nil {
		select {
		case <-desktopDone:
			desktopProcess = nil
		default:
			return nil, fmt.Errorf("已有桌宠正在运行，请先关闭现有桌宠窗口")
		}
	}
	report, err := pb.VerifyDesktopModel(ctx, modelDir)
	if err != nil {
		return nil, err
	}
	argv, ok := report["launch_argv"].([]string)
	if !ok || len(argv) != 4 {
		return nil, fmt.Errorf("启动参数无效")
	}
	hash, ok := report["package_sha256"].(string)
	if !ok || len(hash) != 64 {
		return nil, fmt.Errorf("验收缺少模型指纹")
	}
	cmd := exec.Command(argv[0], append(argv[1:], "--expected-sha256", hash)...)
	cmd.Dir = pb.cfg.Python.ScriptsDir
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8", "PYTHONUNBUFFERED=1", "PYTHONPATH="+pb.cfg.Python.ScriptsDir)
	configurePythonProcess(cmd)
	// Keep logs off API responses; Python/SDK may emit arbitrary diagnostics.
	logDir := filepath.Join(pb.cfg.Output.BaseDir, ".desktop-logs")
	if err := os.MkdirAll(logDir, 0700); err != nil {
		return nil, err
	}
	logFile, err := os.CreateTemp(logDir, "pet-*.log")
	if err != nil {
		return nil, err
	}
	cmd.Stderr = logFile
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		logFile.Close()
		return nil, err
	}
	// 桌宠参数输入管道：原生面捕参数经此以 JSON Lines 喂给桌宠。
	var petStdin io.WriteCloser
	if attach != nil {
		petStdin, err = cmd.StdinPipe()
		if err != nil {
			stdout.Close()
			logFile.Close()
			return nil, err
		}
	}
	if err := cmd.Start(); err != nil {
		stdout.Close()
		if petStdin != nil {
			petStdin.Close()
		}
		logFile.Close()
		return nil, err
	}
	ready := make(chan map[string]interface{}, 1)
	done := make(chan struct{})
	go func() {
		defer close(done)
		defer logFile.Close()
		if petStdin != nil {
			defer petStdin.Close()
		}
		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 4096), 1024*1024)
		for scanner.Scan() {
			line := scanner.Text()
			fmt.Fprintln(logFile, line)
			if strings.HasPrefix(line, "LIVE2D_PET=") {
				var msg map[string]interface{}
				if json.Unmarshal([]byte(strings.TrimPrefix(line, "LIVE2D_PET=")), &msg) == nil &&
					msg["event"] == "ready" && msg["package_sha256"] == hash &&
					msg["frames_drawn"] == float64(1) {
					select {
					case ready <- msg:
					default:
					}
				}
			}
		}
		if scanner.Err() != nil {
			killPythonProcess(cmd)
		}
		_ = cmd.Wait()
	}()
	timer := time.NewTimer(30 * time.Second)
	defer timer.Stop()
	select {
	case msg := <-ready:
		select {
		case <-done:
			return nil, fmt.Errorf("桌宠首帧后提前退出")
		default:
		}
		report["deployed"] = true
		report["ready_to_launch"] = false
		report["deployment_status"] = "running"
		report["pid"] = msg["pid"]
		report["launcher_pid"] = cmd.Process.Pid
		report["window"] = msg
		delete(report, "launch_argv")
		desktopProcess, desktopDone, desktopResult = cmd, done, copyDesktopResult(report)

		// 首帧已绘制：现在才接入参数源，避免桌宠启动失败时空转摄像头。
		if attach != nil && petStdin != nil {
			detach := attach(petStdin)
			if detach != nil {
				go func() {
					<-done
					detach()
				}()
			}
		}
		return report, nil
	case <-done:
		return nil, fmt.Errorf("桌宠在首帧就绪前退出，请查看本地桌宠日志")
	case <-timer.C:
		killPythonProcess(cmd)
		<-done
		return nil, fmt.Errorf("桌宠启动超时，进程已终止")
	}
}

func DesktopDeploymentStatus() map[string]interface{} {
	desktopLaunchMu.Lock()
	defer desktopLaunchMu.Unlock()
	if desktopProcess == nil {
		return map[string]interface{}{"deployed": false, "deployment_status": "stopped"}
	}
	result := copyDesktopResult(desktopResult)
	select {
	case <-desktopDone:
		result["deployed"] = false
		result["deployment_status"] = "stopped"
	default:
	}
	return result
}
