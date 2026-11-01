import {
  Application,
  Container,
  Mesh,
  MeshGeometry,
  MeshMaterial,
  PlaneGeometry,
  Texture,
  Assets,
} from 'pixi.js';
import type { Model3Json, ParameterDef } from '../types';
import {
  STANDARD_PARAMS,
  clampToBounds,
  resolveBounds,
  resolveParameters,
  type ParameterBounds,
} from './live2d-params';
import {
  applyDeformerChain,
  blendFlat,
  blendScalar,
  keyformTerms,
  type BindingsDeformer,
  type BindingsDocument,
  type BindingsMesh,
} from './live2d-bindings';
import { MotionPlayback, parseMotion, type MotionFile } from './live2d-motion';
import { PhysicsEngine, parsePhysics3, type ParameterRange } from './live2d-physics';

/** exp3.json 里一条参数项；Blend 决定它是叠加、乘算还是覆盖。 */
interface ExpressionParameter {
  id: string;
  value: number;
  blend: 'Add' | 'Multiply' | 'Overwrite';
}

interface ExpressionSpec {
  name: string;
  parameters: ExpressionParameter[];
  fadeIn: number;
  fadeOut: number;
}

export type PartGroup = 'hair' | 'face' | 'eyes' | 'mouth' | 'body' | 'other';

/** Normalised (0..1) opaque-content bounding box for a layer. */
interface ContentBounds {
  minU: number;
  maxU: number;
  minV: number;
  maxV: number;
  /** Vertical centre of the content (0..1). */
  centerV: number;
}

interface LayerEntry {
  name: string;
  mesh: Mesh;
  geometry: PlaneGeometry;
  baseX: number;
  baseY: number;
  /** Initial (centered) vertex positions, Float32Array of [x,y,x,y,...]. */
  basePositions: Float32Array;
  partGroup: PartGroup;
  /** Texture / plane dimensions used to scale vertex offsets. */
  texWidth: number;
  texHeight: number;
  segW: number;
  segH: number;
  /** Localised content bounds (for mouth/eye deformation); null until analysed. */
  contentBounds: ContentBounds | null;
}

/** 一个美术网格：绑定表里的一条 meshes 记录 + 它自己的顶点缓冲。 */
interface ArtMeshEntry {
  id: string;
  mesh: Mesh;
  geometry: MeshGeometry;
  /** 实时顶点（局部坐标，画布中心为原点）。 */
  positions: Float32Array;
  /** 静止顶点（局部坐标）。 */
  rest: Float32Array;
  /** 键形顶点（局部坐标），与 `keyforms.axes` 一一对应。 */
  keyformVertices: Float32Array[] | null;
  keyformOpacities: number[] | null;
  axes: Array<[string, number[]]>;
  baseOpacity: number;
  deformerId: string;
  /** 每帧复用的顶点混合缓冲，避免抖动回收。 */
  scratch: Float32Array | null;
}

interface WarpEntry {
  spec: BindingsDeformer;
  /** 键形控制点（局部坐标）。 */
  grids: Float32Array[];
  axes: Array<[string, number[]]>;
  /** 每帧复用的网格混合缓冲。 */
  scratch: Float32Array | null;
  /** 本帧求值结果。 */
  current: Float32Array | null;
}

/** 扁平 [x,y,...] 图像坐标 -> 局部坐标 Float32Array（减去画布半宽/半高）。 */
function offsetFlat(values: readonly number[], dx: number, dy: number): Float32Array {
  const out = new Float32Array(values.length);
  for (let i = 0; i + 1 < values.length; i += 2) {
    out[i] = values[i] + dx;
    out[i + 1] = values[i + 1] + dy;
  }
  return out;
}

export interface Live2DPlayerOptions {
  backgroundColor?: number;
  backgroundAlpha?: number;
  antialias?: boolean;
  resolution?: number;
  autoStart?: boolean;
}

// Vertex grid subdivision for the deformable planes. 5 vertices per side
// yields a 4x4 cell grid (25 verts) which is enough for smooth warp/mouth.
const PLANE_SEGMENTS = 5;

/**
 * 外部输入（面捕 / 麦克风 / 滑杆）写完参数后，动作让位的秒数。
 *
 * 输入源是事件驱动的（摄像头 30 fps、麦克风按 rAF、鼠标按移动像素），不是每帧
 * 都写；这个窗口让两次写入之间的空帧继续用外部值，输入一停又会自己交还给动作。
 */
const EXTERNAL_HOLD_SECONDS = 0.25;

/**
 * Browser-side Live2D player using PixiJS.
 *
 * 两种渲染模式：
 * - **绑定模式**（有 `FileReferences.Bindings`）：按导出期的真实美术网格、
 *   多轴键形与 warp 变形器逐帧摆顶点，参数与官方内核用的是同一份绑定表；
 * - **预览层兜底**（老产物没有 bindings.json）：每层一张整画布平面 + 手写启发
 *   形变，只能看个大概。兜底保留是为了旧包还能打开，不是为了替代前者。
 */
