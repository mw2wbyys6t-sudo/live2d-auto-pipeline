package main

// P3-2（H2）：状态变更请求的跨站护栏（DNS rebinding 之外的另一层）。
// multipart/form-data 等"简单请求"不触发 CORS 预检，恶意网页可借浏览器
// 对 127.0.0.1 发起 POST；Host 白名单挡不住（Host 本来就是 127.0.0.1）。
// 规则：Sec-Fetch-Site: cross-site 拒绝；带 Origin 时只允许同源或 CORS
// 白名单来源；无 Origin（curl/Tauri 原生/测试）放行；GET 不拦。

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

func p3GuardRouter() *gin.Engine {
	r := gin.New()
	r.Use(crossSiteGuardMiddleware([]string{"http://localhost:3000", "http://127.0.0.1:3000"}))
	r.GET("/api/x", func(c *gin.Context) { c.String(http.StatusOK, "ok") })
	r.POST("/api/x", func(c *gin.Context) { c.String(http.StatusOK, "ok") })
	return r
}

func guardPost(t *testing.T, r *gin.Engine, host string, headers map[string]string) int {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/api/x", nil)
	if host != "" {
		req.Host = host
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w.Code
}

func TestCrossSiteGuard_BlocksCrossSiteStateChange(t *testing.T) {
	r := p3GuardRouter()
	// 浏览器在跨站简单请求时会带 Sec-Fetch-Site: cross-site（multipart 表单）
	if code := guardPost(t, r, "127.0.0.1:8080", map[string]string{
		"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example",
	}); code != http.StatusForbidden {
		t.Fatalf("跨站 POST 必须 403，实得 %d", code)
	}
	// 旧浏览器无 Fetch Metadata：伪造 Origin 同样必须拒绝
	if code := guardPost(t, r, "127.0.0.1:8080", map[string]string{
		"Origin": "http://evil.example",
	}); code != http.StatusForbidden {
		t.Fatalf("非白名单 Origin 的 POST 必须 403，实得 %d", code)
	}
	// 服务绑到局域网地址时，回环 Origin 不再算「本地前端」
	if code := guardPost(t, r, "192.168.1.20:8080", map[string]string{
		"Origin": "http://127.0.0.1:9999",
	}); code != http.StatusForbidden {
		t.Fatalf("Host 非回环时回环 Origin 必须 403，实得 %d", code)
	}
}

// 开发前端换端口（3000 被占用、多实例并行）必须照常可写：
// Chrome 对**同源 POST 也会带 Origin**，此前白名单只登记 3000，换端口即 403。
func TestCrossSiteGuard_AllowsLoopbackOriginsOnAnyPort(t *testing.T) {
	r := p3GuardRouter()
	for _, origin := range []string{
		"http://127.0.0.1:9999",
		"http://localhost:3200",
		"http://localhost:4173",
	} {
		t.Run(origin, func(t *testing.T) {
			if code := guardPost(t, r, "127.0.0.1:8080", map[string]string{
				"Sec-Fetch-Site": "same-site", "Origin": origin,
			}); code != http.StatusOK {
				t.Fatalf("回环来源 %s 应放行，实得 %d", origin, code)
			}
		})
	}
}

func TestCrossSiteGuard_AllowsLegitimateStateChange(t *testing.T) {
	r := p3GuardRouter()
	cases := []struct {
		name    string
		host    string
		headers map[string]string
	}{
		{"同源 Go 托管前端 127", "127.0.0.1:8080", map[string]string{
			"Sec-Fetch-Site": "same-origin", "Origin": "http://127.0.0.1:8080"}},
		{"同源 Tauri localhost", "localhost:8080", map[string]string{
			"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost:8080"}},
		{"dev 前端白名单 3000", "127.0.0.1:8080", map[string]string{
			"Sec-Fetch-Site": "same-site", "Origin": "http://127.0.0.1:3000"}},
		{"无 Origin 的非浏览器客户端", "127.0.0.1:8080", nil},
		{"地址栏/书签导航 none", "127.0.0.1:8080", map[string]string{"Sec-Fetch-Site": "none"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if code := guardPost(t, r, tc.host, tc.headers); code != http.StatusOK {
				t.Fatalf("合法 POST 应放行，实得 %d", code)
			}
		})
	}
}

func TestCrossSiteGuard_GetAndOptionsNeverBlocked(t *testing.T) {
	r := p3GuardRouter()
	req := httptest.NewRequest(http.MethodGet, "/api/x", nil)
	req.Host = "127.0.0.1:8080"
	req.Header.Set("Sec-Fetch-Site", "cross-site")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("GET 不应受跨站护栏限制，实得 %d", w.Code)
	}
}
