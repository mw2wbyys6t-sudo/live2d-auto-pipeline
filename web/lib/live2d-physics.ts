/**
 * physics3.json 的运行时。官方内核用 CubismPhysics 驱动；我们自研渲染器里要
 * 复现的是同一件事：输入参数（头/身体角度、呼吸）变化后，头发/裙摆/肩膀的
 * 输出参数带滞后、过冲，最后回落到静止。
 *
 * 模型：**逐顶点 Verlet 位置约束链**（逆向 Cubism Editor 5.3 的
 * `CPhysicsFtlAlgorithm`）。每条链是一串质点：
 *
 *   1. 第 0 个质点是锚点，位置 = 输入参数换算出的平移（再按总角度旋转）；
 *   2. 其余质点受**重力方向 × Acceleration + 风**的力；
 *   3. 每步先按「上一帧重力方向 → 本帧重力方向」的夹角 / 气动阻尼 5.0 旋转
 *      段向量，再叠上速度项与力项（速度项按 Delay × dt × 30 缩放）；
 *   4. **半径约束**：质点到父质点的距离被拉回 Radius；
 *   5. 速度 = (新位置 − 旧位置) / 延迟 × Mobility（Mobility 就是阻尼）；
 *   6. 输出取**相邻两段之间的夹角**（不是与竖直方向的夹角）—— 头发的"摆角"
 *      正是关节处的弯折。
 *
 * 换掉之前的弹簧-阻尼近似：那条路的刚度/阻尼是按阶跃响应相关系数标定的
 * （头发 0.66、身体 0.53、呼吸 0.87、裙摆 0.76），形状像但与内核不是同一个
 * 动力学；这里直接复刻内核的积分，同一份 physics3.json 在浏览器与桌面上的
 * 运动才是一回事。
 */

export interface PhysicsNormalization {
  minimum: number;
  default: number;
  maximum: number;
}

