/**
 * physics3.json 的运行时。官方内核用 CubismPhysics 驱动；我们自研渲染器里要
 * 复现的是同一件事：输入参数（头/身体角度、呼吸）变化后，头发/裙摆/肩膀的
 * 输出参数带滞后、过冲，最后回落到静止。
 *
 * 模型：每条物理链是一串质点。第 0 个质点随输入平移；其余质点用阻尼弹簧追
 * 「挂在父质点正下方」的静止位。弹簧刚度来自 Mobility、阻尼来自 Delay、
 * Acceleration 放大刚度——这正是 live2d_builder/physics/config.py 生成
 * physics3.json 时用的反向映射，语义在两边对得上。
 *
 * 输出取（被引用质点 − 根质点）的几何夹角：输入阶跃当帧，子质点还没跟上，
 * 夹角立刻出现；随后弹簧把它拉回去，于是有滞后与过冲。输出再按 Angle 归一
 * 化区间折算到目标参数自己的量程——bindings 的键形就是按参数全量程授权的，
 * 写归一化值会小得看不出形变。
 */

export interface PhysicsNormalization {
  minimum: number;
  default: number;
  maximum: number;
}

export interface PhysicsInput {
  source: string;
  weight: number;
  type: 'X' | 'Y';
  reflect: boolean;
}

export interface PhysicsOutput {
  destination: string;
  vertexIndex: number;
  scale: number;
  weight: number;
  type: 'Angle' | 'X' | 'Y';
  reflect: boolean;
}

export interface PhysicsVertex {
  position: [number, number];
  mobility: number;
  delay: number;
  acceleration: number;
  radius: number;
}

export interface PhysicsSetting {
  id: string;
  name: string;
  input: PhysicsInput[];
  output: PhysicsOutput[];
  vertices: PhysicsVertex[];
  position: PhysicsNormalization;
  angle: PhysicsNormalization;
}

export interface PhysicsDocument {
  fps: number;
  gravity: [number, number];
  settings: PhysicsSetting[];
}

/** 参数行程：物理输入归一化与输出折算都要用。 */
export interface ParameterRange {
  min: number;
  default: number;
  max: number;
}

const num = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback;

function normalization(raw: any, fallback: number): PhysicsNormalization {
  const source = raw || {};
  const minimum = num(source.Minimum ?? source.minimum, -fallback);
  const maximum = num(source.Maximum ?? source.maximum, fallback);
  const fallbackDefault = (minimum + maximum) / 2;
  const def = num(source.Default ?? source.default, fallbackDefault);
  return { minimum, default: def, maximum };
}

export function parsePhysics3(json: any): PhysicsDocument {
  const meta = json?.Meta || {};
  const gravity = meta.EffectiveForces?.Gravity || {};
  const settings: PhysicsSetting[] = [];
  for (const raw of json?.PhysicsSettings || []) {
    const vertices: PhysicsVertex[] = [];
    for (const vertex of raw.Vertices || []) {
      vertices.push({
        position: [num(vertex.Position?.X, 0), num(vertex.Position?.Y, 0)],
        mobility: num(vertex.Mobility, 1),
        delay: num(vertex.Delay, 1),
        acceleration: num(vertex.Acceleration, 1),
        radius: num(vertex.Radius, 0),
      });
    }
    if (vertices.length < 2) continue;
    settings.push({
      id: String(raw.Id ?? raw.Name ?? `setting_${settings.length}`),
      name: String(raw.Name ?? raw.Id ?? ''),
      input: (raw.Input || []).map((entry: any) => ({
        source: String(entry.Source?.Id ?? ''),
        weight: num(entry.Weight, 100),
        type: entry.Type === 'Y' ? 'Y' : 'X',
        reflect: Boolean(entry.Reflect),
      })),
      output: (raw.Output || []).map((entry: any) => ({
        destination: String(entry.Destination?.Id ?? ''),
        vertexIndex: Math.min(vertices.length - 1, Math.max(1, Math.trunc(num(entry.VertexIndex, 1)))),
        scale: num(entry.Scale, 1),
        weight: num(entry.Weight, 100),
        type: entry.Type === 'Y' ? 'Y' : entry.Type === 'X' ? 'X' : 'Angle',
        reflect: Boolean(entry.Reflect),
      })),
      vertices,
      position: normalization(raw.Normalization?.Position, 10),
      angle: normalization(raw.Normalization?.Angle, 10),
    });
  }
  return {
    fps: Math.max(1, num(meta.Fps, 60)),
    gravity: [num(gravity.X, 0), num(gravity.Y, -1)],
    settings,
  };
}

