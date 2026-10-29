import { useRef } from 'react';
import { ArrowDown, ArrowUp, Eye, EyeOff, FileArchive, ImagePlus, Loader2, Scissors } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import ImageUploader from '../../ImageUploader';

const PRESETS: Array<{ id: 'minimal' | 'default' | 'full'; label: string }> = [
  { id: 'minimal', label: '精简' },
  { id: 'default', label: '标准' },
  { id: 'full', label: '完整' },
];

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
        <div className="text-xs font-medium text-gray-400">分层来源</div>
        <ImageUploader
          value={sourceUpload?.url || current?.imageUrl || null}
          onChange={setSourceFile}
          useExternalPreview
          label="拖入或点击上传立绘（不选则用当前作品主图）"
        />
        <div className="flex gap-1">
          {(['semantic', 'kmeans'] as const).map((method) => (
            <button
              key={method}
              onClick={() => setSegmentationMethod(method)}
              className={`flex-1 py-1.5 rounded-md text-xs border transition-colors ${
                segmentation.method === method
                  ? 'bg-pink-500/20 border-pink-500/50 text-pink-300'
                  : 'bg-gray-900 border-gray-800 text-gray-400 hover:border-gray-700'
              }`}
            >
              {method === 'semantic' ? '语义分层' : 'K-means 快速'}
            </button>
          ))}
        </div>
        <button
          onClick={() => void runSegmentation()}
          disabled={!canSegment || busy}
          className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-all"
        >
          {segmentation.running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Scissors className="w-4 h-4" />}
          {segmentation.running ? '正在分层…' : sourceUpload ? '对上传图片分层' : '重新分层当前立绘'}
        </button>
        {!canSegment && <p className="text-[10px] text-gray-600">先在「生成」产出一张立绘，或上传一张图片。</p>}
        {segmentation.error && (
          <p className="text-[11px] text-amber-400/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-2.5 py-2">
            {segmentation.error}
          </p>
        )}
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-xs font-medium text-gray-400">导入已有分层</div>
          <div className="flex gap-1">
            {PRESETS.map((preset) => (
              <button
                key={preset.id}
                onClick={() => setImportPreset(preset.id)}
                className={`px-1.5 py-0.5 rounded text-[10px] border transition-colors ${
                  segmentation.importPreset === preset.id
                    ? 'border-purple-500/50 bg-purple-500/10 text-purple-300'
                    : 'border-gray-800 bg-gray-900 text-gray-500'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <button
            onClick={() => psdRef.current?.click()}
            disabled={segmentation.importing}
            className="inline-flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 disabled:opacity-50 transition-colors"
          >
            {segmentation.importing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileArchive className="w-3.5 h-3.5" />}
            导入 PSD
          </button>
          <button
            onClick={() => pngRef.current?.click()}
            disabled={segmentation.importing}
            className="inline-flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 disabled:opacity-50 transition-colors"
          >
            {segmentation.importing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ImagePlus className="w-3.5 h-3.5" />}
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
          <p className="text-[10px] text-gray-500">{segmentation.importSummary}</p>
        )}
      </section>

      {segmentation.manifest && (
        <section className="rounded-lg border border-gray-800 bg-gray-900/60 p-2.5 text-[11px] space-y-1">
          <div className="flex items-center justify-between text-gray-400">
            <span>分层清单</span>
            <span className={segmentation.manifest.rigReady ? 'text-emerald-400' : 'text-amber-400'}>
              {segmentation.manifest.rigReady ? '可绑定' : '缺少必需部位'}
            </span>
          </div>
          <p className="text-gray-600">
            来源 {segmentation.manifest.source || '—'}
            {segmentation.manifest.missing.length > 0 && ` · 缺少：${segmentation.manifest.missing.join('、')}`}
          </p>
          {segmentation.manifest.warnings.length > 0 && (
            <p className="text-amber-500/80">提醒：{segmentation.manifest.warnings.join('；')}</p>
          )}
          {segmentation.manifest.url && (
            <a href={segmentation.manifest.url} target="_blank" rel="noreferrer" className="text-pink-400 hover:underline">
              查看 manifest
            </a>
          )}
        </section>
      )}

      <section>
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-medium text-gray-400">图层（顶层在上）</div>
          <span className="text-[10px] text-gray-600">{layers.length} 层</span>
        </div>
        {layersLoading ? (
          <p className="flex items-center gap-2 text-[11px] text-gray-500">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> 正在读取图层…
          </p>
        ) : layersError ? (
          <p className="text-[11px] text-red-400">{layersError}</p>
        ) : layers.length === 0 ? (
          <p className="text-[11px] text-gray-600">尚未分层：运行分层或导入 PSD/PNG。</p>
        ) : (
          <ul className="space-y-1">
            {[...layers].reverse().map((layer) => {
              const position = layers.findIndex((item) => item.id === layer.id);
              return (
                <li
                  key={layer.id}
                  className={`flex items-center gap-1.5 px-2 py-1.5 rounded-md border text-[11px] cursor-pointer transition-colors ${
                    selectedLayer?.id === layer.id
                      ? 'border-pink-500/50 bg-pink-500/10 text-white'
                      : 'border-gray-800 bg-gray-900 text-gray-400 hover:border-gray-700'
                  }`}
                  onClick={() => setSelectedLayerId(layer.id)}
                >
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleLayerVisibility(layer.id);
                    }}
                    className="text-gray-500 hover:text-gray-200"
                    title={layer.visible ? '隐藏图层' : '显示图层'}
                  >
                    {layer.visible ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
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
                    className="text-gray-600 hover:text-gray-200 disabled:opacity-30"
                    title="上移一层"
                  >
                    <ArrowUp className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      const below = layers[position - 1];
                      if (below) moveLayer(layer.id, below.id);
                    }}
                    disabled={position === 0}
                    className="text-gray-600 hover:text-gray-200 disabled:opacity-30"
                    title="下移一层"
                  >
                    <ArrowDown className="w-3.5 h-3.5" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {selectedLayer && (
        <section className="rounded-lg border border-gray-800 bg-gray-900/60 p-2.5 space-y-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-gray-300 truncate" title={selectedLayer.name}>
              {selectedLayer.name}
            </span>
            <span className="text-gray-500 tabular-nums">{Math.round(selectedLayer.opacity * 100)}%</span>
          </div>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={selectedLayer.opacity}
            onChange={(e) => updateLayer(selectedLayer.id, { opacity: Number(e.target.value) })}
            className="w-full accent-pink-500"
          />
        </section>
      )}

      {current?.psdUrl && (
        <a
          href={current.psdUrl}
          download
          className="block text-center px-3 py-2 rounded-lg bg-emerald-500/10 border border-emerald-500/40 text-emerald-300 text-xs hover:bg-emerald-500/20 transition-colors"
        >
          下载 PSD
        </a>
      )}
    </div>
  );
}
