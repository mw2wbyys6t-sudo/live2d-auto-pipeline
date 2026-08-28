import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Sparkles,
  Users,
  Wand2,
  Eye,
  MessageSquare,
  Cpu,
  Server,
  Package,
  ArrowRight,
  Activity,
  Zap,
} from 'lucide-react';
import type { NextPage } from 'next';
import { apiClient } from '../lib/api-client';
import type { Character, SystemStatus } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import CharacterCard from '../components/CharacterCard';

interface QuickAction {
  href: string;
  title: string;
  desc: string;
  icon: typeof Sparkles;
  gradient: string;
}

const QUICK_ACTIONS: QuickAction[] = [
  {
    href: '/characters',
    title: 'Create Character',
    desc: 'Define a new persona with appearance, personality, and palette',
    icon: Users,
    gradient: 'from-pink-500 to-rose-500',
  },
  {
    href: '/generate',
    title: 'Generate Image',
    desc: 'Produce layered art with AI providers and pipeline QA',
    icon: Wand2,
    gradient: 'from-purple-500 to-fuchsia-500',
  },
  {
    href: '/preview',
    title: 'Open Preview',
    desc: 'Real-time tracking preview with webcam and mic',
    icon: Eye,
    gradient: 'from-cyan-500 to-blue-500',
  },
  {
    href: '/chat',
    title: 'Chat',
    desc: 'Converse with your character with emotion-aware responses',
    icon: MessageSquare,
    gradient: 'from-emerald-500 to-teal-500',
  },
];

