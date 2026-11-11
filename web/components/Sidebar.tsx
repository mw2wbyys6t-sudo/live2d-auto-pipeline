import Link from 'next/link';
import { useRouter } from 'next/router';
import { APP_NAME, APP_VERSION } from '../lib/brand';
import { Boxes, LayoutDashboard } from 'lucide-react';

interface NavItem {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  match?: (pathname: string) => boolean;
}

// 目前只有这两条真实链路可走；角色库 / AI 对话 / 环境自检先撤掉，等做完再放回来。
const NAV_ITEMS: NavItem[] = [
  { href: '/studio', label: '工作台', icon: LayoutDashboard, match: (p) => p.startsWith('/studio') },
  { href: '/models', label: '模型库', icon: Boxes, match: (p) => p.startsWith('/models') },
];

interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

export default function Sidebar({ open, onClose }: SidebarProps) {
  const router = useRouter();

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-[#241634]/25 backdrop-blur-sm lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={`fixed left-0 top-0 z-40 h-full w-64 transform border-r border-black/[0.07] bg-ink-850/95 transition-transform duration-300 lg:static lg:z-auto lg:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex h-full flex-col">
          <div className="border-b border-black/[0.07] p-5">
            <Link href="/" className="group flex items-center gap-3">
              {/* 与工作台顶栏同一枚应用图标，避免两套视觉符号 */}
              <img
                src="/app-icon.png"
                alt={`${APP_NAME} 图标`}
                width={40}
                height={40}
                className="h-10 w-10 shrink-0 rounded-[11px] bg-white object-contain p-[2px] ring-1 ring-black/10 shadow-md shadow-black/10 transition-transform group-hover:scale-105"
              />
              <div>
                <h1 className="text-sm font-semibold leading-tight tracking-wide text-haze-100">{APP_NAME}</h1>
                <p className="text-[10px] tracking-wider text-haze-500">中文角色工作台</p>
              </div>
            </Link>
          </div>

          <nav className="flex-1 space-y-1 overflow-y-auto p-3">
            {NAV_ITEMS.map((item) => {
              const active = item.match ? item.match(router.pathname) : router.pathname === item.href;
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onClose}
                  className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors ${
                    active
                      ? 'ring-gradient bg-sakura-500/[0.08] text-sakura-600'
                      : 'text-haze-400 hover:bg-black/[0.04] hover:text-haze-100'
                  }`}
                >
                  <Icon className={`h-4 w-4 ${active ? 'text-sakura-600' : 'text-haze-500'}`} />
                  <span>{item.label}</span>
                  {active && (
                    <span className="ml-auto h-1.5 w-1.5 rounded-full bg-gradient-to-br from-sakura-400 to-iris-500" />
                  )}
                </Link>
              );
            })}
          </nav>

          <div className="border-t border-black/[0.07] p-4">
            <p className="text-center text-[10px] text-haze-600">v{APP_VERSION} · Made with ♥</p>
          </div>
        </div>
      </aside>
    </>
  );
}
