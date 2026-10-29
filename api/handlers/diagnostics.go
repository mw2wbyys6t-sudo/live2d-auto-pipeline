package handlers

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/gin-gonic/gin"

	"live2d-api/models"
)

// GetDiagnostics 环境自检（P2）：返回本机解释器、脚本目录、产物目录可写性、
// 面捕三件套（exe/onnx/dll）可用性、监听地址、版本。供前端自检页逐条绿/黄/红展示，
// 全部只读，不触发任何生成。
func (h *Handler) GetDiagnostics(c *gin.Context) {
	cfg := h.cfg

	// 产物目录可写性：尝试建一个临时文件再删掉。
	outputWritable := true
	outputNote := ""
	if err := func() error {
		if err := os.MkdirAll(cfg.Output.BaseDir, 0o755); err != nil {
			return err
		}
		tmp := filepath.Join(cfg.Output.BaseDir, ".write-probe")
		if err := os.WriteFile(tmp, []byte("ok"), 0o644); err != nil {
			return err
		}
		return os.Remove(tmp)
	}(); err != nil {
		outputWritable = false
		outputNote = err.Error()
	}

	// 面捕三件套：exe（NativeBridge.FindBinary）、YuNet onnx、onnxruntime.dll。
	trackerPath := h.nativeBridge.FindBinary()
	trackerExe := trackerPath != ""
	trackerModel, trackerDll := checkTrackerAssets(trackerPath)

	// 诊断页是用户排查环境的入口，结论必须是**当下**的，不能吃 /api/health
	// 那 30s 缓存 —— 用户刚装完依赖点开诊断，看到旧结论会以为没装上。
	h.pythonBridge.InvalidatePythonEnvironment()
	pyOK, pyIssues := h.pythonBridge.CheckPythonEnvironment()

	// GPU 行同理要「当下」结论：用户刚在设置页装好 CUDA / DirectML 运行时，
	// 再点诊断就该看到变化，而不是 30s 缓存里的旧答案。
	h.pythonBridge.InvalidateGPUProbe()
	gpuCheck := gpuDiagnosticCheck(h.pythonBridge.ProbeGPU(c.Request.Context()))

	checks := []models.DiagnosticCheck{
		{Key: "backend", Label: "Go 后端", OK: true, Detail: Version},
		{Key: "python", Label: "Python 解释器", OK: pyOK,
			Detail: cfg.Python.PythonPath, Issues: pyIssues},
		{Key: "scripts_dir", Label: "工程脚本目录", OK: dirExists(cfg.Python.ScriptsDir),
			Detail: cfg.Python.ScriptsDir},
		{Key: "output_dir", Label: "产物目录可写", OK: outputWritable,
			Detail: cfg.Output.BaseDir, Issues: noteIf(outputNote)},
		{Key: "tracker_exe", Label: "面捕程序", OK: trackerExe, Detail: trackerPath},
		{Key: "tracker_model", Label: "YuNet 人脸模型", OK: trackerModel,
			Detail: "缺失时回退肤色检测（精度更低）"},
		{Key: "tracker_dll", Label: "ONNX Runtime 运行库", OK: trackerDll,
			Detail: "缺失时回退肤色检测（精度更低）"},
		gpuCheck,
		{Key: "bind_loopback", Label: "仅本机回环监听", OK: isLoopback(cfg.Server.Host),
			Detail: cfg.Server.Host},
	}

	c.JSON(http.StatusOK, models.Response{
		Success: true,
		Data: gin.H{
			"version":     Version,
			"os":          runtime.GOOS,
			"host":        cfg.Server.Host,
			"port":        cfg.Server.Port,
			"checks":      checks,
			"python_path": cfg.Python.PythonPath,
			"output_dir":  cfg.Output.BaseDir,
		},
	})
}

func dirExists(p string) bool {
	if p == "" {
		return false
	}
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

func noteIf(s string) []string {
	if s == "" {
		return nil
	}
	return []string{s}
}

// gpuDiagnosticCheck 把探测结果压成自检页的一行。
//
// 「装了但用不上」必须看得出来：硬件在列表里、但 onnxruntime 只有 CPU
// provider 时，之前这一片是完全静默的 —— 用户以为在跑 GPU，实际在跑 CPU。
// 探测失败只报「暂不可用」，错误原文（含解释器名与本机路径）不进响应。
func gpuDiagnosticCheck(gpu map[string]interface{}, err error) models.DiagnosticCheck {
	row := models.DiagnosticCheck{Key: "gpu", Label: "GPU 与推理运行时"}
	if err != nil {
		log.Printf("[error] GPU 探测: %v", err)
		row.OK = false
		row.Detail = "GPU 探测暂不可用"
		row.Issues = noteIf("GPU 探测暂不可用，分层按 CPU 运行")
		return row
	}

	available, _ := gpu["available"].(bool)
	rec, _ := gpu["recommendation"].(map[string]interface{})
	device, _ := rec["device"].(string)
	names := make([]string, 0, 4)
	gpuNames := make([]string, 0, 4)
	if adapters, ok := gpu["adapters"].([]interface{}); ok {
		for _, item := range adapters {
			a, ok := item.(map[string]interface{})
			if !ok {
				continue
			}
			name, _ := a["name"].(string)
			if name == "" {
				continue
			}
			names = append(names, name)
			// issue 文案只点真显卡：虚拟显示器（远程桌面/采集卡）也带驱动版本，
			// 把它们写进「已检测到」会让人白装 CUDA/DirectML。
			vendor, _ := a["vendor"].(string)
			if vendor == "NVIDIA" || vendor == "AMD" || vendor == "INTEL" {
				gpuNames = append(gpuNames, name)
			}
		}
	}

	row.Detail = strings.Join(names, " / ")
	if row.Detail == "" {
		row.Detail = "未枚举到显示适配器"
	}
	switch {
	case !available:
		row.OK = false
		row.Detail += " → cpu"
		row.Issues = noteIf("未检测到可用独立显卡，分层按 CPU 运行")
	case device == "cpu":
		row.OK = false
		row.Detail += " → cpu"
		row.Issues = noteIf(fmt.Sprintf(
			"%s 已检测到，但当前推理运行时只有 CPU provider（onnxruntime 缺 CUDA/DirectML），分层按 CPU 运行",
			strings.Join(gpuNames, "/")))
	default:
		row.OK = true
		row.Detail += " → " + device
	}
	return row
}

// checkTrackerAssets 从 exe 同目录推断 YuNet onnx 与 onnxruntime.dll 是否就位。
func checkTrackerAssets(binaryPath string) (modelOK, dllOK bool) {
	dir := filepath.Dir(binaryPath)
	if binaryPath == "" || dir == "." {
		return false, false
	}
	for _, name := range []string{
		"face_detection_yunet_2023mar.onnx", "yunet.onnx", "face_detector.onnx",
	} {
		if dirExists(filepath.Join(dir, "models")) &&
			fileExists(filepath.Join(dir, "models", name)) {
			modelOK = true
			break
		}
	}
	dllName := "onnxruntime.dll"
	if runtime.GOOS != "windows" {
		dllName = "libonnxruntime.so"
	}
	dllOK = fileExists(filepath.Join(dir, dllName))
	return
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func isLoopback(host string) bool {
	switch host {
	case "127.0.0.1", "localhost", "::1", "0.0.0.0":
		return host != "0.0.0.0"
	}
	return false
}
