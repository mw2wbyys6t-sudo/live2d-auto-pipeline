import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import {
  Download,
  FileImage,
  FileJson,
  FileArchive,
  CheckCircle2,
  Loader2,
  RefreshCw,
  ExternalLink,
} from 'lucide-react';
import type { NextPage } from 'next';
import type { Character, ExportDownload, ExportFormat, ExportJob } from '../types';
import { apiClient, modelUrlFromRecord } from '../lib/api-client';
import { getBackendWsUrl } from '../lib/api-client';
import LoadingSpinner from '../components/LoadingSpinner';

interface FormatOption {
  id: ExportFormat;
  label: string;
  desc: string;
  icon: typeof FileImage;
  ext: string;
}

const FORMATS: FormatOption[] = [
  { id: 'live2d-package', label: 'Live2D Package', desc: 'model3.json + textures + physics (.zip)', icon: FileJson, ext: '.zip' },
  { id: 'psd', label: 'PSD 分层方案', desc: '对角色立绘执行智能分层规划，输出层数与方案', icon: FileImage, ext: '.json' },
];

const ExportPage: NextPage = () => {
  const router = useRouter();
  const [characters, setCharacters] = useState<Character[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | undefined>();
  const [layersDir, setLayersDir] = useState<string>('');
  const [lastModelUrl, setLastModelUrl] = useState<string>('');
  const [lastRuntimeReady, setLastRuntimeReady] = useState<boolean | null>(null);
  const [formats, setFormats] = useState<Set<ExportFormat>>(
    new Set(['live2d-package']),
  );
  const [includePhysics, setIncludePhysics] = useState(true);
  const [includeExpressions, setIncludeExpressions] = useState(true);
  const [includeMotions, setIncludeMotions] = useState(false);
  const [compression, setCompression] = useState(6);
  const [job, setJob] = useState<ExportJob | null>(null);
  const [history, setHistory] = useState<ExportJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [blocker, setBlocker] = useState<string>('');
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  const fmtTime = (iso: string | undefined | null) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    return mounted ? d.toLocaleTimeString() : '00:00';
  };

  useEffect(() => {
    apiClient
      .getCharacters()
      .then((list) => {
        setCharacters(list);
        if (list.length > 0) setSelectedId(list[0].id);
      })
      .catch(() => setCharacters([]));
  }, []);

  // 链路交接：分层页「发送到导出」带来 ?layers_dir=；否则回退最近生成记录
  useEffect(() => {
    const fromQuery = router.query.layers_dir;
    if (typeof fromQuery === 'string' && fromQuery) {
      setLayersDir(fromQuery);
      return;
    }
    apiClient
      .getLatestGeneration()
      .then((rec) => {
        if (rec?.layers_dir) setLayersDir(rec.layers_dir);
      })
      .catch(() => undefined);
  }, [router.query.layers_dir]);

  const toggleFormat = (id: ExportFormat) => {
    setFormats((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const startExport = useCallback(async () => {
    if (!selectedId || formats.size === 0) return;
    const selected = characters?.find((c) => c.id === selectedId);
    setError(null);
    setJob({
      id: `job-${Date.now()}`,
      characterId: selectedId,
      characterName: selected?.name || 'Character',
      formats: Array.from(formats),
      status: 'processing',
      progress: 0,
      createdAt: new Date().toISOString(),
    });

    // The API currently exports one real format: Live2D package.
    const downloads: ExportDownload[] = [];
    let exportError: string | null = null;

    // 真实进度：导出期间 Go 向 /api/ws 广播 Python 阶段事件（StageTracker）
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(getBackendWsUrl('/api/ws'));
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as { type?: string; progress?: number; stage?: string; message?: string };
          if (msg.type === 'progress' && typeof msg.progress === 'number' && msg.progress > 0) {
            setJob((j) => (j ? { ...j, progress: Math.min(99, msg.progress!) } : j));
          }
        } catch { /* 忽略坏帧 */ }
      };
    } catch { /* WS 不可用时退化为无过程进度 */ }

    try {
      for (const format of Array.from(formats)) {
        try {
          const opt = FORMATS.find((f) => f.id === format)!;
          const filename = `${(selected?.name || 'character').replace(/\s+/g, '_')}_${format}${opt.ext}`;

          if (format === 'psd') {
            // PSD 分层方案：调用 /api/export/psd 执行智能分层规划
            const psdImagePath = layersDir || '';
            if (!psdImagePath) {
              throw new Error('PSD 分层需要分层目录路径，请先生成或从分层页进入');
            }
            const psdResult = await apiClient
              .exportPSDPlan(psdImagePath)
              .catch(() => null);
            if (!psdResult || !psdResult.success) {
              throw new Error('PSD 分层方案生成失败');
            }
            const planSummary = JSON.stringify({
              layers: psdResult.layers,
              plan: psdResult.plan,
              plan_dir: psdResult.plan_dir,
              applied: psdResult.applied,
            }, null, 2);
            downloads.push({
              format,
              filename,
              size: planSummary.length,
              url: `data:application/json;charset=utf-8,${encodeURIComponent(planSummary)}`,
            });
          } else {
            const result = await apiClient
              .exportModel(selectedId, format, layersDir || undefined)
              .catch(() => null);
            if (!result || !result.success) {
              throw new Error(`Export failed for ${format}`);
            }
            // 链路交接：记录模型 Web 地址（预览页自动加载同一份最新记录）
            const modelUrl = modelUrlFromRecord({
              model3_json: result.model3_json || '',
              model3_url: (result as { model3_url?: string }).model3_url,
            });
            if (modelUrl) setLastModelUrl(modelUrl);
            if (typeof result.runtime_ready === 'boolean') setLastRuntimeReady(result.runtime_ready);
            const payload = modelUrl || result.model3_json || '';
            if (!payload) {
              throw new Error(`Export returned no file for ${format}`);
            }
            downloads.push({ format, filename, size: payload.length, url: payload });
            // 如实反映「能否直接运行」：缺 .moc3 时给出明确阻塞原因
            if (result.runtime_ready === false && result.blocker) {
              setBlocker(result.blocker);
            }
          }
        } catch (err) {
          exportError = err instanceof Error ? err.message : `Export failed for ${format}`;
          setError(exportError);
        }
      }
    } finally {
      if (ws) {
        ws.onmessage = null;
        try { ws.close(); } catch { /* ignore */ }
      }
    }

    const completed: ExportJob = {
      id: `job-${Date.now()}`,
      characterId: selectedId,
      characterName: selected?.name || 'Character',
      formats: Array.from(formats),
      status: downloads.length > 0 && !exportError ? 'done' : 'error',
      progress: 100,
      downloads,
      createdAt: new Date().toISOString(),
      completedAt: new Date().toISOString(),
      error: downloads.length > 0 && !exportError ? undefined : exportError || 'Export failed',
    };
    setJob(completed);
    setHistory((prev) => [completed, ...prev].slice(0, 10));
  }, [selectedId, formats, characters, layersDir]);

  const downloadFile = (dl: ExportDownload) => {
    if (dl.url.startsWith('#')) {
      alert(`Demo download: ${dl.filename} (connect API to generate real file)`);
      return;
    }
    const a = document.createElement('a');
    a.href = dl.url;
    a.download = dl.filename;
    a.click();
  };

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  };

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Download className="w-6 h-6 text-pink-400" /> Export Center
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Package your character for Photoshop, Live2D, or desktop pet deployment
          </p>
        </div>
        <button
          onClick={() => {
            setJob(null);
          }}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-gray-800 border border-gray-700 text-gray-300 hover:bg-gray-700"
        >
          <RefreshCw className="w-3.5 h-3.5" /> New export
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-5">
        {/* Options */}
        <div className="space-y-4">
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <p className="text-xs font-medium text-gray-400 mb-3">Character</p>
            {characters === null ? (
              <LoadingSpinner label="Loading…" size={20} />
            ) : characters.length === 0 ? (
              <p className="text-sm text-gray-500">
                No characters yet.{' '}
                <a href="/characters" className="text-pink-400 hover:text-pink-300">
                  Create one →
                </a>
              </p>
            ) : (
              <select
                value={selectedId || ''}
                onChange={(e) => setSelectedId(e.target.value)}
                className="w-full px-3 py-2.5 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-pink-500"
              >
                {characters.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <p className="text-xs font-medium text-gray-400 mb-3">Export formats</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {FORMATS.map((f) => {
                const Icon = f.icon;
                const selected = formats.has(f.id);
                return (
                  <button
                    key={f.id}
                    onClick={() => toggleFormat(f.id)}
                    className={`flex items-start gap-3 p-3 rounded-lg border text-left transition-all ${
                      selected
                        ? 'bg-pink-500/10 border-pink-500/40'
                        : 'bg-gray-900 border-gray-800 hover:border-gray-700'
                    }`}
                  >
                    <div
                      className={`w-8 h-8 rounded-md flex items-center justify-center shrink-0 ${
                        selected
                          ? 'bg-gradient-to-br from-pink-500 to-purple-600'
                          : 'bg-gray-800'
                      }`}
                    >
                      <Icon className={`w-4 h-4 ${selected ? 'text-white' : 'text-gray-400'}`} />
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-white flex items-center gap-2">
                        {f.label}
                        {selected && <CheckCircle2 className="w-3.5 h-3.5 text-pink-400" />}
                      </p>
                      <p className="text-[11px] text-gray-500 mt-0.5">{f.desc}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <p className="text-xs font-medium text-gray-400 mb-3">Options</p>
            <div className="space-y-3">
              <Toggle
                label="Include physics config"
                value={includePhysics}
                onChange={setIncludePhysics}
              />
              <Toggle
                label="Include expressions"
                value={includeExpressions}
                onChange={setIncludeExpressions}
              />
              <Toggle
                label="Include motions"
                value={includeMotions}
                onChange={setIncludeMotions}
              />
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-gray-300">Compression level</span>
                  <span className="text-xs text-pink-400 font-mono">{compression}</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={9}
                  value={compression}
                  onChange={(e) => setCompression(parseInt(e.target.value))}
                  className="w-full accent-pink-500"
                />
              </div>
            </div>
          </div>

          {error && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-300">
              {error}
            </div>
          )}

          {blocker && (
            <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-xs text-amber-200">
              <span className="font-semibold">导出成功，但该模型包暂不能直接运行：</span> {blocker}
            </div>
          )}

          {job && (
            <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm font-semibold text-white flex items-center gap-2">
                  {job.status === 'processing' ? (
                    <Loader2 className="w-4 h-4 animate-spin text-pink-400" />
                  ) : job.status === 'error' ? (
                      <span className="text-red-400">Failed</span>
                    ) : (
                      <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                    )}
                  {job.status === 'processing'
                    ? 'Exporting…'
                    : job.status === 'error'
                      ? 'Export failed'
                      : 'Export complete'}
                </p>
                <span className="text-xs text-gray-400 font-mono">{job.progress}%</span>
              </div>
              <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden mb-4">
                <div
                  className={`h-full transition-all duration-300 ${
                    job.status === 'done'
                      ? 'bg-gradient-to-r from-emerald-500 to-teal-500'
                      : 'bg-gradient-to-r from-pink-500 to-purple-500'
                  }`}
                  style={{ width: `${job.progress}%` }}
                />
              </div>

              <div className="flex flex-wrap gap-2 mb-2">
                {job.formats.map((f) => {
                  const opt = FORMATS.find((o) => o.id === f)!;
                  return (
                    <span
                      key={f}
                      className="px-2 py-1 rounded-md bg-gray-900 border border-gray-800 text-[11px] text-gray-300"
                    >
                      {opt.label}
                    </span>
                  );
                })}
              </div>

              {/* 链路交接：导出成功后一步进预览 */}
              {job.status === 'done' && lastModelUrl && (
                <a
                  href={lastModelUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 mb-2 rounded-lg text-xs bg-blue-500/15 border border-blue-500/40 text-blue-300 hover:bg-blue-500/25 transition-colors"
                >
                  <ExternalLink className="w-3.5 h-3.5" /> 查看模型文件 / 去预览页自动加载
                </a>
              )}

              {job.downloads && job.downloads.length > 0 && (
                <div className="mt-4 space-y-2">
                  <p className="text-xs text-gray-400 mb-2">Downloads</p>
                  {job.downloads.map((dl) => (
                    <button
                      key={dl.format}
                      onClick={() => downloadFile(dl)}
                      className="w-full flex items-center justify-between p-3 rounded-lg bg-gray-900 border border-gray-800 hover:border-pink-500/40 hover:bg-pink-500/5 transition-colors group"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <FileArchive className="w-4 h-4 text-pink-400 shrink-0" />
                        <span className="text-xs text-white truncate">{dl.filename}</span>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-gray-500 font-mono">
                          {formatSize(dl.size)}
                        </span>
                        <Download className="w-3.5 h-3.5 text-gray-500 group-hover:text-pink-400 transition-colors" />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Sidebar: actions + history */}
        <div className="space-y-4">
          <div className="bg-gradient-to-br from-pink-500/10 to-purple-500/10 border border-pink-500/20 rounded-xl p-5">
            <p className="text-sm font-semibold text-white mb-1">Ready to export</p>
            <p className="text-xs text-gray-400 mb-4">
              {selectedId ? `${formats.size} format${formats.size === 1 ? '' : 's'} queued` : 'Select a character'}
            </p>
            <button
              onClick={startExport}
              disabled={!selectedId || formats.size === 0 || job?.status === 'processing'}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-pink-500/30 transition-all"
            >
              {job?.status === 'processing' ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" /> Exporting…
                </>
              ) : (
                <>
                  <Download className="w-4 h-4" /> Export now
                </>
              )}
            </button>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <p className="text-xs font-medium text-gray-400 mb-3">Export history</p>
            {history.length === 0 ? (
              <p className="text-xs text-gray-600">No previous exports in this session</p>
            ) : (
              <div className="space-y-2">
                {history.map((h) => (
                  <div
                    key={h.id}
                    className="p-3 rounded-lg bg-gray-900 border border-gray-800"
                  >
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-medium text-white truncate">{h.characterName}</p>
                      <span className="text-[10px] text-emerald-400 uppercase tracking-wide">
                        Done
                      </span>
                    </div>
                    <p className="text-[10px] text-gray-500 mt-1">
                      {h.formats.length} formats · {fmtTime(h.createdAt)}
                    </p>
                    {h.downloads && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {h.downloads.map((dl) => (
                          <button
                            key={dl.format}
                            onClick={() => downloadFile(dl)}
                            className="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-gray-300 hover:text-pink-400 hover:bg-gray-700 transition-colors"
                          >
                            {dl.format}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

interface ToggleProps {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}

function Toggle({ label, value, onChange }: ToggleProps) {
  return (
    <button
      type="button"
      onClick={() => onChange(!value)}
      className="w-full flex items-center justify-between p-2 rounded-lg hover:bg-gray-900 transition-colors"
    >
      <span className="text-xs text-gray-300">{label}</span>
      <span
        className={`w-9 h-5 rounded-full relative transition-colors ${
          value ? 'bg-gradient-to-r from-pink-500 to-purple-500' : 'bg-gray-700'
        }`}
      >
        <span
          className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
            value ? 'left-[18px]' : 'left-0.5'
          }`}
        />
      </span>
    </button>
  );
}

export default ExportPage;