/** 参数值按「默认值两侧分别归一化」压到 [-1, 1]，与内核一致。 */
export function normalizeParameter(value: number, range: ParameterRange): number {
  const { min, default: def, max } = range;
  if (value <= def) {
    const span = def - min;
    return span > 1e-9 ? Math.max(-1, (value - def) / span) : 0;
  }
  const span = max - def;
  return span > 1e-9 ? Math.min(1, (value - def) / span) : 0;
}

/** 归一化值折回参数量程。 */
export function denormalizeParameter(value: number, range: ParameterRange): number {
  const clamped = Math.max(-1, Math.min(1, value));
  if (clamped <= 0) {
    const span = range.default - range.min;
    return span > 1e-9 ? range.default + clamped * span : range.default;
  }
  const span = range.max - range.default;
  return span > 1e-9 ? range.default + clamped * span : range.default;
}

/*
 * 常数是按官方内核的阶跃响应标定出来的，不是拍脑袋：把 5 条物理链共 10 组
 * 「输入阶跃 → 输出轨迹」做形状归一后与 kernel_physics_trace 比对，取相关
 * 系数均值最高的一组（0.69；头发 0.66、身体 0.53、呼吸 0.87、裙摆 0.76）。
 */
const STIFFNESS_GAIN = 740;  // 弹簧强度：把 (1 - Mobility) 映射到 1/s²
const DAMPING_GAIN = 107;    // 阻尼系数：Delay 越大越「跟得住」，阻尼越小
const OUTPUT_GAIN = 1;      // 归一化输出 → 参数量程占比
/*
 * Position 归一化半量程和 Radius 不是一个量纲：这份产物里呼吸链 Position 是
 * ±10、Radius 却有 20，直接拿半个量程当根位移会把链推到 Angle 归一化之外，
 * 输出被夹死成常量（实跑：呼吸输出恒为 -0.165，看不见呼吸起伏）。按链长取
 * 一小段位移，保证满量程输入下夹角落在 Angle 归一化区间内。
 */
const TRANSLATION_RATIO = 0.12;
const MAX_SUBSTEPS = 8;

interface Chain {
  setting: PhysicsSetting;
  x: Float64Array;
  y: Float64Array;
  vx: Float64Array;
  vy: Float64Array;
  stiffness: Float64Array;
  damping: Float64Array;
  /** 归一化输入 → 质点位移，量纲与 Radius 一致（取 Position 归一化半量程）。 */
  translation: number;
}

export interface PhysicsListeners {
  read(name: string): number;
  range(name: string): ParameterRange | undefined;
  write(name: string, value: number): void;
}

export class PhysicsEngine {
  readonly document: PhysicsDocument;
  private readonly chains: Chain[] = [];
  private accumulator = 0;

  constructor(document: PhysicsDocument) {
    this.document = document;
    for (const setting of document.settings) {
      const count = setting.vertices.length;
      const chain: Chain = {
        setting,
        x: new Float64Array(count),
        y: new Float64Array(count),
        vx: new Float64Array(count),
        vy: new Float64Array(count),
        stiffness: new Float64Array(count),
        damping: new Float64Array(count),
        translation: 0,
      };
      for (let i = 0; i < count; i++) {
        const vertex = setting.vertices[i];
        chain.x[i] = vertex.position[0];
        chain.y[i] = vertex.position[1];
        if (i > 0) {
          chain.stiffness[i] = (1 - Math.min(1, Math.max(0, vertex.mobility))) *
            STIFFNESS_GAIN * Math.max(0.1, vertex.acceleration);
          chain.damping[i] = Math.max(0.01, 1 - Math.min(1, Math.max(0, vertex.delay))) *
            DAMPING_GAIN;
        }
      }
      const halfRange = (setting.position.maximum - setting.position.minimum) / 2;
      const reach = Math.max(1e-3, Math.abs(setting.vertices[count - 1].radius));
      chain.translation = Math.min(halfRange, reach * TRANSLATION_RATIO);
      this.chains.push(chain);
    }
  }

