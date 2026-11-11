import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import type { NextPage } from 'next';
import {
  Box,
  Boxes,
  Check,
  FolderOpen,
  Layers as LayersIcon,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
} from 'lucide-react';
import { apiClient } from '../lib/api-client';
import { workDisplayName, workFromRecord, workStageTags, type Workpiece } from '../lib/workbench';

/** 阶段筛选：按真实产物归类的标签。 */
const STAGE_FILTERS = [
  { id: 'all', label: '全部阶段' },
  { id: 'layers', label: '已分层' },
  { id: 'preview', label: '已建模' },
  { id: 'export', label: '已导出' },
] as const;

type StageFilter = (typeof STAGE_FILTERS)[number]['id'];

const ModelsPage: NextPage = () => {
  const [works, setWorks] = useState<Workpiece[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('全部');
  const [stage, setStage] = useState<StageFilter>('all');
  const [editingId, setEditingId] = useState('');
  const [draftName, setDraftName] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const records = await apiClient.listGenerations(200);
      setWorks(records.map(workFromRecord).filter((work): work is Workpiece => Boolean(work)));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '模型库加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  // 首屏 + 8s 轮询 + 窗口重新聚焦时刷新：别的窗口导出完模型这里会自己更新。
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(true), 8000);
    const onFocus = () => void load(true);
    window.addEventListener('focus', onFocus);
    return () => {
      clearInterval(timer);
      window.removeEventListener('focus', onFocus);
    };
  }, [load]);

  const categories = useMemo(() => {
    const set = new Set<string>();
    works.forEach((work) => {
      const value = work.category.trim();
      if (value) set.add(value);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b, 'zh-CN'));
  }, [works]);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return works.filter((work) => {
      if (category !== '全部' && work.category !== category) return false;
      if (stage !== 'all' && !workStageTags(work).some((tag) => tag.id === stage)) return false;
      if (!keyword) return true;
      const haystack = [work.name, work.prompt, work.category, ...work.models.map((model) => model.name)]
        .join(' ')
        .toLowerCase();
      return haystack.includes(keyword);
    });
  }, [works, query, category, stage]);

  const saveName = async (work: Workpiece) => {
    const name = draftName.trim();
    setEditingId('');
    if (name === work.name) return;
    setWorks((prev) => prev.map((item) => (item.id === work.id ? { ...item, name } : item)));
    try {
      await apiClient.updateWork(work.id, { name });
    } catch {
      void load(true);
    }
  };

  const saveCategory = async (work: Workpiece, next: string) => {
    const value = next.trim();
    if (value === work.category) return;
    setWorks((prev) => prev.map((item) => (item.id === work.id ? { ...item, category: value } : item)));
    try {
      await apiClient.updateWork(work.id, { category: value });
    } catch {
      void load(true);
    }
  };

  const totalModels = works.reduce((sum, work) => sum + work.models.length, 0);

  return (
      <div className="mx-auto max-w-6xl space-y-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-haze-100">模型库</h1>
            <p className="mt-1 text-xs text-haze-500">
              {works.length} 个作品 · {totalModels} 个模型 · 命名与分类后会实时同步到工作台
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => void load()} className="btn-ghost py-2" title="立即刷新">
              <RefreshCw className="h-3.5 w-3.5" /> 刷新
            </button>
            <Link href="/" className="btn-primary py-2">
              <Plus className="h-4 w-4" /> 新建作品
            </Link>
          </div>
        </header>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-haze-500" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索作品名 / 提示词 / 模型名"
              className="field pl-9"
            />
          </div>
          <div className="flex flex-wrap items-center gap-1">
            {['全部', ...categories].map((item) => (
              <button
                key={item}
                onClick={() => setCategory(item)}
                className={`chip ${category === item ? 'chip-active' : ''}`}
              >
                {item}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-1">
            {STAGE_FILTERS.map((item) => (
              <button
                key={item.id}
                onClick={() => setStage(item.id)}
                className={`chip ${stage === item.id ? 'chip-active' : ''}`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <p className="rounded-xl border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-600">
            {error}
          </p>
        )}

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-haze-500">
            <Loader2 className="h-4 w-4 animate-spin" /> 正在读取作品…
          </div>
        ) : filtered.length === 0 ? (
          <div className="card flex flex-col items-center gap-3 px-6 py-16 text-center">
            <Boxes className="h-6 w-6 text-iris-500" />
            <p className="text-sm text-haze-200">
              {works.length === 0 ? '还没有作品' : '没有匹配的作品'}
            </p>
            <p className="max-w-sm text-xs leading-relaxed text-haze-500">
              从启动页选一条链路：从零开始走「生成 → 分层 → 预览 → 导出」，已有图片则直接从分层开始。
            </p>
            <Link href="/" className="btn-primary mt-1 py-2">
              <Plus className="h-4 w-4" /> 去新建
            </Link>
          </div>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {filtered.map((work) => {
              const tags = workStageTags(work);
              return (
                <article key={work.id} className="card card-hover p-3">
                  <div className="flex gap-3">
                    <div className="h-20 w-20 shrink-0 overflow-hidden rounded-lg border border-black/[0.06] bg-black/[0.03]">
                      {work.imageUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={work.imageUrl} alt={workDisplayName(work)} className="h-full w-full object-cover" />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center text-haze-600">
                          <Box className="h-5 w-5" />
                        </div>
                      )}
                    </div>

                    <div className="min-w-0 flex-1">
                      <div className="flex items-start gap-2">
                        {editingId === work.id ? (
                          <input
                            autoFocus
                            value={draftName}
                            onChange={(event) => setDraftName(event.target.value)}
                            onBlur={() => void saveName(work)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') void saveName(work);
                              if (event.key === 'Escape') setEditingId('');
                            }}
                            placeholder="给作品起个名字"
                            maxLength={40}
                            className="field py-1 text-xs"
                          />
                        ) : (
                          <button
                            onClick={() => {
                              setEditingId(work.id);
                              setDraftName(work.name || '');
                            }}
                            className="group flex min-w-0 items-center gap-1 text-left"
                            title="点击重命名"
                          >
                            <span className="truncate text-sm text-haze-100">{workDisplayName(work, 26)}</span>
                            <Pencil className="h-3 w-3 shrink-0 text-haze-600 opacity-0 transition-opacity group-hover:opacity-100" />
                          </button>
                        )}
                        <span className="chip ml-auto shrink-0">
                          <LayersIcon className="h-2.5 w-2.5" />
                          {work.models.length} 模型
                        </span>
                      </div>

                      <div className="mt-1 flex items-center gap-1.5 text-[10px] text-haze-500">
                        {work.source === 'upload' ? (
                          <span className="rounded bg-mint-500/10 px-1 py-px text-mint-600">已有图片</span>
                        ) : (
                          <span className="rounded bg-iris-500/10 px-1 py-px text-iris-600">生成</span>
                        )}
                        <span className="tabular-nums">{work.width}×{work.height}</span>
                        {work.createdAt && (
                          <span className="tabular-nums">{new Date(work.createdAt).toLocaleDateString('zh-CN')}</span>
                        )}
                      </div>

                      <div className="mt-1.5 flex flex-wrap items-center gap-1">
                        {tags.map((tag) => (
                          <span
                            key={tag.id}
                            className="inline-flex items-center gap-0.5 rounded-full bg-mint-500/10 px-1.5 py-px text-[9px] text-mint-600"
                          >
                            <Check className="h-2.5 w-2.5" /> {tag.label}
                          </span>
                        ))}
                        {tags.length === 0 && <span className="text-[10px] text-haze-600">还没开始分层</span>}
                      </div>

                      <div className="mt-2 flex items-center gap-2">
                        <input
                          list="work-categories"
                          defaultValue={work.category}
                          onBlur={(event) => void saveCategory(work, event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') (event.target as HTMLInputElement).blur();
                          }}
                          placeholder="分类（可新建）"
                          maxLength={20}
                          className="field w-32 py-1 text-[11px]"
                        />
                        <Link
                          href={`/studio?work=${encodeURIComponent(work.id)}`}
                          className="btn-ghost ml-auto py-1.5 text-[11px]"
                        >
                          <FolderOpen className="h-3 w-3" /> 打开工作台
                        </Link>
                      </div>
                    </div>
                  </div>

                  {work.models.length > 0 && (
                    <ul className="mt-2 flex flex-wrap gap-1 border-t border-black/[0.05] pt-2">
                      {work.models.map((model) => (
                        <li key={model.id} className="chip">
                          <Box className="h-2.5 w-2.5" />
                          {model.name}
                        </li>
                      ))}
                    </ul>
                  )}
                </article>
              );
            })}
          </div>
        )}

        <datalist id="work-categories">
          {categories.map((item) => (
            <option key={item} value={item} />
          ))}
        </datalist>
      </div>
  );
};

export default ModelsPage;
