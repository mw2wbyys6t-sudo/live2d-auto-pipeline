import { useEffect, useState } from 'react';
import { Wifi, WifiOff } from 'lucide-react';
import { apiClient } from '../lib/api-client';

interface ConnectionStatusProps {
  className?: string;
}

export default function ConnectionStatus({ className = '' }: ConnectionStatusProps) {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [latency, setLatency] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      const start = performance.now();
      const ok = await apiClient.healthCheck();
      if (cancelled) return;
      setConnected(ok);
      setLatency(ok ? Math.round(performance.now() - start) : null);
    };
    check();
    const id = setInterval(check, 10_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const isOk = connected === true;

  return (
    <div
      className={`flex items-center gap-2 rounded-[10px] border border-black/[0.08] bg-black/[0.03] px-2.5 py-1.5 text-[10px] ${className}`}
      title={isOk ? `API connected (${latency}ms)` : 'API disconnected'}
    >
      {isOk ? (
        <Wifi className="h-3.5 w-3.5 text-mint-600" />
      ) : connected === false ? (
        <WifiOff className="h-3.5 w-3.5 text-rose-600" />
      ) : (
        <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-haze-600 border-t-sakura-400" />
      )}
      <span
        className={`tabular-nums ${isOk ? 'text-mint-600' : connected === false ? 'text-rose-600' : 'text-haze-400'}`}
      >
        {isOk ? `Online${latency ? ` · ${latency}ms` : ''}` : connected === false ? 'Offline' : 'Checking…'}
      </span>
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          isOk ? 'bg-mint-400' : connected === false ? 'bg-rose-400' : 'bg-haze-500'
        }`}
      />
    </div>
  );
}