export class Live2DPlayer {
  private app: Application | null = null;
  /** Bumped on destroy(); stale async loads check this and bail out. */
  private _generation = 0;
  private readonly canvas: HTMLCanvasElement;
  private readonly options: Live2DPlayerOptions;
  private root: Container | null = null;
  private layers: LayerEntry[] = [];
  private artMeshes: ArtMeshEntry[] = [];
  private warps: Map<string, WarpEntry> = new Map();
  /** 绑定模式的画布尺寸（图像坐标原点的换算基准）。 */
  private canvasSize: { width: number; height: number } | null = null;
  private params: Map<string, number> = new Map();
  private targetParams: Map<string, number> = new Map();
  /** 参数清单：模型声明优先，缺失时退回标准表（供 UI / 校验页读取）。 */
  private resolvedParams: ParameterDef[] = STANDARD_PARAMS.map((p) => ({ ...p }));
  /** 可安全夹取的区间；只收可信来源，未知参数保持不夹取。 */
  private bounds: Map<string, ParameterBounds> = resolveBounds(null);
  private expressions: Map<string, ExpressionSpec> = new Map();
  private currentExpression = 'default';
  /** 表情淡入淡出权重（0 = 不生效，1 = 完全生效）。 */
  private expressionWeight = 0;
  private expressionTargetWeight = 0;
  private expressionFadeRate = 2;
  /** 表情生效前的目标值，用于每帧重新叠加（不累加）。 */
  private expressionBase = new Map<string, number>();
  /** 外部输入刚写过的参数 → 剩余让位秒数（见 EXTERNAL_HOLD_SECONDS）。 */
  private externalHold = new Map<string, number>();
  /** 动作组 → 已解析的动作文件。 */
  private motionGroups: Map<string, MotionFile[]> = new Map();
  private motion: MotionPlayback | null = null;
  private idleEnabled = false;
  private physics: PhysicsEngine | null = null;
  private physicsDiagnostics = { chains: 0, loaded: false };
  private running = false;
  private frameCallbacks: Array<() => void> = [];
  private tickerFn: ((dt: number) => void) | null = null;
  private model3: Model3Json | null = null;
  private _fps = 0;
  private _frames = 0;
  private _lastFpsTime = 0;
  private readonly lerpSpeed = 0.15;

  constructor(canvas: HTMLCanvasElement, options: Live2DPlayerOptions = {}) {
    this.canvas = canvas;
    this.options = {
      backgroundColor: 0x000000,
      backgroundAlpha: 0,
      antialias: true,
      resolution: window.devicePixelRatio || 1,
      autoStart: false,
      ...options,
    };
    for (const p of STANDARD_PARAMS) {
      this.params.set(p.id, p.default);
      this.targetParams.set(p.id, p.default);
    }
  }

