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
      className={`flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800/50 border border-gray-700/50 text-xs ${className}`}
      title={isOk ? `API connected (${latency}ms)` : 'API disconnected'}
    >
      {isOk ? (
        <Wifi className="w-3.5 h-3.5 text-emerald-400" />
      ) : connected === false ? (
        <WifiOff className="w-3.5 h-3.5 text-red-400" />
      ) : (
        <div className="w-3.5 h-3.5 rounded-full border-2 border-gray-500 border-t-pink-400 animate-spin" />
      )}
      <span className={isOk ? 'text-emerald-400' : connected === false ? 'text-red-400' : 'text-gray-400'}>
        {isOk ? `Online${latency ? ` · ${latency}ms` : ''}` : connected === false ? 'Offline' : 'Checking…'}
      </span>
      <span
        className={`w-2 h-2 rounded-full ${
          isOk ? 'bg-emerald-400 animate-pulse' : connected === false ? 'bg-red-400' : 'bg-gray-500'
        }`}
      />
    </div>
  );
}
