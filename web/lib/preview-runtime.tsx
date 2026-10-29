import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
  type ReactNode,
} from 'react';
import type { Emotion, ParamMap } from '../types';
import type { Live2DPlayer } from './live2d-player';
import type { ExportedModelInfo } from './api-client';
import { apiClient } from './api-client';
import {
  startNativeTracking as openNativeTracking,
  type NativeTrackerSession,
} from './native-tracker';
import { useWorkbench } from './workbench-context';

export type PreviewBackground = 'transparent' | 'dark' | 'light' | 'sky';

const EXPRESSION_PARAMS: Record<Emotion, ParamMap> = {
  neutral: { ParamMouthForm: 0, ParamCheek: 0, ParamBrowLY: 0, ParamBrowRY: 0 },
  happy: { ParamMouthForm: 0.6, ParamCheek: 0.8, ParamBrowLY: 0.1, ParamBrowRY: 0.1 },
  sad: { ParamMouthForm: -0.5, ParamBrowLAngle: 0.4, ParamBrowRAngle: 0.4 },
  angry: { ParamMouthForm: -0.4, ParamBrowLY: -0.3, ParamBrowRY: -0.3 },
  surprised: { ParamMouthOpenY: 0.5, ParamEyeLOpen: 1, ParamEyeROpen: 1, ParamBrowLY: 0.3 },
  shy: { ParamCheek: 1, ParamEyeLOpen: 0.6, ParamEyeROpen: 0.6, ParamAngleY: 5 },
  thinking: { ParamBrowLAngle: 0.3, ParamMouthForm: -0.2, ParamEyeBallX: 0.4 },
  excited: { ParamMouthForm: 0.8, ParamEyeLOpen: 1, ParamBrowLY: 0.2 },
};

const INITIAL_PARAMS: ParamMap = {
  ParamAngleX: 0,
  ParamAngleY: 0,
  ParamAngleZ: 0,
  ParamEyeLOpen: 1,
  ParamEyeROpen: 1,
  ParamMouthOpenY: 0,
  ParamMouthForm: 0,
};

export interface PreviewRuntimeValue {
  modelUrl: string;
  manualModelUrl: string;
  setManualModelUrl: (url: string) => void;
  reloadNonce: number;
  reloadModel: () => void;
  models: ExportedModelInfo[];
  refreshModels: () => Promise<void>;

  bg: PreviewBackground;
  setBg: (bg: PreviewBackground) => void;
  idle: boolean;
  setIdle: (idle: boolean) => void;

  params: ParamMap;
  publishParams: (params: ParamMap) => void;
  fps: number;
  setFps: (fps: number) => void;

  expression: string;
  applyExpression: (emotion: Emotion) => void;

  trackingOn: boolean;
  micOn: boolean;
  toggleTracking: () => Promise<void>;
  toggleMic: () => Promise<void>;
  stopInputs: () => void;

  error: string | null;
  setError: (message: string | null) => void;

  playerRef: MutableRefObject<Live2DPlayer | null>;
  active: boolean;
  setActive: (active: boolean) => void;
}

const PreviewRuntimeContext = createContext<PreviewRuntimeValue | null>(null);

