package services

import (
	"testing"

	"live2d-api/config"
)

// TestCapabilityManager_RejectsUnknownGroup 验证下载白名单：非白名单组名（含命令注入
// 尝试）必须被拒绝，不能把任意 pip spec 透传给后台安装进程。
func TestCapabilityManager_RejectsUnknownGroup(t *testing.T) {
	cm := NewCapabilityManager(config.DefaultConfig())

	cases := []string{
		"torch; rm -rf /",
		"",
		"shell_injection",
		"sam2_gd --upgrade-all",
		"../evil",
	}
	for _, g := range cases {
		if _, err := cm.Download(g); err == nil {
			t.Errorf("白名单应拒绝组 %q，但没有返回错误", g)
		}
	}
}

// TestCapabilityManager_AcceptsWhitelistedGroup 验证白名单组能进入 running 状态
// （不等待后台 pip 真正完成，只断言任务被接受且状态为 running）。
func TestCapabilityManager_AcceptsWhitelistedGroup(t *testing.T) {
	cm := NewCapabilityManager(config.DefaultConfig())
	job, err := cm.Download("tts")
	if err != nil {
		t.Fatalf("白名单组 tts 应被接受: %v", err)
	}
	if job.Status != "running" {
		t.Errorf("新任务状态应为 running，got %s", job.Status)
	}
	// 清理后台 goroutine，避免测试进程等待
	job.Status = "failed"
}
