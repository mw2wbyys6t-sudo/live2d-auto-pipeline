package services

import (
	"crypto/sha256"
	"fmt"
	"sync"
	"time"

	"live2d-api/config"
	"live2d-api/models"
)

// CacheEntry 缓存条目
type CacheEntry struct {
	Response *models.GenerateImageResponse
	Created  time.Time
	Accesses int
}

// RequestCache 请求缓存服务
type RequestCache struct {
	mu            sync.RWMutex
	cache         map[string]*CacheEntry
	maxEntries    int
	maxSizeMB     int
	currentSizeMB int
	ttl           time.Duration
	hitCount      int64
	missCount     int64
}

// NewRequestCache 创建新的缓存服务
func NewRequestCache(cfg config.CacheConfig) *RequestCache {
	return &RequestCache{
		cache:         make(map[string]*CacheEntry),
		maxEntries:    cfg.MaxEntries,
		maxSizeMB:     cfg.MaxSizeMB,
		currentSizeMB: 0,
		ttl:           time.Duration(cfg.TTLSeconds) * time.Second,
		hitCount:      0,
		missCount:     0,
	}
}

// generateKey 生成缓存键
func (c *RequestCache) generateKey(prompt string, width, height, seed int, modelID string) string {
	key := fmt.Sprintf("%s|%d|%d|%d|%s", prompt, width, height, seed, modelID)
	hash := sha256.Sum256([]byte(key))
	return fmt.Sprintf("%x", hash)[:16]
}

// Get 获取缓存
func (c *RequestCache) Get(prompt string, width, height, seed int, modelID string) (*models.GenerateImageResponse, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()

	key := c.generateKey(prompt, width, height, seed, modelID)
	entry, exists := c.cache[key]
	
	if !exists {
		c.missCount++
		return nil, false
	}

	// 检查是否过期
	if time.Since(entry.Created) > c.ttl {
		c.missCount++
		return nil, false
	}

	entry.Accesses++
	c.hitCount++
	return entry.Response, true
}

// Set 设置缓存
func (c *RequestCache) Set(prompt string, width, height, seed int, modelID string, response *models.GenerateImageResponse) {
	c.mu.Lock()
	defer c.mu.Unlock()

	key := c.generateKey(prompt, width, height, seed, modelID)
	
	// 计算条目大小（粗略估算）
	entrySizeMB := estimateSizeMB(response)

	// 如果超出限制，先清理
	for c.currentSizeMB+entrySizeMB > c.maxSizeMB && len(c.cache) > 0 {
		c.evictOldest()
	}

	// 如果仍然超出限制，不缓存
	if c.currentSizeMB+entrySizeMB > c.maxSizeMB {
		return
	}

	// 检查条目数限制
	if len(c.cache) >= c.maxEntries && len(c.cache) > 0 {
		c.evictOldest()
	}

	c.cache[key] = &CacheEntry{
		Response: response,
		Created:  time.Now(),
		Accesses: 1,
	}
	c.currentSizeMB += entrySizeMB
}

// evictOldest 淘汰最老的缓存
func (c *RequestCache) evictOldest() {
	var oldestKey string
	var oldestTime time.Time = time.Now()

	for key, entry := range c.cache {
		if entry.Created.Before(oldestTime) {
			oldestTime = entry.Created
			oldestKey = key
		}
	}

	if oldestKey != "" {
		entry := c.cache[oldestKey]
		c.currentSizeMB -= estimateSizeMB(entry.Response)
		delete(c.cache, oldestKey)
	}
}

// estimateSizeMB 估算响应大小（MB）
func estimateSizeMB(response *models.GenerateImageResponse) int {
	if response == nil {
		return 0
	}
	// 粗略估算：图片路径约100字节，其他字段约200字节
	return 0 // 实际大小由文件系统管理，这里简化处理
}

// Clear 清除所有缓存
func (c *RequestCache) Clear() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.cache = make(map[string]*CacheEntry)
	c.currentSizeMB = 0
	c.hitCount = 0
	c.missCount = 0
}

// Stats 获取缓存统计
func (c *RequestCache) Stats() map[string]interface{} {
	c.mu.RLock()
	defer c.mu.RUnlock()

	total := c.hitCount + c.missCount
	hitRate := 0.0
	if total > 0 {
		hitRate = float64(c.hitCount) / float64(total) * 100
	}

	return map[string]interface{}{
		"entries":       len(c.cache),
		"current_size_mb": c.currentSizeMB,
		"max_size_mb":   c.maxSizeMB,
		"hit_count":     c.hitCount,
		"miss_count":    c.missCount,
		"hit_rate":      fmt.Sprintf("%.2f%%", hitRate),
	}
}

// PurgeExpired 清除过期缓存
func (c *RequestCache) PurgeExpired() {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := time.Now()
	for key, entry := range c.cache {
		if now.Sub(entry.Created) > c.ttl {
			delete(c.cache, key)
		}
	}
}

// StartCleanupDaemon 启动缓存清理守护进程
func (c *RequestCache) StartCleanupDaemon(interval time.Duration) {
	ticker := time.NewTicker(interval)
	go func() {
		for range ticker.C {
			c.PurgeExpired()
		}
	}()
}
