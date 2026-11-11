import { useEffect, useRef, type PointerEvent } from 'react';
import dynamic from 'next/dynamic';
import { Box, Camera, Download, Loader2, Mic, Network, RefreshCw } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { usePreviewRuntime } from '../../../lib/preview-runtime';
import StageHeader from '../StageHeader';

const ModelCanvas = dynamic(() => import('../../ModelCanvas'), {
  ssr: false,
  loading: () => (
    <div className="flex items-center gap-2 text-xs text-haze-500">
      <Loader2 className="h-4 w-4 animate-spin text-sakura-600" /> 正在加载渲染器…
    </div>
  ),
});

const BG_CLASS: Record<string, string> = {
  transparent: 'bg-[repeating-conic-gradient(#ffffff_0%_25%,#f1ecf9_0%_50%)] bg-[length:20px_20px]',
  dark: 'bg-[#14101d]',
  light: 'bg-[#f4f2f8]',
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
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <StageHeader
        icon={<Box className="h-4 w-4" />}
        title="Live2D 预览"
        description="表情、参数、面捕与麦克风都可以直接驱动模型；切走本阶段会自动释放摄像头与麦克风。"
        badge={
          rt.modelUrl ? (
            <span className="chip border-mint-400/40 bg-mint-400/10 text-mint-600">
              <span className="h-1.5 w-1.5 rounded-full bg-mint-400" />
              {rt.fps.toFixed(0)} FPS
            </span>
          ) : null
        }
        actions={
          <>
            <button
              onClick={() => rt.setBindingsOverlay(!rt.bindingsOverlay)}
              title="显示变形器控制网格与绑定层级（数据来自 bindings.json）"
              className={`btn px-2.5 py-1.5 ${
                rt.bindingsOverlay
                  ? 'border border-cyan-400/40 bg-cyan-400/10 text-cyan-300'
                  : 'btn-ghost'
              }`}
            >
              <Network className="h-3 w-3" /> 绑骨线
            </button>
            <button onClick={rt.reloadModel} className="btn-ghost px-2.5 py-1.5">
              <RefreshCw className="h-3 w-3" /> 重载
            </button>
            <button onClick={saveFrame} disabled={!rt.modelUrl} className="btn-mint px-2.5 py-1.5">
              <Download className="h-3 w-3" /> 当前帧
            </button>
          </>
        }
      />

      <div
        ref={containerRef}
        onPointerMove={onPointerMove}
        className={`stage-frame ${BG_CLASS[rt.bg] || BG_CLASS.dark}`}
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
            <div className="absolute left-3 top-3 flex items-center gap-1.5">
              <span className="rounded-md border border-black/[0.08] bg-ink-950/70 px-2 py-1 text-[10px] text-haze-200 backdrop-blur">
                {modelName}
              </span>
              <span className="rounded-md border border-mint-400/25 bg-mint-400/10 px-2 py-1 text-[10px] tabular-nums text-mint-600 backdrop-blur">
                {rt.fps.toFixed(0)} FPS
              </span>
              {rt.trackingOn && (
                <span className="flex items-center gap-1 rounded-md bg-mint-400/15 px-2 py-1 text-[10px] text-mint-600 backdrop-blur">
                  <Camera className="h-3 w-3" /> 面捕
                </span>
              )}
              {rt.micOn && (
                <span className="flex items-center gap-1 rounded-md bg-mint-400/15 px-2 py-1 text-[10px] text-mint-600 backdrop-blur">
                  <Mic className="h-3 w-3" /> 麦克风
                </span>
              )}
            </div>
            <p className="absolute bottom-3 left-3 rounded-md bg-ink-950/70 px-2 py-1 text-[10px] text-haze-500 backdrop-blur">
              移动鼠标让视线跟随 · 右侧「参数」面板可调表情与手动参数
            </p>
            {rt.bindingsOverlay && (
              <div className="absolute bottom-3 right-3 space-y-1 rounded-lg border border-black/[0.08] bg-ink-950/80 px-2.5 py-1.5 text-[10px] text-haze-300 backdrop-blur">
                <div className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-cyan-400" /> 变形器控制网格
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-amber-500" /> 绑定层级
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-slate-400" /> 图层归属
                </div>
                {!rt.usesBindings && <div className="text-amber-700">当前模型没有 bindings.json</div>}
              </div>
            )}
            {rt.error && (
              <div className="absolute right-3 top-3 max-w-[60%] rounded-lg border border-rose-400/35 bg-rose-500/15 px-2.5 py-1.5 text-[11px] text-rose-600 backdrop-blur">
                {rt.error}
              </div>
            )}
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center px-8 text-center">
            <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl border border-black/[0.08] bg-black/[0.03]">
              <Box className="h-6 w-6 text-haze-500" />
            </div>
            <p className="text-sm text-haze-200">当前作品还没有可加载的模型</p>
            <p className="mt-1 max-w-sm text-[11px] leading-relaxed text-haze-500">
              先在「导出」构建 Live2D 模型包；导出完成后会自动回填到本作品，预览页也就有了模型。
            </p>
            <button onClick={() => setStage('export')} className="btn-soft mt-3">
              去导出
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