export interface PhysicsInput {
  source: string;
  weight: number;
  type: 'X' | 'Y' | 'Angle';
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
  wind: [number, number];
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
  const forces = meta.EffectiveForces || {};
  const gravity = forces.Gravity || {};
  const wind = forces.Wind || {};
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
        type: entry.Type === 'Y' ? 'Y' : entry.Type === 'Angle' ? 'Angle' : 'X',
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
    // physics3.json 里的重力/风是**内部值的 y 取反**后写出的（内部 +Y 向下：
    // 子质点挂在父质点正下方 Radius 处）。所以这里要反解回内部空间，
    // 否则重力会朝上、把整条链掀翻（实测轨迹直接打到 ±π）。
    gravity: [num(gravity.X, 0), -num(gravity.Y, -1)],
    wind: [num(wind.X, 0), -num(wind.Y, 0)],
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
 * Cubism 内核的物理常数（逆向 CPhysicsFtlAlgorithm）。
 *   BASE_FPS=30        延迟项的基准帧率：delay × dt × 30
 *   AERODYNAMIC_DRAG=5 段向量按「重力方向变化量 / 5」旋转
 *   MOVEMENT_THRESHOLD 位置 x 小于 0.001 × 归一化上界时归零
 */
const DEG_TO_RAD = 0.017453292;
const BASE_FPS = 30;
const AERODYNAMIC_DRAG = 5.0;
const MOVEMENT_THRESHOLD = 0.001;
/** 一帧最多补几步（掉帧时不追赶到无限）。 */
const MAX_SUBSTEPS = 8;

/** 角度归一化到 (−π, π]。 */
function wrapPi(angle: number): number {
  let v = angle;
  while (v < -Math.PI) v += Math.PI * 2;
  while (v > Math.PI) v -= Math.PI * 2;
  return v;
}

/** 从 b 转到 a 的有符号夹角（内核 UUMath.b 的语义）。 */
function signedAngle(ax: number, ay: number, bx: number, by: number): number {
  return wrapPi(Math.atan2(ay, ax) - Math.atan2(by, bx));
}

/**
 * 输入参数的归一化（内核 `a(param, normMin, normMax, normDefault, reflect)`）。
 *
 * 归一化值落在**归一化区间自己的量纲**里（Position/Angle 各自 ±10 之类），
 * 以归一化默认值��支点：参数在自身区间中点以上时线性映到 [默认值, 上界]，
 * 以下时映到 [下界, 默认值]。参数值越出自身 [min,max] 时归一化值直接取 0。
 * `reflect` 的极性与内核一致：**不反射时取负**。
 */
function normalizePhysicsInput(
  value: number,
  paramRange: ParameterRange,
  norm: PhysicsNormalization,
  reflect: boolean,
): number {
  const paramMin = Math.min(paramRange.min, paramRange.max);
  const paramMax = Math.max(paramRange.min, paramRange.max);
  if (value > paramMax || value < paramMin) return 0;
  const normLo = Math.min(norm.minimum, norm.maximum);
  const normHi = Math.max(norm.minimum, norm.maximum);
  const paramMid = paramMin + Math.abs(paramMax - paramMin) / 2;
  const delta = value - paramMid;
  let result: number;
  if (delta > 0) {
    const span = paramMax - paramMid;
    result = span !== 0 ? (delta * (normHi - norm.default)) / span + norm.default : norm.default;
  } else if (delta < 0) {
    const span = paramMin - paramMid;
    result = span !== 0 ? (delta * (normLo - norm.default)) / span + norm.default : norm.default;
  } else {
    result = norm.default;
  }
  return reflect ? result : -result;
}

interface VerletVertex {
  /** 当前局部位置。 */
  x: number;
  y: number;
  /** 速度（内核里就是速度向量，不是每帧的位移）。 */
  vx: number;
  vy: number;
  /** 上一帧的位置（用来算速度）。 */
  oldX: number;
  oldY: number;
  /** 上一帧的重力方向（段向量按它与本帧重力方向的夹角旋转）。 */
  gravityX: number;
  gravityY: number;
  radius: number;
  mobility: number;
  delay: number;
  acceleration: number;
}

interface Chain {
  setting: PhysicsSetting;
  vertices: VerletVertex[];
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
  /**
   * 上一帧读到的**输入参数值**。
   *
   * 官方内核的物理响应比参数写入晚一帧（实测：同一个输入阶跃，内核的第一帧
   * 完全没有反应，第二帧才出现与自研实现第一帧逐位相同的数值）。所以物理
   * 要用**上一帧**的输入值来算，自研播放器才会与桌面/内核同相位。
   */
  private readonly previousInputs = new Map<string, number>();
  private readonly currentInputs = new Map<string, number>();

  constructor(document: PhysicsDocument) {
    this.document = document;
    for (const setting of document.settings) {
      const count = setting.vertices.length;
      const vertices: VerletVertex[] = [];
      for (let i = 0; i < count; i++) {
        const vertex = setting.vertices[i];
        vertices.push({
          x: 0, y: 0, vx: 0, vy: 0, oldX: 0, oldY: 0,
          gravityX: document.gravity[0], gravityY: document.gravity[1],
          radius: vertex.radius,
          mobility: vertex.mobility,
          delay: vertex.delay,
          acceleration: vertex.acceleration,
        });
      }
      this.chains.push({ setting, vertices });
      this.resetChain(vertices);
    }
  }

  get chainCount(): number {
    return this.chains.length;
  }

  /** 内核的初始状态：根质点在原点，子质点挂在父质点正下方 Radius 处。 */
  private resetChain(vertices: VerletVertex[]): void {
    const [gx, gy] = this.document.gravity;
    vertices[0].x = 0;
    vertices[0].y = 0;
    vertices[0].oldX = 0;
    vertices[0].oldY = 0;
    vertices[0].vx = 0;
    vertices[0].vy = 0;
    vertices[0].gravityX = gx;
    vertices[0].gravityY = gy;
    let px = 0;
    let py = 0;
    for (let i = 1; i < vertices.length; i++) {
      py += vertices[i].radius;
      vertices[i].x = px;
      vertices[i].y = py;
      vertices[i].oldX = px;
      vertices[i].oldY = py;
      vertices[i].vx = 0;
      vertices[i].vy = 0;
      vertices[i].gravityX = gx;
      vertices[i].gravityY = gy;
      px = vertices[i].x;
      py = vertices[i].y;
    }
  }

