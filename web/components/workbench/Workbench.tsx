import { useEffect, useRef, useState, type ReactNode } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { Activity, Loader2, PanelLeft, Plus, RefreshCw } from 'lucide-react';
import { APP_NAME, APP_VERSION } from '../../lib/brand';
import { useWorkbench } from '../../lib/workbench-context';
import { type StageId, type InspectorTab } from '../../lib/workbench';
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
      className={`absolute inset-0 ${active ? 'stage-enter' : 'invisible pointer-events-none'}`}
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
    works,
    refreshWorks,
    selectWork,
    startNew,
    stage,
    setStage,
    setInspector,
  } = useWorkbench();
  const routeApplied = useRef(false);
  const [railOpen, setRailOpen] = useState(false);

  // 旧入口（/layers、/preview…）重定向到 /studio?stage=…；启动页也会用
  // /studio?work=… 或 /studio?new=1 指定要打开的作品。消费一次后清掉 query。
  useEffect(() => {
    if (!router.isReady || !hydrated || routeApplied.current) return;
    routeApplied.current = true;
    const rawStage = router.query.stage;
    const rawTab = router.query.tab;
    const rawWork = router.query.work;
    const rawNew = router.query.new;
    const stageValue = Array.isArray(rawStage) ? rawStage[0] : rawStage;
    const tabValue = Array.isArray(rawTab) ? rawTab[0] : rawTab;
    const workValue = Array.isArray(rawWork) ? rawWork[0] : rawWork;
    const newValue = Array.isArray(rawNew) ? rawNew[0] : rawNew;

    if (newValue === '1') {
      startNew();
    } else if (workValue) {
      const target = works.find((work) => work.id === workValue);
      if (target) selectWork(target.id);
    }
    if (stageValue && ROUTE_STAGES.includes(stageValue as StageId)) {
      setStage(stageValue as StageId);
    } else if (tabValue && ROUTE_TABS.includes(tabValue as InspectorTab)) {
      setInspector(tabValue as InspectorTab);
    }
    if (stageValue || tabValue || workValue || newValue) {
      void router.replace({ pathname: '/studio' }, undefined, { shallow: true });
    }
  }, [router, hydrated, works, selectWork, startNew, setStage, setInspector]);

  return (
    <div className="app-bg flex h-screen w-full overflow-hidden font-sans text-haze-300">
      <Head>
        <title>{`${APP_NAME} · 工作台`}</title>
      </Head>
      {/* 窄窗口（<1280px）下三栏挤在一起会把画布压成一列竖排文字，
          作品栏改成抽屉浮层，画布保住最小宽度。 */}
      <WorkpieceRail className="hidden xl:flex" />
      {railOpen && (
        <>
          <div
            className="fixed inset-0 z-30 bg-[#241634]/25 backdrop-blur-sm xl:hidden"
            onClick={() => setRailOpen(false)}
            aria-hidden="true"
          />
          <WorkpieceRail
            className="fixed inset-y-0 left-0 z-40 shadow-2xl shadow-black/20 xl:hidden"
            onNavigate={() => setRailOpen(false)}
          />
        </>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-black/[0.07] bg-ink-900/70 px-3.5 backdrop-blur">
          <Link
            href="/"
            title="返回启动页"
            className="group flex min-w-0 items-center gap-3"
          >
            <img
              src="/app-icon.png"
              alt={`${APP_NAME} 图标`}
              width={36}
              height={36}
              className="h-9 w-9 shrink-0 rounded-[10px] bg-white object-contain p-[2px] ring-1 ring-black/10 shadow-md shadow-black/15 transition-transform group-hover:scale-105"
            />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="bg-gradient-to-r from-mint-600 to-mint-500 bg-clip-text text-[15px] font-semibold leading-tight tracking-wide text-transparent">
                  {APP_NAME}
                </h1>
                <span className="rounded-full border border-mint-600/30 bg-mint-500/10 px-1.5 py-px text-[10px] font-normal text-mint-600">
                  工作台
                </span>
              </div>
            </div>
          </Link>

          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={() => setRailOpen((open) => !open)}
              className="btn-icon xl:hidden"
              aria-label="打开作品列表"
              title="作品列表"
            >
              <PanelLeft className="h-4 w-4" />
            </button>
            <ConnectionStatus className="hidden sm:flex" />
            <button
              onClick={() => void refreshWorks()}
              className="btn-icon"
              title="刷新作品列表"
              aria-label="刷新作品列表"
            >
              <RefreshCw className={`h-4 w-4 ${worksLoading ? 'animate-spin' : ''}`} />
            </button>
            <button onClick={startNew} className="btn-soft px-2.5 py-1.5">
              <Plus className="h-3.5 w-3.5" /> 新建
            </button>
            <Link href="/health" className="btn-icon" aria-label="环境自检" title="环境自检">
              <Activity className="h-4 w-4" />
            </Link>
            <span className="pl-1 text-[10px] tabular-nums text-haze-600">v{APP_VERSION}</span>
          </div>
        </header>

        <StageBar />

        <div className="flex min-h-0 flex-1">
          <main className="relative min-w-0 flex-1 bg-ink-950/30">
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
              <div className="absolute inset-0 z-10 flex items-center justify-center bg-ink-950/80 backdrop-blur-sm">
                <span className="flex items-center gap-2 text-xs text-haze-400">
                  <Loader2 className="h-4 w-4 animate-spin text-sakura-600" /> 正在恢复工作台…
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
