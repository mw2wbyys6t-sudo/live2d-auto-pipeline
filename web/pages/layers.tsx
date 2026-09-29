import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Upload,
  Eye,
  EyeOff,
  GripVertical,
  Download,
  ZoomIn,
  ZoomOut,
  Grid3x3,
  Layers as LayersIcon,
  RefreshCw,
  AlertCircle,
  Image as ImageIcon,
} from 'lucide-react';
import type { NextPage } from 'next';
import Link from 'next/link';
import type { BlendMode, LayerInfo, SegmentationMethod } from '../types';
import type { SegmentedLayer } from '../lib/api-client';
import LayerCanvas, { getCheckerboardStyle } from '../components/LayerCanvas';
import ImageUploader from '../components/ImageUploader';
import LoadingSpinner from '../components/LoadingSpinner';
import { LayerRenderer } from '../lib/layer-renderer';
import { apiClient, type ImportResult } from '../lib/api-client';

const BLEND_MODES: BlendMode[] = [
  'normal',
  'multiply',
  'screen',
  'overlay',
  'darken',
  'lighten',
  'color-dodge',
  'color-burn',
  'soft-light',
  'hard-light',
];

/**
 * Map real backend segmentation output into the UI's LayerInfo shape.
 * Each layer PNG is full-canvas RGBA, so bounds span the whole canvas.
 */
function toLayerInfo(segments: SegmentedLayer[], w: number, h: number): LayerInfo[] {
  return segments.map((s, i) => ({
    id: `layer-${i}`,
    name: s.name || `layer_${i}`,
    index: i,
    visible: true,
    opacity: 1,
    blendMode: 'normal' as BlendMode,
    offsetX: 0,
    offsetY: 0,
    width: w,
    height: h,
    imageUrl: s.url,
    bounds: { x: 0, y: 0, width: w, height: h },
    isGroup: false,
  }));
}

