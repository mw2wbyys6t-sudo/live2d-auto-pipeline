package main

import (
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"runtime"
	"strings"
	"time"

	"github.com/gin-contrib/gzip"
	"github.com/gin-gonic/gin"

	"live2d-api/config"
	"live2d-api/handlers"
	"live2d-api/services"
)

func main() {
	// 命令行参数
	var (
		configPath = flag.String("config", "", "配置文件路径")
		host       = flag.String("host", "", "服务器地址")
		port       = flag.Int("port", 0, "服务器端口")
	)
	flag.Parse()

	// 加载配置
	cfg, err := config.LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("加载配置失败: %v", err)
	}

	// 命令行参数覆盖配置
	if *host != "" {
		cfg.Server.Host = *host
	}
	if *port != 0 {
		cfg.Server.Port = *port
	}

	// 设置最大并发数为 CPU 核心数的 2 倍
	runtime.GOMAXPROCS(runtime.NumCPU() * 2)

	// 确保输出目录存在
	os.MkdirAll(cfg.Output.BaseDir, 0755)

	// 设置 Gin 模式
	gin.SetMode(gin.ReleaseMode)

	// 创建路由
	r := gin.Default()

	// ========== 安全中间件 ==========

	// Gzip 压缩中间件（提升响应速度）
	r.Use(gzip.Gzip(gzip.DefaultCompression))

	// 请求体大小限制中间件
	r.Use(func(c *gin.Context) {
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, cfg.Server.MaxRequestBodySize)
		c.Next()
	})

	// 请求超时中间件
	r.Use(func(c *gin.Context) {
		c.Request.Header.Set("Connection", "keep-alive")
		c.Next()
	})

	// 安全响应头中间件
	r.Use(func(c *gin.Context) {
		c.Header("X-Content-Type-Options", "nosniff")
		c.Header("X-Frame-Options", "DENY")
		c.Header("X-XSS-Protection", "1; mode=block")
		c.Header("Referrer-Policy", "strict-origin-when-cross-origin")
		c.Header("Content-Security-Policy", "default-src 'self'")
		c.Header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
		c.Next()
	})

	// 输入验证中间件 - 防止恶意请求
	r.Use(validateRequestMiddleware())

	// 速率限制中间件 - 防止API滥用
	r.Use(rateLimitMiddleware(cfg))

	// CORS 中间件
	r.Use(func(c *gin.Context) {
		origin := c.Request.Header.Get("Origin")
		if origin != "" {
			// 严格白名单：只有命中列表才回 CORS 头。空列表 = 不启用跨域，
			// 而不是"放行所有"——否则任意站点都能携带凭据调用本服务。
			if contains(cfg.Server.AllowedOrigins, origin) {
				c.Header("Access-Control-Allow-Origin", origin)
				c.Header("Vary", "Origin")
				c.Header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
				c.Header("Access-Control-Allow-Headers", "Content-Type, Authorization")
				c.Header("Access-Control-Allow-Credentials", "true")
			}
		}
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	})

	// ========== 创建服务和处理器 ==========

	// 创建图像生成服务（带缓存）
	imageService := services.NewImageGenerator(cfg)
	cacheService := services.NewRequestCache(cfg.Cache)

	// 创建处理器
	h := handlers.NewHandler(cfg, imageService, cacheService)

	// ========== 注册路由 ==========
	setupRoutes(r, h)

	// ========== 启动服务器 ==========
	addr := fmt.Sprintf("%s:%d", cfg.Server.Host, cfg.Server.Port)

	printServerInfo(cfg, addr)

	// 配置高性能 HTTP 服务器
	server := &http.Server{
		Addr:              addr,
		Handler:           r,
		ReadHeaderTimeout: cfg.Server.ReadHeaderTimeout,
		ReadTimeout:       cfg.Server.ReadTimeout,
		WriteTimeout:      cfg.Server.WriteTimeout,
		IdleTimeout:       cfg.Server.IdleTimeout,
		MaxHeaderBytes:    cfg.Server.MaxHeaderBytes,
	}

	if err := server.ListenAndServe(); err != nil {
		log.Fatalf("服务器启动失败: %v", err)
	}
}

func setupRoutes(r *gin.Engine, h *handlers.Handler) {
	// API 路由组
	api := r.Group("/api")
	{
		// 基础
		api.GET("/health", h.HealthCheck)
		api.GET("/status", h.GetSystemStatus)
		api.GET("/info", h.GetAPIInfo)
		api.GET("/models", h.GetModels)
		api.GET("/expressions", h.GetExpressions)

		// 生成 & 导出
		api.POST("/generate", h.GenerateImage)
		api.POST("/generate/character", h.GenerateCharacter) // v0.10: 角色一致性生成
		api.POST("/psd-plan", h.CreatePSDPlan)
		api.POST("/see-through", h.RunSeeThrough)
		api.POST("/export/live2d", h.ExportLive2D)
		api.POST("/export/psd", h.ExportPSD)
		api.POST("/export/spine", h.ExportSpine)
		api.POST("/deploy/desktop", h.DeployDesktop)
		api.GET("/deploy/desktop/status", h.DesktopStatus)

		// 角色管理 v0.10
		chars := api.Group("/characters")
		{
			chars.GET("", h.ListCharacters)
			chars.POST("", h.CreateCharacter)
			chars.GET("/:id", h.GetCharacter)
			chars.PUT("/:id", h.UpdateCharacter)
			chars.DELETE("/:id", h.DeleteCharacter)
		}

		// LLM 聊天 v0.10
		api.POST("/chat", h.Chat)
		api.POST("/chat/stream", h.ChatStream)

		// WebSocket v0.10
		api.GET("/ws", h.WSHandle)

		// 摄像头面捕：Go 后端未实现，显式回 501（前端 /preview 会调用这两个端点）
		api.POST("/tracking/start", h.TrackingUnavailable)
		api.POST("/tracking/stop", h.TrackingUnavailable)

		// 工具
		api.GET("/scripts", h.GetPythonScripts)
		api.GET("/cache/stats", h.GetCacheStats)
		api.POST("/cache/clear", h.ClearCache)
	}

	// 静态文件服务（带缓存）
	r.GET("/output/:filename", h.ServeOutput)

	// 根路径
	r.GET("/", h.GetAPIInfo)
}

