import type { WSMessage } from '../types';

export type WSEvent =
  | 'progress'
  | 'tracking'
  | 'chat'
  | 'error'
  | 'open'
  | 'close'
  | 'message'
  | 'reconnect';

type Listener = (...args: unknown[]) => void;

// Use same-origin WebSocket so it works behind reverse proxies and preview URLs.
// The Next.js dev server proxies /ws to the Go backend's /ws endpoint.
const DEFAULT_URL =
  typeof window !== 'undefined'
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${
        window.location.host
      }/ws`
    : 'ws://localhost:8080/ws';

export interface WSManagerOptions {
  url?: string;
  autoReconnect?: boolean;
  initialBackoffMs?: number;
  maxBackoffMs?: number;
  maxRetries?: number;
  heartbeatIntervalMs?: number;
}

export class WSManager {
  private ws: WebSocket | null = null;
  private url: string;
  private readonly autoReconnect: boolean;
  private readonly initialBackoffMs: number;
  private readonly maxBackoffMs: number;
  private readonly maxRetries: number;
  private readonly heartbeatIntervalMs: number;
  private retries = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private manuallyClosed = false;
  private listeners = new Map<string, Set<Listener>>();
  private messageQueue: WSMessage[] = [];

  constructor(options: WSManagerOptions = {}) {
    this.url = options.url || DEFAULT_URL;
    this.autoReconnect = options.autoReconnect ?? true;
    this.initialBackoffMs = options.initialBackoffMs ?? 500;
    this.maxBackoffMs = options.maxBackoffMs ?? 30_000;
    this.maxRetries = options.maxRetries ?? 20;
    this.heartbeatIntervalMs = options.heartbeatIntervalMs ?? 25_000;
  }

  connect(url?: string): void {
    if (url) this.url = url;
    this.manuallyClosed = false;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.doConnect();
  }

  private doConnect(): void {
    try {
      this.ws = new WebSocket(this.url);
    } catch (err) {
      this.emit('error', err);
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.retries = 0;
      this.emit('open');
      this.flushQueue();
      this.startHeartbeat();
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data as string) as WSMessage;
        this.handleMessage(data);
      } catch (err) {
        this.emit('error', err);
      }
    };

    this.ws.onerror = (event) => {
      this.emit('error', event);
    };

    this.ws.onclose = (event) => {
      this.stopHeartbeat();
      this.emit('close', event);
      if (!this.manuallyClosed && this.autoReconnect) {
        this.scheduleReconnect();
      }
    };
  }

  private handleMessage(msg: WSMessage): void {
    this.emit('message', msg);
    switch (msg.type) {
      case 'progress':
      case 'generation_step':
      case 'generation_complete':
      case 'generation_start':
        this.emit('progress', msg);
        break;
      case 'tracking':
        this.emit('tracking', msg);
        break;
      case 'chat':
        this.emit('chat', msg);
        break;
      case 'error':
        this.emit('error', msg);
        break;
      case 'pong':
        // heartbeat response, ignore
        break;
      default:
        break;
    }
  }

  private scheduleReconnect(): void {
    if (this.manuallyClosed) return;
    if (this.retries >= this.maxRetries) {
      this.emit('error', new Error('Max reconnect retries reached'));
      return;
    }
    const backoff = Math.min(
      this.initialBackoffMs * Math.pow(2, this.retries),
      this.maxBackoffMs,
    );
    this.retries++;
    this.emit('reconnect', { attempt: this.retries, delayMs: backoff });
    this.reconnectTimer = setTimeout(() => this.doConnect(), backoff);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        try {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        } catch {
          // ignore
        }
      }
    }, this.heartbeatIntervalMs);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private flushQueue(): void {
    while (this.messageQueue.length > 0) {
      const msg = this.messageQueue.shift();
      if (msg) this.send(msg);
    }
  }

  disconnect(): void {
    this.manuallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopHeartbeat();
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
  }

  send(msg: WSMessage): void {
    const payload = JSON.stringify({ ...msg, timestamp: msg.timestamp || new Date().toISOString() });
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(payload);
    } else {
      this.messageQueue.push(msg);
      if (this.manuallyClosed) {
        this.connect();
      }
    }
  }

  on(event: WSEvent, callback: Listener): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(callback);
    return () => this.off(event, callback);
  }

  off(event: WSEvent, callback: Listener): void {
    this.listeners.get(event)?.delete(callback);
  }

  private emit(event: WSEvent, ...args: unknown[]): void {
    this.listeners.get(event)?.forEach((cb) => {
      try {
        cb(...args);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error(`WS listener error for ${event}:`, err);
      }
    });
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CLOSED;
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

export const wsManager = new WSManager();
