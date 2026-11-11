import { useMemo } from 'react';
import Link from 'next/link';
import {
  Image as ImageIcon,
  Layers as LayersIcon,
  Plus,
  RefreshCw,
  Box,
  Check,
  AlertTriangle,
  Boxes,
} from 'lucide-react';
import { useWorkbench } from '../../lib/workbench-context';
import { formatWorkTime, workDisplayName } from '../../lib/workbench';
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
    <aside className={`panel-rail flex w-[260px] shrink-0 flex-col min-h-0 ${className}`}>
      <div className="flex h-11 items-center justify-between border-b border-black/[0.07] px-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium tracking-wide text-haze-200">作品库</span>
          <span className="rounded-full bg-black/[0.05] px-1.5 py-px text-[10px] tabular-nums text-haze-500">
            {works.length}
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          <Link href="/models" className="btn-icon p-1.5" title="打开模型库" aria-label="打开模型库">
            <Boxes className="h-3.5 w-3.5" />
          </Link>
          <button
            onClick={() => void refreshWorks()}
            title="刷新作品列表"
            aria-label="刷新作品列表"
            className="btn-icon p-1.5"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => {
              startNew();
              onNavigate?.();
            }}
            title="开始一个新作品"
            className="btn-soft px-2 py-1"
          >
            <Plus className="h-3 w-3" /> 新建
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-1.5 overflow-y-auto p-2">
        {worksLoading ? (
          <div className="space-y-1.5">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[68px] animate-shimmer rounded-xl bg-black/[0.04]" />
            ))}
          </div>
        ) : sorted.length === 0 ? (
          <div className="rounded-xl border border-dashed border-black/10 px-4 py-6 text-center">
            <div className="mx-auto mb-2.5 flex h-10 w-10 items-center justify-center rounded-xl border border-black/[0.08] bg-black/[0.03]">
              <ImageIcon className="h-5 w-5 text-haze-600" />
            </div>
            <p className="text-[11px] leading-relaxed text-haze-500">
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
                aria-current={active ? 'true' : undefined}
                className={`relative flex w-full items-center gap-2.5 rounded-xl p-2 text-left transition-colors ${
                  active
                    ? 'ring-gradient bg-sakura-500/[0.07] shadow-glow-sakura'
                    : 'border border-transparent bg-black/[0.02] hover:bg-black/[0.05]'
                }`}
              >
                {active && (
                  <span className="absolute left-0 top-1/2 h-7 w-[2px] -translate-y-1/2 rounded-full bg-gradient-to-b from-sakura-400 to-iris-500" />
                )}
                <div className="flex h-[60px] w-[48px] shrink-0 items-center justify-center overflow-hidden rounded-lg border border-black/[0.08] bg-ink-950">
                  {work.imageUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={work.imageUrl} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <LayersIcon className="h-4 w-4 text-haze-600" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className={`truncate text-xs ${active ? 'text-haze-100' : 'text-haze-200'}`}>
                    {workDisplayName(work, 16)}
                  </p>
                  <p className="mt-0.5 flex items-center gap-1 truncate text-[10px] tabular-nums text-haze-500">
                    {work.category && (
                      <span className="rounded bg-iris-500/10 px-1 py-px text-[9px] text-iris-600">
                        {work.category}
                      </span>
                    )}
                    <span>{formatWorkTime(work.createdAt)}</span>
                    {work.models.length > 0 && (
                      <span className="text-haze-600">· {work.models.length} 个模型</span>
                    )}
                  </p>
                  <div className="mt-1 flex items-center gap-1">
                    {work.layersDir && (
                      <span className="inline-flex items-center gap-0.5 rounded-full bg-mint-400/10 px-1.5 py-px text-[9px] text-mint-600">
                        <Check className="h-2.5 w-2.5" /> 分层
                      </span>
                    )}
                    {work.model3Url && (
                      <span className="inline-flex items-center gap-0.5 rounded-full bg-iris-500/10 px-1.5 py-px text-[9px] text-iris-600">
                        <Box className="h-2.5 w-2.5" /> 模型
                      </span>
                    )}
                    {work.fallback && (
                      <span
                        className="inline-flex items-center gap-0.5 rounded-full bg-amber-400/10 px-1.5 py-px text-[9px] text-amber-700"
                        title={work.fallbackReason || '本地占位图，非 AI 生成'}
                      >
                        <AlertTriangle className="h-2.5 w-2.5" /> 占位图
                      </span>
                    )}
                  </div>
                </div>
              </button>
            );
          })
        )}
      </div>

      <div className="flex items-center justify-between border-t border-black/[0.07] px-3 py-2 text-[10px] tabular-nums text-haze-600">
        <span>{works.length} 件作品</span>
        <span>v{APP_VERSION}</span>
      </div>
    </aside>
  );
}
