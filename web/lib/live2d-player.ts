import { Application, Container, Mesh, MeshMaterial, PlaneGeometry, Texture, Assets } from 'pixi.js';
import type { Model3Json, ParameterDef } from '../types';
import {
  STANDARD_PARAMS,
  clampToBounds,
  resolveBounds,
  resolveParameters,
  type ParameterBounds,
} from './live2d-params';

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
 * Browser-side Live2D-like player using PixiJS.
 *
 * Layers are rendered as deformable 4x4 {@link PlaneGeometry} meshes (not
 * flat sprites), so head turn and mouth open/smile are driven by real
 * vertex displacement each frame. This is a lightweight preview/debug
 * renderer rather than the full Cubism SDK runtime.
 */
export class Live2DPlayer {
  private app: Application | null = null;
  /** Bumped on destroy(); stale async loads check this and bail out. */
  private _generation = 0;
  private readonly canvas: HTMLCanvasElement;
  private readonly options: Live2DPlayerOptions;
  private root: Container | null = null;
  private layers: LayerEntry[] = [];
  private params: Map<string, number> = new Map();
  private targetParams: Map<string, number> = new Map();
  /** 参数清单：模型声明优先，缺失时退回标准表（供 UI / 校验页读取）。 */
  private resolvedParams: ParameterDef[] = STANDARD_PARAMS.map((p) => ({ ...p }));
  /** 可安全夹取的区间；只收可信来源，未知参数保持不夹取。 */
  private bounds: Map<string, ParameterBounds> = resolveBounds(null);
  private expressions: Map<string, Record<string, number>> = new Map();
  private currentExpression = 'default';
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
    this.destroy();
    this._generation++;
    // destroy() 会清空参数表；重新载入后必须恢复标准参数默认值，
    // 否则 update() 的插值循环没有可动参数，角色只会停在静止姿态。
    for (const p of STANDARD_PARAMS) {
      this.params.set(p.id, p.default);
      this.targetParams.set(p.id, p.default);
    }
    const generation = this._generation;
    let app: Application;
    try {
      const viewW =
        this.canvas.clientWidth || this.canvas.width || 512;
      const viewH =
        this.canvas.clientHeight || this.canvas.height || 512;
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
    this.root = new Container();
    app.stage.addChild(this.root);

    try {
      await this.loadModel3(modelUrl);
    } catch (err) {
      // Clean up the half-initialised app on failure so destroy()/retry work.
      if (generation === this._generation) {
        this.destroy();
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
    this.model3 = model3;
    this._applyModelParameters(model3);

    const cx = (app.renderer.width / (this.options.resolution || 1)) / 2;
    const cy = (app.renderer.height / (this.options.resolution || 1)) / 2;

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
   * Uniformly scale all layer meshes so the composed character fits the
   * canvas. Layer textures are full-canvas (often larger than the viewport),
   * so without this the character would overflow. Scaling is applied to the
   * mesh transform; vertex deformation offsets live in the same local space
   * and therefore scale consistently.
   */
  private _fitLayers(): void {
    if (!this.layers.length || !this.app) return;
    const viewW = this.app.renderer.width / (this.options.resolution || 1);
    const viewH = this.app.renderer.height / (this.options.resolution || 1);
    let maxW = 0;
    let maxH = 0;
    for (const l of this.layers) {
      maxW = Math.max(maxW, l.texWidth);
      maxH = Math.max(maxH, l.texHeight);
    }
    if (maxW <= 0 || maxH <= 0) return;
    const fit = Math.min(viewW / maxW, viewH / maxH) * 0.95;
    for (const l of this.layers) {
      l.mesh.scale.set(fit, fit);
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
   * 用模型声明的参数刷新清单与夹取区间。
   * 调用方在载入前设过的值会保留（用 has 判断），避免覆盖用户已拖动的滑块。
   */
  private _applyModelParameters(model3: Model3Json | null): void {
    this.resolvedParams = resolveParameters(model3);
    this.bounds = resolveBounds(model3);
    for (const p of this.resolvedParams) {
      if (!this.params.has(p.id)) this.params.set(p.id, p.default);
      if (!this.targetParams.has(p.id)) this.targetParams.set(p.id, p.default);
    }
  }

  setParameter(name: string, value: number): void {
    this.targetParams.set(name, clampToBounds(value, this.bounds.get(name)));
  }

  setParameters(params: Record<string, number>): void {
    for (const [k, v] of Object.entries(params)) {
      if (typeof v === 'number') {
        this.targetParams.set(k, clampToBounds(v, this.bounds.get(k)));
      }
    }
  }

  setExpression(name: string): void {
    this.currentExpression = name;
    const exp = this.expressions.get(name);
    if (!exp) return;
    for (const [k, v] of Object.entries(exp)) {
      this.targetParams.set(k, clampToBounds(v, this.bounds.get(k)));
    }
  }

  get currentExpressionName(): string {
    return this.currentExpression;
  }

  registerExpression(name: string, params: Record<string, number>): void {
    this.expressions.set(name, params);
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
    return this.layers.length;
  }

  get hasMeshGeometry(): boolean {
    // This renderer always uses PlaneGeometry meshes.
    return this.layers.length > 0;
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

  private update(deltaFrames: number): void {
    const dt = deltaFrames / 60;
    // lerp toward targets
    this.targetParams.forEach((target, key) => {
      const current = this.params.get(key) ?? target;
      const next = current + (target - current) * Math.min(1, this.lerpSpeed * 60 * dt);
      this.params.set(key, next);
    });

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

  onFrame(callback: () => void): void {
    this.frameCallbacks.push(callback);
  }

  offFrame(callback: () => void): void {
    const idx = this.frameCallbacks.indexOf(callback);
    if (idx >= 0) this.frameCallbacks.splice(idx, 1);
  }

  destroy(): void {
    this._generation++;
    this.stop();
    this.frameCallbacks = [];
    this.layers = [];
    this.params.clear();
    this.targetParams.clear();
    this.expressions.clear();
    this.resolvedParams = STANDARD_PARAMS.map((p) => ({ ...p }));
    this.bounds = resolveBounds(null);
    if (this.app) {
      this.app.destroy(true, { children: true, texture: true, baseTexture: true });
      this.app = null;
    }
    this.root = null;
    this.model3 = null;
  }

  get stage(): Container | null {
    return this.root;
  }

  get pixiApp(): Application | null {
    return this.app;
  }
}
