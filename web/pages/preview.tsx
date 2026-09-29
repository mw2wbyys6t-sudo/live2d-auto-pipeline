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
import {
  apiClient,
  modelDirFromRecord,
  modelUrlFromRecord,
  type ExportedModelInfo,
  type LatestGeneration,
} from '../lib/api-client';
import { loadFaceLandmarker, resultToLive2DParams } from '../lib/face-tracker';
import type { FaceLandmarker } from '@mediapipe/tasks-vision';
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
  const [petBusy, setPetBusy] = useState(false);
  const [petMsg, setPetMsg] = useState<string | null>(null);
  const [models, setModels] = useState<ExportedModelInfo[]>([]);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const animRef = useRef<number | null>(null);
  const trackRafRef = useRef<number | null>(null);
  const landmarkerRef = useRef<FaceLandmarker | null>(null);
  const lastVideoTimeRef = useRef(-1);
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

  /** 拉取最近一次生成产物并加载其 model3.json（同一 URL 也会强制重载） */
  const reloadLatestModel = useCallback(async () => {
    try {
      const record = await apiClient.getLatestGeneration();
      setLatest(record);
      if (!record) {
        setModelUrl('');
        setError('尚未有任何生成记录 —— 请先在「Generate」生成一次角色。');
        return;
      }
      const url = modelUrlFromRecord(record);
      if (!url) {
        setModelUrl('');
        setError('最近一次生成没有可加载的 Live2D 模型，请先完成一次导出。');
        return;
      }
      setError(null);
      setModelUrl('');
      // 用 setTimeout 强制重挂载（同一 URL 也重新加载）。
      // 不能用 requestAnimationFrame：后台标签页的 rAF 会被浏览器冻结，
      // 导致模型永不挂载（切换走再切回来才加载的幽灵状态）。
      window.setTimeout(() => setModelUrl(url), 50);
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取最近生成记录失败');
    }
  }, []);

  // 进入页面即自动载入最近模型，并拉取全部可切换的已导出模型
  useEffect(() => {
    reloadLatestModel();
    apiClient
      .listExportedModels()
      .then(setModels)
      .catch(() => setModels([]));
  }, [reloadLatestModel]);

  /** 手动切换到列表中的某个模型 */
  const loadModel = useCallback((model3Url: string) => {
    if (!model3Url) return;
    setError(null);
    setModelUrl('');
    requestAnimationFrame(() => setModelUrl(model3Url));
  }, []);

  /** 启动桌面桌宠（后端先做官方 Cubism Core 像素验收，再拉起原生窗口） */
  const launchPet = useCallback(async () => {
    if (!latest || petBusy) return;
    const modelDir = modelDirFromRecord(latest);
    if (!modelDir) {
      setPetMsg('没有可部署的模型目录 —— 请先生成并导出一次角色。');
      return;
    }
    setPetBusy(true);
    setPetMsg('正在验收模型并启动桌宠…');
    try {
      await apiClient.deployDesktop(modelDir);
      setPetMsg('桌宠已启动并完成首帧验收；透明外观请在桌面确认。');
    } catch (e) {
      setPetMsg(e instanceof Error ? e.message : '桌宠启动失败');
    } finally {
      setPetBusy(false);
    }
  }, [latest, petBusy]);

  /** 浏览器端面捕主循环：getUserMedia + MediaPipe FaceLandmarker(WASM) */
  const startBrowserTracking = useCallback(async () => {
    const video = videoRef.current;
    if (!video) throw new Error('视频元素未就绪');
    const landmarker = await loadFaceLandmarker();
    landmarkerRef.current = landmarker;

    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480, facingMode: 'user' },
    });
    mediaStreamRef.current = stream;
    video.srcObject = stream;
    await video.play().catch(() => undefined);

    const tick = () => {
      const v = videoRef.current;
      const lm = landmarkerRef.current;
      if (v && lm && v.readyState >= 2 && v.currentTime !== lastVideoTimeRef.current) {
        lastVideoTimeRef.current = v.currentTime;
        try {
          const result = lm.detectForVideo(v, performance.now());
          const params = resultToLive2DParams(result);
          if (Object.keys(params).length > 0) {
            playerRef.current?.setParameters(params);
            const now = performance.now();
            if (now - lastReadoutRef.current > 100) {
              lastReadoutRef.current = now;
              setParams((prev) => ({ ...prev, ...params }));
            }
          }
        } catch { /* 单帧失败不中断跟踪 */ }
      }
      trackRafRef.current = requestAnimationFrame(tick);
    };
    trackRafRef.current = requestAnimationFrame(tick);
  }, []);

  const stopBrowserTracking = useCallback(() => {
    if (trackRafRef.current !== null) {
      cancelAnimationFrame(trackRafRef.current);
      trackRafRef.current = null;
    }
    // FaceLandmarker.close() 是同步的，且实例可能已释放
    try {
      landmarkerRef.current?.close();
    } catch { /* 已释放 */ }
    landmarkerRef.current = null;
    lastVideoTimeRef.current = -1;
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

  const toggleWebcam = useCallback(async () => {
    if (webcamOn) {
      stopBrowserTracking();
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setWebcamOn(false);
      setStreamActive(false);
      return;
    }
    try {
      // 面捕完全在浏览器内运行：摄像头 getUserMedia + MediaPipe WASM 推理，
      // 参数直接驱动播放器（不经后端，Go/Python 部署行为一致）。
      await startBrowserTracking();
      setWebcamOn(true);
      setStreamActive(true);
      setError(null);
    } catch (e) {
      stopBrowserTracking();
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
      setWebcamOn(false);
      setError(e instanceof Error ? e.message : '面捕启动失败');
    }
  }, [webcamOn, startBrowserTracking, stopBrowserTracking]);

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
      if (animRef.current) cancelAnimationFrame(animRef.current);
      stopBrowserTracking();
    };
  }, [stopBrowserTracking]);

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
            Local preview — mouse and microphone controls are available
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
            onClick={launchPet}
            disabled={petBusy || !latest}
            title={latest ? '验收模型并在桌面启动透明桌宠（需 Windows + live2d-py）' : '先在「Generate」生成一次角色'}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-blue-500/15 border border-blue-500/40 text-blue-300 hover:bg-blue-500/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Monitor className="w-3.5 h-3.5" /> {petBusy ? 'Launching…' : 'Launch Desktop Pet'}
          </button>
        </div>
      </div>
      {petMsg && (
        <p className="mb-3 text-xs text-blue-200/80 bg-blue-500/10 border border-blue-500/30 rounded-lg px-3 py-2">
          {petMsg}
        </p>
      )}

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

          {/* Webcam mini：浏览器端 MediaPipe 接管，显示摄像头小窗（单一 video 元素） */}
          <div
            className={`absolute bottom-3 right-3 w-40 h-28 rounded-lg overflow-hidden border border-gray-700 shadow-xl bg-black/80 ${
              webcamOn ? '' : 'hidden'
            }`}
          >
            <video
              ref={videoRef}
              muted
              playsInline
              className="w-full h-full object-cover scale-x-[-1]"
            />
          </div>

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
                <span className="text-[11px]">{webcamOn ? 'Stop tracking' : 'Webcam tracking'}</span>
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
            <p className="text-xs font-medium text-gray-400 mb-2">Model source</p>
            {models.length > 0 ? (
              <select
                value={modelUrl}
                onChange={(e) => loadModel(e.target.value)}
                className="w-full px-2 py-2 bg-gray-900 border border-gray-700 rounded-md text-xs text-white focus:outline-none focus:border-pink-500"
              >
                {models.map((m) => (
                  <option key={m.model3_json} value={m.model3_url}>
                    {m.name}
                    {m.mod_time ? ` · ${m.mod_time.slice(0, 16).replace('T', ' ')}` : ''}
                  </option>
                ))}
              </select>
            ) : (
              <p className="text-[11px] text-gray-600">尚无已导出模型 —— 完成一次生成/导出后可在此切换。</p>
            )}
            <button
              onClick={reloadLatestModel}
              className="mt-3 w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-gray-900 border border-gray-800 text-gray-300 hover:border-pink-500/40 hover:text-white transition-colors"
            >
              <Download className="w-3.5 h-3.5" /> 重新载入最近模型
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
