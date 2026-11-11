import { CheckCircle2, Circle, Download, Loader2, Monitor, Package } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import StageHeader from '../StageHeader';

export default function ExportStage() {
  const { current, exportState, startExport, downloadExport, pet, launchPet, setStage } = useWorkbench();
  const job = exportState.job;
  const processing = job?.status === 'processing';

  const artifacts = [
    { label: '角色立绘', ready: !!current?.imagePath, hint: current?.imagePath ? '已保存' : '先生成一张立绘' },
    { label: '分层目录', ready: !!current?.layersDir, hint: current?.layersDir ? '可用于构建模型' : '还没有分层产物' },
    { label: 'PSD', ready: !!current?.psdPath, hint: current?.psdPath ? '可在图层面板下载' : 'PSD 可选' },
    { label: 'Live2D 模型', ready: !!current?.model3Url, hint: current?.model3Url ? '可直接预览' : '尚未导出' },
  ];

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-4">
      <StageHeader
        icon={<Package className="h-4 w-4" />}
        title="导出与交付"
        description="从当前作品构建模型包，或把模型交给桌面桌宠。"
        badge={
          job?.status === 'done' ? (
            <span className="chip border-mint-400/40 bg-mint-400/10 text-mint-600">已导出</span>
          ) : job?.status === 'processing' ? (
            <span className="chip chip-active">处理中 {job.progress}%</span>
          ) : null
        }
      />

      <div className="grid grid-cols-2 gap-2">
        {artifacts.map((item) => (
          <div key={item.label} className="card flex items-start gap-2 px-3 py-2">
            {item.ready ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-mint-600" />
            ) : (
              <Circle className="mt-0.5 h-4 w-4 shrink-0 text-haze-600" />
            )}
            <div className="min-w-0">
              <p className={item.ready ? 'text-xs text-haze-200' : 'text-xs text-haze-500'}>{item.label}</p>
              <p className="truncate text-[10px] text-haze-600">{item.hint}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="stage-frame min-h-[220px] p-5">
        {processing ? (
          <div className="w-full max-w-md space-y-3">
            <p className="flex items-center justify-center gap-2 text-sm text-haze-200">
              <Loader2 className="h-4 w-4 animate-spin text-sakura-600" /> 正在构建 {job?.formats.join('、')}…
            </p>
            <div className="h-1.5 overflow-hidden rounded-full bg-black/[0.05]">
              <div
                className="h-full rounded-full bg-gradient-to-r from-sakura-500 to-iris-500 transition-all duration-300"
                style={{ width: `${job?.progress ?? 0}%` }}
              />
            </div>
            <p className="text-center text-[10px] tabular-nums text-haze-600">{job?.progress ?? 0}%</p>
          </div>
        ) : job && job.status === 'done' && job.downloads?.length ? (
          <div className="w-full max-w-md space-y-2">
            <p className="flex items-center justify-center gap-2 text-sm text-mint-600">
              <CheckCircle2 className="h-4 w-4" /> 导出完成
            </p>
            {job.downloads.map((download) => (
              <button
                key={download.format}
                onClick={() => void downloadExport(download)}
                disabled={exportState.packingFormat !== null}
                className="flex w-full items-center justify-between rounded-[10px] border border-black/[0.08] bg-black/[0.03] px-3 py-2 text-xs text-haze-200 transition-colors hover:border-mint-400/40 hover:text-mint-600 disabled:opacity-50"
              >
                <span className="truncate">{download.filename}</span>
                {exportState.packingFormat === download.format ? (
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                ) : (
                  <Download className="h-4 w-4 shrink-0" />
                )}
              </button>
            ))}
            {job.error && <p className="text-[11px] text-rose-600">{job.error}</p>}
            <div className="flex justify-center gap-2 pt-1">
              {current?.model3Url && (
                <button onClick={() => setStage('preview')} className="btn-ghost">
                  去预览
                </button>
              )}
              <button onClick={() => void startExport()} className="btn-ghost">
                再导出一次
              </button>
            </div>
          </div>
        ) : (
          <div className="max-w-sm">
            <div className="ring-gradient mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-sakura-500/20 to-iris-500/20">
              <Package className="h-6 w-6 text-sakura-600" />
            </div>
            <p className="text-center text-sm text-haze-200">
              {current?.layersDir ? '分层已就绪，可以构建 Live2D 模型包' : '先生成并分层，才能导出模型'}
            </p>
            <p className="mt-1 text-center text-[11px] leading-relaxed text-haze-500">
              在右侧勾选导出格式后开始；模型包会通过官方 Cubism Core 验收，再打包为 zip。
            </p>
            <div className="mt-4 flex justify-center">
              <button
                onClick={() => void startExport()}
                disabled={!current || exportState.formats.length === 0}
                className="btn-primary px-4 py-2 text-xs"
              >
                <Package className="h-3.5 w-3.5" /> 开始导出
              </button>
            </div>
            {exportState.error && <p className="mt-2 text-center text-[11px] text-rose-600">{exportState.error}</p>}
          </div>
        )}
      </div>

      <div className="card flex shrink-0 items-center gap-3 p-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-iris-500/30 bg-iris-500/10">
          <Monitor className="h-4 w-4 text-iris-600" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-haze-200">桌面桌宠</p>
          <p className="truncate text-[10px] text-haze-500">
            {pet.message || '验收模型后在桌面拉起透明窗口（Windows）'}
          </p>
        </div>
        <button onClick={() => void launchPet()} disabled={pet.busy} className="btn-ghost shrink-0 px-2.5 py-1.5">
          {pet.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Monitor className="h-3.5 w-3.5" />}
          {pet.busy ? '启动中…' : '启动'}
        </button>
      </div>
    </div>
  );
}
