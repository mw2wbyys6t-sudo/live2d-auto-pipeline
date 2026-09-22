import { useCallback, useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import {
  Eye,
  Video,
  VideoOff,
  Mic,
  MicOff,
  Camera,
  Monitor,
  Smile,
  Frown,
  Heart,
  Zap,
  Activity,
  Download,
  Maximize2,
} from 'lucide-react';
import type { NextPage } from 'next';
import type { Emotion, ParamMap } from '../types';
import type { ModelCanvasHandle } from '../components/ModelCanvas';
import LoadingSpinner from '../components/LoadingSpinner';
import { apiClient, getBackendWsUrl, type LatestGeneration } from '../lib/api-client';
import type { Live2DPlayer } from '../lib/live2d-player';

const ModelCanvas = dynamic(() => import('../components/ModelCanvas'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center">
      <LoadingSpinner label="Loading renderer…" />
    </div>
  ),
});

const EXPRESSIONS: { id: Emotion; label: string; icon: typeof Smile }[] = [
  { id: 'neutral', label: 'Neutral', icon: Zap },
  { id: 'happy', label: 'Happy', icon: Smile },
  { id: 'sad', label: 'Sad', icon: Frown },
  { id: 'angry', label: 'Angry', icon: Activity },
  { id: 'surprised', label: 'Surprised', icon: Eye },
  { id: 'shy', label: 'Shy', icon: Heart },
];

