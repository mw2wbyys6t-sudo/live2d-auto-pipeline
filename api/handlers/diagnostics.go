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

	// 图像上游：自检页的副标题承诺「全部通过即可正常生成与导出」，那么当唯一
	// 可用通道是免密上游时不能报绿 —— 实测（2026-10-30 装机版）九项全绿、
	// 页面写着「环境就绪，可以开始生成」，而唯一的 pollinations 每次返回 402，
	// 用户最后拿到的是一张几何占位图，且界面上没有任何地方说这件事。
	providers, _, provErr := h.pythonBridge.ListImageProviders(c.Request.Context())
	imgCheck := imageProviderCheck(providers, provErr)

	checks := []models.DiagnosticCheck{
		{Key: "backend", Label: "Go 后端", OK: true, Detail: Version},
		{Key: "python", Label: "Python 解释器", OK: pyOK,
			Detail: cfg.Python.PythonPath, Issues: pyIssues},
		{Key: "scripts_dir", Label: "工程脚本目录", OK: dirExists(cfg.Python.ScriptsDir),
			Detail: cfg.Python.ScriptsDir},
		{Key: "output_dir", Label: "产物目录可写", OK: outputWritable,
			Detail: cfg.Output.BaseDir, Issues: noteIf(outputNote)},
		{Key: "tracker_exe", Label: "面捕程序", OK: trackerExe, Detail: trackerPath},
		assetCheck("tracker_model", "YuNet 人脸模型", trackerModel),
		assetCheck("tracker_dll", "ONNX Runtime 运行库", trackerDll),
		imgCheck,
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
			// 自检页照这个值显示日志位置。此前页面写死
			// "%APPDATA%\PygmalionStudio\logs"，而实测本机日志在别处 ——
			// 用户按提示去找 live2d.log 会一无所获，反馈问题时也带不上证据。
			"log_dir": cfg.LogsDir(),
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

// assetCheck 面捕随包资源的诊断行。
//
// 此前 Detail 无论通过与否都写「缺失时回退肤色检测（精度更低）」——
// 实测装机版自检页上，YuNet 与 ONNX 两行是绿的却带着这句失败解释，
// 用户读到的是一行报错。通过就说通过，缺失才把后果讲清楚。
func assetCheck(key, label string, present bool) models.DiagnosticCheck {
	if present {
		return models.DiagnosticCheck{Key: key, Label: label, OK: true, Detail: "已就位"}
	}
	return models.DiagnosticCheck{Key: key, Label: label, OK: false,
		Detail: "未找到",
		Issues: []string{"面捕将回退肤色检测（精度更低），或无法启动"}}
}

// imageProviderCheck 图像上游是否**可靠可用**。
//
// 判据是「有没有一个需要密钥、因此已被用户配置好的上游」，不是「有没有任何
// 上游」：免密通道按配额放行、随时 402/429，而本地占位图根本不是生成结果。
// 只有这两类时自检页不该说「环境就绪，可以开始生成」—— 实测装机版正是这样，
// 九项全绿却每次只出占位图。
//
// 取不到上游列表（Python 侧失败）也判不通过：诊断页不能把"查不到"说成"没问题"。
func imageProviderCheck(available []map[string]interface{}, err error) models.DiagnosticCheck {
	const label = "图像上游"
	if err != nil || len(available) == 0 {
		detail := "查不到可用上游"
		if err != nil {
			log.Printf("[config] 图像上游查询失败: %v", err)
		}
		return models.DiagnosticCheck{Key: "image_provider", Label: label, OK: false,
			Detail: detail,
			Issues: []string{"生成会失败或只得到本地占位图；请检查网络，或在设置里配置图像服务"}}
	}
	names := make([]string, 0, len(available))
	configured := 0
	for _, p := range available {
		name, _ := p["name"].(string)
		if name == "" {
			continue
		}
		names = append(names, name)
		if needsKey, _ := p["requires_key"].(bool); needsKey {
			configured++
		}
	}
	joined := strings.Join(names, ", ")
	if configured == 0 {
		return models.DiagnosticCheck{Key: "image_provider", Label: label, OK: false,
			Detail: "仅免密通道: " + joined,
			Issues: []string{"免密通道按配额放行，随时可能 402/429；要稳定出图请在设置里配置 Seedream / SenseNova / OpenAI 兼容端点"}}
	}
	return models.DiagnosticCheck{Key: "image_provider", Label: label, OK: true,
		Detail: fmt.Sprintf("%d 个已配置上游（当前可用: %s）", configured, joined)}
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
