import { ReactNode, useState } from 'react';
import Link from 'next/link';
import Head from 'next/head';
import { Menu, Activity } from 'lucide-react';
import Sidebar from './Sidebar';
import ConnectionStatus from './ConnectionStatus';
import { APP_NAME } from '../lib/brand';

interface LayoutProps {
  children: ReactNode;
  title?: string;
  actions?: ReactNode;
}

export default function Layout({ children, title, actions }: LayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="app-bg flex min-h-screen font-sans text-haze-300">
      {/* title 此前只渲染成页内 <h2>，不碰文档标题 —— 实测装机版除工作台外
          的每一页（环境自检 / 角色库 / AI 对话）浏览器标签页都是空的。
          只有 Workbench 自己用 next/head 设过，所以这个缺口一直没被发现。 */}
      {title && (
        <Head>
          <title>{`${APP_NAME} · ${title}`}</title>
        </Head>
      )}
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-black/[0.07] bg-ink-900/70 backdrop-blur-xl">
          <div className="flex h-14 items-center justify-between px-4 sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <button
                onClick={() => setSidebarOpen(true)}
                className="btn-icon -ml-2 lg:hidden"
                aria-label="Open navigation"
              >
                <Menu className="h-5 w-5" />
              </button>
              {title && (
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold tracking-wide text-haze-100">{title}</h2>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2 sm:gap-3">
              {actions}
              <ConnectionStatus className="hidden sm:flex" />
              <Link href="/health" className="btn-icon" aria-label="环境自检" title="环境自检">
                <Activity className="h-4 w-4" />
              </Link>
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-x-hidden p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}