const PreviewPage: NextPage = () => {
  const canvasRef = useRef<ModelCanvasHandle>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [webcamOn, setWebcamOn] = useState(false);
  const [micOn, setMicOn] = useState(false);
  const [fps, setFps] = useState(0);
  const [params, setParams] = useState<Record<string, number>>({
    ParamAngleX: 0,
    ParamAngleY: 0,
    ParamAngleZ: 0,
    ParamEyeLOpen: 1,
    ParamEyeROpen: 1,
    ParamMouthOpenY: 0,
    ParamMouthForm: 0,
  });
  const [bg, setBg] = useState<'transparent' | 'dark' | 'light' | 'sky'>('dark');
  const [streamActive, setStreamActive] = useState(false);
  const [latest, setLatest] = useState<LatestGeneration | null>(null);
  const [modelUrl, setModelUrl] = useState<string>('');
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const animRef = useRef<number | null>(null);
  const trackWsRef = useRef<WebSocket | null>(null);
  const lastReadoutRef = useRef(0);
  const [error, setError] = useState<string | null>(null);
  /** Normalised gaze (-1..1) driven by pointer position over the stage. */
  const gazeRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  /**
   * 播放器实例，直接由 onReady 取得。
   * 经 next/dynamic 转发的命令式 ref 在部分环境下拿不到实例，导致
   * setParameters 静默丢失（UI 参数在动、角色却静止）。这里直接持有实例驱动。
   */
  const playerRef = useRef<Live2DPlayer | null>(null);

  /** 后端 HTTP 基地址（与 api-client 同一推导） */
  const apiBase = () =>
    (typeof window !== 'undefined' &&
      (window as unknown as { __LIVE2D_API_URL__?: string }).__LIVE2D_API_URL__) ||
    process.env.NEXT_PUBLIC_API_URL ||
    '';

  /** 连接面捕参数推送通道（定义需在 toggleWebcam 之前，避免 TDZ） */
  const connectTrackingWs = useCallback(() => {
    try {
      const ws = new WebSocket(getBackendWsUrl('/ws/tracking'));
      trackWsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type !== 'params' || !msg.params) return;
          playerRef.current?.setParameters(msg.params);
          const now = performance.now();
          if (now - lastReadoutRef.current > 100) {
            lastReadoutRef.current = now;
            setParams((prev) => ({ ...prev, ...msg.params }));
          }
        } catch { /* 忽略坏帧 */ }
      };
      ws.onclose = () => {
        trackWsRef.current = null;
      };
    } catch { /* 连接失败由按钮状态兜底 */ }
  }, []);

  // 自动接力最近一次生成的 model3.json，无需手动加载模型
  useEffect(() => {
    let cancelled = false;
    apiClient
      .getLatestGeneration()
      .then((gen) => {
        if (cancelled || !gen) return;
        setLatest(gen);
        if (gen.model3_json) setModelUrl(gen.model3_json);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // FPS loop
  useEffect(() => {
    const id = setInterval(() => {
      setFps(playerRef.current?.fps ?? 0);
    }, 500);
    return () => clearInterval(id);
  }, []);

  // 闲置动画驱动：呼吸 + 自然眨眼 + 视线跟随指针
  useEffect(() => {
    if (streamActive) return;
    const t0 = performance.now();
    const BLINK_MS = 130;
    let blinkUntil = 0;
    let nextBlinkAt = t0 + 1500 + Math.random() * 2500;
    let lastReadout = 0;

    const tick = (now: number) => {
      const t = (now - t0) / 1000;

      // 呼吸：缓慢正弦 0..1
      const breath = Math.sin(t * 1.6) * 0.5 + 0.5;

      // 自然眨眼：随机间隔触发，单次约 130ms 闭合并恢复
      if (now >= nextBlinkAt) {
        blinkUntil = now + BLINK_MS;
        nextBlinkAt = now + 1800 + Math.random() * 3200;
      }
      let eyeOpen = 1;
      if (now < blinkUntil) {
        const p = (blinkUntil - now) / BLINK_MS; // 1 → 0
        eyeOpen = 1 - Math.sin(p * Math.PI);
      }

      const gaze = gazeRef.current;
      const p: ParamMap = {
        ParamBreath: breath,
        ParamEyeLOpen: eyeOpen,
        ParamEyeROpen: eyeOpen,
        ParamEyeBallX: gaze.x,
        ParamEyeBallY: gaze.y,
        ParamAngleX: gaze.x * 12,
        ParamAngleY: -gaze.y * 8,
        ParamAngleZ: Math.sin(t * 0.5) * 3,
        ParamBodyAngleX: Math.sin(t * 0.4) * 2,
      };

      const handle = playerRef.current;
      if (handle) {
        handle.setParameters(p);
      }
      if (now - lastReadout > 100) {
        lastReadout = now;
        setParams((prev) => ({ ...prev, ...p }));
      }
      animRef.current = requestAnimationFrame(tick);
    };
    animRef.current = requestAnimationFrame(tick);
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [streamActive]);

  /** 指针位置 → 归一化视线，驱动眼球与头部朝向 */
  const handleStageMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    gazeRef.current = {
      x: ((e.clientX - r.left) / r.width) * 2 - 1,
      y: ((e.clientY - r.top) / r.height) * 2 - 1,
    };
  }, []);

  /** 重新拉取最近一次生成并强制重载模型（同一 URL 也会重新加载） */
  const reloadLatestModel = useCallback(async () => {
    const gen = await apiClient.getLatestGeneration().catch(() => null);
    if (!gen?.model3_json) {
      setModelUrl('');
      return;
    }
    setLatest(gen);
    setModelUrl('');
    window.setTimeout(() => setModelUrl(gen.model3_json), 0);
  }, []);

  const toggleWebcam = useCallback(async () => {
    if (webcamOn) {
      // 停止真实面捕
      trackWsRef.current?.close();
      trackWsRef.current = null;
      fetch(`${apiBase()}/api/tracking/stop`, { method: 'POST' }).catch(() => undefined);
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setWebcamOn(false);
      setStreamActive(false);
      return;
    }
    try {
      // 真实面捕：摄像头由后端 Python(mediapipe) 接管，参数经 WS 推送。
      // 浏览器不再自行打开摄像头，避免与后端争抢同一设备。
      const startRes = await fetch(`${apiBase()}/api/tracking/start`, { method: 'POST' });
      if (!startRes.ok) {
        const body = await startRes.json().catch(() => ({}));
        throw new Error(body.error || `面捕启动失败 (${startRes.status})`);
      }
      setWebcamOn(true);
      setStreamActive(true);
      connectTrackingWs();
    } catch (e) {
      setWebcamOn(false);
      setError(e instanceof Error ? e.message : '面捕启动失败');
    }
  }, [webcamOn, micOn, connectTrackingWs]);

  const toggleMic = useCallback(async () => {
    if (micOn) {
      audioCtxRef.current?.close().catch(() => undefined);
      audioCtxRef.current = null;
      setMicOn(false);
      return;
    }
    try {
      const stream =
        mediaStreamRef.current ||
        (await navigator.mediaDevices.getUserMedia({ audio: true }));
      if (!mediaStreamRef.current) mediaStreamRef.current = stream;
      const ctx = new AudioContext();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      audioCtxRef.current = ctx;
      setMicOn(true);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const readMouth = () => {
        if (!audioCtxRef.current) return;
        analyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        const avg = sum / data.length / 255;
        playerRef.current?.setParameters({ ParamMouthOpenY: Math.min(1, avg * 2) });
        setParams((prev) => ({ ...prev, ParamMouthOpenY: Math.min(1, avg * 2) }));
        requestAnimationFrame(readMouth);
      };
      readMouth();
    } catch {
      setMicOn(false);
    }
  }, [micOn]);

  useEffect(() => {
    return () => {
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      audioCtxRef.current?.close().catch(() => undefined);
      trackWsRef.current?.close();
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, []);

  const applyExpression = (emotion: Emotion) => {
    playerRef.current?.setExpression(emotion);
    // also set some params for visual feedback
    const map: Record<Emotion, ParamMap> = {
      neutral: { ParamMouthForm: 0, ParamCheek: 0, ParamBrowLY: 0, ParamBrowRY: 0 },
      happy: { ParamMouthForm: 0.6, ParamCheek: 0.8, ParamBrowLY: 0.1, ParamBrowRY: 0.1 },
      sad: { ParamMouthForm: -0.5, ParamBrowLAngle: 0.4, ParamBrowRAngle: 0.4 },
      angry: { ParamMouthForm: -0.4, ParamBrowLY: -0.3, ParamBrowRY: -0.3 },
      surprised: { ParamMouthOpenY: 0.5, ParamEyeLOpen: 1, ParamEyeROpen: 1, ParamBrowLY: 0.3 },
      shy: { ParamCheek: 1, ParamEyeLOpen: 0.6, ParamEyeROpen: 0.6, ParamAngleY: 5 },
      thinking: { ParamBrowLAngle: 0.3, ParamMouthForm: -0.2, ParamEyeBallX: 0.4 },
      excited: { ParamMouthForm: 0.8, ParamEyeLOpen: 1, ParamBrowLY: 0.2 },
    };
    playerRef.current?.setParameters(map[emotion] || {});
  };

  const screenshot = () => {
    const canvas = document.querySelector<HTMLCanvasElement>('[class*="ModelCanvas"] canvas') ||
      document.querySelector('canvas');
    if (!canvas) return;
    try {
      const url = canvas.toDataURL('image/png');
      const a = document.createElement('a');
      a.href = url;
      a.download = `preview-${Date.now()}.png`;
      a.click();
    } catch {
      // cross-origin tainting
    }
  };

  const bgStyle: React.CSSProperties =
    bg === 'transparent'
      ? {
          backgroundImage:
            'linear-gradient(45deg, #1f1f2b 25%, transparent 25%), linear-gradient(-45deg, #1f1f2b 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #1f1f2b 75%), linear-gradient(-45deg, transparent 75%, #1f1f2b 75%)',
          backgroundSize: '20px 20px',
          backgroundPosition: '0 0, 0 10px, 10px -10px, 10px 0',
          backgroundColor: '#14141c',
        }
      : bg === 'dark'
      ? { background: 'radial-gradient(circle at 50% 40%, #1e1b2e 0%, #0a0a12 100%)' }
      : bg === 'light'
      ? { background: 'linear-gradient(180deg, #fce7f3 0%, #e0e7ff 100%)' }
      : { background: 'linear-gradient(180deg, #0c1a3a 0%, #1e3a5f 60%, #4a7fb5 100%)' };

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Eye className="w-6 h-6 text-pink-400" /> Live Preview
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Real-time tracking — webcam face & mic drive the character
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={screenshot}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-gray-800 border border-gray-700 text-gray-200 hover:bg-gray-700"
          >
            <Camera className="w-3.5 h-3.5" /> Screenshot
          </button>
          <button
            disabled
            title="桌宠部署尚未实现（后端返回 501）"
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-blue-500/10 border border-blue-500/20 text-blue-300/40 cursor-not-allowed"
          >
            <Monitor className="w-3.5 h-3.5" /> Launch Desktop Pet
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4">
        {/* Canvas */}
        <div
          className="relative rounded-xl border border-gray-800 overflow-hidden min-h-[520px]"
          style={bgStyle}
          onMouseMove={handleStageMove}
          onMouseLeave={() => {
            gazeRef.current = { x: 0, y: 0 };
          }}
        >
          <ModelCanvas
            ref={canvasRef}
            modelUrl={modelUrl || undefined}
            onReady={(p) => {
              playerRef.current = p;
            }}
          />

          {!modelUrl && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <p className="text-xs text-gray-300 bg-black/60 px-3 py-2 rounded-lg">
                暂无可用模型 —— 请先在「Generate」生成一次角色
              </p>
            </div>
          )}

          {/* Overlays */}
          <div className="absolute top-3 left-3 flex flex-col gap-2">
            <div className="px-3 py-1.5 rounded-lg bg-black/50 backdrop-blur text-xs text-white/90 flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${webcamOn ? 'bg-red-500 animate-pulse' : 'bg-gray-500'}`} />
              {webcamOn ? 'Tracking · MediaPipe 面捕' : 'Idle · 鼠标可跟随'}
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-black/50 backdrop-blur text-xs text-white/90 font-mono">
              {fps} FPS
            </div>
          </div>

          {error && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 px-4 py-2 rounded-lg bg-red-500/15 border border-red-500/40 text-xs text-red-200">
              {error}
            </div>
          )}

          {/* Webcam mini：摄像头由后端接管，这里只显示状态 */}
          {webcamOn && (
            <div className="absolute bottom-3 right-3 w-40 h-28 rounded-lg overflow-hidden border border-gray-700 shadow-xl bg-black/80 flex items-center justify-center">
              <p className="text-[10px] text-gray-300 text-center px-2 leading-relaxed">
                摄像头由后端<br />MediaPipe 接管
              </p>
            </div>
          )}

          {/* Param readout */}
          <div className="absolute top-3 right-3 w-48 rounded-lg bg-black/60 backdrop-blur p-3 text-[10px] font-mono space-y-1">
            <p className="text-gray-400 mb-1 uppercase tracking-wide text-[9px]">Parameters</p>
            {Object.entries(params).map(([k, v]) => (
              <div key={k} className="flex justify-between text-white/80">
                <span className="truncate">{k}</span>
                <span className="text-pink-400">{v.toFixed(2)}</span>
              </div>
            ))}
          </div>

          {/* Center hint */}
          {!webcamOn && (
            <div className="absolute bottom-6 left-1/2 -translate-x-1/2 px-4 py-2 rounded-full bg-black/50 backdrop-blur text-xs text-white/70">
              Enable webcam to begin tracking — or interact with controls on the right
            </div>
          )}
        </div>

        {/* Controls */}
        <div className="space-y-3">
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
            <p className="text-xs font-medium text-gray-400 mb-3">Tracking inputs</p>
            <div className="flex gap-2">
              <button
                onClick={toggleWebcam}
                className={`flex-1 inline-flex flex-col items-center gap-1 p-3 rounded-lg border transition-colors ${
                  webcamOn
                    ? 'bg-red-500/10 border-red-500/40 text-red-300'
                    : 'bg-gray-900 border-gray-800 text-gray-400 hover:border-gray-700'
                }`}
              >
                {webcamOn ? <VideoOff className="w-5 h-5" /> : <Video className="w-5 h-5" />}
                <span className="text-[11px]">{webcamOn ? 'Stop cam' : 'Webcam'}</span>
              </button>
              <button
                onClick={toggleMic}
                className={`flex-1 inline-flex flex-col items-center gap-1 p-3 rounded-lg border transition-colors ${
                  micOn
                    ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300'
                    : 'bg-gray-900 border-gray-800 text-gray-400 hover:border-gray-700'
                }`}
              >
                {micOn ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
                <span className="text-[11px]">{micOn ? 'Mute' : 'Mic'}</span>
              </button>
            </div>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
            <p className="text-xs font-medium text-gray-400 mb-3">Expressions</p>
            <div className="grid grid-cols-3 gap-2">
              {EXPRESSIONS.map((exp) => {
                const Icon = exp.icon;
                return (
                  <button
                    key={exp.id}
                    onClick={() => applyExpression(exp.id)}
                    className="flex flex-col items-center gap-1 p-2.5 rounded-lg bg-gray-900 border border-gray-800 hover:border-pink-500/40 hover:bg-pink-500/5 transition-colors"
                  >
                    <Icon className="w-4 h-4 text-pink-400" />
                    <span className="text-[10px] text-gray-300">{exp.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
            <p className="text-xs font-medium text-gray-400 mb-3">Background</p>
            <div className="grid grid-cols-4 gap-2">
              {(
                [
                  { id: 'transparent', swatch: 'bg-[conic-gradient(#444_25%,#222_0_50%,#444_0_75%,#222_0)]' },
                  { id: 'dark', swatch: 'bg-gradient-to-br from-[#1e1b2e] to-[#0a0a12]' },
                  { id: 'light', swatch: 'bg-gradient-to-br from-pink-100 to-indigo-100' },
                  { id: 'sky', swatch: 'bg-gradient-to-b from-blue-900 to-blue-400' },
                ] as const
              ).map((b) => (
                <button
                  key={b.id}
                  onClick={() => setBg(b.id)}
                  className={`h-10 rounded-lg border-2 ${b.swatch} ${
                    bg === b.id ? 'border-pink-500' : 'border-gray-800'
                  }`}
                  aria-label={b.id}
                />
              ))}
            </div>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
            <p className="text-xs font-medium text-gray-400 mb-2">Display</p>
            <div className="flex items-center justify-between text-xs text-gray-400">
              <span className="flex items-center gap-1.5">
                <Maximize2 className="w-3.5 h-3.5" /> Full-screen ready
              </span>
              <button className="text-pink-400 hover:text-pink-300" onClick={() => document.documentElement.requestFullscreen?.()}>
                Open
              </button>
            </div>
            <button
              onClick={reloadLatestModel}
              className="mt-3 w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-gray-900 border border-gray-800 text-gray-300 hover:border-gray-700"
            >
              <Download className="w-3.5 h-3.5" /> 重新载入最新模型
            </button>
            {latest && (
              <p className="mt-2 text-[10px] text-gray-500 break-all">
                模型: {latest.model3_json || '—'} · {latest.segmentation_method || ''}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default PreviewPage;
