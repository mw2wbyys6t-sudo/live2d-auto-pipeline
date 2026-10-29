import { useEffect, useRef, useState, type ReactNode } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { Activity, Loader2, PanelLeft, Plus, RefreshCw, Sparkles } from 'lucide-react';
import { APP_NAME, APP_VERSION } from '../../lib/brand';
import { useWorkbench } from '../../lib/workbench-context';
import { promptExcerpt, type StageId, type InspectorTab } from '../../lib/workbench';
import ConnectionStatus from '../ConnectionStatus';
import WorkpieceRail from './WorkpieceRail';
import StageBar from './StageBar';
import Inspector from './Inspector';
import GenerateStage from './stages/GenerateStage';
import LayersStage from './stages/LayersStage';
import PreviewStage from './stages/PreviewStage';
import ExportStage from './stages/ExportStage';

const ROUTE_STAGES: StageId[] = ['generate', 'layers', 'preview', 'export'];
const ROUTE_TABS: InspectorTab[] = ['prompt', 'layers', 'params', 'export'];

function StageShell({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <div
      className={`absolute inset-0 ${active ? '' : 'invisible pointer-events-none'}`}
      aria-hidden={!active}
    >
      {children}
    </div>
  );
}

export default function Workbench() {
  const router = useRouter();
  const {
    hydrated,
    worksLoading,
    current,
    refreshWorks,
    startNew,
    stage,
    setStage,
    setInspector,
  } = useWorkbench();
  const routeApplied = useRef(false);
  const [railOpen, setRailOpen] = useState(false);

  // 旧入口（/layers、/preview…）重定向到 /?stage=…；消费一次后清掉 query。
  useEffect(() => {
    if (!router.isReady || !hydrated || routeApplied.current) return;
    routeApplied.current = true;
    const rawStage = router.query.stage;
    const rawTab = router.query.tab;
    const stageValue = Array.isArray(rawStage) ? rawStage[0] : rawStage;
    const tabValue = Array.isArray(rawTab) ? rawTab[0] : rawTab;
    if (stageValue && ROUTE_STAGES.includes(stageValue as StageId)) {
      setStage(stageValue as StageId);
    } else if (tabValue && ROUTE_TABS.includes(tabValue as InspectorTab)) {
      setInspector(tabValue as InspectorTab);
    }
    if (stageValue || tabValue) {
      void router.replace('/', undefined, { shallow: true });
    }
  }, [router, hydrated, setStage, setInspector]);

  return (
    <div className="h-screen w-full flex overflow-hidden bg-[#0f0f13] text-gray-200">
      <Head>
        <title>{`${APP_NAME} · 工作台`}</title>
      </Head>
      {/* 窄窗口（<1280px）下三栏挤在一起会把画布压成一列竖排文字，
          作品栏改成抽屉浮层，画布保住最小宽度。 */}
      <WorkpieceRail className="hidden xl:flex" />
      {railOpen && (
        <>
          <div
            className="fixed inset-0 z-30 bg-black/60 xl:hidden"
            onClick={() => setRailOpen(false)}
            aria-hidden="true"
          />
          <WorkpieceRail
            className="fixed inset-y-0 left-0 z-40 shadow-2xl shadow-black/60 xl:hidden"
            onNavigate={() => setRailOpen(false)}
          />
        </>
      )}

      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-14 shrink-0 flex items-center justify-between gap-4 px-4 border-b border-gray-800 bg-[#0f0f13]">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-pink-500 to-purple-600 flex items-center justify-center shadow-lg shadow-pink-500/20">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div className="min-w-0">
              <h1 className="text-sm font-bold text-white leading-tight">
                {APP_NAME}
                <span className="ml-2 text-[10px] font-normal text-pink-400/80 border border-pink-500/30 rounded px-1 py-0.5">
                  工作台
                </span>
              </h1>
              <p className="text-[10px] text-gray-500 truncate">
                {current
                  ? `${current.characterId || '未命名角色'} · ${promptExcerpt(current.prompt, 48)}`
                  : '中文提示词 → 立绘 → 分层 → Live2D → 桌宠'}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            <button
              onClick={() => setRailOpen((open) => !open)}
              className="xl:hidden p-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
              aria-label="打开作品列表"
              title="作品列表"
            >
              <PanelLeft className="w-4 h-4" />
            </button>
            <ConnectionStatus className="hidden sm:flex" />
            <button
              onClick={() => void refreshWorks()}
              className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
              title="刷新作品列表"
              aria-label="刷新作品列表"
            >
              <RefreshCw className={`w-4 h-4 ${worksLoading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={startNew}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" /> 新建
            </button>
            <Link
              href="/health"
              className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
              aria-label="环境自检"
              title="环境自检"
            >
              <Activity className="w-4 h-4" />
            </Link>
            <span className="text-[10px] text-gray-600 pl-1">v{APP_VERSION}</span>
          </div>
        </header>

        <StageBar />

        <div className="flex-1 flex min-h-0">
          <main className="flex-1 min-w-0 relative bg-[#0d0d12]">
            <StageShell active={stage === 'generate'}>
              <GenerateStage />
            </StageShell>
            <StageShell active={stage === 'layers'}>
              <LayersStage />
            </StageShell>
            <StageShell active={stage === 'preview'}>
              <PreviewStage />
            </StageShell>
            <StageShell active={stage === 'export'}>
              <ExportStage />
            </StageShell>

            {!hydrated && (
              <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#0d0d12]/80">
                <span className="flex items-center gap-2 text-xs text-gray-400">
                  <Loader2 className="w-4 h-4 animate-spin" /> 正在恢复工作台…
                </span>
              </div>
            )}
          </main>

          <Inspector />
        </div>
      </div>
    </div>
  );
}
