/**
 * 自研渲染器的形变数据与求值。
 *
 * 数据来自导出期写出的 `{name}.bindings.json`，是 moc3 里键形/warp 变形器的
 * 可读投影，坐标**都是图层图像坐标**（左上原点、y 向下）。这里的函数只做三件
 * 事：多轴键形按 moc3 的展平序插值、把顶点按变形器网格双线性搬位置、把各层
 * 的不透明度一起插值。渲染器负责把结果写进顶点缓冲。
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

export interface BindingsDeformer {
  id: string;
  rows: number;
  cols: number;
  parent: string;
  /** [x0, y0, x1, y1]：静止网格外接矩形（图像坐标）。 */
  rest: [number, number, number, number];
  axes: KeyformAxis[];
  /** 每个键形一份扁平控制点表，(rows+1)×(cols+1) 个点。 */
  grids: number[][];
}

export interface BindingsDocument {
  version: number;
  canvas: { width: number; height: number; pixelsPerUnit: number };
  parameters: BindingsParameter[];
  meshes: BindingsMesh[];
  deformers: BindingsDeformer[];
  skippedRotationDeformers?: string[];
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
 * 变形器网格双线性采样。`grid` 是扁平控制点表（行优先、x 最快），
 * (u, v) 是网格内归一化位置。
 *
 * **越出 0..1 的画按边界那一格线性外推，不夹取**（#72）：官方 Cubism Core 就是这么
 * 画的，实测挂在父级静止矩形外 160px 的一条边，Core 画在屏幕行 452（与「根本没有
 * 变形器」的对照逐字节相同），旧版的 `Math.min(Math.max(u,0),1)` 把它塌到矩形边界
 * 读成 328。判据钉在 `tests/unit/test_bindings_gap_probe.py
 * ::test_the_shipped_player_extrapolates_out_of_grid_art_like_the_core_and_the_ruler`
 * —— 那份 TS 与离线尺子 `tools/bindings_gap_probe.py:sample_warp_grid` 是同一只手
 * 移植的，改一边必须同步另一边，否则自研播放器与官方内核会画出不一样多的画面。
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

  const topX = grid[i00] + (grid[i10] - grid[i00]) * fx;
  const topY = grid[i00 + 1] + (grid[i10 + 1] - grid[i00 + 1]) * fx;
  const botX = grid[i01] + (grid[i11] - grid[i01]) * fx;
  const botY = grid[i01 + 1] + (grid[i11 + 1] - grid[i01 + 1]) * fx;

  out.x = topX + (botX - topX) * fy;
  out.y = topY + (botY - topY) * fy;
}

/**
 * 把一个点依次穿过变形器链（先自己的网格，再逐级父变形器，直到根）。
 * `parent` 为空的字符串表示没有父级。
 */
export function applyDeformerChain(
  x: number,
  y: number,
  deformer: BindingsDeformer | undefined,
  resolve: (id: string) => BindingsDeformer | undefined,
  resolveGrid: (d: BindingsDeformer) => Float32Array,
  out: { x: number; y: number },
): void {
  let px = x;
  let py = y;
  let current = deformer;
  let guard = 0;
  while (current && guard++ < 32) {
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
