package services

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"sync/atomic"

	"live2d-api/config"
	"live2d-api/models"
)

// NativeBridge 管理 Rust face_tracker 子进程。
//
// 生命周期：StartTracking() → spawn 进程 + 发送 start 命令 →
// 后台 goroutine 持续读 stdout → 每帧经 WS Hub 推送 →
// StopTracking() → 发送 stop → Shutdown() → 发送 quit + 杀进程。
type NativeBridge struct {
	cfg     *config.Config
	hub     *WSHub
	mu      sync.Mutex
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	running atomic.Bool
	// frameSink 是每帧参数回调（桌宠 stdin 管道消费）。在 readLoop goroutine
	// 上同步调用，必须立即返回；同一时刻只支持一个消费者。
	sinkMu    sync.RWMutex
	frameSink func(params map[string]float64)
}

// NewNativeBridge 创建原生面捕桥接服务。
func NewNativeBridge(cfg *config.Config, hub *WSHub) *NativeBridge {
	return &NativeBridge{cfg: cfg, hub: hub}
}

// FindBinary 查找 face_tracker 可执行文件。
// 搜索顺序：配置路径 → 开发构建目录 → 生产部署目录。
func (nb *NativeBridge) FindBinary() string {
	// 1. 配置指定的路径
	if p := nb.cfg.NativeTracker.BinaryPath; p != "" {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}

	exeName := "face_tracker"
	if runtime.GOOS == "windows" {
		exeName = "face_tracker.exe"
	}

	// 2. 开发模式：项目根/native/face_tracker/target/release/
	devPath := filepath.Join(nb.cfg.Python.ScriptsDir, "native", "face_tracker", "target", "release", exeName)
	if _, err := os.Stat(devPath); err == nil {
		return devPath
	}

	// 3. 生产模式：与 Go 二进制同目录
	if exe, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exe)
		prodPath := filepath.Join(exeDir, exeName)
		if _, err := os.Stat(prodPath); err == nil {
			return prodPath
		}
	}

	return ""
}

// IsAvailable 检查面捕二进制是否可用。
func (nb *NativeBridge) IsAvailable() bool {
	return nb.FindBinary() != ""
}

// IsRunning 返回面捕是否正在运行。
func (nb *NativeBridge) IsRunning() bool {
	return nb.running.Load()
}

// SetFrameSink 注册每帧参数回调（传 nil 取消）。回调只在检测到人脸时触发，
// 无人脸时不回调，让消费者（如桌宠）自行回落到待机动画。
func (nb *NativeBridge) SetFrameSink(fn func(params map[string]float64)) {
	nb.sinkMu.Lock()
	nb.frameSink = fn
	nb.sinkMu.Unlock()
}

// emitFrame 把一帧参数分发给已注册的消费者。
func (nb *NativeBridge) emitFrame(params map[string]float64) {
	nb.sinkMu.RLock()
	fn := nb.frameSink
	nb.sinkMu.RUnlock()
	if fn != nil {
		fn(params)
	}
}

// StartTracking 启动面捕子进程。
func (nb *NativeBridge) StartTracking() error {
	nb.mu.Lock()
	defer nb.mu.Unlock()

	if nb.running.Load() {
		return fmt.Errorf("面捕已在运行")
	}

	binary := nb.FindBinary()
	if binary == "" {
		return fmt.Errorf("未找到 face_tracker 二进制文件，请先编译：cd native/face_tracker && cargo build --release")
	}

	cmd := exec.Command(binary)
	cmd.Stderr = os.Stderr // 面捕的调试日志走 stderr，不污染 stdout 协议

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("创建 stdin 管道失败: %v", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		stdin.Close()
		return fmt.Errorf("创建 stdout 管道失败: %v", err)
	}

	if err := cmd.Start(); err != nil {
		stdin.Close()
		stdout.Close()
		return fmt.Errorf("启动面捕进程失败: %v", err)
	}

	nb.cmd = cmd
	nb.stdin = stdin
	nb.running.Store(true)

	log.Printf("[native_bridge] 面捕进程已启动 (pid=%d, binary=%s)", cmd.Process.Pid, binary)

	// 启动 stdout 读取循环
	go nb.readLoop(stdout)

	// 发送 start 命令
	return nb.sendCommand(map[string]interface{}{
		"cmd":       "start",
		"camera_id": nb.cfg.NativeTracker.CameraID,
		"width":     nb.cfg.NativeTracker.Width,
		"height":    nb.cfg.NativeTracker.Height,
		"fps":       nb.cfg.NativeTracker.FPS,
	})
}

// StopTracking 停止面捕并回收子进程。
//
// 采用「终止进程」而不是「发送 stop 命令」是有意为之：nokhwa 0.10 的 Windows
// 后端 stop_stream() 只重置内部状态，并不释放 Media Foundation 的 source
// reader，再次 open_stream() 会因硬件 MFT 未释放而失败（0xC00D3704）；而它的
// Drop 又在释放 COM 对象之前就调用 MFShutdown()，直接析构会触发访问冲突。
// 终止进程由 OS 释放摄像头，下次启动开一个全新进程，两端的资源都干净。
func (nb *NativeBridge) StopTracking() error {
	nb.mu.Lock()
	defer nb.mu.Unlock()

	if !nb.running.Load() && nb.cmd == nil {
		return nil
	}
	nb.shutdownLocked()
	return nil
}

// Shutdown 彻底关闭面捕子进程（供进程退出时的信号处理调用）。
func (nb *NativeBridge) Shutdown() {
	nb.mu.Lock()
	defer nb.mu.Unlock()
	nb.shutdownLocked()
}

