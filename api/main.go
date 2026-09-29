package main

import (
	"flag"
	"fmt"
	"io/fs"
	"log"
	"net/http"
	"os"
	"path"
	"runtime"
	"strings"
	"time"

	"github.com/gin-contrib/gzip"
	"github.com/gin-gonic/gin"

	"live2d-api/config"
	"live2d-api/handlers"
	"live2d-api/models"
	"live2d-api/services"
	"live2d-api/webui"
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
			chars.POST("/:id/references", h.AddCharacterReference)
			chars.POST("/:id/embedding", h.RecomputeCharacterEmbedding)
		}

		// LLM 聊天 v0.10
		api.POST("/chat", h.Chat)
		api.POST("/chat/stream", h.ChatStream)

		// WebSocket v0.10
		api.GET("/ws", h.WSHandle)

		// 摄像头面捕：Go 后端不做视觉计算（预览页改用浏览器端 MediaPipe WASM），
		// 保留显式 501 而不是 404，让调用方明确知道该走哪条路。
		api.POST("/tracking/start", h.TrackingUnavailable)
		api.POST("/tracking/stop", h.TrackingUnavailable)

		// 跨页面流水线接力 v0.10.1：最近生成 / 真实分层 / 图片上传
		api.GET("/generations/latest", h.LatestGeneration)
		api.GET("/generations/models", h.ListExportedModels)
		api.POST("/segment", h.SegmentImage)
		api.POST("/upload", h.UploadImage)

		// 生成上游状态 + 外部素材导入 v0.10.1
		api.GET("/providers", h.ListImageProviders)
		api.POST("/import/psd", h.ImportPSD)
		api.POST("/import/pngs", h.ImportPNGs)

		// 工具
		api.GET("/scripts", h.GetPythonScripts)
		api.GET("/cache/stats", h.GetCacheStats)
		api.POST("/cache/clear", h.ClearCache)
	}

	// 静态文件服务（带缓存，支持子目录：/output/layers_123/hair.png）
	r.GET("/output/*filepath", h.ServeOutput)

	// 桌面版：内嵌工作台静态站点（go:embed），UI 与 API 同源。
	// 未打包 UI 时退回原有行为（根路径返回 API 信息）。
	if dist := webui.Dist(); dist != nil {
		fileServer := http.FileServer(http.FS(dist))
		// 静态导出的路由约定：/preview → preview.html（无 trailingSlash）
		serveHTML := func(c *gin.Context, name string) bool {
			f, err := dist.Open(name)
			if err != nil {
				return false
			}
			data, readErr := fs.ReadFile(dist, name)
			f.Close()
			if readErr != nil {
				return false
			}
			// HTML 壳必须 no-cache：SPA 路由的 HTML 会随后端升级被替换，
			// 让浏览器缓存旧壳会导致「页面内容与路由不符」的幽灵状态
			c.Header("Cache-Control", "no-cache")
			c.Data(http.StatusOK, "text/html; charset=utf-8", data)
			return true
		}
		// 首次访问根路径时清空本站 HTTP 缓存： healed 老版本升级后残留的
		// 「路由→旧壳」缓存条目（旧壳无验证器，浏览器可能长期复用）。
		// 只在根响应上发一次，不影响 _next 资源的 immutable 缓存策略。
		rootHeal := func(c *gin.Context) {
			c.Header("Clear-Site-Data", `"cache"`)
			serveHTML(c, "index.html")
		}
		r.NoRoute(func(c *gin.Context) {
			p := c.Request.URL.Path
			// API / 输出 / WS 走 JSON 404，不吞进 SPA
			if strings.HasPrefix(p, "/api/") || strings.HasPrefix(p, "/output/") || strings.HasPrefix(p, "/ws") {
				c.JSON(http.StatusNotFound, models.Response{Success: false, Error: "接口不存在"})
				return
			}
			// 桌面版版本化资源前缀：/_next-<buildTs>/... → 内嵌 _next/...。
			// 每次构建 ts 变化，浏览器端旧资源 URL 全部失效，杜绝缓存错版。
			// 兼容两种形态：/_next-<ts>/static/... 与
			// /_next-<ts>/_next/static/...（Turbopack 运行时会自带一层 _next 前缀）。
			if strings.HasPrefix(p, "/_next-") {
				rest := p[len("/_next-"):]
				if idx := strings.Index(rest, "/"); idx >= 0 {
					rest = rest[idx:]
					rest = strings.TrimPrefix(rest, "/_next")
					p = "/_next" + rest
				}
			}
			clean := strings.Trim(p, "/")
			// 1) 命中真实静态文件（含 _next/assets 子路径）；带内容哈希的
			//    _next 资源可永久缓存，其余静态文件用 no-cache 保证及时更新
			if clean != "" {
				if f, err := dist.Open(clean); err == nil {
					stat, statErr := f.Stat()
					f.Close()
					if statErr == nil && !stat.IsDir() {
						if strings.HasPrefix(clean, "_next/static/") {
							c.Header("Cache-Control", "public, max-age=31536000, immutable")
						} else {
							c.Header("Cache-Control", "no-cache")
						}
						// 版本化前缀已在上面重写进 p；fileServer 按原始 URL 查找，
						// 因此克隆请求并改写路径后再交给它。
						req := c.Request.Clone(c.Request.Context())
						req.URL.Path = p
						fileServer.ServeHTTP(c.Writer, req)
						return
					}
				}
			}
			// 2) 静态导出路由约定：<route>.html；3) 目录 index.html；
			// 4) 动态路由：<dir>/[id].html（如 /characters/<id>）；
			// 5) 仅当路径像前端路由（无扩展名）时回退根 index.html —— 带扩展名的
			//    未知文件请求一律 404，避免把任意路径都吞成 200 HTML
			if clean != "" && serveHTML(c, clean+".html") {
				return
			}
			if serveHTML(c, strings.TrimSuffix(p, "/")+"/index.html") {
				return
			}
			if dir := path.Dir(clean); dir != "." && dir != "/" && serveHTML(c, dir+"/[id].html") {
				return
			}
			if clean == "" {
				rootHeal(c)
				return
			}
			if path.Ext(clean) == "" && serveHTML(c, "index.html") {
				return
			}
			c.JSON(http.StatusNotFound, models.Response{Success: false, Error: "资源不存在"})
		})
	} else {
		// 根路径
		r.GET("/", h.GetAPIInfo)
	}
}

func printServerInfo(cfg *config.Config, addr string) {
	separator := strings.Repeat("=", 80)
	fmt.Println("\n" + separator)
	fmt.Println("║     🎨 Live2D Master Agent API v0.10.0 (Go Edition)          ║")
	fmt.Println("║     高性能优化版本 - 支持连接池、并发处理、请求缓存          ║")
	fmt.Println(separator)
	fmt.Printf("║  服务地址: http://%s\n", addr)
	if webui.Dist() != nil {
		fmt.Printf("║  💡 工作台: http://%s/  （桌面版 UI 已内嵌）\n", addr)
	}
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
		// 本机回环访问不限流：桌面形态下 UI 与服务同机，健康轮询 +
		// 用户操作很容易触顶，限流只会误伤本地功能；远程访问仍按
		// 60 req/min 限流。
		if ip := c.ClientIP(); ip == "127.0.0.1" || ip == "::1" || ip == "localhost" {
			c.Next()
			return
		}

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
