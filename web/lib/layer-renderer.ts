import type { BlendMode, LayerInfo } from '../types';

export interface RendererOptions {
  width?: number;
  height?: number;
  backgroundColor?: string;
  showBounds?: boolean;
  showNames?: boolean;
  scale?: number;
}

interface LoadedLayer {
  info: LayerInfo;
  image: HTMLImageElement | null;
  loaded: boolean;
}

const BLEND_COMPOSITE_MAP: Record<BlendMode, GlobalCompositeOperation> = {
  normal: 'source-over',
  multiply: 'multiply',
  screen: 'screen',
  overlay: 'overlay',
  darken: 'darken',
  lighten: 'lighten',
  'color-dodge': 'color-dodge',
  'color-burn': 'color-burn',
  'soft-light': 'soft-light',
  'hard-light': 'hard-light',
};

export class LayerRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private layers: LoadedLayer[] = [];
  private width: number;
  private height: number;
  private backgroundColor: string;
  private showBounds: boolean;
  private showNames: boolean;
  private scale: number;
  private offsetX = 0;
  private offsetY = 0;
  private visibility = new Map<string, boolean>();
  private opacityOverride = new Map<string, number>();

  constructor(canvas: HTMLCanvasElement, options: RendererOptions = {}) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D context not available');
    this.ctx = ctx;
    this.width = options.width ?? 512;
    this.height = options.height ?? 512;
    this.backgroundColor = options.backgroundColor ?? 'transparent';
    this.showBounds = options.showBounds ?? false;
    this.showNames = options.showNames ?? false;
    this.scale = options.scale ?? 1;
    this.resize(this.width, this.height);
  }

  resize(width: number, height: number): void {
    this.width = width;
    this.height = height;
    this.canvas.width = width;
    this.canvas.height = height;
  }

  setScale(scale: number): void {
    this.scale = scale;
  }

  setOffset(x: number, y: number): void {
    this.offsetX = x;
    this.offsetY = y;
  }

  setShowBounds(show: boolean): void {
    this.showBounds = show;
  }

  setShowNames(show: boolean): void {
    this.showNames = show;
  }

  setBackgroundColor(color: string): void {
    this.backgroundColor = color;
  }

  async setLayers(layers: LayerInfo[]): Promise<void> {
    this.layers = layers.map((info) => ({
      info,
      image: null,
      loaded: false,
    }));
    this.visibility.clear();
    this.opacityOverride.clear();
    await Promise.all(this.layers.map((l) => this.loadLayer(l)));
    this.render();
  }

  private async loadLayer(layer: LoadedLayer): Promise<void> {
    if (!layer.info.imageUrl && !layer.info.thumbnailUrl) {
      layer.loaded = true;
      return;
    }
    const url = layer.info.imageUrl || layer.info.thumbnailUrl!;
    return new Promise<void>((resolve) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        layer.image = img;
        layer.loaded = true;
        resolve();
      };
      img.onerror = () => {
        layer.loaded = true;
        resolve();
      };
      img.src = url;
    });
  }

  reorder(orderedIds: string[]): void {
    const map = new Map(this.layers.map((l) => [l.info.id, l]));
    this.layers = orderedIds
      .map((id) => map.get(id))
      .filter((l): l is LoadedLayer => !!l);
  }

  setVisibility(id: string, visible: boolean): void {
    this.visibility.set(id, visible);
  }

  setOpacity(id: string, opacity: number): void {
    this.opacityOverride.set(id, opacity);
  }

  isVisible(layer: LoadedLayer): boolean {
    const override = this.visibility.get(layer.info.id);
    return override === undefined ? layer.info.visible : override;
  }

  private getOpacity(layer: LoadedLayer): number {
    const override = this.opacityOverride.get(layer.info.id);
    return override === undefined ? layer.info.opacity : override;
  }

  render(): void {
    const ctx = this.ctx;
    ctx.save();
    ctx.clearRect(0, 0, this.width, this.height);
    if (this.backgroundColor !== 'transparent') {
      ctx.fillStyle = this.backgroundColor;
      ctx.fillRect(0, 0, this.width, this.height);
    }
    ctx.translate(this.offsetX, this.offsetY);
    ctx.scale(this.scale, this.scale);

    for (const layer of this.layers) {
      if (!this.isVisible(layer)) continue;
      ctx.globalAlpha = this.getOpacity(layer);
      ctx.globalCompositeOperation =
        BLEND_COMPOSITE_MAP[layer.info.blendMode] || 'source-over';
      if (layer.image) {
        const b = layer.info.bounds;
        ctx.drawImage(
          layer.image,
          b.x + layer.info.offsetX,
          b.y + layer.info.offsetY,
          b.width || layer.image.width,
          b.height || layer.image.height,
        );
      }
      if (this.showBounds) {
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = 'source-over';
        ctx.strokeStyle = 'rgba(236, 72, 153, 0.7)';
        ctx.lineWidth = 1 / this.scale;
        ctx.strokeRect(
          layer.info.bounds.x + layer.info.offsetX,
          layer.info.bounds.y + layer.info.offsetY,
          layer.info.bounds.width,
          layer.info.bounds.height,
        );
      }
      if (this.showNames) {
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = 'source-over';
        const bx = layer.info.bounds.x + layer.info.offsetX;
        const by = layer.info.bounds.y + layer.info.offsetY;
        ctx.font = `${12 / this.scale}px sans-serif`;
        const text = layer.info.name;
        const metrics = ctx.measureText(text);
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.fillRect(bx, by - 16 / this.scale, metrics.width + 8, 16 / this.scale);
        ctx.fillStyle = '#f472b6';
        ctx.fillText(text, bx + 4, by - 4 / this.scale);
      }
    }
    ctx.restore();
  }

  exportPNG(): string {
    return this.canvas.toDataURL('image/png');
  }

  async exportPNGSequence(): Promise<Blob[]> {
    const blobs: Blob[] = [];
    for (const layer of this.layers) {
      if (!layer.image) continue;
      const off = document.createElement('canvas');
      off.width = layer.info.bounds.width || layer.image.width;
      off.height = layer.info.bounds.height || layer.image.height;
      const octx = off.getContext('2d');
      if (!octx) continue;
      octx.drawImage(layer.image, 0, 0, off.width, off.height);
      const blob = await new Promise<Blob | null>((resolve) =>
        off.toBlob(resolve, 'image/png'),
      );
      if (blob) blobs.push(blob);
    }
    return blobs;
  }

  hitTest(x: number, y: number): LayerInfo | null {
    // topmost first
    for (let i = this.layers.length - 1; i >= 0; i--) {
      const layer = this.layers[i];
      if (!this.isVisible(layer)) continue;
      const b = layer.info.bounds;
      if (
        x >= b.x + layer.info.offsetX &&
        x <= b.x + layer.info.offsetX + b.width &&
        y >= b.y + layer.info.offsetY &&
        y <= b.y + layer.info.offsetY + b.height
      ) {
        return layer.info;
      }
    }
    return null;
  }

  destroy(): void {
    this.layers = [];
    this.visibility.clear();
    this.opacityOverride.clear();
    this.ctx.clearRect(0, 0, this.width, this.height);
  }
}
