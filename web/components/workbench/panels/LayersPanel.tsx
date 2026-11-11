import { useRef } from 'react';
import { ArrowDown, ArrowUp, Eye, EyeOff, FileArchive, ImagePlus, Loader2, Scissors } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { LAYER_PRESETS as PRESETS } from '../../../lib/catalog';
import ImageUploader from '../../ImageUploader';

export default function LayersPanel() {
  const {
    current,
    sourceUpload,
    setSourceFile,
    segmentation,
    setSegmentationMethod,
    setImportPreset,
    runSegmentation,
    importPSD,
    importPNGs,
    layers,
    layersLoading,
    layersError,
    selectedLayer,
    setSelectedLayerId,
    updateLayer,
    toggleLayerVisibility,
    moveLayer,
  } = useWorkbench();

  const psdRef = useRef<HTMLInputElement>(null);
  const pngRef = useRef<HTMLInputElement>(null);
  const canSegment = !!sourceUpload || !!current?.imagePath;
  const busy = segmentation.running || segmentation.importing;

  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <div className="section-label">分层来源</div>
        <ImageUploader
          value={sourceUpload?.url || current?.imageUrl || null}
          onChange={setSourceFile}
          useExternalPreview
          label="拖入或点击上传立绘（不选则用当前作品主图）"
        />
        <div className="flex gap-1 rounded-xl border border-black/[0.07] bg-ink-950/50 p-1">
          {(['semantic', 'kmeans'] as const).map((method) => (
            <button
              key={method}
              onClick={() => setSegmentationMethod(method)}
              className={`flex-1 rounded-lg py-1.5 text-xs transition-colors ${
                segmentation.method === method
                  ? 'bg-sakura-500/15 text-sakura-600 ring-1 ring-inset ring-sakura-500/30'
                  : 'text-haze-400 hover:bg-black/[0.04] hover:text-haze-200'
              }`}
            >
              {method === 'semantic' ? '语义分层' : 'K-means 快速'}
            </button>
          ))}
        </div>
        <button onClick={() => void runSegmentation()} disabled={!canSegment || busy} className="btn-primary w-full py-2">
          {segmentation.running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Scissors className="h-4 w-4" />}
          {segmentation.running ? '正在分层…' : sourceUpload ? '对上传图片分层' : '重新分层当前立绘'}
        </button>
        {!canSegment && <p className="text-[10px] text-haze-600">先在「生成」产出一张立绘，或上传一张图片。</p>}
        {segmentation.error && (
          <p className="rounded-xl border border-amber-400/30 bg-amber-400/10 px-2.5 py-2 text-[11px] text-amber-700/90">
            {segmentation.error}
          </p>
        )}
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="section-label">导入已有分层</div>
          <div className="flex gap-1">
            {PRESETS.map((preset) => (
              <button
                key={preset.id}
                onClick={() => setImportPreset(preset.id)}
                className={`rounded-full border px-1.5 py-0.5 text-[10px] transition-colors ${
                  segmentation.importPreset === preset.id
                    ? 'border-iris-500/40 bg-iris-500/10 text-iris-600'
                    : 'border-black/[0.08] bg-black/[0.02] text-haze-500 hover:text-haze-300'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <button onClick={() => psdRef.current?.click()} disabled={segmentation.importing} className="btn-ghost py-2">
            {segmentation.importing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <FileArchive className="h-3.5 w-3.5" />
            )}
            导入 PSD
          </button>
          <button onClick={() => pngRef.current?.click()} disabled={segmentation.importing} className="btn-ghost py-2">
            {segmentation.importing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ImagePlus className="h-3.5 w-3.5" />
            )}
            导入 PNG
          </button>
        </div>
        <input
          ref={psdRef}
          type="file"
          accept=".psd"
          className="hidden"
          onChange={(e) => {
            void importPSD(e.target.files?.[0] || null);
            e.target.value = '';
          }}
        />
        <input
          ref={pngRef}
          type="file"
          accept="image/png"
          multiple
          className="hidden"
          onChange={(e) => {
            void importPNGs(e.target.files);
            e.target.value = '';
          }}
        />
        {segmentation.importSummary && (
          <p className="text-[10px] text-haze-500">{segmentation.importSummary}</p>
        )}
      </section>

      {segmentation.manifest && (
        <section className="card space-y-1 p-2.5 text-[11px]">
          <div className="flex items-center justify-between text-haze-400">
            <span>分层清单</span>
            <span className={segmentation.manifest.rigReady ? 'text-mint-600' : 'text-amber-600'}>
              {segmentation.manifest.rigReady ? '可绑定' : '缺少必需部位'}
            </span>
          </div>
          <p className="text-haze-600">
            来源 {segmentation.manifest.source || '—'}
            {segmentation.manifest.missing.length > 0 && ` · 缺少：${segmentation.manifest.missing.join('、')}`}
          </p>
          {segmentation.manifest.warnings.length > 0 && (
            <p className="text-amber-600/80">提醒：{segmentation.manifest.warnings.join('；')}</p>
          )}
          {segmentation.manifest.url && (
            <a href={segmentation.manifest.url} target="_blank" rel="noreferrer" className="text-sakura-600 hover:underline">
              查看 manifest
            </a>
          )}
        </section>
      )}

      <section>
        <div className="mb-2 flex items-center justify-between">
          <div className="section-label">图层（顶层在上）</div>
          <span className="text-[10px] tabular-nums text-haze-600">{layers.length} 层</span>
        </div>
        {layersLoading ? (
          <p className="flex items-center gap-2 text-[11px] text-haze-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在读取图层…
          </p>
        ) : layersError ? (
          <p className="text-[11px] text-rose-600">{layersError}</p>
        ) : layers.length === 0 ? (
          <p className="text-[11px] text-haze-600">尚未分层：运行分层或导入 PSD/PNG。</p>
        ) : (
          <ul className="space-y-1">
            {[...layers].reverse().map((layer) => {
              const position = layers.findIndex((item) => item.id === layer.id);
              const active = selectedLayer?.id === layer.id;
              return (
                <li
                  key={layer.id}
                  className={`flex cursor-pointer items-center gap-1.5 rounded-lg border px-2 py-1.5 text-[11px] transition-colors ${
                    active
                      ? 'border-sakura-500/40 bg-sakura-500/[0.08] text-haze-100'
                      : 'border-transparent bg-black/[0.02] text-haze-400 hover:bg-black/[0.05]'
                  }`}
                  onClick={() => setSelectedLayerId(layer.id)}
                >
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleLayerVisibility(layer.id);
                    }}
                    className="text-haze-500 transition-colors hover:text-haze-100"
                    title={layer.visible ? '隐藏图层' : '显示图层'}
                  >
                    {layer.visible ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                  </button>
                  <span className={`flex-1 truncate ${layer.visible ? '' : 'line-through opacity-50'}`} title={layer.name}>
                    {layer.name}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      const above = layers[position + 1];
                      if (above) moveLayer(layer.id, above.id);
                    }}
                    disabled={position === layers.length - 1}
                    className="text-haze-600 transition-colors hover:text-haze-100 disabled:opacity-30"
                    title="上移一层"
                  >
                    <ArrowUp className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      const below = layers[position - 1];
                      if (below) moveLayer(layer.id, below.id);
                    }}
                    disabled={position === 0}
                    className="text-haze-600 transition-colors hover:text-haze-100 disabled:opacity-30"
                    title="下移一层"
                  >
                    <ArrowDown className="h-3.5 w-3.5" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {selectedLayer && (
        <section className="card space-y-2 p-2.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="truncate text-haze-200" title={selectedLayer.name}>
              {selectedLayer.name}
            </span>
            <span className="tabular-nums text-haze-500">{Math.round(selectedLayer.opacity * 100)}%</span>
          </div>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={selectedLayer.opacity}
            onChange={(e) => updateLayer(selectedLayer.id, { opacity: Number(e.target.value) })}
            aria-label="图层不透明度"
            className="w-full accent-sakura-500"
          />
        </section>
      )}

      {current?.psdUrl && (
        <a href={current.psdUrl} download className="btn-mint w-full py-2">
          下载 PSD
        </a>
      )}
    </div>
  );
}
