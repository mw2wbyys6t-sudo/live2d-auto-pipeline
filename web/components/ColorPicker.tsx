import { useState } from 'react';

interface ColorPickerProps {
  label?: string;
  value: string;
  onChange: (color: string) => void;
  swatches?: string[];
}

const DEFAULT_SWATCHES = [
  '#ec4899', '#8b5cf6', '#3b82f6', '#10b981', '#f59e0b',
  '#ef4444', '#f97316', '#eab308', '#14b8a6', '#6366f1',
  '#ffffff', '#1f2937', '#0f0f13',
];

export default function ColorPicker({
  label,
  value,
  onChange,
  swatches = DEFAULT_SWATCHES,
}: ColorPickerProps) {
  const [showPicker, setShowPicker] = useState(false);

  return (
    <div className="flex flex-col gap-1.5">
      {label && <label className="text-xs font-medium text-gray-400">{label}</label>}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setShowPicker((s) => !s)}
          className="w-9 h-9 rounded-lg border border-gray-700 shadow-inner hover:scale-105 transition-transform"
          style={{ backgroundColor: value }}
          aria-label="Pick color"
        />
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="flex-1 min-w-0 px-2.5 py-2 text-xs bg-gray-900 border border-gray-700 rounded-lg text-white font-mono focus:outline-none focus:border-pink-500 transition-colors uppercase"
          maxLength={7}
        />
        <input
          type="color"
          value={value.startsWith('#') ? value : '#000000'}
          onChange={(e) => onChange(e.target.value)}
          className="sr-only"
          tabIndex={-1}
          id={`color-${label || 'picker'}`}
        />
        <label
          htmlFor={`color-${label || 'picker'}`}
          className="px-2.5 py-2 text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg cursor-pointer transition-colors text-gray-300"
        >
          Pick
        </label>
      </div>
      {showPicker && (
        <div className="flex flex-wrap gap-1.5 p-2 bg-gray-900 border border-gray-800 rounded-lg animate-fade-in">
          {swatches.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => {
                onChange(c);
                setShowPicker(false);
              }}
              className={`w-6 h-6 rounded-md border transition-transform hover:scale-110 ${
                value.toLowerCase() === c.toLowerCase() ? 'border-white ring-2 ring-pink-500' : 'border-gray-700'
              }`}
              style={{ backgroundColor: c }}
              aria-label={c}
            />
          ))}
        </div>
      )}
    </div>
  );
}
