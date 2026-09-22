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
}

const ModelCanvas = forwardRef<ModelCanvasHandle, ModelCanvasProps>(function ModelCanvas(
  { modelUrl, className = '', wireframe = false, onReady, onFrame },
  ref,
) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<Live2DPlayer | null>(null);
  const onReadyRef = useRef(onReady);
  const onFrameRef = useRef(onFrame);
  onReadyRef.current = onReady;
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
    if (!canvasRef.current) return;
    const player = new Live2DPlayer(canvasRef.current, {
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
      .catch(() => undefined);
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
      <canvas ref={canvasRef} className="w-full h-full block" />
    </div>
  );
});

export default ModelCanvas;