  async loadModel(modelUrl: string): Promise<void> {
    this._generation++;
    // 只清场景，**不销毁 renderer**：一个 <canvas> 只能有一个 WebGL 上下文，
    // 销毁后在同一个 canvas 上重建会拿到失效上下文（checkMaxIfStatementsInShader(0)），
    // 预览从此永久空白。换模型换的是舞台内容，不是渲染器。
    this.clearScene();
    // clearScene() 会清空参数表；重新载入后必须恢复标准参数默认值，
    // 否则 update() 的插值循环没有可动参数，角色只会停在静止姿态。
    for (const p of STANDARD_PARAMS) {
      this.params.set(p.id, p.default);
      this.targetParams.set(p.id, p.default);
    }
    const generation = this._generation;
    const viewW = this.canvas.clientWidth || this.canvas.width || 512;
    const viewH = this.canvas.clientHeight || this.canvas.height || 512;
    let app = this.app;
    if (!app || !app.renderer) {
      try {
        app = new Application({
          view: this.canvas,
          width: viewW,
          height: viewH,
          backgroundColor: this.options.backgroundColor,
          backgroundAlpha: this.options.backgroundAlpha,
          antialias: this.options.antialias,
          resolution: this.options.resolution,
          autoDensity: true,
          autoStart: false,
        });
      } catch (err) {
        throw new Error(
          `WebGL is not available in this browser, cannot initialise the renderer: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
      if (!app || !app.renderer) {
        throw new Error('PixiJS renderer failed to initialise (no WebGL context).');
      }
      this.app = app;
    } else if (viewW > 0 && viewH > 0) {
      app.renderer.resize(viewW, viewH);
    }
    this.root = new Container();
    app.stage.addChild(this.root);

    try {
      await this.loadModel3(modelUrl);
    } catch (err) {
      // 失败时只清场景：renderer 留着，重试或换模型还能用同一个上下文。
      if (generation === this._generation) {
        this.clearScene();
      }
      throw err;
    }
    if (generation !== this._generation) {
      return; // a newer loadModel replaced us
    }

    if (this.options.autoStart) {
      this.start();
    }
  }

  /**
   * 清空当前模型的舞台与内部状态，保留 renderer。
   * 贴图交给 Assets 缓存管理，这里不销毁，否则同一 canvas 换模型时会重复解码。
   */
  private clearScene(): void {
    this.stop();
    if (this.root) {
      this.root.destroy({ children: true });
      this.root = null;
    }
    this.app?.stage.removeChildren();
    this.layers = [];
    this.artMeshes = [];
    this.warps.clear();
    this.canvasSize = null;
    this.params.clear();
    this.targetParams.clear();
    this.expressions.clear();
    this.currentExpression = 'default';
    this.expressionWeight = 0;
    this.expressionTargetWeight = 0;
    this.expressionBase.clear();
    this.externalHold.clear();
    this.motionGroups.clear();
    this.motion = null;
    this.physics = null;
    this.physicsDiagnostics = { chains: 0, loaded: false };
    this.resolvedParams = STANDARD_PARAMS.map((p) => ({ ...p }));
    this.bounds = resolveBounds(null);
    this.model3 = null;
  }

  private async loadModel3(modelUrl: string): Promise<void> {
    const app = this.app;
    if (!app || !app.renderer) {
      throw new Error('Renderer is not initialised.');
    }
    const generation = this._generation;
    const baseUrl = modelUrl.substring(0, modelUrl.lastIndexOf('/') + 1);
    const res = await fetch(modelUrl);
    if (!res.ok) throw new Error(`Failed to load model3.json: ${res.status}`);
    const model3 = (await res.json()) as Model3Json;
    // 每个 await 之后都要重新确认自己还是当前这一代：React 严格模式的双挂载
    // 或快速换模型会在 fetch 期间 destroy()，此时 app/renderer 已经是 null。
    if (generation !== this._generation || !this.app || !this.app.renderer) {
      return;
    }
    this.model3 = model3;
    this._applyModelParameters(model3);
    await this.loadRuntimeAssets(model3, baseUrl, generation);
    if (generation !== this._generation) return;

    const cx = (app.renderer.width / (this.options.resolution || 1)) / 2;
    const cy = (app.renderer.height / (this.options.resolution || 1)) / 2;

    // 绑定模式优先：只有它能让参数真的驱动几何。预览层兜底在老产物（无
    // bindings.json）上仍可用，但那是整画布平面的近似形变，不是模型本身。
    const bindingsFile = model3.FileReferences.Bindings;
    if (bindingsFile) {
      try {
        await this.loadBindingsModel(model3, baseUrl, bindingsFile, cx, cy);
        if (generation !== this._generation) return;
        if (this.artMeshes.length > 0) {
          this._fitLayers();
          return;
        }
        // eslint-disable-next-line no-console
        console.warn('bindings.json 里没有可用网格，退回预览层渲染');
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn('绑定渲染初始化失败，退回预览层渲染:', err);
      }
    }

    // Prefer per-layer preview textures (full-canvas PNGs that stack into a
    // coherent character and can be deformed per part). Fall back to the
    // packed atlas textures when no PreviewLayers manifest exists.
    const preview = model3.PreviewLayers;
    if (preview && preview.length > 0) {
      const ordered = [...preview].sort((a, b) => (a.Z ?? 0) - (b.Z ?? 0));
      for (let i = 0; i < ordered.length; i++) {
        const layer = ordered[i];
        const texUrl = layer.Texture.startsWith('http')
          ? layer.Texture
          : baseUrl + layer.Texture;
        try {
          const tex = (await Assets.load(texUrl)) as Texture;
          if (generation !== this._generation || !this.app || !this.root) {
            return; // destroyed / replaced while loading
          }
          const entry = this.createMeshLayer(
            tex,
            layer.Name,
            i,
            cx,
            cy,
            layer.Group as PartGroup,
            layer.Width,
            layer.Height,
          );
          this.root.addChild(entry.mesh);
          this.layers.push(entry);
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn(`Failed to load preview layer ${layer.Name}:`, err);
        }
      }
      this._fitLayers();
      return;
    }

    const textures = model3.FileReferences.Textures || [];
    for (let i = 0; i < textures.length; i++) {
      const texUrl = textures[i].startsWith('http')
        ? textures[i]
        : baseUrl + textures[i];
      try {
        const tex = (await Assets.load(texUrl)) as Texture;
        if (generation !== this._generation || !this.app || !this.root) {
          return;
        }
        const entry = this.createMeshLayer(tex, textures[i], i, cx, cy);
        this.root.addChild(entry.mesh);
        this.layers.push(entry);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn(`Failed to load texture ${texUrl}:`, err);
      }
    }

    this._fitLayers();
  }

  /**
   * 绑定模式：按 `{name}.bindings.json` 建真实美术网格。
   *
   * 顶点换到**局部坐标**（画布中心为原点）后一次性算好；键形与变形器网格同样
   * 换算。逐帧只做插值 + 写缓冲，不再碰坐标换算。
   */
  private async loadBindingsModel(
    model3: Model3Json,
    baseUrl: string,
    bindingsFile: string,
    cx: number,
    cy: number,
  ): Promise<void> {
    const url = bindingsFile.startsWith('http')
      ? bindingsFile
      : baseUrl + bindingsFile;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to load bindings.json: ${res.status}`);
    const doc = (await res.json()) as BindingsDocument;
    if (!doc || doc.version !== 1) {
      throw new Error(`Unsupported bindings version: ${doc?.version}`);
    }
    if (!Array.isArray(doc.meshes) || doc.meshes.length === 0) return;
    if (!this.app || !this.root) return;

    const canvasW = doc.canvas?.width || 1;
    const canvasH = doc.canvas?.height || 1;
    const halfW = canvasW / 2;
    const halfH = canvasH / 2;
    this.canvasSize = { width: canvasW, height: canvasH };

    // bindings 能驱动但 model3.json 没声明的参数，用绑定表里的默认值补齐。
    for (const p of doc.parameters || []) {
      if (!this.params.has(p.id)) {
        this.params.set(p.id, p.default);
        this.targetParams.set(p.id, p.default);
      }
    }

    const textureFiles = model3.FileReferences.Textures || [];
    const pages: Texture[] = [];
    for (const file of textureFiles) {
      const texUrl = file.startsWith('http') ? file : baseUrl + file;
      pages.push((await Assets.load(texUrl)) as Texture);
      if (!this.app || !this.root) return;
    }
    if (pages.length === 0) {
      throw new Error('bindings 渲染需要至少一张图集贴图');
    }

    for (const spec of doc.deformers || []) {
      // rest 决定顶点在网格里的 u/v；控制点已经搬到局部坐标，rest 不跟着搬
      // 就会把所有点算到网格外，整张网格塌成一个点。
      const localSpec: BindingsDeformer = {
        ...spec,
        rest: [
          spec.rest[0] - halfW,
          spec.rest[1] - halfH,
          spec.rest[2] - halfW,
          spec.rest[3] - halfH,
        ],
      };
      this.warps.set(spec.id, {
        spec: localSpec,
        grids: spec.grids.map((g) => offsetFlat(g, -halfW, -halfH)),
        axes: spec.axes || [],
        scratch: null,
        current: null,
      });
    }

    // 层序以 drawOrder 为准：图集网格不是按目录顺序画出来的。
    const ordered = doc.meshes
      .map((m, index) => ({ m, index }))
      .sort((a, b) => a.m.drawOrder - b.m.drawOrder || a.index - b.index);
    const root = this.root;
    if (!root) return;
    for (const { m } of ordered) {
      const entry = this.createArtMesh(m, pages, cx, cy, halfW, halfH);
      root.addChild(entry.mesh);
      this.artMeshes.push(entry);
    }
  }

  /** 一条 meshes 记录 -> 一个可逐帧改顶点的 Pixi Mesh。 */
  private createArtMesh(
    spec: BindingsMesh,
    pages: Texture[],
    cx: number,
    cy: number,
    halfW: number,
    halfH: number,
  ): ArtMeshEntry {
    const rest = offsetFlat(spec.vertices, -halfW, -halfH);
    const positions = new Float32Array(rest);
    const uvs = new Float32Array(spec.uvs);
    let maxIndex = 0;
    for (const i of spec.indices) if (i > maxIndex) maxIndex = i;
    const indices =
      maxIndex > 65535
        ? new Uint32Array(spec.indices)
        : new Uint16Array(spec.indices);
    // Pixi v7 的 Buffer 类型早于 TS 5.7 的 ArrayBuffer 泛型，直接传 TypedArray
    // 会报类型不符；这里必须传**同一份**数组本体（不能转成普通数组，否则
    // 逐帧改动的位置写不进 GPU 缓冲）。
    const geometry = new MeshGeometry(
      positions as unknown as ArrayBuffer,
      uvs as unknown as ArrayBuffer,
      indices as unknown as ArrayBuffer,
    );
    const page = pages[spec.texture] ?? pages[0];
    const material = new MeshMaterial(page ?? Texture.WHITE);
    const mesh = new Mesh(geometry, material);
    mesh.x = cx;
    mesh.y = cy;
    mesh.alpha = typeof spec.opacity === 'number' ? spec.opacity : 1;

    const keyforms = spec.keyforms;
    return {
      id: spec.id,
      mesh,
      geometry,
      positions,
      rest,
      keyformVertices: keyforms
        ? keyforms.vertices.map((v) => offsetFlat(v, -halfW, -halfH))
        : null,
      keyformOpacities: keyforms?.opacities
        ? keyforms.opacities.slice()
        : null,
      axes: (keyforms?.axes ?? []) as Array<[string, number[]]>,
      baseOpacity: typeof spec.opacity === 'number' ? spec.opacity : 1,
      deformerId: spec.deformer || '',
      scratch: null,
    };
  }

  /**
   * Uniformly scale all layer meshes so the composed character fits the
   * canvas. Layer textures are full-canvas (often larger than the viewport),
   * so without this the character would overflow. Scaling is applied to the
   * mesh transform; vertex deformation offsets live in the same local space
   * and therefore scale consistently.
   */
  private _fitLayers(): void {
    if (!this.app) return;
    if (!this.layers.length && !this.artMeshes.length) return;
    const viewW = this.app.renderer.width / (this.options.resolution || 1);
    const viewH = this.app.renderer.height / (this.options.resolution || 1);
    let maxW = 0;
    let maxH = 0;
    if (this.canvasSize) {
      maxW = this.canvasSize.width;
      maxH = this.canvasSize.height;
    } else {
      for (const l of this.layers) {
        maxW = Math.max(maxW, l.texWidth);
        maxH = Math.max(maxH, l.texHeight);
      }
    }
    if (maxW <= 0 || maxH <= 0) return;
    const fit = Math.min(viewW / maxW, viewH / maxH) * 0.95;
    for (const l of this.layers) {
      l.mesh.scale.set(fit, fit);
    }
    for (const a of this.artMeshes) {
      a.mesh.scale.set(fit, fit);
    }
  }

  /**
   * Build a deformable PlaneGeometry mesh for one texture. Vertices are
   * centered around the mesh origin (0,0) so mesh.position acts as the
   * layer centre, matching the old anchor-0.5 sprite behaviour.
   */
  private createMeshLayer(
    tex: Texture,
    name: string,
    index: number,
    cx: number,
    cy: number,
    group?: PartGroup,
    forcedW?: number,
    forcedH?: number,
  ): LayerEntry {
    const tw = forcedW || tex.width || 1;
    const th = forcedH || tex.height || 1;
    const geometry = new PlaneGeometry(tw, th, PLANE_SEGMENTS, PLANE_SEGMENTS);

    // Re-center vertices around the plane centre.
    const posBuffer = geometry.getBuffer('aVertexPosition');
    const positions = posBuffer.data as unknown as Float32Array;
    for (let k = 0; k < positions.length / 2; k++) {
      positions[k * 2] -= tw / 2;
      positions[k * 2 + 1] -= th / 2;
    }
    posBuffer.update();

    const basePositions = new Float32Array(positions);
    const material = new MeshMaterial(tex);
    const mesh = new Mesh(geometry, material);
    mesh.x = cx;
    mesh.y = cy;

    return {
      name: `layer_${index}_${name}`,
      mesh,
      geometry,
      baseX: cx,
      baseY: cy,
      basePositions,
      partGroup: group ?? this._classifyPartGroup(name),
      texWidth: tw,
      texHeight: th,
      segW: PLANE_SEGMENTS,
      segH: PLANE_SEGMENTS,
      contentBounds: this._analyseContentBounds(tex),
    };
  }

  /**
   * Find the normalised opaque-content bounding box of a texture by scanning
   * its alpha channel. This lets mouth/eye deformation target the *actual*
   * feature (which sits somewhere on a full-canvas layer) instead of the
   * geometric canvas centre. Returns null when pixels can't be read.
   */
  private _analyseContentBounds(tex: Texture): ContentBounds | null {
    try {
      const w = tex.width || 0;
      const h = tex.height || 0;
      if (w <= 0 || h <= 0) return null;
      const source = (tex.baseTexture?.resource as { source?: CanvasImageSource } | null)
        ?.source;
      if (!source) return null;
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      if (!ctx) return null;
      ctx.drawImage(source, 0, 0, w, h);
      const { data } = ctx.getImageData(0, 0, w, h);
      let minX = w;
      let minY = h;
      let maxX = 0;
      let maxY = 0;
      let found = false;
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          if (data[(y * w + x) * 4 + 3] > 16) {
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
            found = true;
          }
        }
      }
      if (!found) return null;
      return {
        minU: minX / w,
        maxU: maxX / w,
        minV: minY / h,
        maxV: maxY / h,
        centerV: (minY + maxY) / 2 / h,
      };
    } catch {
      return null;
    }
  }

