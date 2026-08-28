import { useEffect, useRef } from 'react';
import { LayerRenderer } from '../lib/layer-renderer';
import type { LayerInfo } from '../types';

interface LayerCanvasProps {
  layers: LayerInfo[];
  showBounds?: boolean;
  showNames?: boolean;
  background?: string;
  className?: string;
  onLayerClick?: (layer: LayerInfo | null) => void;
  selectedLayerId?: string | null;
}

export default function LayerCanvas({
  layers,
  showBounds = false,
  showNames = false,
  background = 'transparent',
  className = '',
  onLayerClick,
}: LayerCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<LayerRenderer | null>(null);

  useEffect(() => {
    if (!canvasRef.current || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const width = Math.floor(rect.width);
    const height = Math.floor(rect.height);
    const renderer = new LayerRenderer(canvasRef.current, {
      width,
      height,
      backgroundColor: background,
      showBounds,
      showNames,
    });
    rendererRef.current = renderer;
    renderer.setLayers(layers).catch(() => undefined);

    const onResize = () => {
      if (!containerRef.current || !rendererRef.current) return;
      const r = containerRef.current.getBoundingClientRect();
      rendererRef.current.resize(Math.floor(r.width), Math.floor(r.height));
      rendererRef.current.render();
    };
    const ro = new ResizeObserver(onResize);
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      renderer.destroy();
      rendererRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!rendererRef.current) return;
    rendererRef.current.setLayers(layers).catch(() => undefined);
  }, [layers]);

  useEffect(() => {
    rendererRef.current?.setShowBounds(showBounds);
    rendererRef.current?.setShowNames(showNames);
    rendererRef.current?.setBackgroundColor(background);
    rendererRef.current?.render();
  }, [showBounds, showNames, background]);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!onLayerClick || !rendererRef.current || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const hit = rendererRef.current.hitTest(x, y);
    onLayerClick(hit);
  };

  return (
    <div ref={containerRef} className={`relative w-full h-full overflow-hidden ${className}`}>
      <canvas
        ref={canvasRef}
        onClick={handleClick}
        className="w-full h-full"
        style={{ imageRendering: 'pixelated' }}
      />
    </div>
  );
}

export function getCheckerboardStyle(): React.CSSProperties {
  return {
    backgroundImage:
      'linear-gradient(45deg, #1f1f2b 25%, transparent 25%), linear-gradient(-45deg, #1f1f2b 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #1f1f2b 75%), linear-gradient(-45deg, transparent 75%, #1f1f2b 75%)',
    backgroundSize: '20px 20px',
    backgroundPosition: '0 0, 0 10px, 10px -10px, 10px 0',
    backgroundColor: '#14141c',
  };
}
