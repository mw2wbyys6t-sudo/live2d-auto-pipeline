package services

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"time"
)

// Only one local GPU verification at a time; never trust a client report.
var desktopVerificationSlot = make(chan struct{}, 1)

func (pb *PythonBridge) VerifyDesktopModel(modelDir string) (map[string]interface{}, error) {
	select {
	case desktopVerificationSlot <- struct{}{}:
		defer func() { <-desktopVerificationSlot }()
	default:
		return nil, fmt.Errorf("模型验收正在运行，请稍后重试")
	}
	if err := validatePath(modelDir); err != nil {
		return nil, err
	}
	// Deployment only consumes models in the configured output root.
	root, err := filepath.EvalSymlinks(pb.cfg.Output.BaseDir)
	if err != nil {
		return nil, err
	}
	root, err = filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	target, err := filepath.EvalSymlinks(modelDir)
	if err != nil {
		return nil, err
	}
	target, err = filepath.Abs(target)
	if err != nil {
		return nil, err
	}
	rel, err := filepath.Rel(root, target)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return nil, fmt.Errorf("模型目录必须位于配置的输出目录内")
	}
	manifests, err := filepath.Glob(filepath.Join(target, "*.model3.json"))
	if err != nil || len(manifests) != 1 {
		return nil, fmt.Errorf("模型目录必须包含唯一 model3 清单")
	}
	script := filepath.Join(pb.cfg.Python.ScriptsDir, "drivers", "live2d_runtime", "verify.py")
	raw, err := pb.executePythonScript(script, []string{manifests[0]}, 90*time.Second)
	if err != nil {
		return nil, err
	}
	var report map[string]interface{}
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.HasPrefix(line, "LIVE2D_VERIFICATION=") {
			if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "LIVE2D_VERIFICATION=")), &report); err != nil {
				return nil, err
			}
		}
	}
	if report == nil || report["runtime_verified"] != true {
		return nil, fmt.Errorf("真实运行时验收未通过")
	}
	report["deployed"] = false
	report["ready_to_launch"] = true
	report["launch_argv"] = []string{pb.cfg.Python.PythonPath, "-m", "drivers.desktop_pet.native_window", manifests[0]}
	return report, nil
}