func printServerInfo(cfg *config.Config, addr string) {
	separator := strings.Repeat("=", 80)
	fmt.Println("\n" + separator)
	fmt.Println("║     🎨 Live2D Master Agent API v0.10.0 (Go Edition)          ║")
	fmt.Println("║     高性能优化版本 - 支持连接池、并发处理、请求缓存          ║")
	fmt.Println(separator)
	fmt.Printf("║  服务地址: http://%s\n", addr)
	fmt.Printf("║  输出目录: %s\n", cfg.Output.BaseDir)
	fmt.Printf("║  Python:   %s\n", cfg.Python.PythonPath)
	fmt.Printf("║  最大并发: %d\n", runtime.NumCPU()*2)
	fmt.Printf("║  缓存大小: %dMB\n", cfg.Cache.MaxSizeMB)
	fmt.Println(separator)
	fmt.Println("║  API 端点:                                                   ║")
	fmt.Println("║    GET  /api/health      - 健康检查                         ║")
	fmt.Println("║    GET  /api/status      - 系统状态                         ║")
	fmt.Println("║    GET  /api/info        - API信息                          ║")
	fmt.Println("║    GET  /api/models      - 可用模型列表                     ║")
	fmt.Println("║    POST /api/generate    - 生成图片（支持缓存）              ║")
	fmt.Println("║    POST /api/psd-plan    - PSD分层规划                      ║")
	fmt.Println("║    POST /api/see-through - See-through工作流                ║")
	fmt.Println("║    GET  /api/scripts     - Python脚本列表                   ║")
	fmt.Println("║    GET  /api/cache/stats - 缓存统计                         ║")
	fmt.Println("║    POST /api/cache/clear - 清除缓存                         ║")
	fmt.Println("║    GET  /output/:file    - 获取输出文件                     ║")
	fmt.Println(separator)
	fmt.Println()
}

func contains(slice []string, item string) bool {
	for _, s := range slice {
		if s == item {
			return true
		}
	}
	return false
}

// ========== 安全中间件实现 ==========

// validateRequestMiddleware 输入验证中间件
func validateRequestMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		// 验证Content-Type
		if c.Request.Method == "POST" || c.Request.Method == "PUT" {
			contentType := c.ContentType()
			if contentType != "application/json" && contentType != "multipart/form-data" {
				c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{
					"error": "Content-Type必须是application/json或multipart/form-data",
				})
				return
			}
		}

		// 验证请求路径 - 防止路径遍历
		requestPath := c.Request.URL.Path
		if strings.Contains(requestPath, "..") || strings.Contains(requestPath, "//") {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{
				"error": "非法的请求路径",
			})
			return
		}

		// 验证User-Agent - 防止简单的爬虫
		userAgent := c.Request.UserAgent()
		if userAgent == "" && c.Request.Method != "OPTIONS" {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{
				"error": "缺少User-Agent头",
			})
			return
		}

		c.Next()
	}
}

// rateLimitMiddleware 速率限制中间件
func rateLimitMiddleware(cfg *config.Config) gin.HandlerFunc {
	// 使用内存存储请求计数（生产环境应使用Redis）
	type clientInfo struct {
		count     int
		resetTime time.Time
	}
	clients := make(map[string]*clientInfo)

	// 清理过期客户端的goroutine
	go func() {
		ticker := time.NewTicker(1 * time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			now := time.Now()
			for ip, info := range clients {
				if now.After(info.resetTime) {
					delete(clients, ip)
				}
			}
		}
	}()

	return func(c *gin.Context) {
		// 获取客户端IP
		clientIP := c.ClientIP()

		now := time.Now()
		info, exists := clients[clientIP]

		if !exists || now.After(info.resetTime) {
			// 新客户端或已过期，重置计数
			clients[clientIP] = &clientInfo{
				count:     1,
				resetTime: now.Add(1 * time.Minute),
			}
			c.Next()
			return
		}

		// 检查是否超过限制（每分钟60请求）
		if info.count >= 60 {
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{
				"error":       "请求过于频繁，请稍后再试",
				"retry_after": int(info.resetTime.Sub(now).Seconds()),
			})
			return
		}

		info.count++
		c.Next()
	}
}