  private _classifyPartGroup(name: string): PartGroup {
    const n = name.toLowerCase();
    if (/(hair|bangs|ahoge)/.test(n)) return 'hair';
    if (/(eye|iris|pupil)/.test(n)) return 'eyes';
    if (/(mouth|lip|teeth)/.test(n)) return 'mouth';
    if (/(face|cheek|nose)/.test(n)) return 'face';
    if (/(body|chest|clothes)/.test(n)) return 'body';
    return 'other';
  }

  /**
   * 用模型声明的参数刷新清单、夹取区间与**静止姿态**。
   *
   * ``loadModel`` 会先用标准表预置同名参数（``ParamBreath`` = 0），所以这里必须
   * 按模型声明覆写，不能只在「缺键」时填默认值：否则声明值永远进不来，待机淡入
   * 的 0.5 秒里胸口从 0 抬到 0.5，静止预览更是整段停在吸气姿态。
   */
  private _applyModelParameters(model3: Model3Json | null): void {
    this.resolvedParams = resolveParameters(model3);
    this.bounds = resolveBounds(model3);
    for (const p of this.resolvedParams) {
      this.params.set(p.id, p.default);
      this.targetParams.set(p.id, p.default);
    }
  }

  setParameter(name: string, value: number): void {
    this.targetParams.set(name, clampToBounds(value, this.bounds.get(name)));
  }

