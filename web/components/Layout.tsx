import { ReactNode, useState } from 'react';
import { Menu, Settings, Bell } from 'lucide-react';
import Sidebar from './Sidebar';
import ConnectionStatus from './ConnectionStatus';

interface LayoutProps {
  children: ReactNode;
  title?: string;
  actions?: ReactNode;
}

export default function Layout({ children, title, actions }: LayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="min-h-screen bg-[#0f0f13] text-white flex">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex-1 flex flex-col min-w-0">
        <header className="sticky top-0 z-20 bg-[#0f0f13]/80 backdrop-blur-xl border-b border-gray-800">
          <div className="flex items-center justify-between px-4 sm:px-6 h-14">
            <div className="flex items-center gap-3 min-w-0">
              <button
                onClick={() => setSidebarOpen(true)}
                className="lg:hidden p-2 -ml-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
                aria-label="Open navigation"
              >
                <Menu className="w-5 h-5" />
              </button>
              {title && (
                <div className="min-w-0">
                  <h2 className="text-base font-semibold text-white truncate">{title}</h2>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2 sm:gap-3">
              {actions}
              <ConnectionStatus className="hidden sm:flex" />
              <button className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors" aria-label="Notifications">
                <Bell className="w-4 h-4" />
              </button>
              <button className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors" aria-label="Settings">
                <Settings className="w-4 h-4" />
              </button>
            </div>
          </div>
        </header>

        <main className="flex-1 p-4 sm:p-6 overflow-x-hidden">{children}</main>
      </div>
    </div>
  );
}
