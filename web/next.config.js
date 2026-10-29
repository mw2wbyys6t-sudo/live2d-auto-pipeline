/** @type {import('next').NextConfig} */
// 桌面版构建：NEXT_STATIC_EXPORT=1 时输出纯静态站点（web/out），
// 由 Go 单文件二进制内嵌服务（UI 与 API 同源，无需 rewrites 代理）。
// 日常开发保持 standalone + rewrites 代理到独立后端，互不影响。
const isStaticExport = process.env.NEXT_STATIC_EXPORT === '1';
// 界面显示的版本取自 web/package.json（由版本单真源测试钉在 core/version.py 上），
// 构建期注入。以前三处组件各写死过 v0.10.x，升版本时页脚会静默停在旧版本。
const appVersion = require('./package.json').version;

const nextConfig = {
  reactStrictMode: true,
  env: { NEXT_PUBLIC_APP_VERSION: appVersion },
  // On slow/networked filesystems (e.g. mounted /mnt), Next's dev server can
  // fail writing its generated types. Allow pointing the build output at a
  // fast local disk via NEXT_DIST_DIR. Defaults to the standard ".next".
  distDir: process.env.NEXT_DIST_DIR || '.next',
  images: {
    unoptimized: true,
  },
  trailingSlash: false,
  output: isStaticExport ? 'export' : 'standalone',
  // 桌面版：资源前缀带构建时间戳（/_next-<ts>/...），每次构建后所有
  // chunk/资源 URL 全部变化 —— 彻底杜绝「浏览器缓存了上一版本的 HTML
  // 壳/chunk」导致的升级后页面错乱。Go 侧 NoRoute 会把 /_next-<ts>/
  // 映射回内嵌的 _next/ 目录。
  ...(isStaticExport ? { assetPrefix: `/_next-${Date.now()}` } : {}),
  // Allow preview environments and local development
  allowedDevOrigins: [
    '127.0.0.1',
    'localhost',
    'run-agent-6a708b26e262f904bb42bec5-msd7q5pk-preview.agent-sandbox-bj-d1-gw.traecontent.cn',
  ],
  // 开发期 rewrite 代理默认 30s 超时：一次完整生成常要 40s+，代理会先掐断
  // 连接（后端日志仍记 200），浏览器只收到 500「生成请求失败」。桌面形态由
  // Go 同源服务、不经此代理，所以这只影响 `npm run dev`。
  experimental: {
    proxyTimeout: 10 * 60_000,
  },
  ...(isStaticExport
    ? {}
    : {
        async rewrites() {
          const backend = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8080';
          return [
            {
              source: '/api/:path*',
              destination: `${backend}/api/:path*`,
            },
            {
              source: '/output/:path*',
              destination: `${backend}/output/:path*`,
            },
            {
              source: '/ws',
              destination: `${backend}/ws`,
            },
            {
              source: '/ws/progress',
              destination: `${backend}/ws/progress`,
            },
            {
              // 其余 WS 通道（如 Python 后端的 /ws/tracking）同样走代理
              source: '/ws/:path*',
              destination: `${backend}/ws/:path*`,
            },
          ];
        },
      }),
};

module.exports = nextConfig;
