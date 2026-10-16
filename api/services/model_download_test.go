package services

import (
	"testing"
)

// 清单只读、未知组件报错、空缓存目录拒绝启动、取消幂等。
func TestModelDownloaderListAndGuards(t *testing.T) {
	m := NewModelDownloader("") // 空 HfCache：未配置可写目录
	list := m.List()
	if len(list) == 0 {
		t.Fatal("可选模型清单不应为空")
	}
	first := list[0]
	if first["name"] == "" || first["state"] == "" {
		t.Fatal("清单条目必须含 name/state")
	}

	if err := m.Start("nonexistent-model"); err == nil {
		t.Fatal("未知模型 Start 应报错")
	}
	if err := m.Start(first["name"].(string)); err == nil {
		t.Fatal("空 HfCache 时 Start 应报错（无可写目录）")
	}
	// 取消不存在的任务不报错（幂等）。
	m.Cancel("nonexistent-model")

	st := m.Status(first["name"].(string))
	if st.State != "idle" {
		t.Errorf("未下载状态应为 idle，实得 %q", st.State)
	}
}
