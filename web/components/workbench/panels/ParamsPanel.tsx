import { useEffect, useMemo, useState } from 'react';
import { Camera, CameraOff, Loader2, Mic, MicOff, RefreshCw, RotateCcw, Sparkles } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { usePreviewRuntime, type PreviewBackground } from '../../../lib/preview-runtime';
import { clampToBounds, resolveBounds, resolveParameters } from '../../../lib/live2d-params';
import ParameterSlider from '../../ParameterSlider';
import type { Emotion } from '../../../types';

const EXPRESSIONS: Array<{ id: Emotion; label: string }> = [
  { id: 'neutral', label: '中性' },
  { id: 'happy', label: '开心' },
  { id: 'sad', label: '难过' },
  { id: 'angry', label: '生气' },
  { id: 'surprised', label: '惊讶' },
  { id: 'shy', label: '害羞' },
  { id: 'thinking', label: '思考' },
  { id: 'excited', label: '兴奋' },
];

const BACKGROUNDS: Array<{ id: PreviewBackground; label: string; css: string }> = [
  { id: 'transparent', label: '透明', css: 'bg-[repeating-conic-gradient(#2a2a33_0%_25%,#1a1a22_0%_50%)] bg-[length:16px_16px]' },
  { id: 'dark', label: '深色', css: 'bg-[#0b0b10]' },
  { id: 'light', label: '浅色', css: 'bg-[#f4f4f6]' },
  { id: 'sky', label: '天空', css: 'bg-gradient-to-b from-sky-300 to-indigo-200' },
];

