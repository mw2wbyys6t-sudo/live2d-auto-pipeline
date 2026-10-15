/**
 * 原生面捕客户端：驱动 Go 后端的 Rust face_tracker 子进程（ONNX YuNet）。
 *
 * 工作方式：
 * - 摄像头由 Rust 进程独占采集（Windows 下设备不可共享），浏览器端拿不到
 *   画面，因此没有本地预览，改由后端 WS 回传参数驱动模型；
 * - 表情/头部参数经 /api/ws 的 tracking_frame 消息送达；
 * - 需要先编译原生二进制：cd native/face_tracker && cargo build --release。
 */
import { apiClient, getBackendWsUrl } from './api-client';

/** 一帧原生面捕数据（tracking_frame 的 data 字段）。 */
export interface NativeTrackingFrame {
  fps?: number;
  timestamp?: number;
  /** Live2D Cubism 参数（ParamAngleX 等），可直接喂给播放器。 */
  live2d_params?: Record<string, number>;
  blendshapes?: Record<string, number>;
  head_rotation?: { pitch: number; yaw: number; roll: number };
}

export interface NativeTrackerHandlers {
  /** 每帧面捕数据（约 30fps） */
  onFrame: (frame: NativeTrackingFrame) => void;
  /** 后端上报的面捕错误 */
  onError?: (message: string) => void;
}

export interface NativeTrackerSession {
  stop: () => Promise<void>;
}

const TRACKING_WS_PATH = '/api/ws';
const WS_OPEN_TIMEOUT_MS = 5000;

/**
 * 启动原生面捕会话：先建立 WS 收帧通道，再请求后端拉起子进程。
 *
 * WS 建连失败或后端启动失败时会清理已开的连接并抛错，调用方无需回滚。
 */
export async function startNativeTracking(
  handlers: NativeTrackerHandlers,
): Promise<NativeTrackerSession> {
  const ws = new WebSocket(getBackendWsUrl(TRACKING_WS_PATH));
  let closed = false;

  const closeWs = () => {
    if (closed) return;
    closed = true;
    try {
      ws.close();
    } catch {
      /* 已关闭/正在关闭：忽略 */
    }
  };

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data as string) as {
        type?: string;
        data?: Record<string, unknown>;
        message?: string;
      };
      if (msg.type === 'tracking_frame' && msg.data) {
        handlers.onFrame(msg.data as NativeTrackingFrame);
      } else if (msg.type === 'tracking_error') {
        const m =
          (msg.data?.message as string | undefined) || msg.message || '原生面捕运行错误';
        handlers.onError?.(m);
      }
    } catch {
      /* 忽略坏帧 */
    }
  };
  ws.onerror = () => {
    /* 由 HTTP 请求本身兜底（startTracking 失败会抛错） */
  };

  // 等 WS 握手完成再启动，避免丢掉最初几帧
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('面捕 WebSocket 连接超时')), WS_OPEN_TIMEOUT_MS);
    ws.onopen = () => {
      clearTimeout(timer);
      resolve();
    };
    ws.addEventListener(
      'close',
      () => {
        clearTimeout(timer);
        reject(new Error('面捕 WebSocket 已被关闭'));
      },
      { once: true },
    );
  }).catch((e) => {
    closeWs();
    throw e;
  });

  try {
    await apiClient.startTracking();
  } catch (e) {
    closeWs();
    throw e;
  }

  return {
    stop: async () => {
      closeWs();
      // 后端 stop 失败（如进程已退出）不应阻断前端清理
      await apiClient.stopTracking().catch(() => undefined);
    },
  };
}