const LayersPage: NextPage = () => {
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [layers, setLayers] = useState<LayerInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [method, setMethod] = useState<SegmentationMethod>('semantic');
  const [segmenting, setSegmenting] = useState(false);
  const [showBounds, setShowBounds] = useState(false);
  const [showNames, setShowNames] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [bg, setBg] = useState<'transparent' | 'dark' | 'light'>('transparent');
  const [dragId, setDragId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [psdUrl, setPsdUrl] = useState<string>('');
  const [segMethod, setSegMethod] = useState<string>('');
  const previewCanvasRef = useRef<HTMLCanvasElement>(null);
  const previewRendererRef = useRef<LayerRenderer | null>(null);
  /** 上传的原始文件：运行真实分层前需要先 POST /api/upload 送到后端 */
  const fileRef = useRef<File | null>(null);
  const dimsRef = useRef<{ w: number; h: number }>({ w: 1024, h: 1024 });
  const psdInputRef = useRef<HTMLInputElement>(null);
  const pngsInputRef = useRef<HTMLInputElement>(null);
  /** 分层/导入产物目录：交给导出页复用（?layers_dir= 交接） */
  const [layersDir, setLayersDir] = useState<string>('');
  const [importing, setImporting] = useState(false);

  /** 把导入结果渲染进工作台（各层 PNG 均为整幅画布） */
  const applyImportResult = useCallback((result: ImportResult, dims?: { w: number; h: number }) => {
    const w = dims?.w || 1024;
    const h = dims?.h || 1024;
    const segmented = (result.layers || []).map((l) => ({
      name: l.name,
      part_name: l.name,
      url: l.url || '',
      pixel_count: l.pixel_count || 0,
    }));
    setLayers(toLayerInfo(segmented, w, h));
    setLayersDir(result.layers_dir || '');
    setPsdUrl(result.psd_url || '');
    setSegMethod('import');
    setError(null);
  }, []);

  const handleImportPSD = useCallback(async (file: File | null) => {
    if (!file || importing) return;
    setImporting(true);
    try {
      const result = await apiClient.importPSD(file);
      applyImportResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'PSD 导入失败');
    } finally {
      setImporting(false);
    }
  }, [importing, applyImportResult]);

  const handleImportPNGs = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0 || importing) return;
    setImporting(true);
    try {
      const result = await apiClient.importPNGs(Array.from(files));
      applyImportResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'PNG 导入失败');
    } finally {
      setImporting(false);
    }
  }, [importing, applyImportResult]);

  const selected = useMemo(
    () => layers.find((l) => l.id === selectedId) || null,
    [layers, selectedId],
  );

  const handleFile = useCallback((file: File | null) => {
    fileRef.current = file;
    if (!file) {
      setSourceUrl(null);
      setLayers([]);
      setPsdUrl('');
      return;
    }
    const url = URL.createObjectURL(file);
    setSourceUrl(url);
    setLayers([]);
    setPsdUrl('');
    setSegMethod('');
    setError(null);
    // 记录源图尺寸：分层产物为整幅画布 PNG，预览按此尺寸摆放
    const img = new Image();
    img.onload = () => {
      dimsRef.current = { w: img.naturalWidth || 1024, h: img.naturalHeight || 1024 };
    };
    img.src = url;
  }, []);

  // 进入页面时拉取最近生成记录：无上传文件时也能直接对最近生成图跑分层
  useEffect(() => {
    apiClient
      .getLatestGeneration()
      .then((rec) => {
        if (rec?.image_url) {
          setSourceUrl(rec.image_url);
          fileRef.current = null;
        }
      })
      .catch(() => undefined);
  }, []);

  const runSegmentation = useCallback(async () => {
    if (segmenting) return;
    setSegmenting(true);
    setError(null);
    try {
      // 1) 新上传的文件先送到后端；页面重进后没有 File 对象时回退为最近生成图
      let imagePath = '';
      if (fileRef.current) {
        const uploaded = await apiClient.uploadImage(fileRef.current);
        imagePath = uploaded.path;
      } else {
        const record = await apiClient.getLatestGeneration();
        imagePath = record?.image_path || '';
      }
      if (!imagePath) {
        throw new Error('找不到可用的源图片 —— 请上传图片，或先在「Generate」生成角色。');
      }

      // 2) 调用真实分层（Go 端桥接 Python：SAM/ISNet 语义分割 + PSD 导出）
      const result = await apiClient.segmentImage(imagePath, method);

      // 3) 渲染分层结果；各层 PNG 为整幅画布，bounds 覆盖全图
      const { w, h } = dimsRef.current;
      setLayers(toLayerInfo(result.layers || [], w, h));
      setLayersDir(result.layers_dir || '');
      setPsdUrl(result.psd_url || '');
      setSegMethod(result.method || method);
      if (result.source_image_url) {
        setSourceUrl(result.source_image_url);
        fileRef.current = null; // 源图已是服务器产物，无需重复上传
      }
      if (!result.psd_success && result.psd_url) {
        setError('分层完成，但 PSD 写出失败 —— 可在导出页重试。');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '分层失败');
    } finally {
      setSegmenting(false);
    }
  }, [segmenting, method]);

  // Preview canvas render (for export composite preview not the LayerCanvas component)
  useEffect(() => {
    if (!previewCanvasRef.current || layers.length === 0) return;
    if (!previewRendererRef.current) {
      previewRendererRef.current = new LayerRenderer(previewCanvasRef.current, {
        width: 400,
        height: 400,
        backgroundColor: bg === 'transparent' ? 'transparent' : bg === 'dark' ? '#0f0f13' : '#f3f4f6',
        showBounds,
        showNames,
      });
    }
    previewRendererRef.current.setBackgroundColor(
      bg === 'transparent' ? 'transparent' : bg === 'dark' ? '#0f0f13' : '#f3f4f6',
    );
    previewRendererRef.current.setShowBounds(showBounds);
    previewRendererRef.current.setShowNames(showNames);
    previewRendererRef.current.setLayers(layers).catch(() => undefined);
  }, [layers, showBounds, showNames, bg]);

  const toggleVisibility = (id: string) => {
    setLayers((prev) =>
      prev.map((l) => (l.id === id ? { ...l, visible: !l.visible } : l)),
    );
  };

  const updateLayer = (id: string, patch: Partial<LayerInfo>) => {
    setLayers((prev) => prev.map((l) => (l.id === id ? { ...l, ...patch } : l)));
  };

  const handleDragStart = (id: string) => setDragId(id);
  const handleDragOver = (e: React.DragEvent, overId: string) => {
    e.preventDefault();
    if (!dragId || dragId === overId) return;
    setLayers((prev) => {
      const from = prev.findIndex((l) => l.id === dragId);
      const to = prev.findIndex((l) => l.id === overId);
      if (from < 0 || to < 0) return prev;
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next.map((l, i) => ({ ...l, index: i }));
    });
  };
  const handleDragEnd = () => setDragId(null);

  const downloadComposite = () => {
    const url = previewRendererRef.current?.exportPNG();
    if (!url) return;
    const a = document.createElement('a');
    a.href = url;
    a.download = `composite-${Date.now()}.png`;
    a.click();
  };

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-white">Layer Workstation</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Inspect, reorder, and tune segmentation layers before Live2D rigging
          </p>
        </div>
        <div className="flex items-center gap-2">
          {segMethod && (
            <span className="text-[11px] text-gray-500">
              分层方式 <span className="text-pink-300 font-mono">{segMethod}</span>
            </span>
          )}
          {psdUrl && (
            <a
              href={psdUrl}
              download
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/25 transition-colors"
            >
              <Download className="w-3.5 h-3.5" /> 下载 PSD
            </a>
          )}
          {sourceUrl && layers.length > 0 && (
            <button
              onClick={runSegmentation}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-gray-800 border border-gray-700 text-gray-200 hover:bg-gray-700 transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" /> Re-segment
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr_300px] gap-4">
        {/* Left: source + layer list */}
        <div className="space-y-3">
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
            <p className="text-xs font-medium text-gray-400 mb-2 flex items-center gap-1.5">
              <Upload className="w-3.5 h-3.5" /> Source image
            </p>
            <ImageUploader value={sourceUrl} onChange={handleFile} label="Upload generated art" />
            {sourceUrl && (
              <button
                onClick={runSegmentation}
                disabled={segmenting}
                className="mt-3 w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs bg-gradient-to-r from-pink-500 to-purple-600 text-white font-medium disabled:opacity-50 hover:shadow-lg hover:shadow-pink-500/30 transition-all"
              >
                {segmenting ? (
                  <LoadingSpinner size={14} label="Segmenting…" />
                ) : (
                  <>
                    <Grid3x3 className="w-3.5 h-3.5" /> 运行真实分层
                  </>
                )}
              </button>
            )}
            {/* 外部素材导入：自有 PSD / 零散 PNG 直接进入工作台与导出链路 */}
            <div className="mt-2 grid grid-cols-2 gap-2">
              <button
                onClick={() => psdInputRef.current?.click()}
                disabled={importing}
                className="inline-flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg text-[11px] bg-gray-900 border border-gray-800 text-gray-300 hover:border-pink-500/40 disabled:opacity-50 transition-colors"
              >
                <Upload className="w-3.5 h-3.5" /> 导入 PSD
              </button>
              <button
                onClick={() => pngsInputRef.current?.click()}
                disabled={importing}
                className="inline-flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg text-[11px] bg-gray-900 border border-gray-800 text-gray-300 hover:border-pink-500/40 disabled:opacity-50 transition-colors"
              >
                <Upload className="w-3.5 h-3.5" /> 导入 PNG…
              </button>
            </div>
            <input
              ref={psdInputRef}
              type="file"
              accept=".psd"
              className="hidden"
              onChange={(e) => {
                handleImportPSD(e.target.files?.[0] || null);
                e.target.value = '';
              }}
            />
            <input
              ref={pngsInputRef}
              type="file"
              accept=".png"
              multiple
              className="hidden"
              onChange={(e) => {
                handleImportPNGs(e.target.files);
                e.target.value = '';
              }}
            />
            {layersDir && (
              <Link
                href={`/export?layers_dir=${encodeURIComponent(layersDir)}`}
                className="mt-2 w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/25 transition-colors"
              >
                <Download className="w-3.5 h-3.5" /> 发送到导出
              </Link>
            )}
            <div className="mt-3 flex gap-1">
              {(['semantic', 'kmeans'] as SegmentationMethod[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setMethod(m)}
                  className={`flex-1 px-2 py-1 rounded-md text-[10px] border transition-colors ${
                    method === m
                      ? 'bg-pink-500/20 border-pink-500/40 text-pink-300'
                      : 'bg-gray-900 border-gray-800 text-gray-400 hover:border-gray-700'
                  }`}
                >
                  {m === 'semantic' ? 'Semantic' : 'K-means'}
                </button>
              ))}
            </div>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl overflow-hidden flex flex-col">
            <div className="p-3 border-b border-gray-800 flex items-center justify-between">
              <p className="text-xs font-medium text-gray-400 flex items-center gap-1.5">
                <LayersIcon className="w-3.5 h-3.5" /> Layers ({layers.length})
              </p>
            </div>
            <div className="overflow-y-auto max-h-[60vh]">
              {layers.length === 0 ? (
                <div className="p-6 text-center">
                  <ImageIcon className="w-8 h-8 text-gray-700 mx-auto mb-2" />
                  <p className="text-xs text-gray-500">No layers yet</p>
                </div>
              ) : (
                layers.map((layer) => (
                  <div
                    key={layer.id}
                    draggable
                    onDragStart={() => handleDragStart(layer.id)}
                    onDragOver={(e) => handleDragOver(e, layer.id)}
                    onDragEnd={handleDragEnd}
                    onClick={() => setSelectedId(layer.id)}
                    className={`flex items-center gap-2 p-2 border-b border-gray-800/60 cursor-pointer transition-colors ${
                      selectedId === layer.id
                        ? 'bg-pink-500/10 border-l-2 border-l-pink-500'
                        : 'hover:bg-gray-800/40 border-l-2 border-l-transparent'
                    } ${dragId === layer.id ? 'opacity-50' : ''}`}
                  >
                    <GripVertical className="w-3.5 h-3.5 text-gray-600 cursor-grab" />
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleVisibility(layer.id);
                      }}
                      className="text-gray-500 hover:text-white"
                    >
                      {layer.visible ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                    </button>
                    <span className="text-xs text-gray-300 truncate flex-1">{layer.name}</span>
                    <span className="text-[10px] text-gray-600 font-mono">
                      {Math.round(layer.opacity * 100)}%
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Center: preview */}
        <div className="bg-[#1a1a23] border border-gray-800 rounded-xl flex flex-col min-h-[600px]">
          <div className="p-3 border-b border-gray-800 flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setZoom((z) => Math.max(0.25, z - 0.1))}
                className="p-1.5 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"
              >
                <ZoomOut className="w-3.5 h-3.5" />
              </button>
              <span className="text-xs text-gray-400 font-mono w-12 text-center">
                {Math.round(zoom * 100)}%
              </span>
              <button
                onClick={() => setZoom((z) => Math.min(4, z + 0.1))}
                className="p-1.5 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"
              >
                <ZoomIn className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="flex items-center gap-1">
              {(['transparent', 'dark', 'light'] as const).map((b) => (
                <button
                  key={b}
                  onClick={() => setBg(b)}
                  className={`px-2 py-1 rounded-md text-[10px] border transition-colors ${
                    bg === b
                      ? 'bg-pink-500/20 border-pink-500/40 text-pink-300'
                      : 'bg-gray-900 border-gray-800 text-gray-400'
                  }`}
                >
                  {b}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1 text-[11px] text-gray-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showBounds}
                  onChange={(e) => setShowBounds(e.target.checked)}
                  className="accent-pink-500"
                />
                Bounds
              </label>
              <label className="flex items-center gap-1 text-[11px] text-gray-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showNames}
                  onChange={(e) => setShowNames(e.target.checked)}
                  className="accent-pink-500"
                />
                Names
              </label>
              <button
                onClick={downloadComposite}
                disabled={layers.length === 0}
                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-[11px] bg-gray-800 border border-gray-700 text-gray-200 hover:bg-gray-700 disabled:opacity-40 transition-colors"
              >
                <Download className="w-3 h-3" /> PNG
              </button>
            </div>
          </div>
          <div
            className="flex-1 relative overflow-auto"
            style={bg === 'transparent' ? getCheckerboardStyle() : undefined}
          >
            {error && (
              <div className="absolute top-3 left-3 right-3 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-xs text-red-300 flex items-center gap-2 z-10">
                <AlertCircle className="w-4 h-4" /> {error}
              </div>
            )}
            {sourceUrl ? (
              layers.length > 0 ? (
                <div
                  className="flex items-center justify-center min-h-full p-6"
                  style={{ transform: `scale(${zoom})`, transformOrigin: 'center' }}
                >
                  <div className="relative bg-transparent" style={{ width: 512, height: 512 }}>
                    <LayerCanvas
                      layers={layers}
                      showBounds={showBounds}
                      showNames={showNames}
                      background={bg === 'transparent' ? 'transparent' : bg === 'dark' ? '#0f0f13' : '#f3f4f6'}
                      onLayerClick={(l) => setSelectedId(l?.id || null)}
                      selectedLayerId={selectedId}
                    />
                  </div>
                </div>
              ) : (
                <div className="absolute inset-0 flex items-center justify-center p-4">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={sourceUrl}
                    alt="source"
                    className="max-w-full max-h-[70vh] rounded-lg border border-gray-800"
                  />
                </div>
              )
            ) : (
              <div className="absolute inset-0 flex flex-col items-center justify-center text-center p-6">
                <div className="w-16 h-16 rounded-2xl bg-gray-800/50 border border-gray-700 flex items-center justify-center mb-3">
                  <LayersIcon className="w-7 h-7 text-gray-600" />
                </div>
                <p className="text-sm text-gray-400">
                  Upload an image to get started
                </p>
                <p className="text-xs text-gray-600 mt-1">
                  请手动上传需要处理的图片。
                </p>
              </div>
            )}
          </div>
          <canvas ref={previewCanvasRef} className="hidden" />
        </div>

        {/* Right: selected layer properties */}
        <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
          <p className="text-xs font-medium text-gray-400 mb-3">Layer properties</p>
          {selected ? (
            <div className="space-y-4">
              <div>
                <p className="text-[10px] text-gray-500 uppercase tracking-wide">Name</p>
                <input
                  value={selected.name}
                  onChange={(e) => updateLayer(selected.id, { name: e.target.value })}
                  className="mt-1 w-full px-2.5 py-1.5 bg-gray-900 border border-gray-700 rounded-md text-xs text-white focus:outline-none focus:border-pink-500"
                />
              </div>
              <div>
                <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">
                  Opacity <span className="text-pink-400">{Math.round(selected.opacity * 100)}%</span>
                </p>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={selected.opacity}
                  onChange={(e) => updateLayer(selected.id, { opacity: parseFloat(e.target.value) })}
                  className="w-full accent-pink-500"
                />
              </div>
              <div>
                <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">Blend mode</p>
                <select
                  value={selected.blendMode}
                  onChange={(e) =>
                    updateLayer(selected.id, { blendMode: e.target.value as BlendMode })
                  }
                  className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded-md text-xs text-white focus:outline-none focus:border-pink-500"
                >
                  {BLEND_MODES.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <NumberField
                  label="Offset X"
                  value={selected.offsetX}
                  onChange={(v) => updateLayer(selected.id, { offsetX: v })}
                />
                <NumberField
                  label="Offset Y"
                  value={selected.offsetY}
                  onChange={(v) => updateLayer(selected.id, { offsetY: v })}
                />
              </div>
              <div>
                <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">Bounds</p>
                <div className="grid grid-cols-2 gap-1 text-[11px] font-mono text-gray-400 bg-gray-900 p-2 rounded-md">
                  <span>x: {selected.bounds.x}</span>
                  <span>y: {selected.bounds.y}</span>
                  <span>w: {selected.bounds.width}</span>
                  <span>h: {selected.bounds.height}</span>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-gray-500">Select a layer to edit properties</p>
          )}
        </div>
      </div>
    </div>
  );
};

interface NumberFieldProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
}

function NumberField({ label, value, onChange }: NumberFieldProps) {
  return (
    <label className="block">
      <span className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        className="mt-1 w-full px-2 py-1 bg-gray-900 border border-gray-700 rounded-md text-xs text-white focus:outline-none focus:border-pink-500"
      />
    </label>
  );
}

export default LayersPage;
