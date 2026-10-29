import { useMemo, useState, type CSSProperties } from 'react';
import { Download, Layers, Loader2, RotateCcw, Scissors, Upload } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';

type CanvasBg = 'checker' | 'dark' | 'light';

const BG_CLASS: Record<CanvasBg, string> = {
  checker: 'bg-[repeating-conic-gradient(#24242f_0%_25%,#16161d_0%_50%)] bg-[length:20px_20px]',
  dark: 'bg-[#0b0b10]',
  light: 'bg-[#f4f4f6]',
};

export default function LayersStage() {
  const {
    current,
    layers,
    layersLoading,
    layersError,
    layersPreviewUrl,
    segmentation,
    runSegmentation,
    setStage,
    importPSD,
  } = useWorkbench();
  const [bg, setBg] = useState<CanvasBg>('checker');
  const [zoom, setZoom] = useState(1);
  const [compositeOnly, setCompositeOnly] = useState(false);

  const width = current?.width || 1024;
  const height = current?.height || 1024;
  const visibleLayers = useMemo(
    () => (compositeOnly ? [] : layers.filter((layer) => layer.visible)),
    [layers, compositeOnly],
  );

  return (
    <div className="h-full flex flex-col p-4 gap-3 min-h-0">
      <header className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-white">图层检查</h2>
          <p className="text-[11px] text-gray-500">
            {layers.length > 0
              ? `${layers.length} 个图层 · 自上而下叠放${current?.layersDir ? '' : ' · 尚未落盘'}`
              : '分层产物会在这里逐层叠放，用来检查遮挡与顺序'}
          </p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <div className="flex rounded-lg overflow-hidden border border-gray-700">
            {(['checker', 'dark', 'light'] as CanvasBg[]).map((item) => (
              <button
                key={item}
                onClick={() => setBg(item)}
                className={`px-2 py-1 text-[10px] transition-colors ${
                  bg === item ? 'bg-pink-500/20 text-pink-300' : 'bg-gray-900 text-gray-500 hover:text-gray-300'
                }`}
              >
                {item === 'checker' ? '棋盘' : item === 'dark' ? '深色' : '浅色'}
              </button>
            ))}
          </div>
          <button
            onClick={() => setCompositeOnly((value) => !value)}
            className={`px-2 py-1 rounded-lg border text-[10px] transition-colors ${
              compositeOnly
                ? 'border-purple-500/50 bg-purple-500/10 text-purple-300'
                : 'border-gray-700 bg-gray-900 text-gray-400 hover:text-gray-200'
            }`}
            title="只显示合成预览图"
          >
            合成图
          </button>
          <button
            onClick={() => void runSegmentation()}
            disabled={segmentation.running || !current?.imagePath}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-gray-700 bg-gray-900 text-[10px] text-gray-300 hover:bg-gray-800 disabled:opacity-40 transition-colors"
          >
            {segmentation.running ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
            重新分层
          </button>
          {layersPreviewUrl && (
            <a
              href={layersPreviewUrl}
              download
              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-[10px] text-emerald-300 hover:bg-emerald-500/25 transition-colors"
            >
              <Download className="w-3 h-3" /> 合成图
            </a>
          )}
        </div>
      </header>

      <div className={`flex-1 min-h-0 rounded-xl border border-gray-800 ${BG_CLASS[bg]} overflow-auto flex items-center justify-center p-4`}>
        {layersLoading ? (
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <Loader2 className="w-4 h-4 animate-spin" /> 正在读取图层…
          </div>
        ) : layers.length === 0 ? (
          <div className="flex flex-col items-center text-center max-w-sm">
            <div className="w-14 h-14 rounded-2xl bg-gray-900 border border-gray-700 flex items-center justify-center mb-3">
              <Layers className="w-6 h-6 text-gray-500" />
            </div>
            <p className="text-sm text-gray-300">还没有分层产物</p>
            <p className="text-[11px] text-gray-500 mt-1 leading-relaxed">
              生成立绘时后端会自动分层；也可以在右侧上传图片重新分层，或导入已有 PSD/PNG。
            </p>
            <div className="flex gap-2 mt-3">
              <button
                onClick={() => setStage('generate')}
                className="px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 transition-colors"
              >
                去生成
              </button>
              <label className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 cursor-pointer transition-colors">
                <Upload className="w-3 h-3" /> 导入 PSD
                <input
                  type="file"
                  accept=".psd"
                  className="hidden"
                  onChange={(e) => {
                    void importPSD(e.target.files?.[0] || null);
                    e.target.value = '';
                  }}
                />
              </label>
            </div>
            {layersError && <p className="mt-3 text-[11px] text-red-400">{layersError}</p>}
          </div>
        ) : (
          <div
            className="relative shrink-0 shadow-2xl shadow-black/40"
            style={{
              width: Math.min(width * zoom, 1400),
              aspectRatio: `${width} / ${height}`,
            }}
          >
            {compositeOnly && layersPreviewUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={layersPreviewUrl} alt="合成预览" className="absolute inset-0 w-full h-full object-contain" />
            )}
            {visibleLayers.map((layer) => (
              <img
                key={layer.id}
                src={layer.imageUrl}
                alt={layer.name}
                draggable={false}
                className="absolute inset-0 w-full h-full object-contain select-none"
                style={{
                  opacity: layer.opacity,
                  mixBlendMode: layer.blendMode as CSSProperties['mixBlendMode'],
                }}
              />
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center gap-3 text-[11px] text-gray-500">
        <Scissors className="w-3.5 h-3.5 shrink-0" />
        <span className="shrink-0">缩放</span>
        <input
          type="range"
          min={0.4}
          max={2}
          step={0.05}
          value={zoom}
          onChange={(e) => setZoom(Number(e.target.value))}
          className="w-40 accent-pink-500"
        />
        <span className="tabular-nums">{Math.round(zoom * 100)}%</span>
        <span className="ml-auto">
          {segmentation.manifest
            ? `来源 ${segmentation.manifest.source || '—'} · ${segmentation.manifest.rigReady ? '可绑定' : '缺少必需部位'}`
            : current?.layersDir || '—'}
        </span>
      </div>
    </div>
  );
}
