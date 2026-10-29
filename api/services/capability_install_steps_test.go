package services

import (
	"strings"
	"testing"
)

// Windows 基础包带的是 onnxruntime-directml；`rembg[cpu]` 钉着 CPU 版 onnxruntime，
// 两个发行名共用同一个 onnxruntime/ 目录 —— 装完 rembg 会把 DirectML 静默顶掉，
// 用户失去 GPU 加速却看不到任何报错。按需安装必须把这一步补回来。

func contains(haystack []string, needle string) bool {
	for _, s := range haystack {
		if strings.Contains(s, needle) {
			return true
		}
	}
	return false
}

func TestInstallSteps_RembgRestoresDirectMLRuntimeOnWindows(t *testing.T) {
	steps := installSteps("rembg", "windows")
	if len(steps) != 2 {
		t.Fatalf("Windows 上装 rembg 需要两步（装 rembg + 恢复 GPU 运行时），实际 %d 步: %v", len(steps), steps)
	}
	if !contains(steps[0], "rembg[cpu]") {
		t.Errorf("第一步应安装 rembg，实际 %v", steps[0])
	}
	second := strings.Join(steps[1], " ")
	if !strings.Contains(second, "onnxruntime-directml") ||
		!strings.Contains(second, "--force-reinstall") ||
		!strings.Contains(second, "--no-deps") {
		t.Errorf("第二步必须强制重装 DirectML 运行时且不动其它依赖，实际: %s", second)
	}
}

func TestInstallSteps_NoRuntimeRestoreOffWindows(t *testing.T) {
	// 非 Windows 基础包本来就是 CPU 版 onnxruntime，没有可恢复的 GPU 运行时。
	for _, goos := range []string{"linux", "darwin"} {
		steps := installSteps("rembg", goos)
		if len(steps) != 1 {
			t.Errorf("%s 上装 rembg 应只有一步，实际 %d 步: %v", goos, len(steps), steps)
		}
	}
}

func TestInstallSteps_OtherGroupsAreSingleStep(t *testing.T) {
	for _, group := range []string{"tts", "mediapipe", "asr", "diffusion", "sam2_gd"} {
		steps := installSteps(group, "windows")
		if len(steps) != 1 {
			t.Errorf("%s 不该有恢复步骤，实际 %v", group, steps)
		}
		if !contains(steps[0], "install") {
			t.Errorf("%s 第一步应是 pip install，实际 %v", group, steps[0])
		}
	}
}

func TestInstallSteps_UnknownGroupHasNoSteps(t *testing.T) {
	if steps := installSteps("rm -rf", "windows"); steps != nil {
		t.Errorf("白名单外的组不应产出安装步骤，实际 %v", steps)
	}
}
