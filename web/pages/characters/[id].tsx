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
import { apiClient, type LatestGeneration } from '../../lib/api-client';
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

const OUTPUT_UPLOADS_PREFIX = '/output/uploads/';

const CharacterDetailPage: NextPage = () => {
  const router = useRouter();
  const { id: queryId } = router.query;

  // 桌面版静态导出：直载 /characters/<id> 时 query 为空（服务端无法预填
  // 动态路由参数），从 location.pathname 提取 id 作为回退。
  const [id, setId] = useState('');
  useEffect(() => {
    if (typeof queryId === 'string' && queryId) {
      setId(queryId);
      return;
    }
    const fromUrl = window.location.pathname.split('/').pop();
    if (fromUrl) setId(decodeURIComponent(fromUrl));
  }, [queryId]);

  const [character, setCharacter] = useState<Character | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [form, setForm] = useState<Partial<Character>>({});
  const [dirty, setDirty] = useState(false);
  const [latestRecord, setLatestRecord] = useState<LatestGeneration | null>(null);

  // Defer locale-dependent date formatting to client-side to avoid SSR/CSR hydration mismatch
  // NOTE: This must be declared before any early return to follow the Rules of Hooks.
  const [mounted, setMounted] = useState(false);
  const [createdText, setCreatedText] = useState('');

  useEffect(() => {
    setMounted(true);
    // 拉取最近一次生成记录（当前只有全局一条，属于该角色时才展示产物）
    apiClient
      .getLatestGeneration()
      .then(setLatestRecord)
      .catch(() => setLatestRecord(null));
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
          // 后端不可达或角色不存在时如实反映，不伪造角色数据误导用户
          setError('无法加载角色数据。请确认后端服务已启动，或该角色 ID 是否存在。');
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

  /** 重算视觉 embedding：真实调用 POST /api/characters/:id/embedding */
  const handleRecomputeEmbedding = async () => {
    if (!id || typeof id !== 'string') return;
    setCharacter((prev) => (prev ? { ...prev, embeddingStatus: 'processing' } : prev));
    setError(null);
    try {
      const result = await apiClient.recomputeCharacterEmbedding(id);
      if (result.has_embedding) {
        setCharacter((prev) => (prev ? { ...prev, embeddingStatus: 'ready' } : prev));
      } else {
        setCharacter((prev) => (prev ? { ...prev, embeddingStatus: 'failed' } : prev));
        setError(result.reason || 'embedding 生成失败：角色缺少参考图。');
      }
    } catch (err) {
      setCharacter((prev) => (prev ? { ...prev, embeddingStatus: 'failed' } : prev));
      setError(err instanceof Error ? err.message : 'embedding 生成失败');
    }
  };

  /** 上传参考图并登记到角色卡（后端尽力提取 embedding） */
  const handleReferenceUpload = async (view: 'front' | 'side' | 'back', file: File | null) => {
    if (!id || typeof id !== 'string' || !file) return;
    setError(null);
    try {
      const uploaded = await apiClient.uploadImage(file);
      const res = await apiClient.addCharacterReference(id, uploaded.path, view);
      setCharacter((prev) => {
        if (!prev) return prev;
        const others = (prev.referenceImages || []).filter((r) => r.view !== view);
        return {
          ...prev,
          referenceImages: [
            ...others,
            { id: `${view}-${Date.now()}`, view, url: uploaded.url, filename: file.name },
          ],
        };
      });
      if (!res.embedding_extracted) {
        setError(res.embedding_note || '参考图已登记，但 embedding 提取失败。');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '参考图上传失败');
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
        <p className="text-gray-300">{error || 'Character not found'}</p>
        <Link href="/characters" className="text-pink-400 text-sm mt-3 inline-block">
          返回角色列表
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
                className="mt-3 w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                disabled={embedding === 'processing' || !id}
                onClick={handleRecomputeEmbedding}
              >
                <Sparkles className="w-3.5 h-3.5 text-pink-400" />
                {embedding === 'processing' ? 'Computing…' : 'Regenerate embedding'}
              </button>
            </div>
          </div>

          {/* Reference images */}
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide mb-3">Reference images</p>
            <div className="grid grid-cols-3 gap-2">
              {(['front', 'side', 'back'] as const).map((view) => {
                const ref = character.referenceImages?.find((r) => r.view === view);
                // Go 卡片的 references.<view> 是服务器路径；uploads 内的文件可映射为 Web URL
                const serverPath = (character as unknown as { references?: Record<string, string> }).references?.[view];
                const serverUrl = serverPath
                  ? OUTPUT_UPLOADS_PREFIX.concat(serverPath.split(/[\\/]/).pop() || '')
                  : '';
                return (
                  <div key={view}>
                    <p className="text-[10px] text-gray-500 mb-1 capitalize">{view}</p>
                    <ImageUploader
                      value={ref?.url || serverUrl || undefined}
                      onChange={(file) => handleReferenceUpload(view, file)}
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
              {latestRecord ? (
                <li className="ml-4">
                  <span className="absolute -left-1.5 w-3 h-3 rounded-full bg-gradient-to-br from-pink-500 to-purple-600 border-2 border-[#0f0f13]" />
                  <p className="text-xs text-gray-300">
                    最近一次生成
                    {latestRecord.character_id && latestRecord.character_id !== id ? '（属于其他角色）' : ''}
                  </p>
                  <p className="text-[11px] text-gray-500 mt-0.5 break-all">
                    {latestRecord.created_at || '—'} · 分层 {latestRecord.layer_count || 0} 层 ·{' '}
                    {latestRecord.segmentation_method || ''}
                  </p>
                  <div className="flex gap-3 mt-1.5 text-[11px]">
                    {latestRecord.image_url && (
                      <a href={latestRecord.image_url} target="_blank" rel="noreferrer" className="text-pink-400 hover:text-pink-300">
                        查看立绘
                      </a>
                    )}
                    {latestRecord.psd_url && (
                      <a href={latestRecord.psd_url} download className="text-pink-400 hover:text-pink-300">
                        下载 PSD
                      </a>
                    )}
                    {(latestRecord.model3_url || latestRecord.model3_json) && (
                      <Link href="/preview" className="text-pink-400 hover:text-pink-300">
                        在预览页加载模型
                      </Link>
                    )}
                  </div>
                </li>
              ) : (
                <li className="ml-4">
                  <p className="text-xs text-gray-500">还没有生成记录。</p>
                  <p className="text-[11px] text-gray-600 mt-0.5">在「Generate」页完成一次生成后，产物会显示在这里。</p>
                </li>
              )}
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
