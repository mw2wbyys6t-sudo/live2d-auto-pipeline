import { Download, ImageOff, Image as ImageIcon, Layers, Loader2, RefreshCw, Wand2 } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { promptExcerpt } from '../../../lib/workbench';
import StageHeader from '../StageHeader';

export default function GenerateStage() {
  const { current, generation, setStage, setInspector, generate } = useWorkbench();
  const imageUrl = current?.imageUrl || '';

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <StageHeader
        icon={<ImageIcon className="h-4 w-4" />}
        title="角色立绘"
        description={
          current
            ? promptExcerpt(current.prompt, 46)
            : '输入中文提示词，一次跑完生成 → 质检 → 分层 → 绑定'
        }
        badge={
          current?.fallback ? (
            <span className="chip chip-active border-amber-400/40 bg-amber-400/10 text-amber-700">占位图</span>
          ) : imageUrl ? (
            <span className="chip tabular-nums">
              {current?.width}×{current?.height}
            </span>
          ) : null
        }
        actions={
          imageUrl ? (
            <>
              <button onClick={() => void generate()} disabled={generation.running} className="btn-ghost">
                <RefreshCw className="h-3.5 w-3.5" /> 重新生成
              </button>
              <a href={imageUrl} download={`${current?.characterId || 'character'}-${Date.now()}.png`} className="btn-mint">
                <Download className="h-3.5 w-3.5" /> 保存图片
              </a>
              {current?.layersDir && (
                <button onClick={() => setStage('layers')} className="btn-soft">
                  <Layers className="h-3.5 w-3.5" /> 去分层
                </button>
              )}
            </>
          ) : null
        }
      />

      <div className="stage-frame">
        {generation.running ? (
          <div className="w-full max-w-sm space-y-4 px-6">
            <div className="flex items-center gap-2 text-sm text-haze-200">
              <Loader2 className="h-4 w-4 animate-spin text-sakura-600" />
              正在生成
            </div>
            <ul className="space-y-2">
              {generation.steps.map((step) => (
                <li key={step.id} className="flex items-center gap-2.5 text-[11px]">
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      step.status === 'done'
                        ? 'bg-mint-400'
                        : step.status === 'active'
                          ? 'bg-sakura-400'
                          : step.status === 'error'
                            ? 'bg-rose-400'
                            : 'bg-black/15'
                    }`}
                  />
                  <span className={step.status === 'active' ? 'text-haze-100' : 'text-haze-500'}>{step.label}</span>
                  <span className="ml-auto tabular-nums text-haze-600">
                    {step.status === 'done' ? '完成' : step.status === 'active' ? `${step.progress || 0}%` : ''}
                  </span>
                </li>
              ))}
            </ul>
            {generation.message && <p className="text-[10px] text-haze-600">{generation.message}</p>}
          </div>
        ) : imageUrl ? (
          <div className="relative flex h-full w-full items-center justify-center p-4">
            {/* 占位图必须显眼地自报身份：后端 success=true 只说明"跑完了"，
                不说明"画出了人物"。实测安装包把本地几何占位图当正常立绘展示。 */}
            {current?.fallback && (
              <div className="absolute left-3 right-3 top-3 z-10 rounded-xl border border-amber-400/35 bg-ink-950/90 px-3 py-2 text-[11px] leading-relaxed text-amber-700 backdrop-blur">
                <span className="font-medium">离线占位图 —— 这不是 AI 生成的人物。</span>
                {current.fallbackReason ? ` ${current.fallbackReason}` : ''}
                <span> 在「设置」里配置可用的图像服务后重试。</span>
              </div>
            )}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={imageUrl}
              alt={current?.prompt || '生成结果'}
              className="relative max-h-full max-w-full rounded-xl object-contain shadow-2xl shadow-black/20 ring-1 ring-black/10"
            />
            <div className="absolute bottom-3 left-3 flex items-center gap-2 rounded-lg bg-ink-950/75 px-2 py-1 text-[10px] text-haze-400 backdrop-blur">
              <span className="tabular-nums">
                {current?.width}×{current?.height}
              </span>
              <span className="h-3 w-px bg-black/10" />
              <span className="max-w-[320px] truncate">{promptExcerpt(current?.prompt || '', 40)}</span>
            </div>
          </div>
        ) : (
          <div className="flex max-w-sm flex-col items-center px-8 text-center">
            <div className="ring-gradient mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-sakura-500/20 to-iris-500/20">
              <Wand2 className="h-7 w-7 text-sakura-600" />
            </div>
            <p className="text-sm font-medium text-haze-200">还没有作品</p>
            <p className="mt-1.5 text-xs leading-relaxed text-haze-500">
              在右侧写一句中文描述（例如「一位穿水手服的粉发少女，日系动漫风格」），点「开始生成」。
            </p>
            <button onClick={() => setInspector('prompt')} className="btn-soft mt-4">
              <Wand2 className="h-3.5 w-3.5" /> 去写提示词
            </button>
            <p className="mt-3 flex items-center gap-1 text-[10px] text-haze-600">
              <ImageOff className="h-3 w-3" /> 生成的图片与图层会保存在 output 目录
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
