import type { AppProps } from 'next/app';
import { useEffect } from 'react';
import { useRouter } from 'next/router';
import Layout from '../components/Layout';
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
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        // silently ignore SW registration failures
      });
    }
  }, []);

  const title = resolveTitle(router.pathname);

  return (
    <Layout title={title}>
      <Component {...pageProps} />
    </Layout>
  );
}
