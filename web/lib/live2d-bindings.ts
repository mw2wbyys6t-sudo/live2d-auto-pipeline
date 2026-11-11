/**
 * 自研渲染器的形变数据与求值。
 *
 * 数据来自导出期写出的 `{name}.bindings.json`，是 moc3 里键形/warp/rotation
 * 变形器的可读投影，坐标**都是图层图像坐标**（左上原点、y 向下）。这里的函数
 * 只做四件事：多轴键形按 moc3 的展平序插值、把顶点按 warp 控制网格搬位置
 * （格内是三角形分片线性，见 sampleWarpGrid）、按 rotation 键形绕枢轴做刚体
 * 变换、把各层的不透明度一起插值。渲染器负责把结果写进顶点缓冲。
 *
 * 关键约定（与 exporter/bindings_export.py 对齐，改动必须两边同时改）：
 * - 多轴展平序 `index = i0 + n0*i1 + n0*n1*i2 + …`（第一个轴步长为 1）；
 * - 变形器网格 `grids[i]` 是 (rows+1)×(cols+1) 个控制点，行优先、x 最快；
 * - `rest` 是静止网格的外接矩形，用来把顶点换算成网格内 (u, v)。
 */

export interface BindingsParameter {
  id: string;
  min: number;
  max: number;
  default: number;
}

/** [参数 id, 升序键值表] */
export type KeyformAxis = [string, number[]];

export interface BindingsKeyforms {
  axes: KeyformAxis[];
  /** 每个键形一份扁平顶点表（与网格顶点等长，2 个 float 一个点）。 */
  vertices: number[][];
  /** 与 vertices 等长的不透明度；缺省时沿用网格自身不透明度。 */
  opacities?: number[];
}

export interface BindingsMesh {
  id: string;
  part: string;
  vertices: number[];
  uvs: number[];
  indices: number[];
  drawOrder: number;
  opacity: number;
  texture: number;
  deformer: string;
  keyforms?: BindingsKeyforms;
}

/** warp 变形器：控制点网格，按参数插值后把顶点搬位置（格内三角形分片线性）。 */
export interface BindingsWarpDeformer {
  id: string;
  type?: 'warp';
  rows: number;
  cols: number;
  parent: string;
  /** [x0, y0, x1, y1]：静止网格外接矩形（图像坐标）。 */
  rest: [number, number, number, number];
  axes: KeyformAxis[];
  /** 每个键形一份扁平控制点表，(rows+1)×(cols+1) 个点。 */
  grids: number[][];
}

/** rotation 键形：角度（度）、枢轴（图像坐标）、缩放。 */
export interface BindingsRotationKeyform {
  angle: number;
  origin: [number, number];
  scale: number;
}

/** rotation 变形器：整棵子树绕 origin 刚体旋转（与 moc3 同一套语义）。 */
export interface BindingsRotationDeformer {
  id: string;
  type: 'rotation';
  parent: string;
  /** 总角度 = baseAngle + 键形 angle（内核就是这么求和的）。 */
  baseAngle?: number;
  axes: KeyformAxis[];
  keyforms: BindingsRotationKeyform[];
}

export type BindingsDeformer = BindingsWarpDeformer | BindingsRotationDeformer;

export interface BindingsDocument {
  version: number;
  canvas: { width: number; height: number; pixelsPerUnit: number };
  parameters: BindingsParameter[];
  meshes: BindingsMesh[];
  deformers: BindingsDeformer[];
}

/** 参数取值来源（缺省 0，与内核「未声明即静止值」一致）。 */
export type ParamSource = (id: string) => number;

export interface KeyformTerm {
  /** 展平后的键形下标。 */
  index: number;
  /** 该键形的权重，全部 term 权重和为 1。 */
  weight: number;
}

export interface Bracket {
  index: number;
  next: number;
  /** 插值到 next 的权重（0..1）；index === next 时为 0。 */
  weight: number;
}

/**
 * 在升序键值表中定位 value 的夹取区间。
 * 越界一律贴边（`index === next`），不向表外外推 —— 外推会让超出声明范围的
 * 参数把网格拉飞，而内核的行为是夹到边界键形。
 */
export function bracketKeys(keys: readonly number[], value: number): Bracket {
  const n = keys.length;
  if (n === 0) return { index: 0, next: 0, weight: 0 };
  if (n === 1 || value <= keys[0]) return { index: 0, next: 0, weight: 0 };
  if (value >= keys[n - 1]) return { index: n - 1, next: n - 1, weight: 0 };
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (keys[mid] <= value) lo = mid;
    else hi = mid;
  }
  const span = keys[hi] - keys[lo];
  const weight = span > 0 ? (value - keys[lo]) / span : 0;
  return { index: lo, next: hi, weight };
}

