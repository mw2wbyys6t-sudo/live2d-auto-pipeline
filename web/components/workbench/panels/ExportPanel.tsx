import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Download, Loader2, Monitor, Package, RotateCcw } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { EXPORT_FORMATS as FORMATS } from '../../../lib/catalog';

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
  const [modelName, setModelName] = useState('');

  // 切作品时带上作品名做默认值；用户改了就按改的走。
  useEffect(() => {
    setModelName(current?.name || '');
  }, [current?.id, current?.name]);

  return (
    <div className="space-y-5">
      <section>
        <div className="section-label mb-2">模型名称</div>
        <input
          className="field"
          value={modelName}
          onChange={(event) => setModelName(event.target.value)}
          placeholder="例如：默认版 / 高马尾 v2"
          maxLength={40}
        />
        <p className="mt-1 text-[10px] leading-relaxed text-haze-500">
          同一作品可以导出多个模型，各自命名、互不覆盖。
        </p>
        {current && current.models.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {current.models.map((model) => (
              <span key={model.id} className="chip">
                <Package className="h-2.5 w-2.5" />
                {model.name}
              </span>
            ))}
          </div>
        )}
      </section>

      <section>
        <div className="section-label mb-2">导出格式</div>
        <div className="space-y-1.5">
          {FORMATS.map((format) => {
            const checked = exportState.formats.includes(format.id);
            const missing = format.id === 'psd' ? !current?.imagePath : format.needsLayers ? !current?.layersDir : false;
            return (
              <button
                key={format.id}
                onClick={() => toggleExportFormat(format.id)}
                className={`flex w-full items-start gap-2 rounded-xl border p-2.5 text-left transition-colors ${
                  checked
                    ? 'border-sakura-500/40 bg-sakura-500/[0.08]'
                    : 'border-black/[0.07] bg-black/[0.02] hover:border-black/[0.14]'
                }`}
              >
                <span
                  className={`mt-0.5 flex h-3.5 w-3.5 items-center justify-center rounded border ${
                    checked ? 'border-sakura-500 bg-sakura-500 text-white' : 'border-black/20'
                  }`}
                >
                  {checked && <CheckCircle2 className="h-3 w-3" />}
                </span>
                <span className="flex-1">
                  <span className="block text-xs text-haze-200">{format.label}</span>
                  <span className="mt-0.5 block text-[10px] text-haze-500">{format.hint}</span>
                  {missing && <span className="mt-0.5 block text-[10px] text-amber-600/80">缺少前置产物</span>}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {exportState.error && (
        <p className="flex items-start gap-1.5 rounded-xl border border-rose-400/30 bg-rose-500/10 px-2.5 py-2 text-[11px] text-rose-600">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {exportState.error}
        </p>
      )}
      {exportState.blocker && (
        <p className="flex items-start gap-1.5 rounded-xl border border-amber-400/30 bg-amber-400/10 px-2.5 py-2 text-[11px] text-amber-700">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {exportState.blocker}
        </p>
      )}

      <button
        onClick={() => void startExport(modelName)}
        disabled={processing || exportState.formats.length === 0}
        className="btn-primary w-full py-2.5 text-sm"
      >
        {processing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Package className="h-4 w-4" />}
        {processing ? '正在导出…' : '开始导出'}
      </button>

      {job && (
        <section className="card space-y-2 p-3">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-haze-200">{job.characterName}</span>
            <span
              className={
                job.status === 'done'
                  ? 'text-mint-600'
                  : job.status === 'error'
                    ? 'text-rose-600'
                    : 'text-sakura-600'
              }
            >
              {job.status === 'done' ? '完成' : job.status === 'error' ? '失败' : '处理中'}
            </span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-black/[0.05]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-sakura-500 to-iris-500 transition-all duration-300"
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
                    className="flex w-full items-center justify-between rounded-lg border border-black/[0.08] bg-black/[0.02] px-2 py-1.5 text-[11px] text-haze-300 transition-colors hover:border-mint-400/40 hover:text-mint-600 disabled:opacity-50"
                  >
                    <span className="truncate">{download.filename}</span>
                    {exportState.packingFormat === download.format ? (
                      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                    ) : (
                      <Download className="h-3.5 w-3.5 shrink-0" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {job.error && <p className="text-[11px] text-rose-600">{job.error}</p>}
          <div className="flex gap-1.5">
            {current?.model3Url && (
              <button onClick={() => setStage('preview')} className="btn-ghost flex-1 py-1.5">
                去预览
              </button>
            )}
            <button onClick={resetExport} className="btn-ghost flex-1 py-1.5">
              <RotateCcw className="h-3 w-3" /> 清除记录
            </button>
          </div>
        </section>
      )}

      <section className="card space-y-2 p-3">
        <div className="flex items-center gap-1.5 text-xs font-medium text-haze-200">
          <Monitor className="h-3.5 w-3.5 text-iris-600" /> 桌面桌宠
        </div>
        <p className="text-[10px] leading-relaxed text-haze-500">
          用官方 Cubism Core 验收模型后，在桌面拉起透明窗口。需要 Windows 与已导出的模型目录。
        </p>
        <button onClick={() => void launchPet()} disabled={pet.busy} className="btn-ghost w-full py-2">
          {pet.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Monitor className="h-3.5 w-3.5" />}
          {pet.busy ? '正在启动…' : '启动桌宠'}
        </button>
        {pet.message && <p className="text-[10px] text-iris-200/80">{pet.message}</p>}
      </section>
    </div>
  );
}
