import { Check, Loader2 } from 'lucide-react';
import { useWorkbench } from '../../lib/workbench-context';
import { STAGES, activeStepLabel, overallProgress, stageReady } from '../../lib/workbench';

export default function StageBar() {
  const { stage, setStage, current, generation } = useWorkbench();
  const progress = overallProgress(generation.steps);
  const activeLabel = activeStepLabel(generation.steps);

  return (
    <div className="border-b border-gray-800 bg-[#12121a] px-4">
      <div className="flex items-center gap-1 overflow-x-auto">
        {STAGES.map((item, index) => {
          const active = stage === item.id;
          const ready = stageReady(current, item.id);
          return (
            <button
              key={item.id}
              onClick={() => setStage(item.id)}
              title={item.hint}
              className={`relative flex items-center gap-2.5 px-4 py-3 text-sm font-medium transition-colors shrink-0 ${
                active ? 'text-white' : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              <span
                className={`w-5 h-5 rounded-full flex items-center justify-center text-[11px] border ${
                  active
                    ? 'border-pink-500 bg-pink-500/20 text-pink-300'
                    : ready
                      ? 'border-emerald-600/60 bg-emerald-500/10 text-emerald-400'
                      : 'border-gray-700 text-gray-500'
                }`}
              >
                {ready && !active ? <Check className="w-3 h-3" /> : index + 1}
              </span>
              <span className="whitespace-nowrap">{item.label}</span>
              {active && <span className="absolute bottom-0 left-2 right-2 h-0.5 bg-pink-500 rounded-full" />}
            </button>
          );
        })}
      </div>

      {generation.running && (
        <div className="flex items-center gap-3 pb-2 text-xs text-gray-400">
          <span className="flex items-center gap-1.5 text-pink-300 shrink-0">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            {activeLabel || '处理中'}
          </span>
          <div className="flex-1 h-1 rounded-full bg-gray-800 overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-pink-500 to-purple-500 transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="tabular-nums shrink-0">{progress}%</span>
        </div>
      )}
    </div>
  );
}
