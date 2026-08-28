package services

import (
	"testing"
	"time"

	"live2d-api/config"
)

// TestNewRequestCache 测试缓存创建
func TestNewRequestCache(t *testing.T) {
	cfg := config.CacheConfig{
		MaxEntries: 100,
		MaxSizeMB:  10,
		TTLSeconds: 60,
	}
	cache := NewRequestCache(cfg)
	if cache == nil {
		t.Fatal("NewRequestCache 返回 nil")
	}
	if cache.maxEntries != 100 {
		t.Errorf("maxEntries 期望 100，实际 %d", cache.maxEntries)
	}
	if cache.maxSizeMB != 10 {
		t.Errorf("maxSizeMB 期望 10，实际 %d", cache.maxSizeMB)
	}
	if cache.ttl != 60*time.Second {
		t.Errorf("ttl 期望 60s，实际 %v", cache.ttl)
	}
}

// TestCacheKeyGeneration 测试缓存键生成的一致性
func TestCacheKeyGeneration(t *testing.T) {
	cfg := config.CacheConfig{MaxEntries: 10, MaxSizeMB: 1, TTLSeconds: 60}
	cache := NewRequestCache(cfg)

	k1 := cache.generateKey("test prompt", 512, 512, 42, "model-1")
	k2 := cache.generateKey("test prompt", 512, 512, 42, "model-1")
	if k1 != k2 {
		t.Errorf("相同输入应生成相同 key: %s vs %s", k1, k2)
	}

	// 不同输入应生成不同 key
	k3 := cache.generateKey("other prompt", 512, 512, 42, "model-1")
	if k1 == k3 {
		t.Error("不同 prompt 应生成不同 key")
	}
}

// TestCacheKeyDistinguishesFields 测试每个字段都能区分 cache key
func TestCacheKeyDistinguishesFields(t *testing.T) {
	cfg := config.CacheConfig{MaxEntries: 10, MaxSizeMB: 1, TTLSeconds: 60}
	cache := NewRequestCache(cfg)

	// 基准: prompt="p", w=512, h=512, seed=42, model="m"
	baseKey := cache.generateKey("p", 512, 512, 42, "m")

	// 修改 prompt
	if cache.generateKey("pX", 512, 512, 42, "m") == baseKey {
		t.Error("不同 prompt 应产生不同 key")
	}
	// 修改 width
	if cache.generateKey("p", 256, 512, 42, "m") == baseKey {
		t.Error("不同 width 应产生不同 key")
	}
	// 修改 height
	if cache.generateKey("p", 512, 256, 42, "m") == baseKey {
		t.Error("不同 height 应产生不同 key")
	}
	// 修改 seed
	if cache.generateKey("p", 512, 512, 0, "m") == baseKey {
		t.Error("不同 seed 应产生不同 key")
	}
	// 修改 model
	if cache.generateKey("p", 512, 512, 42, "mX") == baseKey {
		t.Error("不同 model 应产生不同 key")
	}
}

// TestGenerateKeyWithDifferentTypes 测试不同参数组合
func TestGenerateKeyWithDifferentTypes(t *testing.T) {
	cfg := config.CacheConfig{MaxEntries: 10, MaxSizeMB: 1, TTLSeconds: 60}
	cache := NewRequestCache(cfg)

	// 空字符串与不同字符串
	k1 := cache.generateKey("", 0, 0, 0, "")
	k2 := cache.generateKey("a", 0, 0, 0, "")
	if k1 == k2 {
		t.Error("空 prompt 和非空 prompt 应产生不同 key")
	}

	// 不同 size
	k3 := cache.generateKey("p", 256, 256, 0, "")
	k4 := cache.generateKey("p", 512, 512, 0, "")
	if k3 == k4 {
		t.Error("不同尺寸应产生不同 key")
	}
}