// shutdownLocked 关闭子进程并释放资源；调用方必须已持有 nb.mu。
func (nb *NativeBridge) shutdownLocked() {
	if nb.stdin != nil {
		// 尽力让子进程优雅退出（失败无妨，随后 Kill 兜底）
		_ = nb.sendCommand(map[string]interface{}{"cmd": "quit"})
		nb.stdin.Close()
		nb.stdin = nil
	}

	if nb.cmd != nil && nb.cmd.Process != nil {
		nb.cmd.Process.Kill()
		nb.cmd.Wait()
		nb.cmd = nil
	}

	nb.running.Store(false)
	log.Printf("[native_bridge] 面捕进程已关闭")
}

// sendCommand 通过 stdin 发送 JSON 命令。
func (nb *NativeBridge) sendCommand(cmd map[string]interface{}) error {
	if nb.stdin == nil {
		return fmt.Errorf("面捕进程未运行")
	}
	data, err := json.Marshal(cmd)
	if err != nil {
		return fmt.Errorf("序列化命令失败: %v", err)
	}
	data = append(data, '\n')
	_, err = nb.stdin.Write(data)
	return err
}

// readLoop 持续读取面捕子进程的 stdout（JSON Lines）。
func (nb *NativeBridge) readLoop(stdout io.Reader) {
	scanner := bufio.NewScanner(stdout)
	// 面捕帧可能包含较大的 blendshape/live2d_params 字段，扩大缓冲区
	scanner.Buffer(make([]byte, 0, 64*1024), 10*1024*1024)

	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}

		var msg struct {
			Type          string             `json:"type"`
			Version       string             `json:"version,omitempty"`
			HasCamera     bool               `json:"has_camera,omitempty"`
			HasModel      bool               `json:"has_model,omitempty"`
			CameraID      uint32             `json:"camera_id,omitempty"`
			Width         uint32             `json:"width,omitempty"`
			Height        uint32             `json:"height,omitempty"`
			Blendshapes   map[string]float64 `json:"blendshapes,omitempty"`
			HeadRotation  *struct {
				Pitch float64 `json:"pitch"`
				Yaw   float64 `json:"yaw"`
				Roll  float64 `json:"roll"`
			} `json:"head_rotation,omitempty"`
			Live2DParams  map[string]float64 `json:"live2d_params,omitempty"`
			FPS           float64            `json:"fps,omitempty"`
			TimestampMs   uint64             `json:"timestamp_ms,omitempty"`
			Message       string             `json:"message,omitempty"`
			Running       bool               `json:"running,omitempty"`
			Smoothing     float64            `json:"smoothing,omitempty"`
			Deadzone      float64            `json:"deadzone,omitempty"`
		}

		if err := json.Unmarshal(line, &msg); err != nil {
			log.Printf("[native_bridge] JSON 解析失败: %v (line: %.200s)", err, string(line))
			continue
		}

		switch msg.Type {
		case "ready":
			log.Printf("[native_bridge] 面捕就绪 (version=%s, camera=%v, model=%v)",
				msg.Version, msg.HasCamera, msg.HasModel)

		case "started":
			log.Printf("[native_bridge] 面捕已启动 (camera=%d, %dx%d)", msg.CameraID, msg.Width, msg.Height)
			if nb.hub != nil {
				nb.hub.Broadcast("tracking_status", map[string]interface{}{
					"status":     "started",
					"camera_id":  msg.CameraID,
					"width":      msg.Width,
					"height":     msg.Height,
					"source":     "native",
				})
			}

		case "frame":
			// 每帧通过 WebSocket 推送给前端
			if nb.hub != nil {
				frameData := map[string]interface{}{
					"source":    "native",
					"timestamp": msg.TimestampMs,
					"fps":       msg.FPS,
				}
				if msg.Live2DParams != nil {
					frameData["live2d_params"] = msg.Live2DParams
				}
				if msg.Blendshapes != nil {
					frameData["blendshapes"] = msg.Blendshapes
				}
				if msg.HeadRotation != nil {
					frameData["head_rotation"] = msg.HeadRotation
				}
				nb.hub.Broadcast("tracking_frame", frameData)
			}
			// 有真实人脸时才转发给进程内消费者（桌宠）；无人脸时消费者
			// 收不到新帧，会自行回落到待机动画。
			if msg.Blendshapes != nil && msg.Live2DParams != nil {
				nb.emitFrame(msg.Live2DParams)
			}

		case "stopped":
			nb.running.Store(false)
			log.Printf("[native_bridge] 面捕已停止")
			if nb.hub != nil {
				nb.hub.Broadcast("tracking_status", map[string]interface{}{
					"status": "stopped",
					"source": "native",
				})
			}

		case "status":
			// 状态查询响应（日志记录即可）
			log.Printf("[native_bridge] 面捕状态: running=%v, fps=%.1f", msg.Running, msg.FPS)

		case "error":
			log.Printf("[native_bridge] 面捕错误: %s", msg.Message)
			if nb.hub != nil {
				nb.hub.Broadcast("tracking_error", models.WSMessage{
					Type:    "tracking_error",
					Message: msg.Message,
					Time:    int64(msg.TimestampMs),
				})
			}
		}
	}

	// stdout 关闭 = 子进程退出
	nb.running.Store(false)
	log.Printf("[native_bridge] 面捕进程 stdout 已关闭")

	// 确保进程被回收
	nb.mu.Lock()
	if nb.cmd != nil && nb.cmd.Process != nil {
		nb.cmd.Wait()
		nb.cmd = nil
	}
	nb.stdin = nil
	nb.mu.Unlock()
}
