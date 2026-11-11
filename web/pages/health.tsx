import { useEffect, useState } from 'react';
import { CheckCircle2, XCircle, Copy, RefreshCw, FolderOpen, Download, Ban } from 'lucide-react';
import { apiClient, type DiagnosticsReport, type OptionalModel } from '../lib/api-client';
import { APP_NAME } from '../lib/brand';

/**
 * 环境自检页（P2）：逐条绿/红展示解释器、脚本目录、产物可写、面捕三件套、
 * 回环绑定；提供「复制诊断信息」与日志目录指引。只读，不触发生成。
 */
export default function HealthPage() {
  const [report, setReport] = useState<DiagnosticsReport | null>(null);
  const [models, setModels] = useState<OptionalModel[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState('');

  // 失败必须落到界面上：此前这里没有 try/catch，Promise.all 一旦被超时/断网
  // 拒绝，setLoading(false) 永远不执行，页面就一直停在「正在检查…」——
  // 用户看不出是后端慢、代理超时还是没有后端。
  const load = async () => {
    setLoading(true);
    setLoadError('');
    // 两个请求互不依赖：一个失败不该把另一个的结果一起吞掉，更不能让
    // setLoading(false) 被跳过（那就是此前「永远正在检查…」的根因）。
    const [r, m] = await Promise.allSettled([
      apiClient.getDiagnostics(),
      apiClient.listOptionalModels(),
    ]);
    if (r.status === 'fulfilled') {
      setReport(r.value);
      if (!r.value) {
        setLoadError('后端未返回诊断结果（可能仍在启动，或探测超时）');
      }
    } else {
      setLoadError(r.reason instanceof Error ? r.reason.message : '诊断请求失败');
    }
    if (m.status === 'fulfilled') setModels(m.value);
    setLoading(false);
  };

  // 下载中时每 2s 轮询进度；空闲/就绪不轮询。
  useEffect(() => {
    const anyDownloading = models?.some((m) => m.state === 'downloading');
    if (!anyDownloading) return;
    const id = setInterval(async () => {
      setModels(await apiClient.listOptionalModels());
    }, 2000);
    return () => clearInterval(id);
  }, [models]);

  useEffect(() => {
    void load();
  }, []);

  // 后端拒绝（例如降级模式不支持在线下载）时必须把原因显示出来：
  // 早先这里把异常吞掉，用户点了「下载」却毫无反应。
  const startModel = async (name: string) => {
    setActionError('');
    try {
      await apiClient.startOptionalModel(name);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '下载失败');
    }
    setModels(await apiClient.listOptionalModels());
  };
  const cancelModel = async (name: string) => {
    setActionError('');
    try {
      await apiClient.cancelOptionalModel(name);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '取消失败');
    }
    setModels(await apiClient.listOptionalModels());
  };

  const copyDiagnostics = async () => {
    if (!report) return;
    const lines = [
      `${APP_NAME} ${report.version} (${report.os})`,
      `监听: ${report.host}:${report.port}`,
      // 路径必须进剪贴板：反馈问题时最常被追问的就是"日志在哪、产物写到哪"，
      // 只带 checks 列表的话，对方仍然不知道去哪找 live2d.log。
      `日志目录: ${report.log_dir || '(未上报)'}`,
      `产物目录: ${report.output_dir || '(未上报)'}`,
      ...report.checks.map((c) =>
        `${c.ok ? '✅' : '❌'} ${c.label}${c.detail ? ` — ${c.detail}` : ''}${c.issues?.length ? ` [${c.issues.join('; ')}]` : ''}`,
      ),
    ];
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* 剪贴板不可用时静默 */
    }
  };

  const passed = report?.checks.filter((c) => c.ok).length ?? 0;
  const total = report?.checks.length ?? 0;

  return (
    // 页面外壳由 _app 统一提供（侧栏 + 顶栏），这里不能再套一层 Layout，
    // 否则会出现双份侧栏与双份标题。
    <div className="max-w-2xl space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-haze-100">运行环境检查</h2>
            <p className="mt-1 text-sm text-haze-400">
              逐项核对本机解释器、产物目录、面捕组件与图像上游，全部通过即可正常生成与导出。
            </p>
          </div>
          <button
            onClick={load}
            className="flex shrink-0 items-center gap-2 whitespace-nowrap rounded-[10px] border border-black/[0.08] bg-black/[0.03] px-3 py-2 text-sm text-haze-200 hover:bg-black/[0.05]"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>

        {report && (
          <div className="rounded-xl border p-4 flex items-center gap-3">
            {passed === total ? (
              <CheckCircle2 className="w-6 h-6 text-mint-600" />
            ) : (
              <XCircle className="w-6 h-6 text-rose-600" />
            )}
            <div>
              <p className="font-semibold">
                {passed}/{total} 项通过
              </p>
              <p className="text-xs text-haze-400">
                {passed === total
                  ? '环境就绪，可以开始生成。'
                  : '存在未通过项，请按下方提示处理后刷新。'}
              </p>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {loading && <p className="text-haze-400 text-sm">正在检查…</p>}
          {!loading && loadError && (
            <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 p-3 space-y-2">
              <p className="text-rose-600 text-sm">检查失败：{loadError}</p>
              <p className="text-xs text-haze-400">
                后端可能还在启动、或探测耗时较长。稍后点「刷新」重试；若持续失败，
                请确认应用已启动并查看日志目录里的 live2d.log。
              </p>
            </div>
          )}
          {report?.checks.map((c) => (
            <div
              key={c.key}
              className="flex items-start gap-3 rounded-xl border border-black/[0.07] bg-ink-800/80 p-3"
            >
              {c.ok ? (
                <CheckCircle2 className="w-5 h-5 text-mint-600 mt-0.5 shrink-0" />
              ) : (
                <XCircle className="w-5 h-5 text-rose-600 mt-0.5 shrink-0" />
              )}
              <div className="min-w-0">
                <p className="text-sm font-medium">{c.label}</p>
                {c.detail && <p className="text-xs text-haze-500 break-all mt-0.5">{c.detail}</p>}
                {c.issues?.map((iss) => (
                  <p key={iss} className="text-xs text-rose-600 mt-0.5">{iss}</p>
                ))}
              </div>
            </div>
          ))}
          {!loading && !report && (
            <p className="text-rose-600 text-sm">无法连接后端，请确认应用已启动。</p>
          )}
        </div>

        {/* 可选重模型按需下载（SAM2 等，断点续传 + 取消，落 %APPDATA%） */}
        {models && models.length > 0 && (
          <div className="rounded-xl border border-black/[0.07] bg-ink-800/80 p-4 space-y-3">
            <p className="text-sm font-medium">可选增强模型（默认不下载）</p>
            {models.map((m) => (
              <div key={m.name} className="space-y-2 rounded-lg bg-black/[0.03] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{m.name}</p>
                    <p className="text-xs text-haze-500">{m.note}</p>
                    <p className="text-[11px] text-haze-600">
                      约 {m.size_hint_mb} MB · 需另装 {m.requires_pkg} · 落 %APPDATA% 缓存
                    </p>
                  </div>
                  {m.state === 'ready' ? (
                    <span className="flex items-center gap-1 text-xs text-mint-600 shrink-0">
                      <CheckCircle2 className="w-4 h-4" /> 已就绪
                    </span>
                  ) : m.state === 'downloading' ? (
                    <button
                      onClick={() => cancelModel(m.name)}
                      className="flex shrink-0 items-center gap-1 rounded-[10px] border border-black/[0.08] bg-black/[0.03] px-3 py-1.5 text-xs text-haze-300 hover:bg-black/[0.05]"
                    >
                      <Ban className="w-3.5 h-3.5" /> 取消
                    </button>
                  ) : m.state === 'error' ? (
                    // 后端已明确说明拿不到这个模型（如降级模式不支持在线下载）：
                    // 再摆一个「下载」按钮只会让用户点了没反应。
                    <span className="text-xs text-rose-600 shrink-0">不可用</span>
                  ) : (
                    <button
                      onClick={() => startModel(m.name)}
                      className="flex shrink-0 items-center gap-1 rounded-[10px] border border-sakura-500/40 bg-sakura-500/15 px-3 py-1.5 text-xs text-sakura-600 hover:bg-sakura-500/25"
                    >
                      <Download className="w-3.5 h-3.5" /> 下载
                    </button>
                  )}
                </div>
                {m.state === 'downloading' && (
                  <div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-black/[0.08]">
                      <div className="h-full bg-gradient-to-r from-sakura-500 to-iris-500" style={{ width: `${m.progress}%` }} />
                    </div>
                    <p className="text-[11px] text-haze-500 mt-1">
                      {m.downloaded_mb.toFixed(1)} / {m.total_mb} MB · {m.progress.toFixed(0)}%
                    </p>
                  </div>
                )}
                {m.state === 'error' && (
                  <p className="text-xs text-rose-600">{m.message}</p>
                )}
                {m.state === 'cancelled' && (
                  <p className="text-xs text-amber-700">已取消；再次下载将从断点续传。</p>
                )}
              </div>
            ))}
            {actionError && (
              <p className="text-xs text-rose-600">操作失败：{actionError}</p>
            )}
          </div>
        )}

        <div className="rounded-xl border border-black/[0.07] bg-ink-800/80 p-4 space-y-3 text-sm">
          <div className="flex items-center gap-2">
            <FolderOpen className="w-4 h-4 text-haze-400" />
            <span className="font-medium">日志目录</span>
          </div>
          <p className="text-xs text-haze-400 break-all">
            {/* 路径来自后端：写死过一次，结果在本机指向一个不存在的目录，
                用户照着提示找不到 live2d.log。 */}
            {report?.log_dir
              ? <code>{report.log_dir}</code>
              : <code>（未取到日志目录，请点「复制诊断信息」把 log_dir 一并发回）</code>}
            （出问题时把 live2d.log 与下方诊断信息一并反馈）
          </p>
          <p className="text-[11px] text-haze-500 leading-relaxed">
            首次安装若弹 SmartScreen「Windows 已保护你的电脑」：点「更多信息」→「仍要运行」
            （本包未做代码签名，非病毒）；WebView2 缺失会引导在线安装。
          </p>
          <button
            onClick={copyDiagnostics}
            className="flex items-center gap-2 rounded-[10px] border border-black/[0.08] bg-black/[0.03] px-3 py-2 text-sm text-haze-200 hover:bg-black/[0.05]"
          >
            <Copy className="w-4 h-4" />
            {copied ? '已复制' : '复制诊断信息'}
          </button>
        </div>
    </div>
  );
}