  setParameters(params: Record<string, number>): void {
    for (const [k, v] of Object.entries(params)) {
      if (typeof v === 'number') {
        this.targetParams.set(k, clampToBounds(v, this.bounds.get(k)));
        this.externalHold.set(k, EXTERNAL_HOLD_SECONDS);
      }
    }
  }

  setExpression(name: string): void {
    this.restoreExpressionBase();
    this.currentExpression = name;
    const spec = this.expressions.get(name);
    if (!spec) {
      this.expressionTargetWeight = 0;
      return;
    }
    this.expressionTargetWeight = 1;
    this.expressionFadeRate = spec.fadeIn > 0 ? 1 / spec.fadeIn : 30;
  }

  get currentExpressionName(): string {
    return this.currentExpression;
  }

  /** 当前已从产物里载入的表情名（不含手工注册的）。 */
  get expressionNames(): string[] {
    return Array.from(this.expressions.keys());
  }

  /**
   * 手工注册表情。传 Record 时按 exp3 的 Add 语义叠加；传数组时按各自 Blend。
   */
  registerExpression(
    name: string,
    params: Record<string, number> | ExpressionParameter[],
  ): void {
    const parameters = Array.isArray(params)
      ? params
      : Object.entries(params).map(([id, value]) => ({ id, value, blend: 'Add' as const }));
    this.expressions.set(name, {
      name, parameters, fadeIn: 0.5, fadeOut: 0.5,
    });
  }

  // --- 动作 / 表情 / 物理 -------------------------------------------------

  /** 只附带说明：动作组名用于 UI 展示，物理链数用于验收页。 */
  get motionGroupNames(): string[] {
    return Array.from(this.motionGroups.keys());
  }

  get motionCount(): number {
    let count = 0;
    for (const files of this.motionGroups.values()) count += files.length;
    return count;
  }

  get physicsChains(): number {
    return this.physicsDiagnostics.chains;
  }

  get physicsLoaded(): boolean {
    return this.physicsDiagnostics.loaded;
  }

  get idlePlaying(): boolean {
    return this.idleEnabled && this.motion !== null;
  }

  /** 待机动作开关：开启后循环播 idle 组，关闭后停在当前姿态。 */
  setIdle(enabled: boolean): void {
    this.idleEnabled = enabled;
    if (enabled) {
      if (!this.motion) this.startIdleMotion();
    } else if (this.motion && this.motion.group === 'idle') {
      this.motion = null;
    }
  }

  /** 手动播一个动作组；返回是否找到该动作。 */
  playMotion(group: string, index = 0): boolean {
    const files = this.motionGroups.get(group);
    if (!files || !files[index]) return false;
    this.motion = new MotionPlayback(files[index], group, index);
    return true;
  }

  stopMotion(): void {
    this.motion = null;
  }

  private startIdleMotion(): void {
    const files = this.motionGroups.get('idle') || this.motionGroups.values().next().value;
    if (!files || !files.length) return;
    this.motion = new MotionPlayback(files[0], 'idle', 0);
  }

  private paramDefault(name: string): number {
    return this.resolvedParams.find((p) => p.id === name)?.default ?? 0;
  }

  private physicsRange(name: string): ParameterRange | undefined {
    const bounds = this.bounds.get(name);
    if (!bounds) return undefined;
    return { min: bounds.min, default: this.paramDefault(name), max: bounds.max };
  }

  private async loadRuntimeAssets(
    model3: Model3Json,
    baseUrl: string,
    generation: number,
  ): Promise<void> {
    const refs = model3.FileReferences;
    const tasks: Array<Promise<void>> = [];
    if (refs.Physics) {
      tasks.push(this.loadPhysics(`${baseUrl}${refs.Physics}`, generation));
    }
    for (const entry of refs.Expressions || []) {
      tasks.push(this.loadExpression(`${baseUrl}${entry.File}`, entry.Name, generation));
    }
    for (const [group, list] of Object.entries(refs.Motions || {})) {
      for (let i = 0; i < list.length; i++) {
        tasks.push(this.loadMotionFile(`${baseUrl}${list[i].File}`, group, list[i], generation));
      }
    }
    await Promise.all(tasks);
  }