/**
 * 多轴键形的插值项：每个轴至多 2 个键，组合数 ≤ 2^轴数（实测最多 4 轴）。
 * 权重是各轴权重之积，下标按 `index = i0 + n0*i1 + n0*n1*i2 + …` 展开。
 */
export function keyformTerms(
  axes: readonly KeyformAxis[],
  resolve: ParamSource,
): KeyformTerm[] {
  if (axes.length === 0) return [];
  const brackets = axes.map(([pid, keys]) => bracketKeys(keys, resolve(pid)));
  const strides: number[] = [];
  let stride = 1;
  for (const [, keys] of axes) {
    strides.push(stride);
    stride *= keys.length;
  }

  const terms: KeyformTerm[] = [];
  const walk = (axis: number, index: number, weight: number): void => {
    if (weight <= 0) return;
    if (axis === axes.length) {
      terms.push({ index, weight });
      return;
    }
    const bracket = brackets[axis];
    const step = strides[axis];
    walk(axis + 1, index + bracket.index * step, weight * (1 - bracket.weight));
    if (bracket.next !== bracket.index) {
      walk(axis + 1, index + bracket.next * step, weight * bracket.weight);
    }
  };
  walk(0, 0, 1);
  return terms;
}

/** 按 `terms` 把多份扁平数组混合进 `out`（长度按第一份判定）。 */
export function blendFlat(
  terms: readonly KeyformTerm[],
  values: readonly ArrayLike<number>[],
  out: Float32Array,
): void {
  out.fill(0);
  for (const term of terms) {
    const source = values[term.index];
    if (!source) continue;
    const n = Math.min(out.length, source.length);
    for (let i = 0; i < n; i++) out[i] += source[i] * term.weight;
  }
}

/** 标量版混合（键形不透明度）。 */
export function blendScalar(
  terms: readonly KeyformTerm[],
  values: ArrayLike<number>,
): number {
  let out = 0;
  for (const term of terms) {
    const v = values[term.index];
    if (v === undefined) continue;
    out += v * term.weight;
  }
  return out;
}

/**
 * 变形器网格采样。`grid` 是扁平控制点表（行优先、x 最快），
 * (u, v) 是网格内归一化位置。
 *
 * **格内是三角形分片线性，不是双线性**：官方 Core 的「网格形变」
 * （moc3 `additional.quad_transforms=0`）沿 P10-P01 对角线把每格切成两半，
 * 各用三个角点插值。实测（`tools/probe_warp_interpolation.py`，官方 Core 5-r.5）：
 * 控制网格被拉成非仿射时，Core 渲染质心与三角形预测差 0.43%、与双线性差 9.49%。
 * 仿射网格（整体平移）下两种插值结果相同 —— 仓库原有的对照用例全是仿射的，
 * 结构性看不见这个差异。双线性是「quad 形变」（`quad_transforms=1`）的语义。
 *
 * **越出 0..1 的画按边界那一格线性外推，不夹取**（#72）：官方 Cubism Core 就是这么
 * 画的，实测挂在父级静止矩形外 160px 的一条边，Core 画在屏幕行 452（与「根本没有
 * 变形器」的对照逐字节相同），旧版的 `Math.min(Math.max(u,0),1)` 把它塌到矩形边界
 * 读成 328。判据钉在 `tests/unit/test_bindings_gap_probe.py
 * ::test_the_shipped_player_extrapolates_out_of_grid_art_like_the_core_and_the_ruler`
 * —— 那份 TS 与离线尺子 `tools/bindings_gap_probe.py:sample_warp_grid` 是同一只手
 * 移植的，改一边必须同步另一边，否则自研播放器与官方内核会画出不一样多的画面。
 *
 * **越界这一段的规则尚未钉死**（2026-11-11 实测）：#72 那条判据用的是恒等点阵，
 * 恒等网格下各种外推退化成同一条映射，分不出差别；用**变形过**的网格实测，官方
 * Core 把挂在矩形外 160px 的顶点放在模型 x≈562，本函数读 530（UR 三角形平面外推
 * 是 650）—— 候选规则一个都没命中，差约 32 个模型单位。静止/仿射网格上的读数仍与
 * 内核逐像素一致。
 */