  reset(): void {
    for (const chain of this.chains) this.resetChain(chain.vertices);
    this.accumulator = 0;
    this.outputBase.clear();
  }

  /**
   * 各输出参数「本帧物理之前」的值。
   *
   * 内核每帧先把参数还原成物理之前的快照再跑物理，所以 weight<100 的混合基准
   * previous **不含上一帧的物理输出**。实测（tools/probe_breathing_split.py）：
   * 无人外部写 ParamShoulderL 时内核恒为 p_n = 0.5*raw_n；把上一帧输出当基准
   * 会算成 p_n = 0.5*p_{n-1} + 0.5*raw_n，Breathing 链峰值差 80%。而每帧外部
   * 写入 0.4 时两种做法又完全一致 —— 差别只在「物理自己的输出要不要回流」。
   */
  private readonly outputBase = new Map<string, number>();

  private outputDestinations(): string[] {
    const names = new Set<string>();
    for (const chain of this.chains) {
      for (const output of chain.setting.output) names.add(output.destination);
    }
    return [...names];
  }

  /** 帧首调用（动作/表情写入**之前**）：撤掉上一帧的物理输出。 */
  restoreOutputs(io: PhysicsListeners): void {
    for (const name of this.outputDestinations()) {
      const base = this.outputBase.get(name);
      if (base !== undefined) io.write(name, base);
    }
  }

