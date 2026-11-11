import { Fragment } from 'react';
import { Check, Loader2 } from 'lucide-react';
import { useWorkbench } from '../../lib/workbench-context';
import { STAGES, activeStepLabel, overallProgress, stageReady } from '../../lib/workbench';

export default function StageBar() {
  const { stage, setStage, current, generation } = useWorkbench();
  const progress = overallProgress(generation.steps);
  const activeLabel = activeStepLabel(generation.steps);

  return (
    <div className="shrink-0 border-b border-black/[0.07] bg-ink-900/45 px-4">
      <div className="flex items-center overflow-x-auto py-2">
        {STAGES.map((item, index) => {
          const active = stage === item.id;
          const ready = stageReady(current, item.id);
          return (
            <Fragment key={item.id}>
              {index > 0 && (
                <span
                  className={`mx-1 h-px w-4 shrink-0 lg:w-6 ${
                    ready ? 'bg-mint-400/25' : 'bg-black/[0.08]'
                  }`}
                />
              )}
              <button
                onClick={() => setStage(item.id)}
                title={item.hint}
                aria-current={active ? 'step' : undefined}
                className={`group flex shrink-0 items-center gap-2.5 rounded-xl px-3 py-1.5 transition-colors ${
                  active
                    ? 'ring-gradient bg-black/[0.05] text-haze-100'
                    : ready
                      ? 'text-haze-300 hover:bg-black/[0.03]'
                      : 'text-haze-500 hover:bg-black/[0.03] hover:text-haze-300'
                }`}
              >
                <span
                  className={`flex h-6 w-6 items-center justify-center rounded-full border text-[11px] font-semibold tabular-nums transition-colors ${
                    active
                      ? 'border-transparent bg-gradient-to-br from-sakura-500 to-iris-600 text-white shadow-glow-sakura'
                      : ready
                        ? 'border-mint-400/40 bg-mint-400/10 text-mint-600'
                        : 'border-black/10 bg-black/[0.02] text-haze-500'
                  }`}
                >
                  {ready && !active ? <Check className="h-3 w-3" /> : index + 1}
                </span>
                <span className="flex flex-col items-start leading-tight">
                  <span className="whitespace-nowrap text-xs font-medium">{item.label}</span>
                  <span className="hidden whitespace-nowrap text-[10px] font-normal text-haze-600 xl:block">
                    {item.hint}
                  </span>
                </span>
              </button>
            </Fragment>
          );
        })}
      </div>

      {generation.running && (
        <div className="flex items-center gap-3 pb-2 text-[11px] text-haze-400">
          <span className="flex shrink-0 items-center gap-1.5 text-sakura-600">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {activeLabel || '处理中'}
          </span>
          <div className="h-0.5 flex-1 overflow-hidden rounded-full bg-black/[0.05]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-sakura-500 to-iris-500 transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="shrink-0 tabular-nums text-haze-500">{progress}%</span>
        </div>
      )}
    </div>
  );
}
