import type { AppProps } from 'next/app';
import { useEffect } from 'react';
import { useRouter } from 'next/router';
import Layout from '../components/Layout';
import ErrorBoundary from '../components/ErrorBoundary';
import { APP_NAME } from '../lib/brand';
import '../styles/globals.css';

const PAGE_TITLES: Record<string, string> = {
  '/': '工作台',
  '/characters': '角色库',
  '/generate': '生成',
  '/layers': '分层',
  '/live2d': 'Live2D 预览',
  '/preview': '预览',
  '/chat': 'AI 对话',
  '/export': '导出',
  '/health': '环境自检',
};

/** 单页工作台自带外壳，不能再套一层带侧边栏的 Layout。 */
const BARE_ROUTES = new Set(['/', '/generate', '/layers', '/live2d', '/preview', '/export']);

function resolveTitle(pathname: string): string {
  if (pathname.startsWith('/characters/')) return '角色编辑';
  return PAGE_TITLES[pathname] || APP_NAME;
}

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter();

  useEffect(() => {
    // v0.10.1 移除 Service Worker：本地桌面应用（exe 内嵌 UI，HTML 已 no-cache）
    // 不需要离线壳缓存；旧 SW 的 cache-first 策略会在升级后持续供应旧页面，
    // 造成「路由与内容不符」的幽灵状态。这里主动注销历史注册并清空缓存，
    // 已中毒的浏览器会随之自愈。
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker
        .getRegistrations()
        .then((regs) => regs.forEach((r) => r.unregister()))
        .catch(() => undefined);
      if (window.caches) {
        window.caches
          .keys()
          .then((keys) => keys.forEach((k) => window.caches.delete(k)))
          .catch(() => undefined);
      }
    }
  }, []);

  const title = resolveTitle(router.pathname);

  if (BARE_ROUTES.has(router.pathname)) {
    return (
      <ErrorBoundary>
        <Component {...pageProps} />
      </ErrorBoundary>
    );
  }

  return (
    <Layout title={title}>
      <ErrorBoundary>
        <Component {...pageProps} />
      </ErrorBoundary>
    </Layout>
  );
}
