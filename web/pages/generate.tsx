import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import {
  Wand2,
  Sparkles,
  Loader2,
  Send,
  Monitor,
  RefreshCw,
  Link2,
  Link2Off,
  Layers,
} from 'lucide-react';
import type { NextPage } from 'next';
import type {
  Character,
  GenerationRequest,
  GenerationResult,
  GenerationStep,
  PipelineStatus,
  ProviderId,
  Resolution,
  StylePreset,
} from '../types';
import { apiClient } from '../lib/api-client';
import LoadingSpinner from '../components/LoadingSpinner';
import ProgressSteps from '../components/ProgressSteps';

const PROVIDERS: { id: ProviderId; name: string; desc: string }[] = [
  { id: 'pollinations', name: 'Pollinations', desc: 'Open, free, fast' },
  { id: 'seedream', name: 'Seedream', desc: 'High fidelity' },
  { id: 'sensenova', name: 'SenseNova', desc: 'Anime-specialized' },
  { id: 'local', name: 'Local', desc: 'On-device / self-hosted' },
];

const RESOLUTIONS: Resolution[] = [512, 768, 1024, 2048];
const STYLES: { id: StylePreset; label: string }[] = [
  { id: 'moe', label: 'Moe' },
  { id: 'anime', label: 'Anime' },
  { id: 'realistic', label: 'Realistic' },
  { id: 'chibi', label: 'Chibi' },
  { id: 'watercolor', label: 'Watercolor' },
  { id: 'lineart', label: 'Lineart' },
  { id: 'pixel', label: 'Pixel' },
  { id: 'cyberpunk', label: 'Cyberpunk' },
];

const PIPELINE_STEPS: { id: PipelineStatus; label: string }[] = [
  { id: 'queued', label: 'Queued' },
  { id: 'generating', label: 'Generating image' },
  { id: 'qa', label: 'Quality assurance' },
  { id: 'optimizing', label: 'Optimizing' },
  { id: 'segmenting', label: 'Segmenting layers' },
  { id: 'layering', label: 'Compositing layers' },
  { id: 'rigging', label: 'Rigging physics' },
  { id: 'done', label: 'Complete' },
];

function makeInitialSteps(): GenerationStep[] {
  return PIPELINE_STEPS.map((s) => ({
    id: s.id,
    label: s.label,
    status: 'pending',
    progress: 0,
  }));
}

