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
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const animRef = useRef<number | null>(null);

  // FPS loop
  useEffect(() => {
    const id = setInterval(() => {
      const handle = canvasRef.current as (ModelCanvasHandle & { [k: string]: unknown }) | null;
      if (handle && typeof handle.getFps === 'function') {
        setFps(handle.getFps());
      } else {
        setFps(0);
      }
    }, 500);
    return () => clearInterval(id);
  }, []);

  // Auto-idle animation (breathing/blinking) when nothing tracking
  useEffect(() => {
    if (streamActive) return;
    let t = 0;
    const tick = () => {
      t += 0.016;
      const breath = Math.sin(t * 1.5) * 0.3 + 0.5;
      const blink = Math.sin(t * 0.7) > 0.97 ? 0.2 : 1;
      const angleZ = Math.sin(t * 0.5) * 3;
      const p: ParamMap = {
        ParamBreath: breath,
        ParamAngleZ: angleZ,
        ParamEyeLOpen: blink,
        ParamEyeROpen: blink,
        ParamBodyAngleX: Math.sin(t * 0.4) * 2,
      };
      const handle = canvasRef.current as (ModelCanvasHandle & { [k: string]: unknown }) | null;
      if (handle && typeof handle.setParameters === 'function') {
        handle.setParameters(p);
      }
      setParams((prev) => ({ ...prev, ...p }));
      animRef.current = requestAnimationFrame(tick);
    };
    animRef.current = requestAnimationFrame(tick);
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [streamActive]);

  const toggleWebcam = useCallback(async () => {
    if (webcamOn) {
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setWebcamOn(false);
      setStreamActive(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: micOn });
      mediaStreamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => undefined);
      }
      setWebcamOn(true);
      setStreamActive(true);
      runFakeTracking();
    } catch {
      setWebcamOn(false);
    }
  }, [webcamOn, micOn]);

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
        const handle = canvasRef.current as (ModelCanvasHandle & { [k: string]: unknown }) | null;
        if (handle && typeof handle.setParameters === 'function') {
          handle.setParameters({ ParamMouthOpenY: Math.min(1, avg * 2) });
        }
        setParams((prev) => ({ ...prev, ParamMouthOpenY: Math.min(1, avg * 2) }));
        requestAnimationFrame(readMouth);
      };
      readMouth();
    } catch {
      setMicOn(false);
    }
  }, [micOn]);

  // Simulated face tracking — real MediaPipe integration would go here
  const runFakeTracking = () => {
    if (!mediaStreamRef.current) return;
    let t = 0;
    const tick = () => {
      if (!mediaStreamRef.current) return;
      t += 0.03;
      const p: ParamMap = {
        ParamAngleX: Math.sin(t * 0.8) * 15,
        ParamAngleY: Math.cos(t * 0.6) * 10,
        ParamAngleZ: Math.sin(t * 0.5) * 5,
        ParamEyeBallX: Math.sin(t * 1.2) * 0.5,
        ParamEyeBallY: Math.cos(t * 1.0) * 0.3,
        ParamBodyAngleX: Math.sin(t * 0.3) * 3,
        ParamBreath: Math.sin(t * 1.5) * 0.3 + 0.5,
      };
      const handle = canvasRef.current as (ModelCanvasHandle & { [k: string]: unknown }) | null;
      if (handle && typeof handle.setParameters === 'function') {
        handle.setParameters(p);
      }
      setParams((prev) => ({ ...prev, ...p }));
      animRef.current = requestAnimationFrame(tick);
    };
    animRef.current = requestAnimationFrame(tick);
  };

  useEffect(() => {
    return () => {
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      audioCtxRef.current?.close().catch(() => undefined);
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, []);

  const applyExpression = (emotion: Emotion) => {
    const handle = canvasRef.current as (ModelCanvasHandle & { [k: string]: unknown }) | null;
    if (handle && typeof handle.setExpression === 'function') {
      handle.setExpression(emotion);
    }
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
    if (handle && typeof handle.setParameters === 'function') {
      handle.setParameters(map[emotion] || {});
    }
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
            onClick={() => undefined}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-blue-500/20 border border-blue-500/40 text-blue-300 hover:bg-blue-500/30"
          >
            <Monitor className="w-3.5 h-3.5" /> Launch Desktop Pet
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4">
        {/* Canvas */}
        <div className="relative rounded-xl border border-gray-800 overflow-hidden min-h-[520px]" style={bgStyle}>
          <ModelCanvas ref={canvasRef} />

          {/* Overlays */}
          <div className="absolute top-3 left-3 flex flex-col gap-2">
            <div className="px-3 py-1.5 rounded-lg bg-black/50 backdrop-blur text-xs text-white/90 flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${webcamOn ? 'bg-red-500 animate-pulse' : 'bg-gray-500'}`} />
              {webcamOn ? 'Tracking' : 'Idle'}
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-black/50 backdrop-blur text-xs text-white/90 font-mono">
              {fps} FPS
            </div>
          </div>

          {/* Webcam mini */}
          {webcamOn && (
            <div className="absolute bottom-3 right-3 w-40 h-28 rounded-lg overflow-hidden border border-gray-700 shadow-xl bg-black">
              <video ref={videoRef} className="w-full h-full object-cover" muted playsInline />
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
              onClick={() => undefined}
              className="mt-3 w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-gray-900 border border-gray-800 text-gray-300 hover:border-gray-700"
            >
              <Download className="w-3.5 h-3.5" /> Load model3.json
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PreviewPage;
