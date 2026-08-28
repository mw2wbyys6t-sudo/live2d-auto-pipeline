import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import {
  ArrowLeft,
  Save,
  Trash2,
  Sparkles,
  Clock,
  CheckCircle2,
  Loader2,
  AlertCircle,
  Upload,
  Shirt,
} from 'lucide-react';
import type { NextPage } from 'next';
import type { Character, ColorPalette } from '../../types';
import { apiClient } from '../../lib/api-client';
import LoadingSpinner from '../../components/LoadingSpinner';
import ColorPicker from '../../components/ColorPicker';
import ImageUploader from '../../components/ImageUploader';
import Modal from '../../components/Modal';

const DEFAULT_PALETTE: ColorPalette = {
  primary: '#ec4899',
  secondary: '#8b5cf6',
  hair: '#1f2937',
  eyes: '#3b82f6',
  skin: '#fde2c4',
  accent: '#f472b6',
};

const CharacterDetailPage: NextPage = () => {
  const router = useRouter();
  const { id } = router.query;

  const [character, setCharacter] = useState<Character | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [form, setForm] = useState<Partial<Character>>({});
  const [dirty, setDirty] = useState(false);

  // Defer locale-dependent date formatting to client-side to avoid SSR/CSR hydration mismatch
  // NOTE: This must be declared before any early return to follow the Rules of Hooks.
  const [mounted, setMounted] = useState(false);
  const [createdText, setCreatedText] = useState('');

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!id || typeof id !== 'string') return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const c = await apiClient.getCharacter(id).catch(() => null);
        if (cancelled) return;
        if (!c) {
          // fallback: placeholder for demo/offline
          setCharacter({
            id,
            name: `Character ${id}`,
            generationCount: 0,
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
            colorPalette: DEFAULT_PALETTE,
          });
        } else {
          setCharacter(c);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (character) setForm(character);
  }, [character]);

  useEffect(() => {
    if (!character) {
      setCreatedText('');
      return;
    }
    const d = new Date(character.createdAt);
    if (isNaN(d.getTime())) {
      setCreatedText('—');
      return;
    }
    try {
      setCreatedText(d.toLocaleDateString());
    } catch {
      setCreatedText(d.toISOString().slice(0, 10));
    }
  }, [character]);

  const update = useCallback(<K extends keyof Character>(key: K, value: Character[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  }, []);

  const handleSave = async () => {
    if (!character || !id || typeof id !== 'string') return;
    setSaving(true);
    setError(null);
    try {
      const updated = await apiClient
        .updateCharacter(id, form)
        .catch(() => ({ ...character, ...form, updatedAt: new Date().toISOString() } as Character));
      setCharacter(updated);
      setForm(updated);
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!id || typeof id !== 'string') return;
    try {
      await apiClient.deleteCharacter(id).catch(() => undefined);
      router.push('/characters');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete');
    }
  };

  // Build display values even before character is loaded so hooks above are unconditional.
  const palette: ColorPalette =
    form.colorPalette || character?.colorPalette || DEFAULT_PALETTE;
  const embedding = character?.embeddingStatus;
  const safeCreatedAt = character?.createdAt;
  const displayCreated = !safeCreatedAt || isNaN(new Date(safeCreatedAt).getTime())
    ? '—'
    : mounted
    ? createdText
    : new Date(safeCreatedAt).toLocaleDateString('en-US');

  if (loading) {
    return (
      <div className="py-24 flex justify-center">
        <LoadingSpinner label="Loading character…" size={28} />
      </div>
    );
  }

  if (!character) {
    return (
      <div className="text-center py-20">
        <AlertCircle className="w-10 h-10 text-red-400 mx-auto mb-3" />
        <p className="text-gray-400">Character not found</p>
        <Link href="/characters" className="text-pink-400 text-sm mt-3 inline-block">
          Back to characters
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-5 animate-fade-in">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <Link
            href="/characters"
            className="p-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-300 hover:text-white hover:bg-gray-700 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-white truncate">
              {form.name || character.name}
            </h1>
            <p className="text-xs text-gray-500">
              Created {displayCreated} · {character.generationCount} generations
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setDeleteOpen(true)}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm text-red-300 bg-red-500/10 border border-red-500/30 hover:bg-red-500/20 transition-colors"
          >
            <Trash2 className="w-4 h-4" /> Delete
          </button>
          <button
            onClick={handleSave}
            disabled={!dirty || saving}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-gradient-to-r from-pink-500 to-purple-600 text-white disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-pink-500/30 transition-all"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {dirty ? 'Save changes' : 'Saved'}
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Avatar + embedding */}
        <div className="space-y-4">
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl overflow-hidden">
            <div className="aspect-[3/4] bg-gradient-to-br from-gray-800 to-gray-900 relative">
              {character.thumbnailUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={character.thumbnailUrl}
                  alt={character.name}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center">
                  <Upload className="w-10 h-10 text-gray-700" />
                </div>
              )}
            </div>
            <div className="p-4">
              <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">Embedding</p>
              <div className="flex items-center gap-2">
                {embedding === 'ready' && (
                  <span className="inline-flex items-center gap-1.5 text-xs text-emerald-300">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Ready for consistency
                  </span>
                )}
                {embedding === 'processing' && (
                  <span className="inline-flex items-center gap-1.5 text-xs text-amber-300">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> Processing
                  </span>
                )}
                {embedding === 'failed' && (
                  <span className="inline-flex items-center gap-1.5 text-xs text-red-300">
                    <AlertCircle className="w-3.5 h-3.5" /> Failed
                  </span>
                )}
                {(!embedding || embedding === 'pending') && (
                  <span className="inline-flex items-center gap-1.5 text-xs text-gray-500">
                    <Clock className="w-3.5 h-3.5" /> Not generated
                  </span>
                )}
              </div>
              <button
                className="mt-3 w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-200 transition-colors"
                onClick={() =>
                  setForm((f) => ({ ...f, embeddingStatus: 'processing' }))
                }
              >
                <Sparkles className="w-3.5 h-3.5 text-pink-400" /> Regenerate embedding
              </button>
            </div>
          </div>

          {/* Reference images */}
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide mb-3">Reference images</p>
            <div className="grid grid-cols-3 gap-2">
              {(['front', 'side', 'back'] as const).map((view) => {
                const ref = character.referenceImages?.find((r) => r.view === view);
                return (
                  <div key={view}>
                    <p className="text-[10px] text-gray-500 mb-1 capitalize">{view}</p>
                    <ImageUploader
                      value={ref?.url}
                      onChange={() => undefined}
                      label={view}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Form */}
        <div className="lg:col-span-2 space-y-4">
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5 space-y-4">
            <h2 className="text-sm font-semibold text-white">Identity</h2>
            <LabeledInput
              label="Name"
              value={form.name || ''}
              onChange={(v) => update('name', v)}
            />
            <LabeledTextarea
              label="Description"
              value={form.description || ''}
              onChange={(v) => update('description', v)}
              rows={2}
            />
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <LabeledTextarea
                label="Personality"
                value={form.personality || ''}
                onChange={(v) => update('personality', v)}
                rows={4}
              />
              <LabeledTextarea
                label="Appearance"
                value={form.appearance || ''}
                onChange={(v) => update('appearance', v)}
                rows={4}
              />
            </div>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-white mb-4">Color palette</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {(Object.keys(palette) as Array<keyof ColorPalette>).map((key) => (
                <ColorPicker
                  key={key}
                  label={key.charAt(0).toUpperCase() + key.slice(1)}
                  value={palette[key]}
                  onChange={(c) => {
                    const next = { ...palette, [key]: c };
                    update('colorPalette', next);
                  }}
                />
              ))}
            </div>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
              <Shirt className="w-4 h-4 text-pink-400" /> Wardrobe
            </h2>
            {character.outfits && character.outfits.length > 0 ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {character.outfits.map((o) => (
                  <div
                    key={o.id}
                    className="aspect-square rounded-lg bg-gray-900 border border-gray-800 overflow-hidden relative group cursor-pointer hover:border-pink-500/40 transition-colors"
                  >
                    {o.thumbnailUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={o.thumbnailUrl} alt={o.name} className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-gray-700">
                        <Shirt className="w-6 h-6" />
                      </div>
                    )}
                    <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent p-2">
                      <p className="text-xs text-white truncate">{o.name}</p>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-500">No outfits yet.</p>
            )}
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
              <Clock className="w-4 h-4 text-pink-400" /> Generation history
            </h2>
            <ol className="relative border-l border-gray-800 ml-2 space-y-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <li key={i} className="ml-4">
                  <span className="absolute -left-1.5 w-3 h-3 rounded-full bg-gradient-to-br from-pink-500 to-purple-600 border-2 border-[#0f0f13]" />
                  <p className="text-xs text-gray-400">
                    Generation #{character.generationCount - i} — pending results
                  </p>
                  <p className="text-[11px] text-gray-600">Pipeline logs will appear here</p>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>

      <Modal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="Delete character?"
        size="sm"
        footer={
          <>
            <button
              onClick={() => setDeleteOpen(false)}
              className="px-4 py-2 rounded-lg text-sm text-gray-300 hover:text-white hover:bg-gray-800"
            >
              Cancel
            </button>
            <button
              onClick={handleDelete}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-red-500 hover:bg-red-600 text-white transition-colors"
            >
              Delete permanently
            </button>
          </>
        }
      >
        <p className="text-sm text-gray-400">
          This will remove <span className="text-white font-medium">{character.name}</span> and all
          associated generations, embeddings, and outfits. This cannot be undone.
        </p>
      </Modal>
    </div>
  );
};

interface LabeledInputProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}

function LabeledInput({ label, value, onChange, placeholder }: LabeledInputProps) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-gray-400 mb-1.5">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-pink-500 transition-colors"
      />
    </label>
  );
}

interface LabeledTextareaProps extends LabeledInputProps {
  rows?: number;
}

function LabeledTextarea({ label, value, onChange, placeholder, rows = 3 }: LabeledTextareaProps) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-gray-400 mb-1.5">{label}</span>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-pink-500 transition-colors resize-none"
      />
    </label>
  );
}

export default CharacterDetailPage;