  private async loadPhysics(url: string, generation: number): Promise<void> {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const document = parsePhysics3(await res.json());
      if (generation !== this._generation) return;
      this.physics = new PhysicsEngine(document);
      this.physicsDiagnostics = { chains: document.settings.length, loaded: true };
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn('physics3.json 加载失败，物理不生效:', err);
    }
  }

  private async loadExpression(url: string, name: string, generation: number): Promise<void> {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (generation !== this._generation) return;
      const parameters = (json.Parameters || [])
        .filter((p: any) => p && typeof p.Id === 'string')
        .map((p: any): ExpressionParameter => ({
          id: p.Id,
          value: typeof p.Value === 'number' && Number.isFinite(p.Value) ? p.Value : 0,
          blend: p.Blend === 'Multiply' ? 'Multiply' : p.Blend === 'Overwrite' ? 'Overwrite' : 'Add',
        }));
      this.expressions.set(name, {
        name,
        parameters,
        fadeIn: Number.isFinite(json.FadeInTime) ? json.FadeInTime : 0.5,
        fadeOut: Number.isFinite(json.FadeOutTime) ? json.FadeOutTime : 0.5,
      });
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn(`表情 ${name} 加载失败:`, err);
    }
  }

  private async loadMotionFile(
    url: string,
    group: string,
    entry: { File: string; FadeInTime?: number; FadeOutTime?: number },
    generation: number,
  ): Promise<void> {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const file = parseMotion(await res.json(), {
        fadeIn: entry.FadeInTime,
        fadeOut: entry.FadeOutTime,
      });
      if (generation !== this._generation) return;
      const list = this.motionGroups.get(group) || [];
      list.push(file);
      this.motionGroups.set(group, list);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn(`动作 ${group}/${entry.File} 加载失败:`, err);
    }
  }

  /** 当前生效表情覆盖的参数；这些参数留在「目标值 + 低通」的合成路径上。 */
  private activeExpressionParameterIds(): Set<string> {
    if (this.expressionWeight <= 0) return new Set();
    const spec = this.expressions.get(this.currentExpression);
    return new Set((spec?.parameters ?? []).map((p) => p.id));
  }

  /**
   * 每帧推进动作，把采样值写进参数；淡入权重决定它与当前值的比例。
   *
   * 动作是它驱动的那批参数的**权威值**，因此这里同时写 `params`：再走
   * `update()` 的 0.15 低通会把快速动作抹平 —— 实测（2026-11-01，工作台
   * 预览 singer）动作文件里眼睑眨眼到 0，浏览器里最低只到 0.3345，看起来
   * 只是眼皮抖一下。淡入仍按权重缓入，不会开局跳变。
   */
  private updateMotion(dt: number): void {
    if (!this.motion && this.idleEnabled) this.startIdleMotion();
    const playback = this.motion;
    if (!playback) return;
    playback.advance(dt);
    const weight = playback.weight;
    // 表情生效中的参数不直接落值：表情每帧叠在目标值上、再由 update() 的
    // 低通收敛，直接写 params 会让动作每帧把它按回自己的值，表情只剩 15%。
    const held = this.activeExpressionParameterIds();
    playback.sample().forEach((value, id) => {
      // 表情或外部输入正在驱动这个参数时不落值，否则每帧都会把它们按回动作自己的值。
      if (held.has(id) || this.externalHold.has(id)) return;
      const target = clampToBounds(value, this.bounds.get(id));
      const targetNow = this.targetParams.get(id) ?? target;
      this.targetParams.set(
        id, weight >= 1 ? target : targetNow + (target - targetNow) * weight);
      const now = this.params.get(id) ?? target;
      this.params.set(id, weight >= 1 ? target : now + (target - now) * weight);
    });
    if (playback.finished) this.motion = null;
  }

  /**
   * 表情以 exp3 的 Blend 语义叠在当前目标参数上，按淡入/淡出权重插值。
   * 叠加要基于「表情生效前的值」，否则每帧累加会一路漂走。
   */
  private updateExpression(dt: number): void {
    if (this.expressionWeight < this.expressionTargetWeight) {
      this.expressionWeight = Math.min(
        this.expressionTargetWeight,
        this.expressionWeight + this.expressionFadeRate * dt,
      );
    } else if (this.expressionWeight > this.expressionTargetWeight) {
      this.expressionWeight = Math.max(
        this.expressionTargetWeight,
        this.expressionWeight - this.expressionFadeRate * dt,
      );
    }
    const spec = this.expressions.get(this.currentExpression);
    if (!spec || this.expressionWeight <= 0) {
      this.restoreExpressionBase();
      return;
    }
    const weight = this.expressionWeight;
    for (const p of spec.parameters) {
      if (!this.expressionBase.has(p.id)) {
        this.expressionBase.set(p.id, this.targetParams.get(p.id) ?? this.paramDefault(p.id));
      }
      const base = this.expressionBase.get(p.id) ?? 0;
      let value: number;
      if (p.blend === 'Multiply') {
        value = base * (1 + (p.value - 1) * weight);
      } else if (p.blend === 'Overwrite') {
        value = base + (p.value - base) * weight;
      } else {
        value = base + p.value * weight;
      }
      this.targetParams.set(p.id, clampToBounds(value, this.bounds.get(p.id)));
    }
  }

  private restoreExpressionBase(): void {
    for (const [id, value] of this.expressionBase) {
      this.targetParams.set(id, value);
    }
    this.expressionBase.clear();
  }

  /** 物理读的是平滑后的参数，写回直接落到 params + targetParams，避免下一帧被拉回。 */
  private stepPhysics(dt: number): void {
    const engine = this.physics;
    if (!engine) return;
    engine.step(dt, {
      read: (name) => this.params.get(name) ?? 0,
      range: (name) => this.physicsRange(name),
      write: (name, value) => {
        this.params.set(name, value);
        this.targetParams.set(name, clampToBounds(value, this.bounds.get(name)));
      },
    });
  }

  getParameter(name: string): number {
    return this.params.get(name) ?? 0;
  }

  getAllParameters(): Record<string, number> {
    const out: Record<string, number> = {};
    this.params.forEach((v, k) => {
      out[k] = v;
    });
    return out;
  }

  get fps(): number {
    return this._fps;
  }

  get modelMeta(): Model3Json | null {
    return this.model3;
  }

  // --- Validation introspection (used by the Validate tab) ---

  get layersLoaded(): number {
    return this.artMeshes.length || this.layers.length;
  }

  get hasMeshGeometry(): boolean {
    return this.artMeshes.length > 0 || this.layers.length > 0;
  }

  /** 当前是否跑在真实绑定（美术网格 + 键形 + 变形器）模式。 */
  get usesBindings(): boolean {
    return this.artMeshes.length > 0;
  }

  get paramsCount(): number {
    return this.resolvedParams.length;
  }

  get triangleCount(): number {
    let count = 0;
    for (const layer of this.layers) {
      const idx = layer.geometry.indexBuffer?.data;
      if (idx && idx.length) {
        count += idx.length / 3;
      }
    }
    for (const art of this.artMeshes) {
      const idx = art.geometry.indexBuffer?.data;
      if (idx && idx.length) {
        count += idx.length / 3;
      }
    }
    return Math.floor(count);
  }

  resize(width: number, height: number): void {
    if (!this.app) return;
    this.app.renderer.resize(width, height);
    // re-center layers and recompute fit scale for the new viewport.
    for (const layer of this.layers) {
      layer.baseX = width / 2;
      layer.baseY = height / 2;
    }
    for (const art of this.artMeshes) {
      art.mesh.x = width / 2;
      art.mesh.y = height / 2;
    }
    this._fitLayers();
  }

  start(): void {
    if (this.running || !this.app) return;
    this.running = true;
    this._lastFpsTime = performance.now();
    this._frames = 0;
    this.tickerFn = (dt: number) => this.update(dt);
    this.app.ticker.add(this.tickerFn);
    this.app.start();
  }

  stop(): void {
    if (!this.running || !this.app) return;
    this.running = false;
    if (this.tickerFn) {
      this.app.ticker.remove(this.tickerFn);
      this.tickerFn = null;
    }
    this.app.stop();
  }

  /**
   * 导出当前画面（PNG data URL）；渲染器没起来或画布被污染时返回 null。
   *
   * 必须先显式渲染一帧再读像素：WebGL 默认 `preserveDrawingBuffer: false`，
   * 合成之后缓冲就被清空 —— 直接 `toDataURL()` 实测拿到的是 165×444 的
   * **全透明**图（0 个不透明像素），而画面上模型明明在。
   */
  captureFrame(): string | null {
    if (!this.app) return null;
    try {
      this.app.renderer.render(this.app.stage);
      return this.canvas.toDataURL('image/png');
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn('当前帧导出失败:', err);
      return null;
    }
  }

  private update(deltaFrames: number): void {
    const dt = deltaFrames / 60;
    // 外部输入的让位窗口按帧推进：过期就把参数交还给动作。
    for (const [id, remain] of this.externalHold) {
      const left = remain - dt;
      if (left > 0) this.externalHold.set(id, left);
      else this.externalHold.delete(id);
    }
    // 顺序：动作写目标 → 表情叠在目标上 → 插值 → 物理（物理读平滑值、
    // 直接写回），这样次级运动总是滞后于主动作，和内核一致。
    this.updateMotion(dt);
    this.updateExpression(dt);

    // lerp toward targets
    this.targetParams.forEach((target, key) => {
      const current = this.params.get(key) ?? target;
      const next = current + (target - current) * Math.min(1, this.lerpSpeed * 60 * dt);
      this.params.set(key, next);
    });

    this.stepPhysics(dt);

    if (this.artMeshes.length > 0) {
      this.updateArtMeshes();
    } else {
      this.updatePreviewLayers();
    }

    this._frames++;
    const now = performance.now();
    if (now - this._lastFpsTime >= 1000) {
      this._fps = Math.round(
        (this._frames * 1000) / (now - this._lastFpsTime),
      );
      this._frames = 0;
      this._lastFpsTime = now;
    }

    for (const cb of this.frameCallbacks) {
      try {
        cb();
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error('Frame callback error:', err);
      }
    }
  }

  /**
   * 真实形变：先按参数把键形插值成顶点，再让每个点穿过它所属的 warp 变形器
   * 链，最后写进 GPU 缓冲。参数缺省为 0 与内核一致；越界由 bracketKeys 夹边。
   */
  private updateArtMeshes(): void {
    const resolve = (id: string): number => this.params.get(id) ?? 0;
    const point = { x: 0, y: 0 };

    // 变形器一帧只求一次：同一张网格可能被多个美术网格共享。
    for (const warp of this.warps.values()) {
      const rest = warp.grids[0];
      if (!rest) continue;
      const terms = keyformTerms(warp.axes, resolve);
      if (!warp.current || warp.current.length !== rest.length) {
        warp.current = new Float32Array(rest.length);
      }
      if (terms.length === 0) {
        warp.current.set(rest);
      } else if (terms.length === 1) {
        warp.current.set(warp.grids[terms[0].index] ?? rest);
      } else {
        if (!warp.scratch || warp.scratch.length !== rest.length) {
          warp.scratch = new Float32Array(rest.length);
        }
        blendFlat(terms, warp.grids, warp.current);
      }
    }
    const deformerById = (id: string): BindingsDeformer | undefined =>
      this.warps.get(id)?.spec;
    const gridOf = (d: BindingsDeformer): Float32Array =>
      this.warps.get(d.id)?.current ?? new Float32Array(0);

    for (const entry of this.artMeshes) {
      const positions = entry.positions;
      const terms =
        entry.keyformVertices && entry.axes.length
          ? keyformTerms(entry.axes, resolve)
          : [];

      if (terms.length === 0 || !entry.keyformVertices) {
        positions.set(entry.rest);
      } else if (terms.length === 1) {
        positions.set(entry.keyformVertices[terms[0].index] ?? entry.rest);
      } else {
        if (!entry.scratch || entry.scratch.length !== positions.length) {
          entry.scratch = new Float32Array(positions.length);
        }
        blendFlat(terms, entry.keyformVertices, positions);
      }

      const warp = entry.deformerId
        ? this.warps.get(entry.deformerId)
        : undefined;
      if (warp) {
        for (let i = 0; i < positions.length; i += 2) {
          applyDeformerChain(
            positions[i],
            positions[i + 1],
            warp.spec,
            deformerById,
            gridOf,
            point,
          );
          positions[i] = point.x;
          positions[i + 1] = point.y;
        }
      }

      entry.mesh.alpha =
        entry.keyformOpacities && terms.length
          ? blendScalar(terms, entry.keyformOpacities)
          : entry.baseOpacity;

      const buffer = entry.geometry.getBuffer('aVertexPosition');
      buffer.update();
    }
  }

  /**
   * 预览层兜底：整画布平面 + 手写启发式形变。只有老产物（没有 bindings.json）
   * 会走到这里，参数拨动时只是近似位移，不代表模型真实绑定。
   */
  private updatePreviewLayers(): void {
    const angleX = this.params.get('ParamAngleX') ?? 0;
    const angleY = this.params.get('ParamAngleY') ?? 0;
    const angleZ = this.params.get('ParamAngleZ') ?? 0;
    const bodyX = this.params.get('ParamBodyAngleX') ?? 0;
    const breath = this.params.get('ParamBreath') ?? 0;
    const mouthOpen = this.params.get('ParamMouthOpenY') ?? 0;
    const mouthForm = this.params.get('ParamMouthForm') ?? 0;
    const time = performance.now() * 0.001;
    const breathVal = breath + Math.sin(time * 1.5) * 0.5 + 0.5;

    for (let i = 0; i < this.layers.length; i++) {
      const layer = this.layers[i];
      const { mesh, geometry } = layer;

      // Back layers (lower index) deform less than front layers.
      const depthFactor = i / Math.max(1, this.layers.length - 1);
      const group = layer.partGroup;

      // --- Position lerp + breathing (acts on the mesh transform) ---
      mesh.x = layer.baseX + bodyX * 1.5;
      mesh.y = layer.baseY + (i === 0 ? breathVal * -2 : 0);
      // Subtle head roll for head-related groups.
      if (group === 'face' || group === 'hair' || group === 'eyes' || group === 'mouth') {
        mesh.rotation = (angleZ * Math.PI) / 180 * 0.25;
      } else {
        mesh.rotation = 0;
      }

      // --- Vertex-level warp ---
      const positions = geometry.getBuffer('aVertexPosition').data as unknown as Float32Array;
      const base = layer.basePositions;
      const tw = layer.texWidth;
      const th = layer.texHeight;
      const segW = layer.segW;
      const segH = layer.segH;
      const cb = layer.contentBounds;

      // Tuned, subtle parallax for a believable head turn (radians-ish).
      const ax = (angleX * Math.PI) / 180;
      const ay = (angleY * Math.PI) / 180;

      for (let k = 0; k < positions.length / 2; k++) {
        const v = k * 2;
        const col = k % segW;
        const row = Math.floor(k / segW);
        const u = segW > 1 ? col / (segW - 1) : 0.5;
        const vv = segH > 1 ? row / (segH - 1) : 0.5;

        let ox = 0;
        let oy = 0;

        // Head-turn parallax: horizontal shift follows u (left/right),
        // vertical follows vv. Depth layers move by different amounts.
        const px = (u - 0.5) * 2;
        const py = (vv - 0.5) * 2;
        ox += Math.sin(ax) * px * tw * 0.18 * (0.4 + depthFactor);
        oy += -Math.sin(ay) * py * th * 0.12 * (0.4 + depthFactor);

        // Mouth deformation localised to the actual mouth content.
        if (group === 'mouth' && cb) {
          const inMouthX = u >= cb.minU - 0.02 && u <= cb.maxU + 0.02;
          const mouthH = Math.max(0.001, cb.maxV - cb.minV);
          // Open: pull content below the mouth centre downward.
          if (inMouthX && vv > cb.centerV) {
            const t = (vv - cb.centerV) / Math.max(0.001, cb.maxV - cb.centerV);
            oy += mouthOpen * mouthH * th * 1.6 * Math.min(1, t);
          }
          // Smile/frown: lift/drop the corners.
          if (inMouthX && vv >= cb.minV && vv <= cb.maxV) {
            const edge = Math.min(
              Math.abs(u - cb.minU),
              Math.abs(cb.maxU - u),
            ) / Math.max(0.001, cb.maxU - cb.minU);
            const cornerWeight = 1 - Math.min(1, edge * 2.2);
            oy -= mouthForm * mouthH * th * 0.9 * cornerWeight;
          }
        }

        // Eye blink: squeeze the eye content vertically toward its centre.
        if (group === 'eyes' && cb) {
          const eyeOpen = Math.min(
            this.params.get('ParamEyeLOpen') ?? 1,
            this.params.get('ParamEyeROpen') ?? 1,
          );
          const inEyeX = u >= cb.minU - 0.02 && u <= cb.maxU + 0.02;
          if (inEyeX && vv >= cb.minV && vv <= cb.maxV && eyeOpen < 1) {
            const eyeH = Math.max(0.001, cb.maxV - cb.minV);
            const dist = Math.abs(vv - cb.centerV) / (eyeH / 2);
            oy += (1 - eyeOpen) * eyeH * th * 0.5 * dist * Math.sign(vv - cb.centerV);
          }
        }

        positions[v] = base[v] + ox;
        positions[v + 1] = base[v + 1] + oy;
      }

      geometry.getBuffer('aVertexPosition').update();
    }
  }

  onFrame(callback: () => void): void {
    this.frameCallbacks.push(callback);
  }

  offFrame(callback: () => void): void {
    const idx = this.frameCallbacks.indexOf(callback);
    if (idx >= 0) this.frameCallbacks.splice(idx, 1);
  }

  destroy(): void {
    this._generation++;
    // frameCallbacks 属于播放器持有者（ModelCanvas 在构造时注册一次），
    // 这里不清空：清掉会让 onFrame 在首次加载后静默失效（帧率恒为 0）。
    this.clearScene();
    if (this.app) {
      // removeView=false：<canvas> 由 React 持有，Pixi 不能把它从 DOM 里摘走。
      // 实例销毁后这个 canvas 上的 WebGL 上下文不再复用 —— 调用方要么换新
      // canvas，要么保留同一个 player（loadModel 会复用 renderer）。
      this.app.destroy(false, { children: true, texture: true, baseTexture: true });
      this.app = null;
    }
  }

  get stage(): Container | null {
    return this.root;
  }

  get pixiApp(): Application | null {
    return this.app;
  }
}
