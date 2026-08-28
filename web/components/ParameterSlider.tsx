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
        <span className="text-gray-300 font-mono truncate" title={param.name}>
          {param.name || param.id}
        </span>
        <span className="text-pink-400 font-mono tabular-nums">{value.toFixed(2)}</span>
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
          className="w-full h-1.5 appearance-none bg-gray-800 rounded-full cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed
            [&::-webkit-slider-thumb]:appearance-none
            [&::-webkit-slider-thumb]:w-3.5
            [&::-webkit-slider-thumb]:h-3.5
            [&::-webkit-slider-thumb]:rounded-full
            [&::-webkit-slider-thumb]:bg-gradient-to-br
            [&::-webkit-slider-thumb]:from-pink-500
            [&::-webkit-slider-thumb]:to-purple-500
            [&::-webkit-slider-thumb]:shadow-md
            [&::-webkit-slider-thumb]:shadow-pink-500/30
            [&::-webkit-slider-thumb]:cursor-pointer
            [&::-moz-range-thumb]:w-3.5
            [&::-moz-range-thumb]:h-3.5
            [&::-moz-range-thumb]:rounded-full
            [&::-moz-range-thumb]:bg-gradient-to-br
            [&::-moz-range-thumb]:from-pink-500
            [&::-moz-range-thumb]:to-purple-500
            [&::-moz-range-thumb]:border-0
            [&::-moz-range-thumb]:cursor-pointer"
          style={{
            background: `linear-gradient(to right, rgb(236 72 153) 0%, rgb(139 92 246) ${pct}%, rgb(31 41 55) ${pct}%, rgb(31 41 55) 100%)`,
          }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-gray-600 font-mono">
        <span>{param.min}</span>
        <span>default {param.default}</span>
        <span>{param.max}</span>
      </div>
    </div>
  );
}
