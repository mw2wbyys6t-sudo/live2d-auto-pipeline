import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { Live2DPlayer } from '../lib/live2d-player';
import type { ParamMap, Model3Json } from '../types';

export interface ModelCanvasHandle {
  player: Live2DPlayer | null;
  loadModel: (url: string) => Promise<void>;
  setParameters: (params: ParamMap) => void;
  setExpression: (name: string) => void;
  getFps: () => number;
  resize: (w: number, h: number) => void;
  // Validation introspection (used by the Validate tab)
  readonly layersLoaded: number;
  readonly hasMeshGeometry: boolean;
  readonly paramsCount: number;
  readonly modelMeta: Model3Json | null;
  readonly triangleCount: number;
}

interface ModelCanvasProps {
  modelUrl?: string;
  className?: string;
  wireframe?: boolean;
  onReady?: (player: Live2DPlayer) => void;
  onFrame?: () => void;
  /** 模型加载失败时把原因交给调用方展示，不再静默留一张空白画布。 */
  onError?: (message: string) => void;
}

const ModelCanvas = forwardRef<ModelCanvasHandle, ModelCanvasProps>(function ModelCanvas(
  { modelUrl, className = '', wireframe = false, onReady, onFrame, onError },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<Live2DPlayer | null>(null);
  const onReadyRef = useRef(onReady);
  const onFrameRef = useRef(onFrame);
  const onErrorRef = useRef(onError);
  onReadyRef.current = onReady;
  onErrorRef.current = onError;
  onFrameRef.current = onFrame;

  useImperativeHandle(ref, () => ({
    get player() {
      return playerRef.current;
    },
    async loadModel(url: string) {
      if (!playerRef.current) return;
      await playerRef.current.loadModel(url);
      playerRef.current.start();
    },
    setParameters(params: ParamMap) {
      playerRef.current?.setParameters(params);
    },
    setExpression(name: string) {
      playerRef.current?.setExpression(name);
    },
    getFps() {
      return playerRef.current?.fps ?? 0;
    },
    resize(w: number, h: number) {
      playerRef.current?.resize(w, h);
    },
    // Validation introspection getters
    get layersLoaded() {
      return playerRef.current?.layersLoaded ?? 0;
    },
    get hasMeshGeometry() {
      return playerRef.current?.hasMeshGeometry ?? false;
    },
    get paramsCount() {
      return playerRef.current?.paramsCount ?? 0;
    },
    get modelMeta() {
      return playerRef.current?.modelMeta ?? null;
    },
    get triangleCount() {
      return playerRef.current?.triangleCount ?? 0;
    },
  }));

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    // 画布由播放器自己创建并持有：React 严格模式/热重载会把挂载效应跑两遍，
    // 复用同一个 <canvas> 时第二个 Pixi 渲染器拿到的是已被销毁的 WebGL 上下文
    // （报 checkMaxIfStatementsInShader(0)），预览就永久空白。
    const canvas = document.createElement('canvas');
    canvas.className = 'w-full h-full block';
    container.appendChild(canvas);
    const player = new Live2DPlayer(canvas, {
      backgroundAlpha: 0,
      autoStart: false,
    });
    playerRef.current = player;
    if (onFrameRef.current) {
      player.onFrame(onFrameRef.current);
    }
    onReadyRef.current?.(player);

    return () => {
      player.destroy();
      canvas.remove();
      playerRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!modelUrl || !playerRef.current) return;
    let cancelled = false;
    playerRef.current
      .loadModel(modelUrl)
      .then(() => {
        if (!cancelled) {
          playerRef.current?.start();
        }
      })
      .catch((err) => {
        if (cancelled) return;
        onErrorRef.current?.(err instanceof Error ? err.message : '模型加载失败');
      });
    return () => {
      cancelled = true;
    };
  }, [modelUrl]);

  useEffect(() => {
    if (!containerRef.current || !playerRef.current) return;
    const ro = new ResizeObserver(() => {
      if (!containerRef.current || !playerRef.current) return;
      const r = containerRef.current.getBoundingClientRect();
      playerRef.current.resize(Math.floor(r.width), Math.floor(r.height));
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      ref={containerRef}
      className={`relative w-full h-full ${className}`}
      style={{
        backgroundImage:
          wireframe
            ? 'linear-gradient(rgba(139,92,246,0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(139,92,246,0.08) 1px, transparent 1px)'
            : undefined,
        backgroundSize: wireframe ? '40px 40px' : undefined,
      }}
    >
      {/* 画布由播放器在挂载效应里创建并插入（见上方注释），React 不持有它。 */}
    </div>
  );
});

export default ModelCanvas;
