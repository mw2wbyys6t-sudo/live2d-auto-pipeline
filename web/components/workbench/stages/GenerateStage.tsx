import { Download, ImageOff, Layers, Loader2, RefreshCw, Wand2 } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { promptExcerpt } from '../../../lib/workbench';

export default function GenerateStage() {
  const { current, generation, setStage, setInspector, generate } = useWorkbench();
  const imageUrl = current?.imageUrl || '';

  return (
    <div className="h-full flex flex-col p-4 gap-3 min-h-0">
      <header className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-white">角色立绘</h2>
          <p className="text-[11px] text-gray-500 truncate">
            {current ? promptExcerpt(current.prompt, 46) : '输入中文提示词，一次跑完生成 → 质检 → 分层 → 绑定'}
          </p>
        </div>
        {imageUrl && (
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              onClick={() => void generate()}
              disabled={generation.running}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" /> 重新生成
            </button>
            <a
              href={imageUrl}
              download={`${current?.characterId || 'character'}-${Date.now()}.png`}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-[11px] text-emerald-300 hover:bg-emerald-500/25 transition-colors"
            >
              <Download className="w-3.5 h-3.5" /> 保存图片
            </a>
            {current?.layersDir && (
              <button
                onClick={() => setStage('layers')}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-pink-500/15 border border-pink-500/40 text-[11px] text-pink-300 hover:bg-pink-500/25 transition-colors"
              >
                <Layers className="w-3.5 h-3.5" /> 去分层
              </button>
            )}
          </div>
        )}
      </header>

      <div className="flex-1 min-h-0 rounded-xl border border-gray-800 bg-[#0b0b10] overflow-hidden relative flex items-center justify-center">
        {generation.running ? (
          <div className="w-full max-w-sm px-6 space-y-3">
            <div className="flex items-center gap-2 text-sm text-gray-300">
              <Loader2 className="w-4 h-4 animate-spin text-pink-400" />
              正在生成
            </div>
            <ul className="space-y-1.5">
              {generation.steps.map((step) => (
                <li key={step.id} className="flex items-center gap-2 text-[11px]">
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      step.status === 'done'
                        ? 'bg-emerald-400'
                        : step.status === 'active'
                          ? 'bg-pink-400 animate-pulse'
                          : step.status === 'error'
                            ? 'bg-red-400'
                            : 'bg-gray-700'
                    }`}
                  />
                  <span className={step.status === 'active' ? 'text-gray-200' : 'text-gray-500'}>{step.label}</span>
                  <span className="ml-auto tabular-nums text-gray-600">
                    {step.status === 'done' ? '完成' : step.status === 'active' ? `${step.progress || 0}%` : ''}
                  </span>
                </li>
              ))}
            </ul>
            {generation.message && <p className="text-[10px] text-gray-600">{generation.message}</p>}
          </div>
        ) : imageUrl ? (
          <div className="w-full h-full flex items-center justify-center p-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={imageUrl}
              alt={current?.prompt || '生成结果'}
              className="max-w-full max-h-full object-contain rounded-lg shadow-2xl shadow-black/50"
            />
            <div className="absolute left-3 bottom-3 flex items-center gap-2 text-[10px] text-gray-500 bg-black/40 rounded px-2 py-1">
              <span>
                {current?.width}×{current?.height}
              </span>
              <span className="w-px h-3 bg-gray-700" />
              <span className="truncate max-w-[320px]">{promptExcerpt(current?.prompt || '', 40)}</span>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center text-center px-8">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-pink-500/20 to-purple-500/20 border border-pink-500/30 flex items-center justify-center mb-4">
              <Wand2 className="w-7 h-7 text-pink-400" />
            </div>
            <p className="text-sm text-gray-300 font-medium">还没有作品</p>
            <p className="text-xs text-gray-500 mt-1 max-w-sm leading-relaxed">
              在右侧写一句中文描述（例如「一位穿水手服的粉发少女，日系动漫风格」），点「开始生成」。
            </p>
            <button
              onClick={() => setInspector('prompt')}
              className="mt-4 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-[11px] text-gray-300 hover:bg-gray-800 transition-colors"
            >
              <Wand2 className="w-3.5 h-3.5" /> 去写提示词
            </button>
            <p className="mt-3 flex items-center gap-1 text-[10px] text-gray-600">
              <ImageOff className="w-3 h-3" /> 生成的图片与图层会保存在 output 目录
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
