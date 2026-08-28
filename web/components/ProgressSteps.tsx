import { Check, Loader2 } from 'lucide-react';
import type { GenerationStep } from '../types';

interface ProgressStepsProps {
  steps: GenerationStep[];
  className?: string;
}

export default function ProgressSteps({ steps, className = '' }: ProgressStepsProps) {
  return (
    <div className={`w-full ${className}`}>
      <div className="flex flex-col gap-2">
        {steps.map((step, idx) => {
          const isLast = idx === steps.length - 1;
          return (
            <div key={step.id} className="flex items-start gap-3">
              <div className="flex flex-col items-center">
                <div
                  className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold transition-all ${
                    step.status === 'done'
                      ? 'bg-emerald-500 text-white shadow-md shadow-emerald-500/30'
                      : step.status === 'active'
                      ? 'bg-gradient-to-br from-pink-500 to-purple-500 text-white shadow-md shadow-pink-500/30 animate-pulse-glow'
                      : step.status === 'error'
                      ? 'bg-red-500 text-white'
                      : 'bg-gray-800 text-gray-500 border border-gray-700'
                  }`}
                >
                  {step.status === 'done' ? (
                    <Check className="w-4 h-4" />
                  ) : step.status === 'active' ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : step.status === 'error' ? (
                    '!'
                  ) : (
                    idx + 1
                  )}
                </div>
                {!isLast && (
                  <div
                    className={`w-0.5 h-6 ${
                      step.status === 'done' ? 'bg-emerald-500/50' : 'bg-gray-800'
                    }`}
                  />
                )}
              </div>
              <div className="flex-1 pb-1">
                <div className="flex items-center justify-between">
                  <p
                    className={`text-sm font-medium ${
                      step.status === 'done'
                        ? 'text-emerald-300'
                        : step.status === 'active'
                        ? 'text-white'
                        : step.status === 'error'
                        ? 'text-red-400'
                        : 'text-gray-500'
                    }`}
                  >
                    {step.label}
                  </p>
                  {step.status === 'active' && (
                    <span className="text-xs text-pink-400">{step.progress}%</span>
                  )}
                </div>
                {step.message && (
                  <p className="text-xs text-gray-500 mt-0.5">{step.message}</p>
                )}
                {step.status === 'active' && (
                  <div className="mt-2 h-1 bg-gray-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-pink-500 to-purple-500 transition-all duration-300"
                      style={{ width: `${step.progress}%` }}
                    />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
