package config

import (
	"testing"
	"time"
)

// TestWriteTimeoutCoversLongGeneration 守住一条硬约束：
// 生成接口是同步的，HTTP WriteTimeout 必须大于一次真实生成的耗时，
// 否则 Python 还在写产物、连接已经被服务端掐断，前端只看到网络失败。
//
// 实测（2026-11-16，sam2_gd 后端）：1024×1536 单张 266~321 秒，
// 4K 约 257 秒。此前 WriteTimeout 是 180 秒，四次里三次会失败。
func TestWriteTimeoutCoversLongGeneration(t *testing.T) {
	cfg := DefaultConfig()
	const measuredWorst = 321 * time.Second
	if cfg.Server.WriteTimeout <= measuredWorst {
		t.Fatalf("WriteTimeout=%s 小于实测最长生成耗时 %s：同步生成会在返回前被掐断",
			cfg.Server.WriteTimeout, measuredWorst)
	}
	if cfg.Server.WriteTimeout <= cfg.Server.ReadTimeout {
		t.Fatalf("WriteTimeout=%s 不应小于 ReadTimeout=%s",
			cfg.Server.WriteTimeout, cfg.Server.ReadTimeout)
	}
}
