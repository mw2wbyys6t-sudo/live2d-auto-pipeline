import type { Model3Json, ParameterDef } from '../types';

/**
 * Parameter model for the in-house renderer (no third-party runtime).
 *
 * The distinction below matters: `resolveParameters` answers "which parameters
 * exist and what should a slider show", while `resolveBounds` answers "what am
 * I actually allowed to clamp to". Bounds are only recorded when they come from
 * a trustworthy source — the model's own declaration, or the known Live2D
 * standard range. A parameter we know nothing about is deliberately left
 * unclamped rather than squeezed into a guessed [-1, 1].
 */

export const STANDARD_PARAMS: ParameterDef[] = [
  { id: 'ParamAngleX', name: 'Angle X', min: -30, max: 30, default: 0, group: 'Head' },
  { id: 'ParamAngleY', name: 'Angle Y', min: -30, max: 30, default: 0, group: 'Head' },
  { id: 'ParamAngleZ', name: 'Angle Z', min: -30, max: 30, default: 0, group: 'Head' },
  { id: 'ParamEyeLOpen', name: 'Eye L Open', min: 0, max: 1, default: 1, group: 'Eyes' },
  { id: 'ParamEyeROpen', name: 'Eye R Open', min: 0, max: 1, default: 1, group: 'Eyes' },
  { id: 'ParamEyeBallX', name: 'Eye Ball X', min: -1, max: 1, default: 0, group: 'Eyes' },
  { id: 'ParamEyeBallY', name: 'Eye Ball Y', min: -1, max: 1, default: 0, group: 'Eyes' },
  { id: 'ParamMouthForm', name: 'Mouth Form', min: -1, max: 1, default: 0, group: 'Mouth' },
  { id: 'ParamMouthOpenY', name: 'Mouth Open', min: 0, max: 1, default: 0, group: 'Mouth' },
  { id: 'ParamBrowLY', name: 'Brow L Y', min: -1, max: 1, default: 0, group: 'Brows' },
  { id: 'ParamBrowRY', name: 'Brow R Y', min: -1, max: 1, default: 0, group: 'Brows' },
  { id: 'ParamBodyAngleX', name: 'Body Angle X', min: -10, max: 10, default: 0, group: 'Body' },
  { id: 'ParamBreath', name: 'Breath', min: 0, max: 1, default: 0, group: 'Body' },
];

export interface ParameterBounds {
  min: number;
  max: number;
}

/**
 * The parameter list to expose for sliders / the Validate tab.
 *
 * When the manifest declares `Parameters` (our exporter writes the real 28+
 * declarations), that list is authoritative; otherwise we fall back to the
 * standard set. Previously the count was always the hard-coded standard length,
 * which made the Validate tab report "only 13 parameters" for every model.
 */
export function resolveParameters(model3: Model3Json | null | undefined): ParameterDef[] {
  const declared = model3?.Parameters;
  if (!declared || declared.length === 0) {
    return STANDARD_PARAMS.map((p) => ({ ...p }));
  }
  const known = new Map(STANDARD_PARAMS.map((p) => [p.id, p]));
  return declared.map((raw) => {
    const std = known.get(raw.Id);
    return {
      id: raw.Id,
      name: std?.name ?? raw.Id,
      min: typeof raw.Min === 'number' ? raw.Min : std?.min ?? -1,
      max: typeof raw.Max === 'number' ? raw.Max : std?.max ?? 1,
      default: typeof raw.Default === 'number' ? raw.Default : std?.default ?? 0,
      group: std?.group ?? 'Other',
    };
  });
}

/** Clamp bounds, recorded only from trustworthy sources (see module docstring). */
export function resolveBounds(model3: Model3Json | null | undefined): Map<string, ParameterBounds> {
  const out = new Map<string, ParameterBounds>();
  for (const p of STANDARD_PARAMS) {
    out.set(p.id, { min: p.min, max: p.max });
  }
  for (const raw of model3?.Parameters ?? []) {
    if (
      typeof raw.Min === 'number' &&
      typeof raw.Max === 'number' &&
      Number.isFinite(raw.Min) &&
      Number.isFinite(raw.Max) &&
      raw.Max > raw.Min
    ) {
      // 模型自带声明优先于标准表
      out.set(raw.Id, { min: raw.Min, max: raw.Max });
    }
  }
  return out;
}

export function clampToBounds(value: number, bounds?: ParameterBounds): number {
  if (!Number.isFinite(value)) {
    return bounds ? bounds.min : 0;
  }
  if (!bounds || bounds.max <= bounds.min) {
    return value;
  }
  return Math.min(bounds.max, Math.max(bounds.min, value));
}
