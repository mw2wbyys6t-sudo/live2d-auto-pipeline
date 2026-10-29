import { useEffect, useState } from 'react';
import { CheckCircle2, XCircle, Copy, RefreshCw, FolderOpen, Download, Ban } from 'lucide-react';
import Layout from '../components/Layout';
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
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState('');

  const load = async () => {
    setLoading(true);
    const [r, m] = await Promise.all([
      apiClient.getDiagnostics(),
      apiClient.listOptionalModels(),
    ]);
    setReport(r);
    setModels(m);
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
    <Layout title="环境自检">
      <div className="max-w-2xl space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold">运行环境检查</h2>
            <p className="text-sm text-gray-400 mt-1">
              逐项核对本机解释器、产物目录与面捕组件，全部通过即可正常生成与导出。
            </p>
          </div>
          <button
            onClick={load}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>

        {report && (
          <div className="rounded-xl border p-4 flex items-center gap-3">
            {passed === total ? (
              <CheckCircle2 className="w-6 h-6 text-green-400" />
            ) : (
              <XCircle className="w-6 h-6 text-red-400" />
            )}
            <div>
              <p className="font-semibold">
                {passed}/{total} 项通过
              </p>
              <p className="text-xs text-gray-400">
                {passed === total
                  ? '环境就绪，可以开始生成。'
                  : '存在未通过项，请按下方提示处理后刷新。'}
              </p>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {loading && <p className="text-gray-400 text-sm">正在检查…</p>}
          {report?.checks.map((c) => (
            <div
              key={c.key}
              className="flex items-start gap-3 rounded-xl border border-gray-800 bg-[#14141c] p-3"
            >
              {c.ok ? (
                <CheckCircle2 className="w-5 h-5 text-green-400 mt-0.5 shrink-0" />
              ) : (
                <XCircle className="w-5 h-5 text-red-400 mt-0.5 shrink-0" />
              )}
              <div className="min-w-0">
                <p className="text-sm font-medium">{c.label}</p>
                {c.detail && <p className="text-xs text-gray-500 break-all mt-0.5">{c.detail}</p>}
                {c.issues?.map((iss) => (
                  <p key={iss} className="text-xs text-red-300 mt-0.5">{iss}</p>
                ))}
              </div>
            </div>
          ))}
          {!loading && !report && (
            <p className="text-red-300 text-sm">无法连接后端，请确认应用已启动。</p>
          )}
        </div>

        {/* 可选重模型按需下载（SAM2 等，断点续传 + 取消，落 %APPDATA%） */}
        {models && models.length > 0 && (
          <div className="rounded-xl border border-gray-800 bg-[#14141c] p-4 space-y-3">
            <p className="text-sm font-medium">可选增强模型（默认不下载）</p>
            {models.map((m) => (
              <div key={m.name} className="space-y-2 rounded-lg bg-gray-900/60 p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{m.name}</p>
                    <p className="text-xs text-gray-500">{m.note}</p>
                    <p className="text-[11px] text-gray-600">
                      约 {m.size_hint_mb} MB · 需另装 {m.requires_pkg} · 落 %APPDATA% 缓存
                    </p>
                  </div>
                  {m.state === 'ready' ? (
                    <span className="flex items-center gap-1 text-xs text-emerald-400 shrink-0">
                      <CheckCircle2 className="w-4 h-4" /> 已就绪
                    </span>
                  ) : m.state === 'downloading' ? (
                    <button
                      onClick={() => cancelModel(m.name)}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs shrink-0"
                    >
                      <Ban className="w-3.5 h-3.5" /> 取消
                    </button>
                  ) : m.state === 'error' ? (
                    // 后端已明确说明拿不到这个模型（如降级模式不支持在线下载）：
                    // 再摆一个「下载」按钮只会让用户点了没反应。
                    <span className="text-xs text-red-300 shrink-0">不可用</span>
                  ) : (
                    <button
                      onClick={() => startModel(m.name)}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-pink-500/20 border border-pink-500/40 hover:bg-pink-500/30 text-xs text-pink-200 shrink-0"
                    >
                      <Download className="w-3.5 h-3.5" /> 下载
                    </button>
                  )}
                </div>
                {m.state === 'downloading' && (
                  <div>
                    <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
                      <div className="h-full bg-pink-500" style={{ width: `${m.progress}%` }} />
                    </div>
                    <p className="text-[11px] text-gray-500 mt-1">
                      {m.downloaded_mb.toFixed(1)} / {m.total_mb} MB · {m.progress.toFixed(0)}%
                    </p>
                  </div>
                )}
                {m.state === 'error' && (
                  <p className="text-xs text-red-300">{m.message}</p>
                )}
                {m.state === 'cancelled' && (
                  <p className="text-xs text-amber-300">已取消；再次下载将从断点续传。</p>
                )}
              </div>
            ))}
            {actionError && (
              <p className="text-xs text-red-300">操作失败：{actionError}</p>
            )}
          </div>
        )}

        <div className="rounded-xl border border-gray-800 bg-[#14141c] p-4 space-y-3 text-sm">
          <div className="flex items-center gap-2">
            <FolderOpen className="w-4 h-4 text-gray-400" />
            <span className="font-medium">日志目录</span>
          </div>
          <p className="text-xs text-gray-400 break-all">
            Windows: <code>%APPDATA%\PygmalionStudio\logs</code>（出问题时把
            live2d.log 与下方诊断信息一并反馈）
          </p>
          <p className="text-[11px] text-gray-500 leading-relaxed">
            首次安装若弹 SmartScreen「Windows 已保护你的电脑」：点「更多信息」→「仍要运行」
            （本包未做代码签名，非病毒）；WebView2 缺失会引导在线安装。
          </p>
          <button
            onClick={copyDiagnostics}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm"
          >
            <Copy className="w-4 h-4" />
            {copied ? '已复制' : '复制诊断信息'}
          </button>
        </div>
      </div>
    </Layout>
  );
}
