import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Download, Layers, Loader2, RotateCcw, Scissors, Upload } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import StageHeader from '../StageHeader';

type CanvasBg = 'checker' | 'dark' | 'light';

const BG_CLASS: Record<CanvasBg, string> = {
  checker: 'bg-[repeating-conic-gradient(#ffffff_0%_25%,#f1ecf9_0%_50%)] bg-[length:20px_20px]',
  dark: 'bg-[#14101d]',
  light: 'bg-[#f4f2f8]',
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
  // null = 跟随画框自动适配；用户拖动滑杆后固定为用户值。
  const [zoomOverride, setZoomOverride] = useState<number | null>(null);
  const [fitZoom, setFitZoom] = useState(1);
  const [compositeOnly, setCompositeOnly] = useState(false);
  const frameRef = useRef<HTMLDivElement>(null);

  const width = current?.width || 1024;
  const height = current?.height || 1024;
  const zoom = zoomOverride ?? fitZoom;

  // 分层图是整幅画布：默认按画框大小收缩，避免一进页面就被裁掉一半。
  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const update = () => {
      const pad = 40;
      const fit = Math.min((el.clientWidth - pad) / width, (el.clientHeight - pad) / height);
      setFitZoom(Math.max(0.2, Math.min(1.5, fit)));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [width, height]);

  useEffect(() => {
    setZoomOverride(null);
  }, [current?.id]);

  const visibleLayers = useMemo(
    () => (compositeOnly ? [] : layers.filter((layer) => layer.visible)),
    [layers, compositeOnly],
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <StageHeader
        icon={<Layers className="h-4 w-4" />}
        title="图层检查"
        description={
          layers.length > 0
            ? `${layers.length} 个图层 · 自上而下叠放${current?.layersDir ? '' : ' · 尚未落盘'}`
            : '分层产物会在这里逐层叠放，用来检查遮挡与顺序'
        }
        badge={
          segmentation.manifest ? (
            <span
              className={`chip ${
                segmentation.manifest.rigReady
                  ? 'border-mint-400/40 bg-mint-400/10 text-mint-600'
                  : 'border-amber-400/40 bg-amber-400/10 text-amber-700'
              }`}
            >
              {segmentation.manifest.rigReady ? '可绑定' : '缺少必需部位'}
            </span>
          ) : null
        }
        actions={
          <>
            <div className="flex overflow-hidden rounded-[10px] border border-black/[0.08] bg-ink-950/60">
              {(['checker', 'dark', 'light'] as CanvasBg[]).map((item) => (
                <button
                  key={item}
                  onClick={() => setBg(item)}
                  className={`px-2 py-1 text-[10px] transition-colors ${
                    bg === item ? 'bg-sakura-500/15 text-sakura-600' : 'text-haze-500 hover:text-haze-200'
                  }`}
                >
                  {item === 'checker' ? '棋盘' : item === 'dark' ? '深色' : '浅色'}
                </button>
              ))}
            </div>
            <button
              onClick={() => setCompositeOnly((value) => !value)}
              className={`btn px-2.5 py-1.5 ${
                compositeOnly
                  ? 'border border-iris-500/40 bg-iris-500/10 text-iris-600'
                  : 'btn-ghost'
              }`}
              title="只显示合成预览图"
            >
              合成图
            </button>
            <button
              onClick={() => void runSegmentation()}
              disabled={segmentation.running || !current?.imagePath}
              className="btn-ghost px-2.5 py-1.5"
            >
              {segmentation.running ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RotateCcw className="h-3 w-3" />
              )}
              重新分层
            </button>
            {layersPreviewUrl && (
              <a href={layersPreviewUrl} download className="btn-mint px-2.5 py-1.5">
                <Download className="h-3 w-3" /> 合成图
              </a>
            )}
          </>
        }
      />

      <div ref={frameRef} className={`stage-frame ${BG_CLASS[bg]} overflow-auto p-4`}>
        {layersLoading ? (
          <div className="flex items-center gap-2 text-xs text-haze-400">
            <Loader2 className="h-4 w-4 animate-spin text-sakura-600" /> 正在读取图层…
          </div>
        ) : layers.length === 0 ? (
          <div className="flex max-w-sm flex-col items-center text-center">
            <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl border border-black/[0.08] bg-black/[0.03]">
              <Layers className="h-6 w-6 text-haze-500" />
            </div>
            <p className="text-sm text-haze-200">还没有分层产物</p>
            <p className="mt-1 text-[11px] leading-relaxed text-haze-500">
              生成立绘时后端会自动分层；也可以在右侧上传图片重新分层，或导入已有 PSD/PNG。
            </p>
            <div className="mt-3 flex gap-2">
              <button onClick={() => setStage('generate')} className="btn-ghost">
                去生成
              </button>
              <label className="btn-soft cursor-pointer">
                <Upload className="h-3 w-3" /> 导入 PSD
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
            {layersError && <p className="mt-3 text-[11px] text-rose-600">{layersError}</p>}
          </div>
        ) : (
          <div
            className="relative shrink-0 shadow-2xl shadow-black/20 ring-1 ring-black/10"
            style={{
              width: Math.min(width * zoom, 1400),
              aspectRatio: `${width} / ${height}`,
            }}
          >
            {compositeOnly && layersPreviewUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={layersPreviewUrl} alt="合成预览" className="absolute inset-0 h-full w-full object-contain" />
            )}
            {visibleLayers.map((layer) => (
              <img
                key={layer.id}
                src={layer.imageUrl}
                alt={layer.name}
                draggable={false}
                className="absolute inset-0 h-full w-full select-none object-contain"
                style={{
                  opacity: layer.opacity,
                  mixBlendMode: layer.blendMode as CSSProperties['mixBlendMode'],
                }}
              />
            ))}
          </div>
        )}
      </div>

      <div className="meta-bar shrink-0">
        <Scissors className="h-3.5 w-3.5 shrink-0 text-haze-500" />
        <span className="shrink-0">缩放</span>
        <input
          type="range"
          min={0.2}
          max={2}
          step={0.05}
          value={zoom}
          onChange={(e) => setZoomOverride(Number(e.target.value))}
          aria-label="画布缩放"
          className="w-40 accent-sakura-500"
        />
        <span className="shrink-0 tabular-nums text-haze-300">{Math.round(zoom * 100)}%</span>
        <span className="ml-auto truncate text-right">
          {segmentation.manifest
            ? `来源 ${segmentation.manifest.source || '—'} · ${segmentation.manifest.rigReady ? '可绑定' : '缺少必需部位'}`
            : current?.layersDir || '—'}
        </span>
      </div>
    </div>
  );
}
