/**
 * motion3.json 解析与采样。
 *
 * 段布局（官方语义）：`[t0, v0]` 之后是「段类型 + 该段的点」，点的个数按类型定：
 * 0 线性 1 点 / 1 贝塞尔 3 点 / 2 阶跃 1 点 / 3 逆阶跃 1 点。这个点数表由
 * drivers/live2d_runtime/moc3_motion_probe.py 在官方 Haru 的 6 个动作文件上
 * 逐项对账确认，不是猜的。
 */

export type MotionPoint = [number, number];

export interface MotionSegment {
  /** 0 线性 / 1 贝塞尔 / 2 阶跃 / 3 逆阶跃 */
  type: number;
  start: MotionPoint;
  /** 贝塞尔为 [控制点1, 控制点2, 终点]，其余为 [终点]。 */
  points: MotionPoint[];
  endTime: number;
}

export interface MotionCurve {
  id: string;
  start: MotionPoint;
  segments: MotionSegment[];
  /** 每段终点时间，供二分查找。 */
  ends: Float64Array;
  duration: number;
}

export interface MotionFile {
  duration: number;
  loop: boolean;
  fadeIn: number;
  fadeOut: number;
  /** 参数名 -> 曲线。 */
  curves: Map<string, MotionCurve>;
}

const SEGMENT_POINTS: Record<number, number> = { 0: 1, 1: 3, 2: 1, 3: 1 };

export function parseCurve(id: string, segments: readonly number[]): MotionCurve {
  if (segments.length < 2) {
    throw new Error(`曲线 ${id} 的 Segments 不完整`);
  }
  const start: MotionPoint = [segments[0], segments[1]];
  const out: MotionSegment[] = [];
  let time = start[0];
  const ends: number[] = [];
  let i = 2;
  while (i < segments.length) {
    const type = segments[i];
    const count = SEGMENT_POINTS[type];
    if (count === undefined) {
      throw new Error(`曲线 ${id} 里有未知段类型 ${type}（偏移 ${i}）`);
    }
    const points: MotionPoint[] = [];
    for (let k = 0; k < count; k++) {
      const x = segments[i + 1 + k * 2];
      const y = segments[i + 2 + k * 2];
      if (x === undefined || y === undefined) {
        throw new Error(`曲线 ${id} 的段 ${type} 点数不足（偏移 ${i}）`);
      }
      points.push([x, y]);
    }
    const end = points[points.length - 1];
    out.push({ type, start: [time, lastValue(out, start)], points, endTime: end[0] });
    time = end[0];
    ends.push(time);
    i += 1 + count * 2;
  }
  return {
    id,
    start,
    segments: out,
    ends: Float64Array.from(ends),
    duration: out.length ? ends[ends.length - 1] : start[0],
  };
}

function lastValue(segments: readonly MotionSegment[], start: MotionPoint): number {
  return segments.length ? segments[segments.length - 1].points.slice(-1)[0][1] : start[1];
}

function cubic(a: number, b: number, c: number, d: number, t: number): number {
  const s = 1 - t;
  return s * s * s * a + 3 * s * s * t * b + 3 * s * t * t * c + t * t * t * d;
}

/** 贝塞尔段：按 x 反解 t（二分），再取 y。控制点的 x 由导出器夹在段内，单调。 */
function bezierAt(seg: MotionSegment, time: number): number {
  const [c1, c2, end] = seg.points;
  const x0 = seg.start[0];
  if (end[0] <= x0) return end[1];
  let lo = 0;
  let hi = 1;
  for (let k = 0; k < 24; k++) {
    const mid = (lo + hi) * 0.5;
    const x = cubic(x0, c1[0], c2[0], end[0], mid);
    if (x < time) lo = mid;
    else hi = mid;
  }
  return cubic(seg.start[1], c1[1], c2[1], end[1], (lo + hi) * 0.5);
}

