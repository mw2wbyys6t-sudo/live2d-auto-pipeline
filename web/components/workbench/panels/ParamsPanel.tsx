import { useEffect, useMemo, useState } from 'react';
import { Camera, CameraOff, Loader2, Mic, MicOff, RefreshCw, RotateCcw, Sparkles } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { usePreviewRuntime } from '../../../lib/preview-runtime';
import { clampToBounds, resolveBounds, resolveParameters } from '../../../lib/live2d-params';
import { EXPRESSIONS, PREVIEW_BACKGROUNDS as BACKGROUNDS } from '../../../lib/catalog';
import ParameterSlider from '../../ParameterSlider';

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
  // 本作品的模型排在前面，其余已导出模型放在后面，避免同名作品串台。
  const workModels = current?.models ?? [];
  const workModelUrls = new Set(workModels.map((model) => model.model3_url || '').filter(Boolean));
  const otherModels = rt.models.filter((model) => !workModelUrls.has(model.model3_url));

  const setManualParam = (id: string, value: number) => {
    const next = clampToBounds(value, bounds.get(id));
    // 只按住被拖的这一个参数，待机继续跑。以前这里会 setIdle(false)，
    // 拖一次滑杆模型就永久停住，看起来像「绑骨没生效」。
    rt.playerRef.current?.setParameters({ [id]: next }, Infinity);
    rt.publishParams({ [id]: next });
  };

  const resetParams = () => {
    const defaults: Record<string, number> = {};
    for (const param of params) defaults[param.id] = param.default;
    // 先解除手动保持再写默认值，否则这些参数仍被钉住，动作接管不了。
    rt.playerRef.current?.clearParameterHolds();
    rt.playerRef.current?.setParameters(defaults);
    rt.publishParams(defaults);
    rt.setIdle(true);
  };

  const toggleCard = (active: boolean) =>
    `inline-flex items-center justify-center gap-1 rounded-lg border px-1 py-1.5 text-[11px] transition-colors ${
      active
        ? 'border-mint-400/40 bg-mint-400/10 text-mint-600'
        : 'border-black/[0.08] bg-black/[0.02] text-haze-400 hover:border-black/[0.16] hover:text-haze-200'
    }`;

  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="section-label">模型</div>
          <button
            onClick={rt.reloadModel}
            className="inline-flex items-center gap-1 text-[10px] text-haze-500 transition-colors hover:text-haze-200"
            title="强制重新加载当前模型"
          >
            <RefreshCw className="h-3 w-3" /> 重载
          </button>
        </div>
        <select
          value={modelValue}
          onChange={(e) => rt.setManualModelUrl(e.target.value === currentModel ? '' : e.target.value)}
          className="field"
        >
          {currentModel && <option value={currentModel}>当前作品模型</option>}
          {workModels.length > 0 && (
            <optgroup label="本作品模型">
              {workModels.map((model) => (
                <option key={model.id} value={model.model3_url || ''}>
                  {model.name}
                </option>
              ))}
            </optgroup>
          )}
          {otherModels.length > 0 && (
            <optgroup label="全部已导出模型">
              {otherModels.map((model) => (
                <option key={model.model3_json} value={model.model3_url}>
                  {model.name}
                </option>
              ))}
            </optgroup>
          )}
          {!currentModel && rt.models.length === 0 && <option value="">暂无已导出模型</option>}
        </select>
        {!currentModel && (
          <button onClick={() => setStage('export')} className="btn-ghost w-full py-2">
            当前作品还没有模型 —— 去导出
          </button>
        )}
      </section>

      <section>
        <div className="section-label mb-2">表情</div>
        <div className="grid grid-cols-4 gap-1">
          {EXPRESSIONS.map((emotion) => (
            <button
              key={emotion.id}
              onClick={() => rt.applyExpression(emotion.id)}
              className={`rounded-lg border py-1.5 text-[11px] transition-colors ${
                rt.expression === emotion.id
                  ? 'border-sakura-500/40 bg-sakura-500/10 text-sakura-600'
                  : 'border-black/[0.08] bg-black/[0.02] text-haze-400 hover:border-black/[0.16] hover:text-haze-200'
              }`}
            >
              {emotion.label}
            </button>
          ))}
        </div>
      </section>

      {rt.motionGroups.length > 0 && (
        <section>
          <div className="mb-2 flex items-center justify-between">
            <div className="section-label">动作</div>
            <span className="text-[10px] tabular-nums text-haze-600">{rt.motionCount} 个动作</span>
          </div>
          <div className="flex flex-wrap gap-1">
            {rt.motionGroups.map((group) => (
              <button
                key={group}
                onClick={() => rt.playMotion(group)}
                title={`播放 ${group} 动作组`}
                className="chip px-2 py-1 text-[11px]"
              >
                {group}
              </button>
            ))}
          </div>
        </section>
      )}

      <section>
        <div className="section-label mb-2">输入</div>
        <div className="grid grid-cols-3 gap-1">
          <button onClick={() => void rt.toggleTracking()} className={toggleCard(rt.trackingOn)}>
            {rt.trackingOn ? <Camera className="h-3.5 w-3.5" /> : <CameraOff className="h-3.5 w-3.5" />}
            面捕
          </button>
          <button onClick={() => void rt.toggleMic()} className={toggleCard(rt.micOn)}>
            {rt.micOn ? <Mic className="h-3.5 w-3.5" /> : <MicOff className="h-3.5 w-3.5" />}
            麦克风
          </button>
          <button
            onClick={() => rt.setIdle(!rt.idle)}
            className={`inline-flex items-center justify-center gap-1 rounded-lg border px-1 py-1.5 text-[11px] transition-colors ${
              rt.idle
                ? 'border-iris-500/40 bg-iris-500/10 text-iris-600'
                : 'border-black/[0.08] bg-black/[0.02] text-haze-400 hover:border-black/[0.16] hover:text-haze-200'
            }`}
          >
            <Sparkles className="h-3.5 w-3.5" />
            待机
          </button>
        </div>
        {rt.error && (
          <p className="mt-2 rounded-xl border border-amber-400/30 bg-amber-400/10 px-2.5 py-2 text-[11px] text-amber-700/90">
            {rt.error}
          </p>
        )}
      </section>

      <section>
        <div className="section-label mb-2">背景</div>
        <div className="grid grid-cols-4 gap-1">
          {BACKGROUNDS.map((bg) => (
            <button
              key={bg.id}
              onClick={() => rt.setBg(bg.id)}
              className={`h-8 rounded-lg border text-[10px] transition-colors ${bg.css} ${
                bg.id === 'light' || bg.id === 'sky' ? 'text-gray-700' : 'text-haze-200'
              } ${rt.bg === bg.id ? 'border-sakura-500/70 shadow-glow-sakura' : 'border-black/[0.08]'}`}
            >
              {bg.label}
            </button>
          ))}
        </div>
      </section>

      <section className="card p-2.5">
        <div className="grid grid-cols-2 gap-y-1 text-[11px]">
          <span className="text-haze-500">帧率</span>
          <span className="text-right tabular-nums text-haze-200">{fps.toFixed(0)} FPS</span>
          <span className="text-haze-500">图层 / 参数</span>
          <span className="text-right tabular-nums text-haze-200">
            {layersLoaded} / {paramsCount}
          </span>
          <span className="text-haze-500">三角面</span>
          <span className="text-right tabular-nums text-haze-200">{triangleCount.toLocaleString()}</span>
          <span className="text-haze-500">网格几何</span>
          <span className={`text-right ${hasMesh ? 'text-mint-600' : 'text-amber-600'}`}>
            {hasMesh ? '就绪' : '缺失'}
          </span>
          <span className="text-haze-500">物理</span>
          <span className={`text-right tabular-nums ${rt.physicsLoaded ? 'text-mint-600' : 'text-amber-600'}`}>
            {rt.physicsLoaded ? `${rt.physicsChains} 条链` : '未加载'}
          </span>
          <span className="text-haze-500">动作</span>
          <span className={`text-right tabular-nums ${rt.motionCount > 0 ? 'text-mint-600' : 'text-amber-600'}`}>
            {rt.motionCount > 0 ? `${rt.motionCount} 个` : '无（用程序化待机）'}
          </span>
        </div>
        <span className="hidden">{tick}</span>
      </section>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <div className="section-label">手动参数</div>
          <button
            onClick={resetParams}
            className="inline-flex items-center gap-1 text-[10px] text-haze-500 transition-colors hover:text-haze-200"
          >
            <RotateCcw className="h-3 w-3" /> 复位
          </button>
        </div>
        {!rt.modelUrl ? (
          <p className="text-[11px] text-haze-600">模型加载后显示参数表。</p>
        ) : (
          <div className="space-y-3">
            {!meta && <p className="text-[11px] text-haze-600">正在读取模型参数…</p>}
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
        <p className="flex items-center gap-1.5 text-[11px] text-amber-600/90">
          <Loader2 className="h-3 w-3 animate-spin" /> 正在加载模型几何…
        </p>
      )}
    </div>
  );
}
