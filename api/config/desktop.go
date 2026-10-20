package config

import (
	"os"
	"path/filepath"
	"runtime"
)

// resolveDesktopRoot 在候选目录中定位资源根（含 app/core/workflow.py 标记）。
// 抽成纯函数便于单测；exeDir 为 Go 可执行文件所在目录。
func resolveDesktopRoot(exeDir string) string {
	candidates := []string{exeDir}
	if parent := filepath.Dir(exeDir); parent != exeDir {
		candidates = append(candidates, filepath.Join(parent, "resources"), parent)
	}
	for _, c := range candidates {
		if fileExists(filepath.Join(c, "app", "core", "workflow.py")) {
			return c
		}
	}
	return ""
}

// ApplyDesktopLayout 在「打包桌面布局」下把解释器、工程源码、用户数据目录
// 全部解析到正确位置。
//
// 布局标记：Go 可执行文件同目录（Tauri resource_dir）下存在 app/core/workflow.py。
// 开发模式（仓库源码树）不触发，沿用 DefaultConfig 的 cwd/项目根发现。
//
// 目录约定（见 ADR-010）：
//   程序本体只读：<res>/app（源码）、<res>/python（embeddable）、<res>/models、
//                 <res>/onnxruntime.dll、<res>/face_tracker.exe
//   用户数据可写：%APPDATA%\Live2DMasterAgent（config/logs/hf-cache/characters）
//                 文档\Live2DMasterAgent\output（产物）
//
// configProvided=true 表示命令行 -config 指定了文件：用户目录（output/characters）
// 以文件为准，本函数只补 python/scripts/hf/面捕等程序本体字段。
//
// LIVE2D_DATA_DIR 整体覆盖 APPDATA 根（测试/便携），LIVE2D_DOCS_DIR 覆盖文档根。
func ApplyDesktopLayout(cfg *Config, configProvided bool) {
	exePath, err := os.Executable()
	if err != nil {
		return
	}
	resDir := resolveDesktopRoot(filepath.Dir(exePath))
	if resDir == "" {
		return
	}
	applyDesktopRoot(cfg, resDir, configProvided)
}

// applyDesktopRoot 把给定资源根映射进 cfg；纯函数式主体，便于单测。
func applyDesktopRoot(cfg *Config, resDir string, configProvided bool) {

	// ---- 程序本体（只读）----
	pythonExe := "pythonw.exe"
	if runtime.GOOS != "windows" {
		pythonExe = "python3"
	}
	pythonPath := filepath.Join(resDir, "python", pythonExe)
	if !fileExists(pythonPath) {
		// 退回 python.exe（embeddable 两种都在时优先无窗 pythonw）
		alt := "python.exe"
		if runtime.GOOS != "windows" {
			alt = "python3"
		}
		if p := filepath.Join(resDir, "python", alt); fileExists(p) {
			pythonPath = p
		}
	}
	cfg.Python.PythonPath = pythonPath
	cfg.Python.ScriptsDir = filepath.Join(resDir, "app")

	// ---- 用户数据（可写）----
	dataRoot := envOr("LIVE2D_DATA_DIR", appDataRoot())
	docsRoot := envOr("LIVE2D_DOCS_DIR", documentsRoot())

	cfg.Python.HfCache = filepath.Join(dataRoot, "hf-cache")
	// 内置模型权重目录（只读，程序本体）：<res>/models。
	// 注入后 Python 侧的 isnet_onnx 会优先从这里找 ISNet 前景权重，
	// 不再依赖 rembg 的隐式联网下载（离线/内网必然失败 → 静默降级）。
	cfg.Python.ModelsDir = filepath.Join(resDir, "models")
	if !configProvided {
		// 无 -config 文件时由本函数决定用户目录；文件已显式给出则尊重之。
		cfg.Output.BaseDir = filepath.Join(docsRoot, "output")
		cfg.Character.StorageDir = filepath.Join(dataRoot, "characters")
	}

	// 面捕二进制与模型目录：exe 同目录即 resource_dir；未显式配置时留空，
	// 由 NativeBridge.FindBinary 的 exe 同目录兜底命中。
	if cfg.NativeTracker.BinaryPath == "" {
		name := "face_tracker"
		if runtime.GOOS == "windows" {
			name = "face_tracker.exe"
		}
		if p := filepath.Join(resDir, name); fileExists(p) {
			cfg.NativeTracker.BinaryPath = p
		}
	}

	// 确保可写目录存在（失败不阻断启动，交由使用时报错）。
	_ = os.MkdirAll(cfg.Output.BaseDir, 0755)
	_ = os.MkdirAll(cfg.Character.StorageDir, 0755)
	_ = os.MkdirAll(cfg.Python.HfCache, 0755)
	_ = os.MkdirAll(filepath.Join(dataRoot, "logs"), 0755)
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// appDataRoot 返回用户可写配置根：Windows %APPDATA%\Live2DMasterAgent；
// 其他平台 ~/.live2d-master-agent（开发/测试）。
func appDataRoot() string {
	if runtime.GOOS == "windows" {
		if appdata := os.Getenv("APPDATA"); appdata != "" {
			return filepath.Join(appdata, "Live2DMasterAgent")
		}
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return filepath.Join(os.TempDir(), "Live2DMasterAgent")
	}
	return filepath.Join(home, ".live2d-master-agent")
}

// documentsRoot 返回「文档」目录根：Windows %USERPROFILE%\Documents（非 ASCII
// 用户名也由系统展开）；其他平台 ~/Documents。
func documentsRoot() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return filepath.Join(os.TempDir(), "Live2DMasterAgent")
	}
	if runtime.GOOS == "windows" {
		if docs := os.Getenv("USERPROFILE"); docs != "" {
			return filepath.Join(docs, "Documents", "Live2DMasterAgent")
		}
	}
	return filepath.Join(home, "Documents", "Live2DMasterAgent")
}
