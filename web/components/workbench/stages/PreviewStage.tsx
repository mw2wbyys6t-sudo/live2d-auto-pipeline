import { useEffect, useRef, type PointerEvent } from 'react';
import dynamic from 'next/dynamic';
import { Box, Camera, Download, Loader2, Mic, Network, RefreshCw } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { usePreviewRuntime } from '../../../lib/preview-runtime';

const ModelCanvas = dynamic(() => import('../../ModelCanvas'), {
  ssr: false,
  loading: () => (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <Loader2 className="w-4 h-4 animate-spin" /> 正在加载渲染器…
    </div>
  ),
});

const BG_CLASS: Record<string, string> = {
  transparent: 'bg-[repeating-conic-gradient(#24242f_0%_25%,#16161d_0%_50%)] bg-[length:20px_20px]',
  dark: 'bg-[#0b0b10]',
  light: 'bg-[#f4f4f6]',
  sky: 'bg-gradient-to-b from-sky-300 to-indigo-200',
};

export default function PreviewStage() {
  const { stage, current, setStage } = useWorkbench();
  const rt = usePreviewRuntime();
  const containerRef = useRef<HTMLDivElement>(null);
  const fpsClockRef = useRef(0);
  const active = stage === 'preview';
  const { setActive } = rt;

  useEffect(() => {
    setActive(active);
  }, [active, setActive]);

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    // 待机开着也要跟随：视线本来就是待机的一部分，之前的 !rt.idle 让提示文案
    // 里的「移动鼠标让视线跟随」永远失效。面捕/麦克风占用时不抢参数。
    if (rt.trackingOn || rt.micOn) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    const y = ((e.clientY - rect.top) / rect.height) * 2 - 1;
    rt.playerRef.current?.setParameters({
      ParamEyeBallX: Math.max(-1, Math.min(1, x)),
      ParamEyeBallY: Math.max(-1, Math.min(1, -y)),
      ParamAngleX: Math.max(-30, Math.min(30, x * 20)),
      ParamAngleY: Math.max(-30, Math.min(30, -y * 20)),
    });
  };

  const saveFrame = () => {
    const player = rt.playerRef.current;
    if (!player) {
      rt.setError('模型还没加载完成，暂时无法导出当前帧。');
      return;
    }
    const url = player.captureFrame();
    if (!url) {
      rt.setError('当前帧无法导出：画布被跨域资源污染。');
      return;
    }
    const link = document.createElement('a');
    link.href = url;
    link.download = `${current?.characterId || 'live2d'}-frame-${Date.now()}.png`;
    link.click();
  };

  const modelName =
    rt.models.find((model) => model.model3_url === rt.modelUrl)?.name ||
    current?.characterId ||
    '当前作品模型';

  return (
    <div className="h-full flex flex-col p-4 gap-3 min-h-0">
      <header className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-white">Live2D 预览</h2>
          <p className="text-[11px] text-gray-500 truncate">
            表情、参数、面捕与麦克风都可以直接驱动模型；切走本阶段会自动释放摄像头与麦克风。
          </p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={() => rt.setBindingsOverlay(!rt.bindingsOverlay)}
            title="显示变形器控制网格与绑定层级（数据来自 bindings.json）"
            className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg border text-[10px] transition-colors ${
              rt.bindingsOverlay
                ? 'border-cyan-500/50 bg-cyan-500/15 text-cyan-300'
                : 'border-gray-700 bg-gray-900 text-gray-300 hover:bg-gray-800'
            }`}
          >
            <Network className="w-3 h-3" /> 绑骨线
          </button>
          <button
            onClick={rt.reloadModel}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-gray-700 bg-gray-900 text-[10px] text-gray-300 hover:bg-gray-800 transition-colors"
          >
            <RefreshCw className="w-3 h-3" /> 重载
          </button>
          <button
            onClick={saveFrame}
            disabled={!rt.modelUrl}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-[10px] text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-40 transition-colors"
          >
            <Download className="w-3 h-3" /> 当前帧
          </button>
        </div>
      </header>

      <div
        ref={containerRef}
        onPointerMove={onPointerMove}
        className={`flex-1 min-h-0 rounded-xl border border-gray-800 overflow-hidden relative ${BG_CLASS[rt.bg] || BG_CLASS.dark}`}
      >
        {active && rt.modelUrl && (
          <ModelCanvas
            key={`${rt.modelUrl}#${rt.reloadNonce}`}
            modelUrl={rt.modelUrl}
            onReady={(player) => {
              rt.playerRef.current = player;
              // 播放器是命令式实例：加载完成后要把 UI 当前状态推给它，
              // 否则待机动作与表情只在下一次点按钮时才生效。
              player.setIdle(rt.idle);
              player.setExpression(rt.expression);
              player.setBindingsOverlay(rt.bindingsOverlay);
            }}
            onLoaded={() => {
              // onReady 只代表实例就绪；表情/动作/物理是在 loadModel 里异步
              // 加载完的，这里再推一次才不会丢掉切阶段前选中的表情。
              const player = rt.playerRef.current;
              if (!player) return;
              player.setIdle(rt.idle);
              player.setExpression(rt.expression);
              player.setBindingsOverlay(rt.bindingsOverlay);
              rt.syncPlayerDiagnostics();
            }}
            onFrame={() => {
              const player = rt.playerRef.current;
              if (!player) return;
              const now = performance.now();
              if (now - fpsClockRef.current < 500) return;
              fpsClockRef.current = now;
              rt.setFps(player.fps);
              // 动作组/物理是在加载后才知道的，没有事件，只能节流轮询一次。
              rt.syncPlayerDiagnostics();
              // 把真实参数回推给面板：滑杆会跟着呼吸/眨眼/动作走，一眼能看出
              // 模型确实在动，而不是停在导出时的姿态。
              rt.publishParams(player.getAllParameters());
            }}
            onError={(message) => rt.setError(`模型加载失败：${message}`)}
          />
        )}

        {rt.modelUrl ? (
          <>
            <div className="absolute top-3 left-3 flex items-center gap-1.5">
              <span className="px-2 py-1 rounded-md bg-black/50 text-[10px] text-gray-200">{modelName}</span>
              <span className="px-2 py-1 rounded-md bg-black/50 text-[10px] text-emerald-300 tabular-nums">
                {rt.fps.toFixed(0)} FPS
              </span>
              {rt.trackingOn && (
                <span className="px-2 py-1 rounded-md bg-emerald-500/20 text-[10px] text-emerald-300 flex items-center gap-1">
                  <Camera className="w-3 h-3" /> 面捕
                </span>
              )}
              {rt.micOn && (
                <span className="px-2 py-1 rounded-md bg-emerald-500/20 text-[10px] text-emerald-300 flex items-center gap-1">
                  <Mic className="w-3 h-3" /> 麦克风
                </span>
              )}
            </div>
            <p className="absolute bottom-3 left-3 text-[10px] text-gray-500/80">
              移动鼠标让视线跟随 · 右侧「参数」面板可调表情与手动参数
            </p>
            {rt.bindingsOverlay && (
              <div className="absolute bottom-3 right-3 rounded-md bg-black/60 px-2.5 py-1.5 space-y-1 text-[10px] text-gray-300">
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-cyan-400" /> 变形器控制网格
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-amber-500" /> 绑定层级
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-slate-400" /> 图层归属
                </div>
                {!rt.usesBindings && (
                  <div className="text-amber-300">当前模型没有 bindings.json</div>
                )}
              </div>
            )}
            {rt.error && (
              <div className="absolute top-3 right-3 max-w-[60%] rounded-lg bg-red-500/15 border border-red-500/40 px-2.5 py-1.5 text-[11px] text-red-300">
                {rt.error}
              </div>
            )}
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-8">
            <div className="w-14 h-14 rounded-2xl bg-gray-900/80 border border-gray-700 flex items-center justify-center mb-3">
              <Box className="w-6 h-6 text-gray-500" />
            </div>
            <p className="text-sm text-gray-300">当前作品还没有可加载的模型</p>
            <p className="text-[11px] text-gray-500 mt-1 max-w-sm leading-relaxed">
              先在「导出」构建 Live2D 模型包；导出完成后会自动回填到本作品，预览页也就有了模型。
            </p>
            <button
              onClick={() => setStage('export')}
              className="mt-3 px-3 py-1.5 rounded-lg bg-pink-500/15 border border-pink-500/40 text-[11px] text-pink-300 hover:bg-pink-500/25 transition-colors"
            >
              去导出
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
