import { useEffect, useRef, useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import {
  Activity,
  ArrowRight,
  Box,
  Check,
  ImagePlus,
  Layers,
  Loader2,
  Play,
  Plus,
  Sparkles,
  Wand2,
} from 'lucide-react';
import { apiClient } from '../../lib/api-client';
import {
  formatWorkTime,
  workDisplayName,
  workFromRecord,
  type Workpiece,
} from '../../lib/workbench';
import { APP_NAME, APP_VERSION } from '../../lib/brand';
import { PIPELINE_FLOW as FLOW } from '../../lib/catalog';
import ConnectionStatus from '../ConnectionStatus';

/** 启动页：应用打开后的第一屏，先决定「继续 / 新建 / 看看最近作品」，再进工作台。 */
export default function LaunchScreen() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [works, setWorks] = useState<Workpiece[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    apiClient
      .listGenerations(48)
      .then((records) => {
        if (cancelled) return;
        const list = records
          .map(workFromRecord)
          .filter((work): work is Workpiece => Boolean(work));
        setWorks(list);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '无法读取作品历史');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const latest = works[0] || null;
  const recent = works.slice(0, 6);

  /** 「已有图片」链路：上传 → 登记作品 → 直接落到分层阶段。 */
  const handleImportImage = async (file?: File | null) => {
    if (!file) return;
    setImporting(true);
    setImportError('');
    try {
      const uploaded = await apiClient.uploadImage(file);
      const record = await apiClient.createWork(uploaded.path);
      if (!record?.id) throw new Error('作品登记失败');
      await router.push(`/studio?work=${encodeURIComponent(record.id)}&stage=layers`);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : '导入失败，请重试');
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  return (
    <div className="app-bg relative flex min-h-screen flex-col overflow-hidden font-sans text-haze-300">
      <Head>
        <title>{`${APP_NAME} · 启动`}</title>
      </Head>

      {/* 星屑：纯 CSS 点阵，不引入外部图片 */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-70"
        style={{
          backgroundImage:
            'radial-gradient(1px 1px at 18% 26%, rgba(255,255,255,0.55), transparent),' +
            'radial-gradient(1px 1px at 72% 18%, rgba(255,179,209,0.55), transparent),' +
            'radial-gradient(1.5px 1.5px at 46% 68%, rgba(196,181,253,0.5), transparent),' +
            'radial-gradient(1px 1px at 88% 56%, rgba(255,255,255,0.4), transparent),' +
            'radial-gradient(1px 1px at 8% 74%, rgba(255,255,255,0.35), transparent)',
          backgroundSize: '420px 420px',
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -left-40 top-[-18rem] h-[34rem] w-[34rem] rounded-full bg-sakura-500/10 blur-3xl"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-48 top-24 h-[38rem] w-[38rem] rounded-full bg-iris-500/10 blur-3xl"
      />

      <div className="relative mx-auto flex w-full max-w-6xl flex-1 flex-col px-5 py-6 sm:px-8">
        <header className="animate-fade-in flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <img
              src="/app-icon.png"
              alt={`${APP_NAME} 图标`}
              width={40}
              height={40}
              className="h-10 w-10 shrink-0 rounded-[11px] bg-white object-contain p-[2px] ring-1 ring-black/10 shadow-md shadow-black/10"
            />
            <div>
              <p className="text-sm font-semibold tracking-[0.18em] text-haze-100">PYGMALION STUDIO</p>
              <p className="text-[10px] tracking-wider text-haze-500">中文角色工作台 · v{APP_VERSION}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <ConnectionStatus className="hidden sm:flex" />
            <Link href="/health" className="btn-icon" aria-label="环境自检" title="环境自检">
              <Activity className="h-4 w-4" />
            </Link>
          </div>
        </header>

        <main className="grid flex-1 items-center gap-8 py-4 lg:grid-cols-[1.05fr_0.95fr] lg:gap-12 lg:py-6">
          <section className="animate-fade-in-up min-w-0">
            <span className="chip border-iris-500/30 bg-iris-500/[0.08] text-iris-600">
              <Wand2 className="h-3 w-3" /> 提示词 → 立绘 → 可绑定模型
            </span>
            <h1 className="mt-4 text-[30px] font-bold leading-[1.25] tracking-wide text-haze-100 sm:text-4xl">
              让一句中文，
              <br />
              <span className="gradient-text">长成会呼吸的角色。</span>
            </h1>
            <p className="mt-4 max-w-xl text-[13px] leading-relaxed text-haze-400">
              Pygmalion Studio 把生成、语义分层、Live2D 绑定与导出串成一条本地流水线。
              写一句描述，剩下的交给工作台；你随时可以回来继续上次的角色。
            </p>

            <div className="mt-6 grid gap-2.5 sm:grid-cols-2">
              <Link href="/studio?new=1" className="card card-hover flex flex-col gap-1 p-3.5">
                <span className="flex items-center gap-1.5 text-xs font-medium text-haze-100">
                  <Wand2 className="h-3.5 w-3.5 text-sakura-500" /> 从零开始
                  <span className="ml-auto text-[10px] font-normal text-haze-500">四步全链路</span>
                </span>
                <span className="text-[11px] leading-relaxed text-haze-500">
                  写一句中文描述：生成 → 分层 → 模型 → 导出
                </span>
              </Link>
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={importing}
                className="card card-hover flex flex-col gap-1 p-3.5 text-left disabled:opacity-60"
              >
                <span className="flex items-center gap-1.5 text-xs font-medium text-haze-100">
                  <ImagePlus className="h-3.5 w-3.5 text-mint-600" /> 已有图片
                  {importing ? (
                    <Loader2 className="ml-auto h-3 w-3 animate-spin text-haze-500" />
                  ) : (
                    <span className="ml-auto text-[10px] font-normal text-haze-500">从分层开始</span>
                  )}
                </span>
                <span className="text-[11px] leading-relaxed text-haze-500">
                  上传现成立绘，跳过生成：分层 → 模型 → 导出
                </span>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={(event) => void handleImportImage(event.target.files?.[0])}
              />
            </div>
            {importError && <p className="mt-2 text-[10px] text-rose-600">{importError}</p>}

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Link
                href={latest ? '/studio' : '/studio?new=1'}
                className="btn-primary gap-2 px-4 py-2.5 text-[12px]"
              >
                <Play className="h-3.5 w-3.5" />
                {latest ? '继续上次作品' : '开始第一个角色'}
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
              <Link
                href="/studio"
                className="px-2 py-2 text-[11px] text-haze-500 transition-colors hover:text-haze-200"
              >
                直接打开工作台
              </Link>
              <Link
                href="/models"
                className="px-2 py-2 text-[11px] text-haze-500 transition-colors hover:text-haze-200"
              >
                模型库
              </Link>
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-1.5 text-[10px] text-haze-500">
              {FLOW.map((step, index) => (
                <span key={step} className="flex items-center gap-1.5">
                  {index > 0 && <span className="h-px w-4 bg-white/10" />}
                  <span className="rounded-full border border-black/[0.07] bg-black/[0.02] px-2 py-0.5">
                    {step}
                  </span>
                </span>
              ))}
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-haze-600">
              <span className="tabular-nums">{loading ? '正在读取作品…' : `${works.length} 件作品`}</span>
              <span className="h-3 w-px bg-white/10" />
              <span>本地优先 · 产物留在 output 目录</span>
              {error && (
                <button
                  onClick={() => setReloadKey((key) => key + 1)}
                  className="text-amber-700 underline decoration-amber-300/40 underline-offset-2 hover:text-amber-700"
                >
                  读取失败，重试
                </button>
              )}
            </div>
          </section>

          <section className="animate-fade-in relative mx-auto w-full max-w-[300px] sm:max-w-[320px] lg:max-w-[330px]">
            <div
              aria-hidden="true"
              className="animate-pulse-glow absolute inset-6 rounded-[32px] bg-gradient-to-br from-sakura-500/25 to-iris-500/20 blur-3xl"
            />
            <div
              aria-hidden="true"
              className="absolute inset-0 rounded-[30px] border border-black/[0.07]"
              style={{ transform: 'rotate(-3deg) translateY(10px)' }}
            />

            <div className="ring-gradient animate-float relative overflow-hidden rounded-3xl bg-ink-850/90 shadow-panel">
              <div className="relative aspect-[4/5] w-full overflow-hidden">
                {latest?.imageUrl ? (
                  <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={latest.imageUrl}
                      alt={latest.prompt || '最近作品'}
                      className="h-full w-full object-cover"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/10 to-transparent" />
                    <div className="absolute left-3 top-3 flex flex-wrap gap-1.5">
                      <span className="rounded-full border border-sakura-500/40 bg-ink-950/70 px-2 py-0.5 text-[10px] text-sakura-600 backdrop-blur">
                        最近作品
                      </span>
                      {latest.layersDir && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-mint-400/30 bg-ink-950/70 px-2 py-0.5 text-[10px] text-mint-600 backdrop-blur">
                          <Check className="h-2.5 w-2.5" /> 已分层
                        </span>
                      )}
                      {latest.model3Url && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-iris-500/30 bg-ink-950/70 px-2 py-0.5 text-[10px] text-iris-600 backdrop-blur">
                          <Box className="h-2.5 w-2.5" /> 已绑定
                        </span>
                      )}
                    </div>
                    <div className="absolute inset-x-0 bottom-0 p-4">
                      <p className="truncate text-xs text-haze-200">
                        {workDisplayName(latest, 40)}
                      </p>
                      <p className="mt-1 flex items-center gap-2 text-[10px] text-haze-500">
                        <span className="tabular-nums">{formatWorkTime(latest.createdAt)}</span>
                        <span className="h-2.5 w-px bg-white/15" />
                        <span className="tabular-nums">
                          {latest.width}×{latest.height}
                        </span>
                      </p>
                    </div>
                  </>
                ) : (
                  <div className="flex h-full flex-col items-center justify-center px-8 text-center">
                    <div className="ring-gradient mb-4 flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br from-sakura-500/20 to-iris-500/20">
                      {loading ? (
                        <Loader2 className="h-8 w-8 animate-spin text-sakura-600" />
                      ) : (
                        <Sparkles className="h-8 w-8 text-sakura-600" />
                      )}
                    </div>
                    <p className="text-sm text-haze-200">
                      {loading ? '正在读取最近作品…' : '等待第一位角色'}
                    </p>
                    <p className="mt-1.5 max-w-[240px] text-[11px] leading-relaxed text-haze-500">
                      在启动页点「新建角色」，写一句中文描述，就能开始第一条流水线。
                    </p>
                  </div>
                )}
              </div>
              <div className="flex items-center justify-between border-t border-black/[0.07] px-4 py-2.5 text-[10px] text-haze-500">
                <span className="inline-flex items-center gap-1.5">
                  <Layers className="h-3 w-3 text-iris-600" /> 角色舞台
                </span>
                <span>PYGMALION / ATELIER</span>
              </div>
            </div>
          </section>
        </main>

        <section className="animate-fade-in pb-4">
          <div className="mb-3 flex items-end justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold tracking-wide text-haze-100">最近作品</h2>
              <p className="mt-0.5 text-[10px] text-haze-500">点一张卡片，直接从上次的进度继续</p>
            </div>
            <Link
              href="/studio"
              className="inline-flex items-center gap-1 text-[10px] text-haze-500 transition-colors hover:text-haze-200"
            >
              查看全部 <ArrowRight className="h-3 w-3" />
            </Link>
          </div>

          {loading ? (
            <div className="flex gap-2.5 overflow-hidden">
              {[0, 1, 2, 3, 4, 5].map((i) => (
                <div
                  key={i}
                  className="aspect-[4/5] w-[120px] shrink-0 animate-shimmer rounded-2xl bg-black/[0.04] sm:w-[132px]"
                />
              ))}
            </div>
          ) : recent.length === 0 ? (
            <Link
              href="/studio?new=1"
              className="card-hover flex items-center justify-between gap-4 rounded-2xl border border-dashed border-white/[0.1] bg-black/[0.02] px-5 py-6"
            >
              <div>
                <p className="text-xs text-haze-200">还没有作品</p>
                <p className="mt-1 text-[10px] text-haze-500">从第一张立绘开始，工作台会自动保存每一步产物。</p>
              </div>
              <span className="btn-soft shrink-0">
                <Plus className="h-3.5 w-3.5" /> 新建角色
              </span>
            </Link>
          ) : (
            <div className="flex gap-2.5 overflow-x-auto pb-1">
              {recent.map((work) => (
                <Link
                  key={work.id}
                  href={`/studio?work=${encodeURIComponent(work.id)}`}
                  className="group w-[120px] shrink-0 overflow-hidden rounded-2xl border border-black/[0.07] bg-ink-850/70 transition-all duration-200 hover:-translate-y-1 hover:border-sakura-500/30 hover:shadow-glow-sakura sm:w-[132px]"
                >
                  <div className="relative aspect-[4/5] overflow-hidden bg-ink-950">
                    {work.imageUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={work.imageUrl}
                        alt=""
                        className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.04]"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center">
                        <Layers className="h-6 w-6 text-haze-600" />
                      </div>
                    )}
                    <div className="absolute inset-0 bg-gradient-to-t from-ink-950/90 via-transparent to-transparent" />
                    <div className="absolute inset-x-0 bottom-0 p-2.5">
                      <p className="truncate text-[11px] text-haze-200">{workDisplayName(work, 16)}</p>
                      <p className="mt-0.5 text-[9px] tabular-nums text-haze-500">
                        {formatWorkTime(work.createdAt)}
                      </p>
                    </div>
                    {(work.layersDir || work.model3Url) && (
                      <span className="absolute right-2 top-2 rounded-full bg-ink-950/70 px-1.5 py-0.5 text-[9px] text-mint-600 backdrop-blur">
                        {work.model3Url ? '已绑定' : '已分层'}
                      </span>
                    )}
                  </div>
                </Link>
              ))}
              <Link
                href="/studio?new=1"
                className="flex aspect-[4/5] w-[120px] shrink-0 flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-black/[0.14] bg-white/[0.015] text-haze-500 transition-colors hover:border-sakura-500/40 hover:bg-sakura-500/[0.04] hover:text-sakura-600 sm:w-[132px]"
              >
                <span className="flex h-8 w-8 items-center justify-center rounded-full border border-white/[0.1] bg-black/[0.03]">
                  <Plus className="h-4 w-4" />
                </span>
                <span className="text-[10px]">新建角色</span>
              </Link>
            </div>
          )}
        </section>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-black/[0.07] py-3 text-[10px] text-haze-600">
          <span className="inline-flex items-center gap-1.5">
            <Sparkles className="h-3 w-3 text-sakura-600/70" /> {APP_NAME} · 把角色养成带进桌面
          </span>
          <span className="flex items-center gap-3">
            <Link href="/health" className="transition-colors hover:text-haze-300">
              环境自检
            </Link>
            <span className="tabular-nums">v{APP_VERSION}</span>
          </span>
        </footer>
      </div>
    </div>
  );
}