  /** 紧接物理之前调用：记下本帧的混合基准，也是下一帧的还原点。 */
  captureOutputs(io: PhysicsListeners): void {
    for (const name of this.outputDestinations()) {
      this.outputBase.set(name, io.read(name));
    }
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
    const [windX, windY] = this.document.wind;
    // 先把本帧读到的输入值存下来；物理本身用**上一帧**的值（内核晚一帧响应）。
    for (const chain of this.chains) {
      for (const input of chain.setting.input) {
        if (!this.currentInputs.has(input.source)) {
          this.currentInputs.set(input.source, io.read(input.source));
        }
      }
    }
    for (const chain of this.chains) {
      const { setting, vertices } = chain;

      // ---- 输入：位置（X/Y）与角度，各自归一化后按 Weight 汇总 ----
      let tx = 0;
      let ty = 0;
      let totalAngle = 0;
      for (const input of setting.input) {
        const range = io.range(input.source);
        if (!range) continue;
        // 用**上一帧**的输入值：官方内核的物理响应比参数写入晚一帧
        // （实测：输入阶跃当帧内核完全没有反应，下一帧才出现与自研实现首帧
        // 逐位相同的数值）。用当前值会让自研播放器比内核早一帧。
        const raw = this.previousInputs.has(input.source)
          ? this.previousInputs.get(input.source)!
          : this.currentInputs.get(input.source) ?? io.read(input.source);
        const norm = input.type === 'Angle'
          ? normalizePhysicsInput(raw, range, setting.angle, input.reflect)
          : normalizePhysicsInput(raw, range, setting.position, input.reflect);
        const weight = input.weight / 100;
        if (input.type === 'X') tx += norm * weight;
        else if (input.type === 'Y') ty += norm * weight;
        else totalAngle += norm * weight;
      }

      // ---- 锚点平移按总角度旋转（内核：R(-totalAngle) · 平移） ----
      const rad = DEG_TO_RAD * -totalAngle;
      const cos = Math.cos(rad);
      const sin = Math.sin(rad);
      const anchorX = tx * cos - ty * sin;
      const anchorY = tx * sin + ty * cos;

      // ---- 重力方向：内核把「参数总角度」与「重力本身的倾角」相加 ----
      const gravityAngle = DEG_TO_RAD * totalAngle
        + signedAngle(0, 1, this.document.gravity[0], this.document.gravity[1]);
      let gx = Math.sin(gravityAngle);
      let gy = Math.cos(gravityAngle);
      const glen = Math.hypot(gx, gy);
      if (glen > 1e-9) {
        gx /= glen;
        gy /= glen;
      }

      // ---- Verlet 位置约束链 ----
      vertices[0].x = anchorX;
      vertices[0].y = anchorY;
      const limit = MOVEMENT_THRESHOLD * Math.abs(setting.position.maximum);
      for (let i = 1; i < vertices.length; i++) {
        const v = vertices[i];
        const p = vertices[i - 1];
        const fx = gx * v.acceleration + windX;
        const fy = gy * v.acceleration + windY;
        v.oldX = v.x;
        v.oldY = v.y;

        // 段向量按「上帧重力方向 → 本帧重力方向」的夹角 / 气动阻尼旋转
        let sx = v.x - p.x;
        let sy = v.y - p.y;
        const tilt = signedAngle(v.gravityX, v.gravityY, gx, gy) / AERODYNAMIC_DRAG;
        const tc = Math.cos(tilt);
        const ts = Math.sin(tilt);
        const rotatedX = tc * sx - ts * sy;
        const rotatedY = ts * sx + tc * sy;
        sx = rotatedX;
        sy = rotatedY;

        const delay = v.delay * dt * BASE_FPS;
        let nx = p.x + sx + v.vx * delay + fx * delay * delay;
        let ny = p.y + sy + v.vy * delay + fy * delay * delay;

        // 半径约束：到父质点的距离拉回 Radius
        let dx = nx - p.x;
        let dy = ny - p.y;
        const len = Math.hypot(dx, dy);
        if (len > 1e-9) {
          nx = p.x + (dx / len) * v.radius;
          ny = p.y + (dy / len) * v.radius;
        }
        if (Math.abs(nx) < limit) nx = 0;
        v.x = nx;
        v.y = ny;

        if (delay !== 0) {
          v.vx = ((v.x - v.oldX) / delay) * v.mobility;
          v.vy = ((v.y - v.oldY) / delay) * v.mobility;
        }
        v.gravityX = gx;
        v.gravityY = gy;
      }

      // ---- 输出：相邻两段的夹角（头发摆角就是关节处的弯折） ----
      for (const output of setting.output) {
        const range = io.range(output.destination);
        if (!range) continue;
        const index = Math.min(vertices.length - 1, output.vertexIndex);
        if (index < 1) continue;
        const v = vertices[index];
        const prev = vertices[index - 1];
        const segX = v.x - prev.x;
        const segY = v.y - prev.y;
        let value: number;
        if (output.type === 'X') {
          value = segX;
        } else if (output.type === 'Y') {
          value = segY;
        } else {
          const gp = index >= 2 ? vertices[index - 1] : null;
          const before = gp
            ? { x: gp.x - vertices[index - 2].x, y: gp.y - vertices[index - 2].y }
            : { x: 0, y: 1 };
          // 内核是 UUMath.b(seg, prevSeg) = wrapPi(atan2(seg) − atan2(prevSeg))
          value = signedAngle(segX, segY, before.x, before.y);
        }
        if (output.reflect) value = -value;
        const raw = value * output.scale;
        const clamped = Math.max(range.min, Math.min(range.max, raw));
        const weight = output.weight / 100;
        const previous = io.read(output.destination);
        io.write(output.destination, weight >= 1
          ? clamped
          : previous * (1 - weight) + clamped * weight);
      }
    }
    // 本帧读到的输入值成为下一帧的输入（内核的一帧滞后）
    for (const [name, value] of this.currentInputs) {
      this.previousInputs.set(name, value);
    }
    this.currentInputs.clear();
  }
}
