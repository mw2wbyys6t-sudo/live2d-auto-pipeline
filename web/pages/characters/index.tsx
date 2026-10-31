import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Plus, Search, Users, RefreshCw } from 'lucide-react';
import type { NextPage } from 'next';
import type { Character, CharacterCreate, ColorPalette } from '../../types';
import { apiClient } from '../../lib/api-client';
import CharacterCard from '../../components/CharacterCard';
import LoadingSpinner from '../../components/LoadingSpinner';
import Modal from '../../components/Modal';
import ColorPicker from '../../components/ColorPicker';
import Layout from '../../components/Layout';

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

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await apiClient.getCharacters().catch(() => [] as Character[]);
      setCharacters(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : '角色列表加载失败');
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
      };
      const created = await apiClient.createCharacter(data);
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
    setError(null);
    try {
      await apiClient.deleteCharacter(id);
      setCharacters((prev) => (prev || []).filter((c) => c.id !== id));
    } catch (err) {
      // 不再静默吞错：删除失败时保留列表项并提示原因，避免「卡片消失但刷新又回来」。
      setError(err instanceof Error ? err.message : 'Failed to delete character');
    }
  };

  return (
    // 这页此前没套 Layout：既没有页内标题，也拿不到 Layout 现在统一补的
    // 文档标题，而且整页英文文案与「中文角色工作台」的产品承诺冲突
    // （实测装机版）。
    <Layout title="角色库">
    <div className="space-y-5 animate-fade-in">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">角色库</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            管理角色名册、人设与参考图
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            className="p-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-300 hover:text-white hover:bg-gray-700 transition-colors"
            aria-label="刷新角色列表"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => setModalOpen(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-sm font-medium hover:shadow-lg hover:shadow-pink-500/30 transition-all hover-scale"
          >
            <Plus className="w-4 h-4" /> 新建角色
          </button>
        </div>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索角色…"
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
          <LoadingSpinner label="正在加载角色…" size={28} />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 rounded-xl bg-[#1a1a23] border border-dashed border-gray-800">
          <Users className="w-10 h-10 text-gray-700 mx-auto mb-3" />
          <p className="text-sm text-gray-500">
            {characters && characters.length === 0
              ? '还没有角色，先新建一个吧'
              : '没有匹配的角色'}
          </p>
          {(!characters || characters.length === 0) && (
            <button
              onClick={() => setModalOpen(true)}
              className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-800 text-sm text-gray-200 hover:bg-gray-700 transition-colors"
            >
              <Plus className="w-4 h-4" /> 新建角色
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
        title="新建角色"
        size="lg"
        footer={
          <>
            <button
              onClick={() => setModalOpen(false)}
              disabled={creating}
              className="px-4 py-2 rounded-lg text-sm text-gray-300 hover:text-white hover:bg-gray-800 transition-colors"
            >
              取消
            </button>
            <button
              onClick={handleCreate}
              disabled={creating || !name.trim()}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-pink-500/30 transition-all"
            >
              {creating ? <LoadingSpinner size={16} /> : <Plus className="w-4 h-4" />}
              {creating ? '正在创建…' : '创建'}
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="名称 *">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：星野咲良"
              className="input"
            />
          </Field>
          <Field label="简介">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="一句话说明这是个怎样的角色"
              className="input resize-none"
            />
          </Field>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="性格">
              <textarea
                value={personality}
                onChange={(e) => setPersonality(e.target.value)}
                rows={3}
                placeholder="开朗、好奇、有点害羞…"
                className="input resize-none"
              />
            </Field>
            <Field label="外观">
              <textarea
                value={appearance}
                onChange={(e) => setAppearance(e.target.value)}
                rows={3}
                placeholder="粉色双马尾、蓝眼睛、水手服…"
                className="input resize-none"
              />
            </Field>
          </div>

          <div>
            <p className="text-xs font-medium text-gray-400 mb-2">配色</p>
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

          <p className="text-[11px] text-gray-500">
            参考图上传要等后端上传接口接通后开放。
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
    </Layout>
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
