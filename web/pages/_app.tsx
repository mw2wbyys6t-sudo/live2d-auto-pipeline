import type { AppProps } from 'next/app';
import { useEffect } from 'react';
import { useRouter } from 'next/router';
import Layout from '../components/Layout';
import ErrorBoundary from '../components/ErrorBoundary';
import '../styles/globals.css';

const PAGE_TITLES: Record<string, string> = {
  '/': 'Dashboard',
  '/characters': 'Characters',
  '/generate': 'AI Generation',
  '/layers': 'Layer Workstation',
  '/live2d': 'Live2D Builder',
  '/preview': 'Live Preview',
  '/chat': 'AI Chat',
  '/export': 'Export Center',
};

function resolveTitle(pathname: string): string {
  if (pathname.startsWith('/characters/')) return 'Character Editor';
  return PAGE_TITLES[pathname] || 'Live2D Master';
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

  return (
    <Layout title={title}>
      <ErrorBoundary>
        <Component {...pageProps} />
      </ErrorBoundary>
    </Layout>
  );
}
