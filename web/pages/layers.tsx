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
import type { BlendMode, LayerInfo, SegmentationMethod } from '../types';
import LayerCanvas, { getCheckerboardStyle } from '../components/LayerCanvas';
import ImageUploader from '../components/ImageUploader';
import LoadingSpinner from '../components/LoadingSpinner';
import { LayerRenderer } from '../lib/layer-renderer';

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

function makeMockLayers(imageUrl: string, w: number, h: number): LayerInfo[] {
  // synthetic layered mock using region bins
  const groups: Array<{ name: string; color: string; x: number; y: number; w: number; h: number }> = [
    { name: 'background', color: '#1e293b', x: 0, y: 0, w, h: h },
    { name: 'body', color: '#fde2c4', x: w * 0.3, y: h * 0.45, w: w * 0.4, h: h * 0.4 },
    { name: 'hair_back', color: '#ec4899', x: w * 0.25, y: h * 0.1, w: w * 0.5, h: h * 0.45 },
    { name: 'face', color: '#fde2c4', x: w * 0.33, y: h * 0.2, w: w * 0.34, h: h * 0.3 },
    { name: 'eyes', color: '#3b82f6', x: w * 0.38, y: h * 0.32, w: w * 0.24, h: h * 0.06 },
    { name: 'hair_front', color: '#f472b6', x: w * 0.28, y: h * 0.15, w: w * 0.44, h: h * 0.2 },
    { name: 'mouth', color: '#ef4444', x: w * 0.45, y: h * 0.42, w: w * 0.1, h: h * 0.03 },
    { name: 'outfit', color: '#8b5cf6', x: w * 0.25, y: h * 0.55, w: w * 0.5, h: h * 0.35 },
  ];
  return groups.map((g, i) => ({
    id: `layer-${i}`,
    name: g.name,
    index: i,
    visible: true,
    opacity: 1,
    blendMode: 'normal' as BlendMode,
    offsetX: 0,
    offsetY: 0,
    width: Math.round(g.w),
    height: Math.round(g.h),
    imageUrl: undefined,
    bounds: {
      x: Math.round(g.x),
      y: Math.round(g.y),
      width: Math.round(g.w),
      height: Math.round(g.h),
    },
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
  const previewCanvasRef = useRef<HTMLCanvasElement>(null);
  const previewRendererRef = useRef<LayerRenderer | null>(null);

  const selected = useMemo(
    () => layers.find((l) => l.id === selectedId) || null,
    [layers, selectedId],
  );

  const handleFile = useCallback((file: File | null) => {
    if (!file) {
      setSourceUrl(null);
      setLayers([]);
      return;
    }
    const url = URL.createObjectURL(file);
    setSourceUrl(url);
    setLayers([]);
    setError(null);
  }, []);

  const runSegmentation = useCallback(async () => {
    if (!sourceUrl) return;
    setSegmenting(true);
    setError(null);
    try {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      await new Promise<void>((resolve, reject) => {
        img.onload = () => resolve();
        img.onerror = () => reject(new Error('Failed to load image'));
        img.src = sourceUrl;
      });
      // simulate processing delay
      await new Promise((r) => setTimeout(r, 800));
      const mock = makeMockLayers(sourceUrl, img.naturalWidth, img.naturalHeight);
      setLayers(mock);
      setSelectedId(mock[0]?.id || null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Segmentation failed');
    } finally {
      setSegmenting(false);
    }
  }, [sourceUrl]);

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
                    <Grid3x3 className="w-3.5 h-3.5" /> Run segmentation
                  </>
                )}
              </button>
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
                <p className="text-sm text-gray-400">Upload an image to get started</p>
                <p className="text-xs text-gray-600 mt-1">
                  Generate an image first, then open the Layer Workstation to inspect segmentation.
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
