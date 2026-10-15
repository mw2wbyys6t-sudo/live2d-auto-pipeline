package services

// P3-M4：RequestCache 的并发安全与容量记账。
//  - Get 在 RLock 下自增计数器曾被 -race 实测为 DATA RACE；
//  - Set 覆盖同 key 与 PurgeExpired 淘汰时必须回收 currentSizeMB，
//    否则记账单调漂移，容量上限提前失效，新结果静默写不进缓存。

import (
	"os"
	"path/filepath"
	"sync"
	"testing"

	"live2d-api/config"
	"live2d-api/models"
)

func p3Cache(t *testing.T, maxMB, ttlSec int) (*RequestCache, string) {
	t.Helper()
	dir := t.TempDir()
	img := filepath.Join(dir, "a.png")
	// 2MB 稀疏文件，estimateSizeMB 向上取整为 2
	if err := os.WriteFile(img, make([]byte, 2<<20), 0o644); err != nil {
		t.Fatal(err)
	}
	c := NewRequestCache(config.CacheConfig{
		Enabled: 1, MaxEntries: 100, MaxSizeMB: maxMB, TTLSeconds: ttlSec,
	})
	return c, img
}

func TestP3CacheConcurrentGetIsRaceFree(t *testing.T) {
	c, img := p3Cache(t, 10, 60)
	c.Set("p", 512, 512, 1, "m", &models.GenerateImageResponse{ImagePath: img})
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 200; j++ {
				_, _ = c.Get("p", 512, 512, 1, "m")
				_, _ = c.Get("missing", 1, 1, 1, "m")
			}
		}()
	}
	wg.Wait()
}

func TestP3CacheSetOverwriteReclaimsSize(t *testing.T) {
	// 5MB：修复前 3 次覆盖把记账漂移到 6MB，k2 会被错误拒存；
	// 修复后同 key 仅占 2MB，2+2=4 可容纳第二个 key
	c, img := p3Cache(t, 5, 60)
	// 同 key（参数完全一致）连续覆盖 3 次（每次 2MB）；修复前记账会累加到 6MB
	for i := 0; i < 3; i++ {
		c.Set("k", 1, 1, 7, "m", &models.GenerateImageResponse{ImagePath: img})
	}
	stats := c.Stats()
	if stats["current_size_mb"].(int) != 2 {
		t.Fatalf("覆盖同 key 后容量应保持 2MB，实得 %v", stats["current_size_mb"])
	}
	if stats["entries"].(int) != 1 {
		t.Fatalf("覆盖同 key 不应新增条目，实得 %v", stats["entries"])
	}
	// 容量未被虚占：再放一个不同 key（再 2MB）必须能写入
	c.Set("k2", 2, 2, 0, "m", &models.GenerateImageResponse{ImagePath: img})
	if c.Stats()["entries"].(int) != 2 {
		t.Fatal("容量记账漂移导致新 key 被错误拒存")
	}
}

func TestP3CachePurgeExpiredReclaimsSize(t *testing.T) {
	// TTL 取负值：条目在写入时即已过期，判定不依赖时钟前进。刻意不用 TTL=0 ——
	// Windows 上连续两次 time.Now() 常返回同一时间戳（实测约 99.997% 相同），
	// now.Sub(created) 恰为 0，PurgeExpired 的 `> ttl`（ttl=0）不成立，条目看起来
	// 「还没过期」，用例会假失败。
	c, img := p3Cache(t, 10, -1)
	c.Set("k", 1, 1, 0, "m", &models.GenerateImageResponse{ImagePath: img})
	if c.Stats()["current_size_mb"].(int) != 2 {
		t.Fatalf("准备条件：先应记账 2MB")
	}
	c.PurgeExpired()
	stats := c.Stats()
	if stats["entries"].(int) != 0 || stats["current_size_mb"].(int) != 0 {
		t.Fatalf("过期清理后条目与容量都应归零，实得 entries=%v size=%v",
			stats["entries"], stats["current_size_mb"])
	}
}
