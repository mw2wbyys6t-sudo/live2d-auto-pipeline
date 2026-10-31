package main

// P4-A：桌面端面捕（MediaPipe FaceLandmarker WASM）所需的安全头回归测试。
//
// 问题回顾：此前 CSP 为 default-src 'self'，阻止了：
//   - WASM 运行时（需要 unsafe-eval）；
//   - CDN 回退（jsdelivr / googleapis，当本地 vendor 缺失时面捕不应全功能失效）；
//   - blob:/data: 图片与摄像头流（getUserMedia + createObjectURL 产物）。
// Permissions-Policy camera=() 则直接禁用了摄像头访问。
//
// 本测试验证修复后的安全头满足面捕最低需求，同时不放松 frame-ancestors 等
// 防嵌入约束。

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestSecurityHeadersForFaceCapture(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(securityHeadersMiddleware())
	r.GET("/probe", func(c *gin.Context) { c.String(http.StatusOK, "ok") })

	req := httptest.NewRequest(http.MethodGet, "/probe", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	csp := w.Header().Get("Content-Security-Policy")

	checks := []struct {
		name, header, want string
	}{
		{"WASM 运行时需要 unsafe-eval", csp, "'unsafe-eval'"},
		{"MediaPipe WASM CDN 回退", csp, "cdn.jsdelivr.net"},
		{"模型 CDN 回退", csp, "storage.googleapis.com"},
		{"摄像头流需要 media-src blob:", csp, "blob:"},
		{"图片预览需要 img-src blob:/data:", csp, "data:"},
	}
	for _, c := range checks {
		if !strings.Contains(c.header, c.want) {
			t.Errorf("%s: CSP 缺少 %q\n  实际 CSP: %s", c.name, c.want, csp)
		}
	}

	pp := w.Header().Get("Permissions-Policy")
	if !strings.Contains(pp, "camera=(self)") {
		t.Errorf("Permissions-Policy 必须允许 camera=(self)（面捕需要）\n  实际: %s", pp)
	}
	if strings.Contains(pp, "camera=()") {
		t.Errorf("Permissions-Policy 不得完全禁用 camera\n  实际: %s", pp)
	}

	if strings.Contains(csp, "frame-ancestors 'self'") {
		t.Errorf("frame-ancestors 不得放宽为 'self'（防嵌入必须保持 none）\n  CSP: %s", csp)
	}
	if got := w.Header().Get("X-Content-Type-Options"); got != "nosniff" {
		t.Errorf("X-Content-Type-Options 应为 nosniff，实得 %q", got)
	}
	if got := w.Header().Get("X-Frame-Options"); got != "DENY" {
		t.Errorf("X-Frame-Options 应为 DENY，实得 %q", got)
	}
}

// cspSources 按 CSP 的回退语义取某个指令的来源列表。
//
// 必须按指令取，不能对整条头做 strings.Contains：上面那条
// 「media-src blob:」检查用的就是 "blob:" 子串，而 media-src 里本来就有
// blob: —— 于是渲染器 worker 被 CSP 掐死时它照样绿。这就是 P0 漏检的机制。
func cspSources(csp, directive string) []string {
	for _, part := range strings.Split(csp, ";") {
		fields := strings.Fields(part)
		if len(fields) > 0 && fields[0] == directive {
			return fields[1:]
		}
	}
	return nil
}

// workerAllowsBlob 复刻浏览器的真实判定：worker-src 未设时回退 script-src。
func workerAllowsBlob(csp string) bool {
	sources := cspSources(csp, "worker-src")
	if sources == nil {
		sources = cspSources(csp, "script-src")
	}
	for _, s := range sources {
		if s == "blob:" {
			return true
		}
	}
	return false
}

// P0：桌面版内嵌 UI 的 Live2D 预览一张图都渲不出来。
//
// 症状（实测 2026-10-30，安装包与仓库构建一致）：预览阶段 0 FPS、三角面 0、
// 「网格几何 缺失」，model3.json 已 200 拿到但之后再没有 .moc3 请求；
// 控制台报 Creating a worker from 'blob:...' violates script-src，
// 并明确提示 worker-src 未设所以按 script-src 回退。
// 换用另一个模型做对照，读数相同 —— 不是模型坏，是 Pixi 的 blob worker
// 在 Go 内嵌 UI 下必然被拦（dev/standalone 走 Next，不发这个头，所以测不出来）。
func TestSecurityHeadersAllowRendererWorker(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(securityHeadersMiddleware())
	r.GET("/probe", func(c *gin.Context) { c.String(http.StatusOK, "ok") })

	req := httptest.NewRequest(http.MethodGet, "/probe", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	csp := w.Header().Get("Content-Security-Policy")

	if !workerAllowsBlob(csp) {
		t.Errorf("渲染器需要 blob worker：worker-src（或回退的 script-src）必须含 blob:\n  实际 CSP: %s", csp)
	}

	// 只接受"单独放宽 worker-src"这一种改法。把 blob: 挪进 script-src 也能让
	// 上面的回退判定变绿，但那等于允许任意 blob 脚本当页面脚本执行 —— 那是用
	// 安全换功能，必须判红。
	if len(cspSources(csp, "worker-src")) == 0 {
		t.Errorf("必须显式声明 worker-src，不能靠 script-src 回退放行 blob worker\n  实际 CSP: %s", csp)
	}
	for _, source := range cspSources(csp, "script-src") {
		if source == "blob:" || source == "data:" {
			t.Errorf("script-src 不得含 %q（页面脚本来源不因渲染器而放宽）\n  CSP: %s", source, csp)
		}
	}

	// 放宽 worker 不能顺带放宽脚本来源：'unsafe-eval' 之外不该出现通配来源。
	for _, directive := range []string{"script-src", "worker-src"} {
		for _, source := range cspSources(csp, directive) {
			if strings.HasPrefix(source, "http:") || source == "*" || source == "https:" {
				t.Errorf("%s 不得引入通配/明文来源，实得 %q\n  CSP: %s", directive, source, csp)
			}
		}
	}

	// 防嵌入约束必须与 worker 放宽无关地保持原样。
	if got := cspSources(csp, "frame-ancestors"); len(got) != 1 || got[0] != "'none'" {
		t.Errorf("frame-ancestors 必须保持 'none'，实得 %v", got)
	}
}
