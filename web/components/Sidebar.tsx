import Link from 'next/link';
import { useRouter } from 'next/router';
import {
  LayoutDashboard,
  Users,
  Sparkles,
  Layers,
  Box,
  Eye,
  MessageSquare,
  Download,
} from 'lucide-react';

interface NavItem {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  match?: (pathname: string) => boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard, match: (p) => p === '/' },
  { href: '/characters', label: 'Characters', icon: Users, match: (p) => p.startsWith('/characters') },
  { href: '/generate', label: 'Generate', icon: Sparkles },
  { href: '/layers', label: 'Layers', icon: Layers },
  { href: '/live2d', label: 'Live2D Builder', icon: Box },
  { href: '/preview', label: 'Preview', icon: Eye },
  { href: '/chat', label: 'Chat', icon: MessageSquare },
  { href: '/export', label: 'Export', icon: Download },
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
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-30 lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={`fixed top-0 left-0 z-40 h-full w-64 bg-[#14141c] border-r border-gray-800 transform transition-transform duration-300 lg:translate-x-0 lg:static lg:z-auto ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex flex-col h-full">
          <div className="p-5 border-b border-gray-800">
            <Link href="/" className="flex items-center gap-3 group">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-pink-500 to-purple-600 flex items-center justify-center shadow-lg shadow-pink-500/30 group-hover:scale-105 transition-transform">
                <Sparkles className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="text-sm font-bold text-white leading-tight">Live2D Master</h1>
                <p className="text-[10px] text-gray-500 uppercase tracking-wider">Agent Workbench</p>
              </div>
            </Link>
          </div>

          <nav className="flex-1 overflow-y-auto p-3 space-y-1">
            {NAV_ITEMS.map((item) => {
              const active = item.match
                ? item.match(router.pathname)
                : router.pathname === item.href;
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onClose}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                    active
                      ? 'bg-gradient-to-r from-pink-500/20 to-purple-500/10 text-pink-300 border border-pink-500/30 shadow-sm shadow-pink-500/10'
                      : 'text-gray-400 hover:text-white hover:bg-gray-800/60 border border-transparent'
                  }`}
                >
                  <Icon className={`w-4 h-4 ${active ? 'text-pink-400' : ''}`} />
                  <span>{item.label}</span>
                  {active && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-pink-400 animate-pulse" />}
                </Link>
              );
            })}
          </nav>

          <div className="p-4 border-t border-gray-800">
            <div className="rounded-lg bg-gradient-to-br from-pink-500/10 to-purple-500/10 border border-pink-500/20 p-3">
              <p className="text-xs font-semibold text-pink-300 mb-1">Pro tip</p>
              <p className="text-[11px] text-gray-400 leading-relaxed">
                Enable character consistency to keep visual identity across generations.
              </p>
            </div>
            <p className="text-[10px] text-gray-600 text-center mt-3">v2.0.0 · Made with ♥</p>
          </div>
        </div>
      </aside>
    </>
  );
}