export function sampleWarpGrid(
  grid: Float32Array,
  rows: number,
  cols: number,
  u: number,
  v: number,
  out: { x: number; y: number },
): void {
  const cx = u * cols;
  const cy = v * rows;
  // Math.floor 而不是 Math.trunc：越到负方向时 trunc 会把「第一格之前」当成第一格
  // 本身重复用一次，外推方向就错了。
  let c0 = Math.floor(cx);
  let r0 = Math.floor(cy);
  if (c0 > cols - 1) c0 = cols - 1;
  if (r0 > rows - 1) r0 = rows - 1;
  if (c0 < 0) c0 = 0;
  if (r0 < 0) r0 = 0;
  const c1 = Math.min(c0 + 1, cols);
  const r1 = Math.min(r0 + 1, rows);
  // fx/fy 可以落在 [0,1] 之外：那正是外推项。
  const fx = cx - c0;
  const fy = cy - r0;

  const rowStride = (cols + 1) * 2;
  const i00 = r0 * rowStride + c0 * 2;
  const i10 = r0 * rowStride + c1 * 2;
  const i01 = r1 * rowStride + c0 * 2;
  const i11 = r1 * rowStride + c1 * 2;

  if (cx >= 0 && cx <= cols && cy >= 0 && cy <= rows) {
    // 格内：三角形分片线性（官方 Core 的「网格形变」语义，见本函数注释）
    let w00: number;
    let w10: number;
    let w01: number;
    let w11: number;
    if (fx + fy < 1) {
      w00 = 1 - fx - fy;
      w10 = fx;
      w01 = fy;
      w11 = 0;
    } else {
      w00 = 0;
      w10 = 1 - fy;
      w01 = 1 - fx;
      w11 = fx + fy - 1;
    }
    out.x =
      grid[i00] * w00 + grid[i10] * w10 + grid[i01] * w01 + grid[i11] * w11;
    out.y =
      grid[i00 + 1] * w00 + grid[i10 + 1] * w10 +
      grid[i01 + 1] * w01 + grid[i11 + 1] * w11;
    return;
  }

  const topX = grid[i00] + (grid[i10] - grid[i00]) * fx;
  const topY = grid[i00 + 1] + (grid[i10 + 1] - grid[i00 + 1]) * fx;
  const botX = grid[i01] + (grid[i11] - grid[i01]) * fx;
  const botY = grid[i01 + 1] + (grid[i11 + 1] - grid[i01 + 1]) * fx;

  out.x = topX + (botX - topX) * fy;
  out.y = topY + (botY - topY) * fy;
}

/** rotation 变形器本帧的姿态（已按参数插值好的键形）。 */
export interface RotationState {
  angle: number;
  origin: [number, number];
  scale: number;
}

/**
 * 按 rotation 的语义变换一个点。与 moc3 写出侧**同一个公式**：
 *
 *     p' = s·R(θ)·p + (I − R(θ))·origin      θ = baseAngle + 键形 angle
 *
 * 其中 ``(I − R)·origin`` 就是编译器写进 ``origin_xs/ys`` 的平移量
 * （``tools/probe_rotation_offset.py`` 在 0°/30°/60°/−45° 上逐像素验证），
 * ``scale`` 不参与平移项（官方那 253 个键形里 scale 全为 1，但我们照内核
 * 语义复算，不假设它一定等于 1）。
 */
export function applyRotation(
  x: number,
  y: number,
  baseAngle: number,
  state: RotationState,
  out: { x: number; y: number },
): void {
  const theta = ((baseAngle || 0) + state.angle) * (Math.PI / 180);
  const cos = Math.cos(theta);
  const sin = Math.sin(theta);
  const s = state.scale ?? 1;
  const [ox, oy] = state.origin;
  out.x = s * (cos * x - sin * y) + (ox - (cos * ox - sin * oy));
  out.y = s * (sin * x + cos * y) + (oy - (sin * ox + cos * oy));
}

/**
 * 把一个点依次穿过变形器链（先自己的网格，再逐级父变形器，直到根）。
 * `parent` 为空的字符串表示没有父级。
 *
 * 链上可能混着 warp 与 rotation：``resolveRotation`` 给出 rotation 本帧的姿态，
 * 缺省（老产物/未求值）时按恒等处理 —— 不动比乱转安全。
 */
export function applyDeformerChain(
  x: number,
  y: number,
  deformer: BindingsDeformer | undefined,
  resolve: (id: string) => BindingsDeformer | undefined,
  resolveGrid: (d: BindingsWarpDeformer) => Float32Array,
  out: { x: number; y: number },
  resolveRotation?: (d: BindingsRotationDeformer) => RotationState | undefined,
): void {
  let px = x;
  let py = y;
  let current = deformer;
  let guard = 0;
  while (current && guard++ < 32) {
    if (current.type === 'rotation') {
      const state = resolveRotation ? resolveRotation(current) : undefined;
      if (state) {
        applyRotation(px, py, current.baseAngle || 0, state, out);
        px = out.x;
        py = out.y;
      }
      current = current.parent ? resolve(current.parent) : undefined;
      continue;
    }
    const [x0, y0, x1, y1] = current.rest;
    const w = x1 - x0;
    const h = y1 - y0;
    const u = w !== 0 ? (px - x0) / w : 0.5;
    const v = h !== 0 ? (py - y0) / h : 0.5;
    sampleWarpGrid(resolveGrid(current), current.rows, current.cols, u, v, out);
    px = out.x;
    py = out.y;
    current = current.parent ? resolve(current.parent) : undefined;
  }
  out.x = px;
  out.y = py;
}
