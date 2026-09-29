// Package webui 内嵌桌面版工作台的静态资源。
//
// 构建桌面版时由 scripts/build_desktop.bat 先执行
// `NEXT_STATIC_EXPORT=1 npm run build`，再把 web/out 复制到本目录 dist/；
// go:embed 将其打进单个 exe，UI 与 API 同源服务，无需 Node 与端口代理。
// dist 未填充（纯 API 开发构建）时 Dist() 返回 nil，服务器自动退回
// 「API 信息 + 浏览器打开源码工作台」的原有行为。
package webui

import (
	"embed"
	"io/fs"
)

//go:embed all:dist
var embeddedFS embed.FS

// Dist 返回内嵌的静态站点文件系统；未打包 UI 时返回 nil。
func Dist() fs.FS {
	sub, err := fs.Sub(embeddedFS, "dist")
	if err != nil {
		return nil
	}
	if _, err := fs.Stat(sub, "index.html"); err != nil {
		return nil
	}
	return sub
}