/** 曲线在 time 处的值；两端之外取端点值（与内核一致，不外推）。 */
export function sampleCurve(curve: MotionCurve, time: number): number {
  if (!curve.segments.length) return curve.start[1];
  if (time <= curve.start[0]) return curve.start[1];
  const { segments, ends } = curve;
  if (time >= ends[ends.length - 1]) {
    const last = segments[segments.length - 1];
    return last.points[last.points.length - 1][1];
  }
  let lo = 0;
  let hi = ends.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ends[mid] <= time) lo = mid + 1;
    else hi = mid;
  }
  const seg = segments[lo];
  switch (seg.type) {
    case 1:
      return bezierAt(seg, time);
    case 2: // 阶跃：到下一段之前保持段首值
      return seg.start[1];
    case 3: // 逆阶跃：段一开始就跳到段末值
      return seg.points[0][1];
    default: {
      const end = seg.points[0];
      const span = end[0] - seg.start[0];
      if (span <= 0) return end[1];
      return seg.start[1] + ((end[1] - seg.start[1]) * (time - seg.start[0])) / span;
    }
  }
}

export interface MotionJsonLike {
  Meta?: { Duration?: number; Loop?: boolean; FadeInTime?: number; FadeOutTime?: number };
  Curves?: Array<{ Target?: string; Id?: string; Segments?: number[] }>;
}

/** 把 motion3.json 转成可采样结构；只保留 Parameter 目标（Part 曲线暂不驱动）。 */
export function parseMotion(
  json: MotionJsonLike,
  override: { fadeIn?: number; fadeOut?: number } = {},
): MotionFile {
  const meta = json.Meta || {};
  const num = (value: unknown, fallback = 0): number =>
    typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  const curves = new Map<string, MotionCurve>();
  for (const c of json.Curves || []) {
    if (c.Target !== 'Parameter' || !c.Id || !Array.isArray(c.Segments)) continue;
    curves.set(c.Id, parseCurve(c.Id, c.Segments));
  }
  return {
    duration: num(meta.Duration),
    loop: meta.Loop !== false,
    fadeIn: override.fadeIn ?? num(meta.FadeInTime),
    fadeOut: override.fadeOut ?? num(meta.FadeOutTime),
    curves,
  };
}

/**
 * 动作播放位置。`elapsed` 是自播放开始的秒数；淡入/淡出按线性权重给，
 * 循环动作在环回点不重放淡入（与内核的 Loop 语义一致）。
 */
export class MotionPlayback {
  readonly file: MotionFile;
  readonly group: string;
  readonly index: number;
  elapsed = 0;

  constructor(file: MotionFile, group: string, index: number) {
    this.file = file;
    this.group = group;
    this.index = index;
  }

  get time(): number {
    if (this.file.duration <= 0) return 0;
    return this.file.loop ? this.elapsed % this.file.duration : Math.min(this.elapsed, this.file.duration);
  }

  get finished(): boolean {
    return !this.file.loop && this.elapsed >= this.file.duration;
  }

  /** 权重：开头淡入、结尾淡出；循环时只有第一次淡入。 */
  get weight(): number {
    const { fadeIn, fadeOut, duration } = this.file;
    let weight = 1;
    if (fadeIn > 0 && this.elapsed < fadeIn) weight = this.elapsed / fadeIn;
    if (fadeOut > 0 && duration > 0 && !this.file.loop) {
      const remain = duration - this.elapsed;
      if (remain < fadeOut) weight = Math.min(weight, Math.max(0, remain / fadeOut));
    }
    return weight;
  }

  advance(dt: number): void {
    this.elapsed += dt;
  }

  /** 该时刻要写进参数的键值对（未混入基础值）。 */
  sample(): Map<string, number> {
    const out = new Map<string, number>();
    const t = this.time;
    for (const [id, curve] of this.file.curves) out.set(id, sampleCurve(curve, t));
    return out;
  }
}