const Dashboard: NextPage = () => {
  const [characters, setCharacters] = useState<Character[] | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [chars, st] = await Promise.all([
          apiClient.getCharacters().catch(() => [] as Character[]),
          apiClient.getStatus().catch(() => null),
        ]);
        if (cancelled) return;
        setCharacters(chars);
        setStatus(st);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load dashboard');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Hero */}
      <section className="relative overflow-hidden rounded-2xl border border-gray-800 bg-gradient-to-br from-[#1a1024] via-[#0f0f13] to-[#0f1422] p-6 sm:p-10">
        <div className="absolute inset-0 noise pointer-events-none" />
        <div className="absolute -top-20 -right-20 w-72 h-72 bg-pink-500/20 rounded-full blur-3xl" />
        <div className="absolute -bottom-20 -left-20 w-72 h-72 bg-purple-500/20 rounded-full blur-3xl" />
        <div className="relative">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-pink-500/10 border border-pink-500/30 text-xs text-pink-300 mb-4">
            <Sparkles className="w-3 h-3" /> Live2D Master Agent · v2.0
          </div>
          <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold mb-3">
            <span className="text-white">Build anime characters</span>
            <br />
            <span className="gradient-text">from prompt to Live2D</span>
          </h1>
          <p className="text-sm sm:text-base text-gray-400 max-w-2xl">
            AI-powered generation, layer segmentation, physics rigging, and real-time
            tracking — all in one workbench.
          </p>
          <div className="flex flex-wrap gap-3 mt-6">
            <Link
              href="/generate"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-pink-500 to-purple-600 text-white font-medium hover:shadow-lg hover:shadow-pink-500/30 transition-all hover-scale"
            >
              <Wand2 className="w-4 h-4" /> Start generating
            </Link>
            <Link
              href="/live2d"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gray-800/80 border border-gray-700 text-gray-200 font-medium hover:bg-gray-700 transition-colors"
            >
              <Package className="w-4 h-4" /> Open builder
            </Link>
          </div>
        </div>
      </section>

      {/* Status */}
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatusCard
          label="API"
          value={status?.apiConnected ? 'Connected' : 'Offline'}
          subtext={status?.latencyMs ? `${status.latencyMs}ms` : undefined}
          ok={!!status?.apiConnected}
          icon={Activity}
        />
        <StatusCard
          label="GPU"
          value={status?.gpuAvailable ? status.gpuName || 'Available' : 'Not detected'}
          subtext={
            status?.vramTotal
              ? `${status?.vramUsed ?? 0}/${status.vramTotal} GB VRAM`
              : undefined
          }
          ok={!!status?.gpuAvailable}
          icon={Server}
        />
        <StatusCard
          label="Models"
          value={status ? `${status.modelsLoaded?.length ?? 0} loaded` : '—'}
          subtext={status?.modelsLoaded?.slice(0, 2)?.join(', ') || undefined}
          ok={!!status?.modelsLoaded?.length}
          icon={Cpu}
        />
        <StatusCard
          label="Providers"
          value={status ? `${status.providers?.filter((p) => p.available)?.length ?? 0}/${status.providers?.length ?? 0} online` : '—'}
          ok={!!(status?.providers?.length && status.providers.some((p) => p.available))}
          icon={Zap}
        />
      </section>

      {/* Quick actions */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-3">Quick actions</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {QUICK_ACTIONS.map((a) => {
            const Icon = a.icon;
            return (
              <Link
                key={a.href}
                href={a.href}
                className="group relative bg-[#1a1a23] border border-gray-800 rounded-xl p-5 hover:border-gray-700 transition-all hover-lift overflow-hidden"
              >
                <div
                  className={`w-10 h-10 rounded-lg bg-gradient-to-br ${a.gradient} flex items-center justify-center mb-3 shadow-lg group-hover:scale-110 transition-transform`}
                >
                  <Icon className="w-5 h-5 text-white" />
                </div>
                <h3 className="text-sm font-semibold text-white mb-1 flex items-center gap-1">
                  {a.title}
                  <ArrowRight className="w-3.5 h-3.5 opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-all" />
                </h3>
                <p className="text-xs text-gray-500 leading-relaxed">{a.desc}</p>
              </Link>
            );
          })}
        </div>
      </section>

      {/* Recent characters */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-white">Recent characters</h2>
          <Link href="/characters" className="text-xs text-pink-400 hover:text-pink-300 flex items-center gap-1">
            View all <ArrowRight className="w-3 h-3" />
          </Link>
        </div>
        {loading ? (
          <div className="py-12 flex justify-center">
            <LoadingSpinner label="Loading characters…" />
          </div>
        ) : error ? (
          <div className="p-6 rounded-xl bg-red-500/10 border border-red-500/30 text-sm text-red-300">
            {error}
          </div>
        ) : characters && characters.length > 0 ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
            {characters.slice(0, 5).map((c) => (
              <CharacterCard key={c.id} character={c} />
            ))}
          </div>
        ) : (
          <div className="text-center py-12 rounded-xl bg-[#1a1a23] border border-dashed border-gray-800">
            <Users className="w-10 h-10 text-gray-700 mx-auto mb-2" />
            <p className="text-sm text-gray-500">No characters yet</p>
            <Link
              href="/characters"
              className="inline-flex items-center gap-1 mt-3 text-xs text-pink-400 hover:text-pink-300"
            >
              Create your first character <ArrowRight className="w-3 h-3" />
            </Link>
          </div>
        )}
      </section>
    </div>
  );
};

interface StatusCardProps {
  label: string;
  value: string;
  subtext?: string;
  ok: boolean;
  icon: typeof Activity;
}

function StatusCard({ label, value, subtext, ok, icon: Icon }: StatusCardProps) {
  return (
    <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500 uppercase tracking-wide">{label}</span>
        <Icon className={`w-4 h-4 ${ok ? 'text-emerald-400' : 'text-gray-600'}`} />
      </div>
      <p className="text-sm font-semibold text-white truncate">{value}</p>
      {subtext && <p className="text-xs text-gray-500 mt-0.5 truncate">{subtext}</p>}
      <div
        className={`mt-2 h-0.5 rounded-full ${
          ok ? 'bg-gradient-to-r from-emerald-500 to-teal-500' : 'bg-gray-800'
        }`}
      />
    </div>
  );
}

export default Dashboard;