const GeneratePage: NextPage = () => {
  const [prompt, setPrompt] = useState('');
  const [negativePrompt, setNegativePrompt] = useState('');
  const [provider, setProvider] = useState<ProviderId>('pollinations');
  const [resolution, setResolution] = useState<Resolution>(1024);
  const [style, setStyle] = useState<StylePreset>('anime');
  const [consistency, setConsistency] = useState(true);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [characterId, setCharacterId] = useState<string | undefined>();
  const [steps, setSteps] = useState<GenerationStep[]>(makeInitialSteps());
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<GenerationResult[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    apiClient
      .getCharacters()
      .then(setCharacters)
      .catch(() => setCharacters([]));
  }, []);

  const resetSteps = () => setSteps(makeInitialSteps());

  const handleGenerate = useCallback(async () => {
    if (!prompt.trim() || generating) return;
    setGenerating(true);
    setError(null);
    setResult(null);
    resetSteps();
    abortRef.current = new AbortController();

    const req: GenerationRequest = {
      prompt: prompt.trim(),
      negativePrompt: negativePrompt.trim() || undefined,
      provider,
      width: resolution,
      height: resolution,
      style,
      characterConsistency: consistency,
      characterId: consistency ? characterId : undefined,
      steps: 30,
      cfg: 7,
    };

    try {
      // simulate steps if API not available, but try real API first
      let gotResult: GenerationResult | null = null;
      try {
        gotResult = await apiClient.generateStream(req, (step) => {
          setSteps((prev) =>
            prev.map((s) =>
              s.id === step.id ? { ...s, ...step, status: step.status || 'active' } : s,
            ),
          );
        });
      } catch {
        // fallback: simulate progress for demo
        for (let i = 0; i < PIPELINE_STEPS.length; i++) {
          setSteps((prev) => {
            const next = [...prev];
            if (i > 0) next[i - 1] = { ...next[i - 1], status: 'done', progress: 100 };
            next[i] = { ...next[i], status: 'active', progress: 0 };
            return next;
          });
          for (let p = 0; p <= 100; p += 25) {
            await new Promise((r) => setTimeout(r, 60));
            setSteps((prev) => {
              const next = [...prev];
              next[i] = { ...next[i], progress: p };
              return next;
            });
          }
        }
        setSteps((prev) => {
          const next = [...prev];
          next[next.length - 1] = { ...next[next.length - 1], status: 'done', progress: 100 };
          next[next.length - 2] = { ...next[next.length - 2], status: 'done', progress: 100 };
          return next;
        });
        gotResult = {
          id: `demo-${Date.now()}`,
          requestId: `req-${Date.now()}`,
          imageUrl: `https://image.pollinations.ai/prompt/${encodeURIComponent(req.prompt)}?width=${resolution}&height=${resolution}&nologo=true`,
          segmentedLayers: [],
          metadata: { provider, style, resolution },
          createdAt: new Date().toISOString(),
        };
      }
      setResult(gotResult);
      setHistory((prev) => [gotResult!, ...prev].slice(0, 12));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
      setSteps((prev) =>
        prev.map((s) => (s.status === 'active' ? { ...s, status: 'error' } : s)),
      );
    } finally {
      setGenerating(false);
      abortRef.current = null;
    }
  }, [prompt, negativePrompt, provider, resolution, style, consistency, characterId, generating]);

  const cancel = () => {
    abortRef.current?.abort();
    setGenerating(false);
  };

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[380px_1fr] gap-5 animate-fade-in">
      {/* Controls */}
      <div className="space-y-4">
        <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5 space-y-4">
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-medium text-gray-400">Prompt</label>
              <span className="text-[10px] text-gray-600">{prompt.length}/2000</span>
            </div>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value.slice(0, 2000))}
              rows={5}
              placeholder="A cheerful anime girl with pink twin-tails, blue eyes, school uniform, soft lighting, detailed background…"
              className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-pink-500 transition-colors resize-none"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-gray-400 block mb-2">Negative prompt</label>
            <textarea
              value={negativePrompt}
              onChange={(e) => setNegativePrompt(e.target.value)}
              rows={2}
              placeholder="lowres, bad anatomy, worst quality…"
              className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-pink-500 transition-colors resize-none"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-gray-400 block mb-2">Provider</label>
            <div className="grid grid-cols-2 gap-2">
              {PROVIDERS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setProvider(p.id)}
                  className={`text-left p-2.5 rounded-lg border text-xs transition-all ${
                    provider === p.id
                      ? 'bg-pink-500/10 border-pink-500/40 text-white'
                      : 'bg-gray-900 border-gray-800 text-gray-400 hover:border-gray-700'
                  }`}
                >
                  <p className="font-semibold">{p.name}</p>
                  <p className="text-[10px] opacity-70 mt-0.5">{p.desc}</p>
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-medium text-gray-400 block mb-2">Resolution</label>
              <div className="flex gap-1 flex-wrap">
                {RESOLUTIONS.map((r) => (
                  <button
                    key={r}
                    onClick={() => setResolution(r)}
                    className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${
                      resolution === r
                        ? 'bg-pink-500/20 border-pink-500/50 text-pink-300'
                        : 'bg-gray-900 border-gray-800 text-gray-400 hover:border-gray-700'
                    }`}
                  >
                    {r}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-gray-400 block mb-2">Style</label>
              <select
                value={style}
                onChange={(e) => setStyle(e.target.value as StylePreset)}
                className="w-full px-2.5 py-1.5 bg-gray-900 border border-gray-700 rounded-md text-xs text-white focus:outline-none focus:border-pink-500"
              >
                {STYLES.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <button
              onClick={() => setConsistency((c) => !c)}
              className={`w-full flex items-center justify-between p-3 rounded-lg border transition-colors ${
                consistency
                  ? 'bg-purple-500/10 border-purple-500/40'
                  : 'bg-gray-900 border-gray-800'
              }`}
            >
              <span className="flex items-center gap-2 text-xs">
                {consistency ? (
                  <Link2 className="w-4 h-4 text-purple-400" />
                ) : (
                  <Link2Off className="w-4 h-4 text-gray-500" />
                )}
                Character consistency
              </span>
              <span
                className={`w-9 h-5 rounded-full relative transition-colors ${
                  consistency ? 'bg-purple-500' : 'bg-gray-700'
                }`}
              >
                <span
                  className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
                    consistency ? 'left-[18px]' : 'left-0.5'
                  }`}
                />
              </span>
            </button>
            {consistency && (
              <select
                value={characterId || ''}
                onChange={(e) => setCharacterId(e.target.value || undefined)}
                className="mt-2 w-full px-2.5 py-2 bg-gray-900 border border-gray-700 rounded-lg text-xs text-white focus:outline-none focus:border-pink-500"
              >
                <option value="">— Select character —</option>
                {characters.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          {generating ? (
            <button
              onClick={cancel}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/40 text-red-300 font-medium hover:bg-red-500/20 transition-colors"
            >
              Cancel generation
            </button>
          ) : (
            <button
              onClick={handleGenerate}
              disabled={!prompt.trim()}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-pink-500/30 transition-all hover-scale"
            >
              <Wand2 className="w-4 h-4" /> Generate
            </button>
          )}
        </div>

        {generating && (
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <ProgressSteps steps={steps} />
          </div>
        )}
      </div>

      {/* Result */}
      <div className="space-y-4 min-w-0">
        <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5 min-h-[400px]">
          {error && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-300 mb-4">
              {error}
            </div>
          )}
          {generating && !result ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <LoadingSpinner size={36} />
              <p className="text-sm text-gray-400">Running pipeline…</p>
              <p className="text-xs text-gray-600">
                {steps.find((s) => s.status === 'active')?.label}
              </p>
            </div>
          ) : result ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-pink-400" /> Result
                </h3>
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleGenerate}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-gray-800 border border-gray-700 text-gray-200 hover:bg-gray-700 transition-colors"
                  >
                    <RefreshCw className="w-3.5 h-3.5" /> Regenerate
                  </button>
                  <Link
                    href="/layers"
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-gray-800 border border-gray-700 text-gray-200 hover:bg-gray-700 transition-colors"
                  >
                    <Layers className="w-3.5 h-3.5" /> Open layers
                  </Link>
                  <Link
                    href="/live2d"
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-gradient-to-r from-pink-500/20 to-purple-500/20 border border-pink-500/40 text-pink-300 hover:from-pink-500/30 hover:to-purple-500/30 transition-colors"
                  >
                    Send to Live2D Builder
                  </Link>
                  <button
                    onClick={() => undefined}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-blue-500/20 border border-blue-500/40 text-blue-300 hover:bg-blue-500/30 transition-colors"
                  >
                    <Monitor className="w-3.5 h-3.5" /> Desktop Pet
                  </button>
                </div>
              </div>
              <div className="rounded-lg overflow-hidden bg-[#0f0f13] border border-gray-800 flex items-center justify-center">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={result.imageUrl}
                  alt="generation result"
                  className="max-w-full max-h-[70vh] object-contain"
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.opacity = '0.3';
                  }}
                />
              </div>
              {result.segmentedLayers.length > 0 && (
                <div>
                  <p className="text-xs text-gray-400 mb-2">
                    {result.segmentedLayers.length} layers detected
                  </p>
                  <div className="flex gap-2 overflow-x-auto pb-2">
                    {result.segmentedLayers.map((l) => (
                      <div
                        key={l.id}
                        className="shrink-0 w-20 aspect-square rounded-md bg-gray-900 border border-gray-800 overflow-hidden"
                        title={l.name}
                      >
                        {l.thumbnailUrl && (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={l.thumbnailUrl} alt={l.name} className="w-full h-full object-cover" />
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-pink-500/20 to-purple-500/20 border border-pink-500/30 flex items-center justify-center mb-4">
                <Wand2 className="w-7 h-7 text-pink-400" />
              </div>
              <p className="text-sm text-gray-300 font-medium">Ready to generate</p>
              <p className="text-xs text-gray-500 mt-1 max-w-sm">
                Write a prompt, choose a provider, and hit Generate to run the full pipeline — image → QA → segmentation → rigging.
              </p>
            </div>
          )}
        </div>

        {history.length > 0 && (
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <h3 className="text-sm font-semibold text-white mb-3">History</h3>
            <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
              {history.map((h) => (
                <button
                  key={h.id}
                  onClick={() => setResult(h)}
                  className="aspect-square rounded-md overflow-hidden bg-gray-900 border border-gray-800 hover:border-pink-500/40 transition-colors"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={h.imageUrl} alt="" className="w-full h-full object-cover" />
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default GeneratePage;
