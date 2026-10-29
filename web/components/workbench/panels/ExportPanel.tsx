import { AlertTriangle, CheckCircle2, Download, Loader2, Monitor, Package, RotateCcw } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import type { ExportFormat } from '../../../types';

const FORMATS: Array<{ id: ExportFormat; label: string; hint: string; needsLayers: boolean }> = [
  { id: 'live2d-package', label: 'Live2D 模型包', hint: 'moc3 + 贴图 + model3.json，打包为 zip', needsLayers: true },
  { id: 'psd', label: 'PSD 分层方案', hint: '按部位输出分层规划 JSON', needsLayers: false },
];

export default function ExportPanel() {
  const {
    current,
    exportState,
    toggleExportFormat,
    startExport,
    downloadExport,
    resetExport,
    pet,
    launchPet,
    setStage,
  } = useWorkbench();

  const job = exportState.job;
  const processing = job?.status === 'processing';

  return (
    <div className="space-y-5">
      <section>
        <div className="text-xs font-medium text-gray-400 mb-2">导出格式</div>
        <div className="space-y-1.5">
          {FORMATS.map((format) => {
            const checked = exportState.formats.includes(format.id);
            const missing = format.id === 'psd' ? !current?.imagePath : format.needsLayers ? !current?.layersDir : false;
            return (
              <button
                key={format.id}
                onClick={() => toggleExportFormat(format.id)}
                className={`w-full flex items-start gap-2 p-2.5 rounded-lg border text-left transition-colors ${
                  checked ? 'border-pink-500/50 bg-pink-500/10' : 'border-gray-800 bg-gray-900'
                }`}
              >
                <span
                  className={`mt-0.5 w-3.5 h-3.5 rounded border flex items-center justify-center ${
                    checked ? 'border-pink-500 bg-pink-500 text-white' : 'border-gray-600'
                  }`}
                >
                  {checked && <CheckCircle2 className="w-3 h-3" />}
                </span>
                <span className="flex-1">
                  <span className="block text-xs text-gray-200">{format.label}</span>
                  <span className="block text-[10px] text-gray-500 mt-0.5">{format.hint}</span>
                  {missing && <span className="block text-[10px] text-amber-400/80 mt-0.5">缺少前置产物</span>}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {exportState.error && (
        <p className="flex items-start gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-2.5 py-2 text-[11px] text-red-300">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          {exportState.error}
        </p>
      )}
      {exportState.blocker && (
        <p className="flex items-start gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-300">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          {exportState.blocker}
        </p>
      )}

      <button
        onClick={() => void startExport()}
        disabled={processing || exportState.formats.length === 0}
        className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-all"
      >
        {processing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Package className="w-4 h-4" />}
        {processing ? '正在导出…' : '开始导出'}
      </button>

      {job && (
        <section className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 space-y-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-gray-300">{job.characterName}</span>
            <span
              className={
                job.status === 'done' ? 'text-emerald-400' : job.status === 'error' ? 'text-red-400' : 'text-pink-300'
              }
            >
              {job.status === 'done' ? '完成' : job.status === 'error' ? '失败' : '处理中'}
            </span>
          </div>
          <div className="h-1 rounded-full bg-gray-800 overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-pink-500 to-purple-500 transition-all duration-300"
              style={{ width: `${job.progress}%` }}
            />
          </div>
          {job.downloads && job.downloads.length > 0 && (
            <ul className="space-y-1">
              {job.downloads.map((download) => (
                <li key={download.format}>
                  <button
                    onClick={() => void downloadExport(download)}
                    disabled={exportState.packingFormat !== null}
                    className="w-full flex items-center justify-between px-2 py-1.5 rounded-md border border-gray-700 bg-gray-900 text-[11px] text-gray-300 hover:border-emerald-500/40 hover:text-emerald-300 disabled:opacity-50 transition-colors"
                  >
                    <span className="truncate">{download.filename}</span>
                    {exportState.packingFormat === download.format ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
                    ) : (
                      <Download className="w-3.5 h-3.5 shrink-0" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {job.error && <p className="text-[11px] text-red-400">{job.error}</p>}
          <div className="flex gap-1.5">
            {current?.model3Url && (
              <button
                onClick={() => setStage('preview')}
                className="flex-1 px-2 py-1.5 rounded-md bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 transition-colors"
              >
                去预览
              </button>
            )}
            <button
              onClick={resetExport}
              className="flex-1 inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-md bg-gray-900 border border-gray-700 text-[11px] text-gray-400 hover:bg-gray-800 transition-colors"
            >
              <RotateCcw className="w-3 h-3" /> 清除记录
            </button>
          </div>
        </section>
      )}

      <section className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 space-y-2">
        <div className="text-xs font-medium text-gray-300 flex items-center gap-1.5">
          <Monitor className="w-3.5 h-3.5 text-blue-400" /> 桌面桌宠
        </div>
        <p className="text-[10px] text-gray-500 leading-relaxed">
          用官方 Cubism Core 验收模型后，在桌面拉起透明窗口。需要 Windows 与已导出的模型目录。
        </p>
        <button
          onClick={() => void launchPet()}
          disabled={pet.busy}
          className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-blue-500/15 border border-blue-500/40 text-blue-300 text-xs hover:bg-blue-500/25 disabled:opacity-50 transition-colors"
        >
          {pet.busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Monitor className="w-3.5 h-3.5" />}
          {pet.busy ? '正在启动…' : '启动桌宠'}
        </button>
        {pet.message && <p className="text-[10px] text-blue-200/80">{pet.message}</p>}
      </section>
    </div>
  );
}