export function PreviewProvider({ children }: { children: ReactNode }) {
  const { current } = useWorkbench();
  const currentModelUrl = current?.model3Url || '';

  const playerRef = useRef<Live2DPlayer | null>(null);
  const [manualModelUrl, setManualModelUrl] = useState('');
  const [reloadNonce, setReloadNonce] = useState(0);
  const [models, setModels] = useState<ExportedModelInfo[]>([]);
  const [bg, setBg] = useState<PreviewBackground>('dark');
  const [idle, setIdle] = useState(true);
  const [params, setParams] = useState<ParamMap>(INITIAL_PARAMS);
  const [fps, setFps] = useState(0);
  const [expression, setExpression] = useState('neutral');
  const [trackingOn, setTrackingOn] = useState(false);
  const [micOn, setMicOn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(false);

  // 面捕会话：启动是异步的，期间用户点停止必须靠令牌识别过期会话。
  const trackingSessionRef = useRef(0);
  const nativeSessionRef = useRef<NativeTrackerSession | null>(null);
  const lastReadoutRef = useRef(0);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);

  // 切换作品后重新回到作品的模型，手动选择不跨作品保留。
  useEffect(() => {
    setManualModelUrl('');
  }, [currentModelUrl]);

  const modelUrl = manualModelUrl || currentModelUrl;

  const refreshModels = useCallback(async () => {
    const list = await apiClient.listExportedModels();
    setModels(list);
  }, []);

  useEffect(() => {
    void refreshModels();
  }, [refreshModels]);

  const reloadModel = useCallback(() => {
    setReloadNonce((nonce) => nonce + 1);
  }, []);

  const publishParams = useCallback((next: ParamMap) => {
    setParams((prev) => ({ ...prev, ...next }));
  }, []);

  const applyExpression = useCallback((emotion: Emotion) => {
    setExpression(emotion);
    playerRef.current?.setExpression(emotion);
    const mapped = EXPRESSION_PARAMS[emotion] || {};
    playerRef.current?.setParameters(mapped);
    setParams((prev) => ({ ...prev, ...mapped }));
  }, []);

  const stopNativeTracking = useCallback(() => {
    trackingSessionRef.current = 0;
    lastReadoutRef.current = 0;
    const tracker = nativeSessionRef.current;
    nativeSessionRef.current = null;
    if (tracker) void tracker.stop();
  }, []);

  const startNativeTracking = useCallback(async () => {
    const session = ++trackingSessionRef.current;
    const tracker = await openNativeTracking({
      onFrame: (frame) => {
        const p = frame.live2d_params;
        if (!p || Object.keys(p).length === 0) return;
        playerRef.current?.setParameters(p);
        const now = performance.now();
        if (now - lastReadoutRef.current > 100) {
          lastReadoutRef.current = now;
          setParams((prev) => ({ ...prev, ...p }));
        }
      },
      onError: (msg) => setError(msg),
    });
    if (trackingSessionRef.current !== session) {
      await tracker.stop();
      throw new Error('面捕已取消');
    }
    nativeSessionRef.current = tracker;
  }, []);

  const stopMic = useCallback(() => {
    audioCtxRef.current?.close().catch(() => undefined);
    audioCtxRef.current = null;
    // 只关 AudioContext 不释放设备：必须显式停轨，否则系统麦克风指示灯常亮。
    audioStreamRef.current?.getTracks().forEach((track) => track.stop());
    audioStreamRef.current = null;
    setMicOn(false);
  }, []);

  const stopInputs = useCallback(() => {
    stopNativeTracking();
    stopMic();
    setTrackingOn(false);
  }, [stopNativeTracking, stopMic]);

  const toggleTracking = useCallback(async () => {
    if (trackingOn) {
      stopNativeTracking();
      setTrackingOn(false);
      return;
    }
    try {
      await startNativeTracking();
      setTrackingOn(true);
      setError(null);
    } catch (err) {
      stopNativeTracking();
      setTrackingOn(false);
      // 启动途中用户主动停止（会话令牌已清零）：不是错误。
      if (trackingSessionRef.current === 0) return;
      setError(err instanceof Error ? err.message : '面捕启动失败');
    }
  }, [trackingOn, startNativeTracking, stopNativeTracking]);

  const toggleMic = useCallback(async () => {
    if (micOn) {
      stopMic();
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioStreamRef.current = stream;
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
        const level = Math.min(1, (sum / data.length / 255) * 2);
        playerRef.current?.setParameters({ ParamMouthOpenY: level });
        setParams((prev) => ({ ...prev, ParamMouthOpenY: level }));
        requestAnimationFrame(readMouth);
      };
      readMouth();
    } catch (err) {
      stopMic();
      setError(err instanceof Error ? err.message : '麦克风启动失败');
    }
  }, [micOn, stopMic]);

  // 阶段切走时释放摄像头/麦克风：后台占用设备对用户是惊吓，不是便利。
  useEffect(() => {
    if (!active) stopInputs();
  }, [active, stopInputs]);

  useEffect(() => {
    return () => {
      stopInputs();
    };
  }, [stopInputs]);

  const value = useMemo<PreviewRuntimeValue>(
    () => ({
      modelUrl,
      manualModelUrl,
      setManualModelUrl,
      reloadNonce,
      reloadModel,
      models,
      refreshModels,
      bg,
      setBg,
      idle,
      setIdle,
      params,
      publishParams,
      fps,
      setFps,
      expression,
      applyExpression,
      trackingOn,
      micOn,
      toggleTracking,
      toggleMic,
      stopInputs,
      error,
      setError,
      playerRef,
      active,
      setActive,
    }),
    [
      modelUrl,
      manualModelUrl,
      reloadNonce,
      reloadModel,
      models,
      refreshModels,
      bg,
      idle,
      params,
      publishParams,
      fps,
      expression,
      applyExpression,
      trackingOn,
      micOn,
      toggleTracking,
      toggleMic,
      stopInputs,
      error,
      active,
    ],
  );

  return (
    <PreviewRuntimeContext.Provider value={value}>{children}</PreviewRuntimeContext.Provider>
  );
}

export function usePreviewRuntime(): PreviewRuntimeValue {
  const value = useContext(PreviewRuntimeContext);
  if (!value) throw new Error('usePreviewRuntime 必须在 PreviewProvider 内使用');
  return value;
}
