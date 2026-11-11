import { ParameterDef } from '../types';

interface ParameterSliderProps {
  param: ParameterDef;
  value: number;
  onChange?: (value: number) => void;
  disabled?: boolean;
}

export default function ParameterSlider({
  param,
  value,
  onChange,
  disabled = false,
}: ParameterSliderProps) {
  const range = param.max - param.min;
  const pct = range === 0 ? 0 : ((value - param.min) / range) * 100;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="truncate font-mono text-haze-300" title={param.name}>
          {param.name || param.id}
        </span>
        <span className="font-mono tabular-nums text-sakura-600">{value.toFixed(2)}</span>
      </div>
      <div className="relative">
        <input
          type="range"
          min={param.min}
          max={param.max}
          step={range / 100}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange?.(parseFloat(e.target.value))}
          className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-ink-700 disabled:cursor-not-allowed disabled:opacity-50
            [&::-webkit-slider-thumb]:appearance-none
            [&::-webkit-slider-thumb]:w-3.5
            [&::-webkit-slider-thumb]:h-3.5
            [&::-webkit-slider-thumb]:rounded-full
            [&::-webkit-slider-thumb]:bg-gradient-to-br
            [&::-webkit-slider-thumb]:from-sakura-400
            [&::-webkit-slider-thumb]:to-iris-500
            [&::-webkit-slider-thumb]:shadow-md
            [&::-webkit-slider-thumb]:shadow-sakura-500/40
            [&::-webkit-slider-thumb]:cursor-pointer
            [&::-moz-range-thumb]:w-3.5
            [&::-moz-range-thumb]:h-3.5
            [&::-moz-range-thumb]:rounded-full
            [&::-moz-range-thumb]:bg-gradient-to-br
            [&::-moz-range-thumb]:from-sakura-400
            [&::-moz-range-thumb]:to-iris-500
            [&::-moz-range-thumb]:border-0
            [&::-moz-range-thumb]:cursor-pointer"
          style={{
            background: `linear-gradient(to right, rgb(255 92 157) 0%, rgb(139 92 246) ${pct}%, rgb(35 27 52) ${pct}%, rgb(35 27 52) 100%)`,
          }}
        />
      </div>
      <div className="flex justify-between font-mono text-[10px] text-haze-600">
        <span>{param.min}</span>
        <span>default {param.default}</span>
        <span>{param.max}</span>
      </div>
    </div>
  );
}
