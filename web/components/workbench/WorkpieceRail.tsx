import { useMemo } from 'react';
import {
  Image as ImageIcon,
  Layers as LayersIcon,
  Plus,
  RefreshCw,
  Box,
  Check,
} from 'lucide-react';
import { useWorkbench } from '../../lib/workbench-context';
import { formatWorkTime, promptExcerpt } from '../../lib/workbench';
import { APP_VERSION } from '../../lib/brand';

interface WorkpieceRailProps {
  /** 窄窗口下作品栏改成抽屉浮层，由工作台控制显隐。 */
  className?: string;
  /** 选中作品/新建后回调，用于收起抽屉。 */
  onNavigate?: () => void;
}

export default function WorkpieceRail({ className = '', onNavigate }: WorkpieceRailProps) {
  const { works, worksLoading, current, selectWork, startNew, refreshWorks } = useWorkbench();

  const sorted = useMemo(() => works, [works]);

  return (
    <aside className={`w-[248px] shrink-0 border-r border-gray-800 bg-[#14141c] flex flex-col min-h-0 ${className}`}>
      <div className="h-11 px-3 border-b border-gray-800 flex items-center justify-between">
        <p className="text-xs font-medium text-gray-400">作品</p>
        <div className="flex items-center gap-1">
          <button
            onClick={() => void refreshWorks()}
            title="刷新作品列表"
            aria-label="刷新作品列表"
            className="p-1.5 rounded-md text-gray-500 hover:text-white hover:bg-gray-800 transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => {
              startNew();
              onNavigate?.();
            }}
            title="开始一个新作品"
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] bg-pink-500/15 border border-pink-500/40 text-pink-300 hover:bg-pink-500/25 transition-colors"
          >
            <Plus className="w-3 h-3" /> 新建
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {worksLoading ? (
          <div className="space-y-1.5">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-16 rounded-lg bg-gray-800/40 animate-shimmer" />
            ))}
          </div>
        ) : sorted.length === 0 ? (
          <div className="p-4 rounded-lg border border-dashed border-gray-800 text-center">
            <ImageIcon className="w-6 h-6 text-gray-700 mx-auto mb-2" />
            <p className="text-[11px] text-gray-500 leading-relaxed">
              还没有作品。
              <br />
              写一段中文提示词，生成第一张角色立绘。
            </p>
          </div>
        ) : (
          sorted.map((work) => {
            const active = current?.id === work.id;
            return (
              <button
                key={work.id}
                onClick={() => {
                  selectWork(work.id);
                  onNavigate?.();
                }}
                className={`w-full flex items-center gap-2.5 p-2 rounded-lg border text-left transition-colors ${
                  active
                    ? 'bg-pink-500/10 border-pink-500/40'
                    : 'bg-[#1a1a23] border-gray-800 hover:border-gray-700'
                }`}
              >
                <div className="w-11 h-11 rounded-md overflow-hidden bg-gray-900 border border-gray-800 shrink-0 flex items-center justify-center">
                  {work.imageUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={work.imageUrl} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <LayersIcon className="w-4 h-4 text-gray-600" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className={`text-xs truncate ${active ? 'text-white' : 'text-gray-300'}`}>
                    {promptExcerpt(work.prompt, 16)}
                  </p>
                  <p className="text-[10px] text-gray-500 mt-0.5">{formatWorkTime(work.createdAt)}</p>
                  <div className="flex items-center gap-2 mt-1 text-[9px] text-gray-500">
                    {work.layersDir && (
                      <span className="inline-flex items-center gap-0.5">
                        <Check className="w-2.5 h-2.5 text-emerald-400" /> 分层
                      </span>
                    )}
                    {work.model3Url && (
                      <span className="inline-flex items-center gap-0.5">
                        <Box className="w-2.5 h-2.5 text-blue-400" /> 模型
                      </span>
                    )}
                  </div>
                </div>
              </button>
            );
          })
        )}
      </div>

      <div className="px-3 py-2 border-t border-gray-800 text-[10px] text-gray-600 flex items-center justify-between">
        <span>{works.length} 件作品</span>
        <span>v{APP_VERSION}</span>
      </div>
    </aside>
  );
}