  get chainCount(): number {
    return this.chains.length;
  }

  reset(): void {
    for (const chain of this.chains) {
      const { setting, x, y, vx, vy } = chain;
      for (let i = 0; i < setting.vertices.length; i++) {
        x[i] = setting.vertices[i].position[0];
        y[i] = setting.vertices[i].position[1];
        vx[i] = 0;
        vy[i] = 0;
      }
    }
    this.accumulator = 0;
  }

  /** 以固定子步推进，dt 由渲染循环给。 */
  step(dt: number, io: PhysicsListeners): void {
    if (!(dt > 0)) return;
    this.accumulator += Math.min(dt, 0.25);
    const fixed = 1 / this.document.fps;
    let steps = 0;
    while (this.accumulator >= fixed && steps < MAX_SUBSTEPS) {
      this.advance(fixed, io);
      this.accumulator -= fixed;
      steps++;
    }
    if (steps === MAX_SUBSTEPS) this.accumulator = 0;
  }

  private advance(dt: number, io: PhysicsListeners): void {
    // 多条链可能写同一个目标参数（这份产物里 HairFront/HairBack 都写
    // ParamHairSwing，BodyBounce/Skirt 都写 ParamBodySway）。逐链直接写会变成
    // 后写者覆盖先写者，按输出 Weight 加权平均才是两条链都在起作用。
    const pending = new Map<string, { value: number; weight: number }>();
    for (const chain of this.chains) {
      const { setting } = chain;
      let total = 0;
      for (const input of setting.input) {
        const range = io.range(input.source);
        if (!range) continue;
        const normalized = normalizeParameter(io.read(input.source), range);
        total += (input.reflect ? -normalized : normalized) * (input.weight / 100);
      }
      total = Math.max(-1, Math.min(1, total));
      const rootX = total * chain.translation;

      const count = setting.vertices.length;
      chain.x[0] = rootX;
      chain.y[0] = 0;
      chain.vx[0] = 0;
      chain.vy[0] = 0;

      for (let i = 1; i < count; i++) {
        const targetX = chain.x[i - 1];
        const targetY = chain.y[i - 1] + (setting.vertices[i].radius - setting.vertices[i - 1].radius);
        const k = chain.stiffness[i];
        const c = chain.damping[i];
        chain.vx[i] += ((targetX - chain.x[i]) * k - chain.vx[i] * c) * dt;
        chain.vy[i] += ((targetY - chain.y[i]) * k - chain.vy[i] * c) * dt;
        chain.x[i] += chain.vx[i] * dt;
        chain.y[i] += chain.vy[i] * dt;
      }

      for (const output of setting.output) {
        const range = io.range(output.destination);
        if (!range) continue;
        const index = Math.min(count - 1, output.vertexIndex);
        const dx = chain.x[index] - chain.x[0];
        const dy = chain.y[index] - chain.y[0];
        const angle = Math.atan2(dx, Math.abs(dy) < 1e-6 ? 1e-6 : dy);
        const angleSpan = Math.max(1e-3,
          Math.max(Math.abs(setting.angle.maximum), Math.abs(setting.angle.minimum)) *
          (Math.PI / 180));
        const normalized = Math.max(-1, Math.min(1, angle / angleSpan));
        const signed = output.reflect ? -normalized : normalized;
        const value = denormalizeParameter(signed * output.scale * OUTPUT_GAIN, range);
        const basket = pending.get(output.destination) || { value: 0, weight: 0 };
        basket.value += value * output.weight;
        basket.weight += output.weight;
        pending.set(output.destination, basket);
      }
    }
    for (const [name, basket] of pending) {
      if (basket.weight > 0) io.write(name, Math.max(-1e4, Math.min(1e4, basket.value / basket.weight)));
    }
  }
}
