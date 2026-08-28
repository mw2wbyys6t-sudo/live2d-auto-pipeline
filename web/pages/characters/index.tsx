import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Plus, Search, Users, RefreshCw } from 'lucide-react';
import type { NextPage } from 'next';
import type { Character, CharacterCreate, ColorPalette } from '../../types';
import { apiClient } from '../../lib/api-client';
import CharacterCard from '../../components/CharacterCard';
import LoadingSpinner from '../../components/LoadingSpinner';
import Modal from '../../components/Modal';
import ImageUploader from '../../components/ImageUploader';
import ColorPicker from '../../components/ColorPicker';

const DEFAULT_PALETTE: ColorPalette = {
  primary: '#ec4899',
  secondary: '#8b5cf6',
  hair: '#1f2937',
  eyes: '#3b82f6',
  skin: '#fde2c4',
  accent: '#f472b6',
};

const CharactersPage: NextPage = () => {
  const [characters, setCharacters] = useState<Character[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // create form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [personality, setPersonality] = useState('');
  const [appearance, setAppearance] = useState('');
  const [palette, setPalette] = useState<ColorPalette>(DEFAULT_PALETTE);
  const [refFiles, setRefFiles] = useState<File[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await apiClient.getCharacters().catch(() => [] as Character[]);
      setCharacters(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load characters');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    if (!characters) return [];
    if (!query.trim()) return characters;
    const q = query.toLowerCase();
    return characters.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        (c.description || '').toLowerCase().includes(q),
    );
  }, [characters, query]);

  const resetForm = () => {
    setName('');
    setDescription('');
    setPersonality('');
    setAppearance('');
    setPalette(DEFAULT_PALETTE);
    setRefFiles([]);
  };

  const handleCreate = async () => {
    if (!name.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const data: CharacterCreate = {
        name: name.trim(),
        description: description.trim() || undefined,
        personality: personality.trim() || undefined,
        appearance: appearance.trim() || undefined,
        colorPalette: palette,
        referenceImages: refFiles,
      };
      const created = await apiClient.createCharacter(data).catch((err) => {
        // fallback: optimistic if API not available
        return {
          id: `local-${Date.now()}`,
          name: data.name!,
          description: data.description,
          personality: data.personality,
          appearance: data.appearance,
          colorPalette: data.colorPalette,
          generationCount: 0,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        } as Character;
      });
      setCharacters((prev) => [created, ...(prev || [])]);
      setModalOpen(false);
      resetForm();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create character');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await apiClient.deleteCharacter(id).catch(() => undefined);
      setCharacters((prev) => (prev || []).filter((c) => c.id !== id));
    } catch {
      // ignore
    }
  };

  return (
    <div className="space-y-5 animate-fade-in">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Characters</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Manage your character roster, personas, and reference images
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            className="p-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-300 hover:text-white hover:bg-gray-700 transition-colors"
            aria-label="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => setModalOpen(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-sm font-medium hover:shadow-lg hover:shadow-pink-500/30 transition-all hover-scale"
          >
            <Plus className="w-4 h-4" /> New character
          </button>
        </div>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search characters…"
          className="w-full pl-9 pr-4 py-2.5 bg-[#1a1a23] border border-gray-800 rounded-lg text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-pink-500 transition-colors"
        />
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="py-24 flex justify-center">
          <LoadingSpinner label="Loading characters…" size={28} />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 rounded-xl bg-[#1a1a23] border border-dashed border-gray-800">
          <Users className="w-10 h-10 text-gray-700 mx-auto mb-3" />
          <p className="text-sm text-gray-500">
            {characters && characters.length === 0
              ? 'No characters yet. Create your first one!'
              : 'No characters match your search'}
          </p>
          {(!characters || characters.length === 0) && (
            <button
              onClick={() => setModalOpen(true)}
              className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-800 text-sm text-gray-200 hover:bg-gray-700 transition-colors"
            >
              <Plus className="w-4 h-4" /> New character
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
          {filtered.map((c) => (
            <CharacterCard key={c.id} character={c} onDelete={handleDelete} />
          ))}
        </div>
      )}

      <Modal
        open={modalOpen}
        onClose={() => !creating && setModalOpen(false)}
        title="New character"
        size="lg"
        footer={
          <>
            <button
              onClick={() => setModalOpen(false)}
              disabled={creating}
              className="px-4 py-2 rounded-lg text-sm text-gray-300 hover:text-white hover:bg-gray-800 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={creating || !name.trim()}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-pink-500/30 transition-all"
            >
              {creating ? <LoadingSpinner size={16} /> : <Plus className="w-4 h-4" />}
              {creating ? 'Creating…' : 'Create'}
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name *">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Sakura Hoshino"
              className="input"
            />
          </Field>
          <Field label="Description">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="Short description of the character"
              className="input resize-none"
            />
          </Field>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Personality">
              <textarea
                value={personality}
                onChange={(e) => setPersonality(e.target.value)}
                rows={3}
                placeholder="Cheerful, curious, a bit shy…"
                className="input resize-none"
              />
            </Field>
            <Field label="Appearance">
              <textarea
                value={appearance}
                onChange={(e) => setAppearance(e.target.value)}
                rows={3}
                placeholder="Pink twin-tails, blue eyes, school uniform…"
                className="input resize-none"
              />
            </Field>
          </div>

          <div>
            <p className="text-xs font-medium text-gray-400 mb-2">Color palette</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {(Object.keys(palette) as Array<keyof ColorPalette>).map((key) => (
                <ColorPicker
                  key={key}
                  label={key.charAt(0).toUpperCase() + key.slice(1)}
                  value={palette[key]}
                  onChange={(c) => setPalette((p) => ({ ...p, [key]: c }))}
                />
              ))}
            </div>
          </div>

          <div>
            <p className="text-xs font-medium text-gray-400 mb-2">Reference images</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <ImageUploader
                label="Front view"
                onChange={(f) =>
                  setRefFiles((prev) => {
                    const next = prev.filter((_, i) => i !== 0);
                    if (f) next[0] = f;
                    return next;
                  })
                }
              />
              <ImageUploader
                label="Side view"
                onChange={(f) =>
                  setRefFiles((prev) => {
                    const next = [...prev];
                    if (f) next[1] = f;
                    else next.splice(1, 1);
                    return next;
                  })
                }
              />
              <ImageUploader
                label="Back view"
                onChange={(f) =>
                  setRefFiles((prev) => {
                    const next = [...prev];
                    if (f) next[2] = f;
                    else next.splice(2, 1);
                    return next;
                  })
                }
              />
            </div>
          </div>
          <p className="text-[11px] text-gray-500">
            Tip: Character consistency in Generation uses these references as visual ground truth.
          </p>
        </div>
      </Modal>

      <style jsx>{`
        :global(.input) {
          width: 100%;
          padding: 0.625rem 0.875rem;
          background: rgb(24 24 33);
          border: 1px solid rgb(39 39 48);
          border-radius: 0.5rem;
          color: white;
          font-size: 0.875rem;
          outline: none;
          transition: border-color 0.2s;
        }
        :global(.input:focus) {
          border-color: rgb(236 72 153);
        }
        :global(.input::placeholder) {
          color: rgb(75 85 99);
        }
      `}</style>
    </div>
  );
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-gray-400 mb-1.5">{label}</span>
      {children}
    </label>
  );
}

export default CharactersPage;
