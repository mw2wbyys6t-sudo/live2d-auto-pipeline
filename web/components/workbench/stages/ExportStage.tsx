import { CheckCircle2, Circle, Download, Loader2, Monitor, Package } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';

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
    <div className="h-full flex flex-col p-4 gap-3 min-h-0 overflow-y-auto">
      <header>
        <h2 className="text-sm font-semibold text-white">导出与交付</h2>
        <p className="text-[11px] text-gray-500">从当前作品构建模型包，或把模型交给桌面桌宠。</p>
      </header>

      <div className="grid grid-cols-2 gap-2">
        {artifacts.map((item) => (
          <div key={item.label} className="flex items-start gap-2 rounded-lg border border-gray-800 bg-[#12121a] px-3 py-2">
            {item.ready ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            ) : (
              <Circle className="w-4 h-4 text-gray-600 shrink-0 mt-0.5" />
            )}
            <div className="min-w-0">
              <p className={item.ready ? 'text-xs text-gray-200' : 'text-xs text-gray-500'}>{item.label}</p>
              <p className="text-[10px] text-gray-600 truncate">{item.hint}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="flex-1 min-h-[220px] rounded-xl border border-gray-800 bg-[#0b0b10] p-5 flex flex-col items-center justify-center text-center">
        {processing ? (
          <div className="w-full max-w-md space-y-3">
            <p className="flex items-center justify-center gap-2 text-sm text-gray-300">
              <Loader2 className="w-4 h-4 animate-spin text-pink-400" /> 正在构建 {job?.formats.join('、')}…
            </p>
            <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-pink-500 to-purple-500 transition-all duration-300"
                style={{ width: `${job?.progress ?? 0}%` }}
              />
            </div>
            <p className="text-[10px] text-gray-600 tabular-nums">{job?.progress ?? 0}%</p>
          </div>
        ) : job && job.status === 'done' && job.downloads?.length ? (
          <div className="w-full max-w-md space-y-2">
            <p className="flex items-center justify-center gap-2 text-sm text-emerald-300">
              <CheckCircle2 className="w-4 h-4" /> 导出完成
            </p>
            {job.downloads.map((download) => (
              <button
                key={download.format}
                onClick={() => void downloadExport(download)}
                disabled={exportState.packingFormat !== null}
                className="w-full flex items-center justify-between px-3 py-2 rounded-lg border border-gray-700 bg-gray-900 text-xs text-gray-200 hover:border-emerald-500/40 hover:text-emerald-300 disabled:opacity-50 transition-colors"
              >
                <span className="truncate">{download.filename}</span>
                {exportState.packingFormat === download.format ? (
                  <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                ) : (
                  <Download className="w-4 h-4 shrink-0" />
                )}
              </button>
            ))}
            {job.error && <p className="text-[11px] text-red-400">{job.error}</p>}
            <div className="flex justify-center gap-2 pt-1">
              {current?.model3Url && (
                <button
                  onClick={() => setStage('preview')}
                  className="px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 transition-colors"
                >
                  去预览
                </button>
              )}
              <button
                onClick={() => void startExport()}
                className="px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 transition-colors"
              >
                再导出一次
              </button>
            </div>
          </div>
        ) : (
          <div className="max-w-sm">
            <div className="w-14 h-14 mx-auto rounded-2xl bg-gradient-to-br from-pink-500/20 to-purple-500/20 border border-pink-500/30 flex items-center justify-center mb-3">
              <Package className="w-6 h-6 text-pink-400" />
            </div>
            <p className="text-sm text-gray-300">
              {current?.layersDir ? '分层已就绪，可以构建 Live2D 模型包' : '先生成并分层，才能导出模型'}
            </p>
            <p className="text-[11px] text-gray-500 mt-1 leading-relaxed">
              在右侧勾选导出格式后开始；模型包会通过官方 Cubism Core 验收，再打包为 zip。
            </p>
            <button
              onClick={() => void startExport()}
              disabled={!current || exportState.formats.length === 0}
              className="mt-4 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            >
              <Package className="w-3.5 h-3.5" /> 开始导出
            </button>
            {exportState.error && <p className="mt-2 text-[11px] text-red-400">{exportState.error}</p>}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-gray-800 bg-[#12121a] p-4 flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-blue-500/15 border border-blue-500/40 flex items-center justify-center shrink-0">
          <Monitor className="w-4 h-4 text-blue-300" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-gray-200">桌面桌宠</p>
          <p className="text-[10px] text-gray-500 truncate">
            {pet.message || '验收模型后在桌面拉起透明窗口（Windows）'}
          </p>
        </div>
        <button
          onClick={() => void launchPet()}
          disabled={pet.busy}
          className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-500/15 border border-blue-500/40 text-[11px] text-blue-300 hover:bg-blue-500/25 disabled:opacity-50 transition-colors"
        >
          {pet.busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Monitor className="w-3.5 h-3.5" />}
          {pet.busy ? '启动中…' : '启动'}
        </button>
      </div>
    </div>
  );
}