export default function ParamsPanel() {
  const { current, setStage } = useWorkbench();
  const rt = usePreviewRuntime();
  const [tick, setTick] = useState(0);

  // 播放器是命令式实例，指标只能轮询读取（没有响应式通知）。
  useEffect(() => {
    const id = setInterval(() => setTick((value) => value + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const meta = rt.playerRef.current?.modelMeta ?? null;
  const params = useMemo(() => resolveParameters(meta), [meta]);
  const bounds = useMemo(() => resolveBounds(meta), [meta]);
  const fps = rt.playerRef.current?.fps ?? rt.fps;
  const layersLoaded = rt.playerRef.current?.layersLoaded ?? 0;
  const triangleCount = rt.playerRef.current?.triangleCount ?? 0;
  const hasMesh = rt.playerRef.current?.hasMeshGeometry ?? false;
  const paramsCount = rt.playerRef.current?.paramsCount ?? 0;

  const currentModel = current?.model3Url || '';
  const modelValue = rt.manualModelUrl || currentModel;

  const setManualParam = (id: string, value: number) => {
    const next = clampToBounds(value, bounds.get(id));
    rt.setIdle(false);
    rt.playerRef.current?.setParameters({ [id]: next });
    rt.publishParams({ [id]: next });
  };

  const resetParams = () => {
    const defaults: Record<string, number> = {};
    for (const param of params) defaults[param.id] = param.default;
    rt.setIdle(true);
    rt.playerRef.current?.setParameters(defaults);
    rt.publishParams(defaults);
  };

  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-xs font-medium text-gray-400">模型</div>
          <button
            onClick={rt.reloadModel}
            className="inline-flex items-center gap-1 text-[10px] text-gray-500 hover:text-gray-300"
            title="强制重新加载当前模型"
          >
            <RefreshCw className="w-3 h-3" /> 重载
          </button>
        </div>
        <select
          value={modelValue}
          onChange={(e) => rt.setManualModelUrl(e.target.value === currentModel ? '' : e.target.value)}
          className="w-full px-2.5 py-2 bg-gray-900 border border-gray-700 rounded-lg text-xs text-white focus:outline-none focus:border-pink-500"
        >
          {currentModel && <option value={currentModel}>当前作品模型</option>}
          {rt.models.map((model) => (
            <option key={model.model3_json} value={model.model3_url}>
              {model.name}
            </option>
          ))}
          {!currentModel && rt.models.length === 0 && <option value="">暂无已导出模型</option>}
        </select>
        {!currentModel && (
          <button
            onClick={() => setStage('export')}
            className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 transition-colors"
          >
            当前作品还没有模型 —— 去导出
          </button>
        )}
      </section>

      <section>
        <div className="text-xs font-medium text-gray-400 mb-2">表情</div>
        <div className="grid grid-cols-4 gap-1">
          {EXPRESSIONS.map((emotion) => (
            <button
              key={emotion.id}
              onClick={() => rt.applyExpression(emotion.id)}
              className={`py-1.5 rounded-md text-[11px] border transition-colors ${
                rt.expression === emotion.id
                  ? 'border-pink-500/50 bg-pink-500/10 text-pink-300'
                  : 'border-gray-800 bg-gray-900 text-gray-400 hover:border-gray-700'
              }`}
            >
              {emotion.label}
            </button>
          ))}
        </div>
      </section>

      <section>
        <div className="text-xs font-medium text-gray-400 mb-2">输入</div>
        <div className="grid grid-cols-3 gap-1">
          <button
            onClick={() => void rt.toggleTracking()}
            className={`inline-flex items-center justify-center gap-1 py-1.5 rounded-md text-[11px] border transition-colors ${
              rt.trackingOn
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                : 'border-gray-800 bg-gray-900 text-gray-400'
            }`}
          >
            {rt.trackingOn ? <Camera className="w-3.5 h-3.5" /> : <CameraOff className="w-3.5 h-3.5" />}
            面捕
          </button>
          <button
            onClick={() => void rt.toggleMic()}
            className={`inline-flex items-center justify-center gap-1 py-1.5 rounded-md text-[11px] border transition-colors ${
              rt.micOn
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                : 'border-gray-800 bg-gray-900 text-gray-400'
            }`}
          >
            {rt.micOn ? <Mic className="w-3.5 h-3.5" /> : <MicOff className="w-3.5 h-3.5" />}
            麦克风
          </button>
          <button
            onClick={() => rt.setIdle(!rt.idle)}
            className={`inline-flex items-center justify-center gap-1 py-1.5 rounded-md text-[11px] border transition-colors ${
              rt.idle
                ? 'border-purple-500/40 bg-purple-500/10 text-purple-300'
                : 'border-gray-800 bg-gray-900 text-gray-400'
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            待机
          </button>
        </div>
        {rt.error && (
          <p className="mt-2 text-[11px] text-amber-400/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-2.5 py-2">
            {rt.error}
          </p>
        )}
      </section>

      <section>
        <div className="text-xs font-medium text-gray-400 mb-2">背景</div>
        <div className="grid grid-cols-4 gap-1">
          {BACKGROUNDS.map((bg) => (
            <button
              key={bg.id}
              onClick={() => rt.setBg(bg.id)}
              className={`h-8 rounded-md border text-[10px] transition-colors ${bg.css} ${
                bg.id === 'light' || bg.id === 'sky' ? 'text-gray-700' : 'text-gray-200'
              } ${rt.bg === bg.id ? 'border-pink-500' : 'border-gray-800'}`}
            >
              {bg.label}
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-gray-800 bg-gray-900/60 p-2.5">
        <div className="grid grid-cols-2 gap-y-1 text-[11px]">
          <span className="text-gray-500">帧率</span>
          <span className="text-right tabular-nums text-gray-300">{fps.toFixed(0)} FPS</span>
          <span className="text-gray-500">图层 / 参数</span>
          <span className="text-right tabular-nums text-gray-300">
            {layersLoaded} / {paramsCount}
          </span>
          <span className="text-gray-500">三角面</span>
          <span className="text-right tabular-nums text-gray-300">{triangleCount.toLocaleString()}</span>
          <span className="text-gray-500">网格几何</span>
          <span className={`text-right ${hasMesh ? 'text-emerald-400' : 'text-amber-400'}`}>
            {hasMesh ? '就绪' : '缺失'}
          </span>
        </div>
        <span className="hidden">{tick}</span>
      </section>

      <section>
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-medium text-gray-400">手动参数</div>
          <button
            onClick={resetParams}
            className="inline-flex items-center gap-1 text-[10px] text-gray-500 hover:text-gray-300"
          >
            <RotateCcw className="w-3 h-3" /> 复位
          </button>
        </div>
        {!rt.modelUrl ? (
          <p className="text-[11px] text-gray-600">模型加载后显示参数表。</p>
        ) : (
          <div className="space-y-3">
            {!meta && <p className="text-[11px] text-gray-600">正在读取模型参数…</p>}
            {params.map((param) => (
              <ParameterSlider
                key={param.id}
                param={param}
                value={rt.params[param.id] ?? param.default}
                onChange={(value) => setManualParam(param.id, value)}
              />
            ))}
          </div>
        )}
      </section>

      {rt.modelUrl && !hasMesh && (
        <p className="flex items-center gap-1.5 text-[11px] text-amber-400/90">
          <Loader2 className="w-3 h-3 animate-spin" /> 正在加载模型几何…
        </p>
      )}
    </div>
  );
}
