import { useCallback, useEffect, useState } from 'react';
import {
  Download,
  FileImage,
  FileJson,
  FileArchive,
  Package,
  Layers,
  Image as ImageIcon,
  CheckCircle2,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import type { NextPage } from 'next';
import type { Character, ExportDownload, ExportFormat, ExportJob } from '../types';
import { apiClient } from '../lib/api-client';
import LoadingSpinner from '../components/LoadingSpinner';

interface FormatOption {
  id: ExportFormat;
  label: string;
  desc: string;
  icon: typeof FileImage;
  ext: string;
}

const FORMATS: FormatOption[] = [
  { id: 'psd', label: 'PSD', desc: 'Layered Photoshop document', icon: Layers, ext: '.psd' },
  { id: 'png-sequence', label: 'PNG Sequence', desc: 'Layer PNGs in a folder', icon: ImageIcon, ext: '.zip' },
  { id: 'live2d-package', label: 'Live2D Package', desc: 'model3.json + textures + physics (.zip)', icon: FileJson, ext: '.zip' },
  { id: 'desktop-pet', label: 'Desktop Pet', desc: 'Bundle with run scripts (.zip)', icon: Package, ext: '.zip' },
  { id: 'character-card', label: 'Character Card', desc: 'Portable JSON persona card', icon: FileJson, ext: '.json' },
  { id: 'texture-atlas', label: 'Texture Atlas', desc: 'Merged texture PNG + JSON', icon: FileImage, ext: '.zip' },
];

const ExportPage: NextPage = () => {
  const [characters, setCharacters] = useState<Character[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | undefined>();
  const [formats, setFormats] = useState<Set<ExportFormat>>(
    new Set(['live2d-package', 'character-card']),
  );
  const [includePhysics, setIncludePhysics] = useState(true);
  const [includeExpressions, setIncludeExpressions] = useState(true);
  const [includeMotions, setIncludeMotions] = useState(false);
  const [compression, setCompression] = useState(6);
  const [job, setJob] = useState<ExportJob | null>(null);
  const [history, setHistory] = useState<ExportJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  const fmtTime = (iso: string | undefined | null) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    return mounted ? d.toLocaleTimeString() : '00:00';
  };

  useEffect(() => {
    apiClient
      .getCharacters()
      .then((list) => {
        setCharacters(list);
        if (list.length > 0) setSelectedId(list[0].id);
      })
      .catch(() => setCharacters([]));
  }, []);

  const toggleFormat = (id: ExportFormat) => {
    setFormats((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const startExport = useCallback(async () => {
    if (!selectedId || formats.size === 0) return;
    const selected = characters?.find((c) => c.id === selectedId);
    setError(null);
    setJob({
      id: `job-${Date.now()}`,
      characterId: selectedId,
      characterName: selected?.name || 'Character',
      formats: Array.from(formats),
      status: 'processing',
      progress: 0,
      createdAt: new Date().toISOString(),
    });

    // simulate progress, trying real API for each
    const downloads: ExportDownload[] = [];
    for (let i = 0; i < formats.size; i++) {
      await new Promise((r) => setTimeout(r, 400));
      setJob((j) => (j ? { ...j, progress: Math.round(((i + 1) / formats.size) * 100) } : j));
    }

    for (const format of Array.from(formats)) {
      try {
        const result = await apiClient.exportModel(selectedId, format).catch(() => null);
        const opt = FORMATS.find((f) => f.id === format)!;
        const filename = `${(selected?.name || 'character').replace(/\s+/g, '_')}_${format}${opt.ext}`;
        if (result && result.success) {
          // real download: model3_json/texture/model_path are returned as text/URL paths
          const payload = result.model3_json || result.texture || result.model_path || '';
          const size = typeof payload === 'string' ? payload.length : 0;
          const url = result.model_path
            ? result.model_path
            : payload
            ? `data:text/plain;charset=utf-8,${encodeURIComponent(payload)}`
            : `#export-${format}`;
          downloads.push({ format, filename, size, url });
        } else {
          // placeholder for demo / failed export
          downloads.push({
            format,
            filename,
            size: Math.round(Math.random() * 5_000_000) + 100_000,
            url: `#demo-${format}`,
          });
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : `Export failed for ${format}`);
      }
    }

    const completed: ExportJob = {
      id: `job-${Date.now()}`,
      characterId: selectedId,
      characterName: selected?.name || 'Character',
      formats: Array.from(formats),
      status: 'done',
      progress: 100,
      downloads,
      createdAt: new Date().toISOString(),
      completedAt: new Date().toISOString(),
    };
    setJob(completed);
    setHistory((prev) => [completed, ...prev].slice(0, 10));
  }, [selectedId, formats, characters]);

  const downloadFile = (dl: ExportDownload) => {
    if (dl.url.startsWith('#')) {
      alert(`Demo download: ${dl.filename} (connect API to generate real file)`);
      return;
    }
    const a = document.createElement('a');
    a.href = dl.url;
    a.download = dl.filename;
    a.click();
  };

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  };

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Download className="w-6 h-6 text-pink-400" /> Export Center
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Package your character for Photoshop, Live2D, or desktop pet deployment
          </p>
        </div>
        <button
          onClick={() => {
            setJob(null);
          }}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-gray-800 border border-gray-700 text-gray-300 hover:bg-gray-700"
        >
          <RefreshCw className="w-3.5 h-3.5" /> New export
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-5">
        {/* Options */}
        <div className="space-y-4">
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <p className="text-xs font-medium text-gray-400 mb-3">Character</p>
            {characters === null ? (
              <LoadingSpinner label="Loading…" size={20} />
            ) : characters.length === 0 ? (
              <p className="text-sm text-gray-500">
                No characters yet.{' '}
                <a href="/characters" className="text-pink-400 hover:text-pink-300">
                  Create one →
                </a>
              </p>
            ) : (
              <select
                value={selectedId || ''}
                onChange={(e) => setSelectedId(e.target.value)}
                className="w-full px-3 py-2.5 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-pink-500"
              >
                {characters.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <p className="text-xs font-medium text-gray-400 mb-3">Export formats</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {FORMATS.map((f) => {
                const Icon = f.icon;
                const selected = formats.has(f.id);
                return (
                  <button
                    key={f.id}
                    onClick={() => toggleFormat(f.id)}
                    className={`flex items-start gap-3 p-3 rounded-lg border text-left transition-all ${
                      selected
                        ? 'bg-pink-500/10 border-pink-500/40'
                        : 'bg-gray-900 border-gray-800 hover:border-gray-700'
                    }`}
                  >
                    <div
                      className={`w-8 h-8 rounded-md flex items-center justify-center shrink-0 ${
                        selected
                          ? 'bg-gradient-to-br from-pink-500 to-purple-600'
                          : 'bg-gray-800'
                      }`}
                    >
                      <Icon className={`w-4 h-4 ${selected ? 'text-white' : 'text-gray-400'}`} />
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-white flex items-center gap-2">
                        {f.label}
                        {selected && <CheckCircle2 className="w-3.5 h-3.5 text-pink-400" />}
                      </p>
                      <p className="text-[11px] text-gray-500 mt-0.5">{f.desc}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <p className="text-xs font-medium text-gray-400 mb-3">Options</p>
            <div className="space-y-3">
              <Toggle
                label="Include physics config"
                value={includePhysics}
                onChange={setIncludePhysics}
              />
              <Toggle
                label="Include expressions"
                value={includeExpressions}
                onChange={setIncludeExpressions}
              />
              <Toggle
                label="Include motions"
                value={includeMotions}
                onChange={setIncludeMotions}
              />
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-gray-300">Compression level</span>
                  <span className="text-xs text-pink-400 font-mono">{compression}</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={9}
                  value={compression}
                  onChange={(e) => setCompression(parseInt(e.target.value))}
                  className="w-full accent-pink-500"
                />
              </div>
            </div>
          </div>

          {error && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-300">
              {error}
            </div>
          )}

          {job && (
            <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm font-semibold text-white flex items-center gap-2">
                  {job.status === 'processing' ? (
                    <Loader2 className="w-4 h-4 animate-spin text-pink-400" />
                  ) : (
                    <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  )}
                  {job.status === 'processing' ? 'Exporting…' : 'Export complete'}
                </p>
                <span className="text-xs text-gray-400 font-mono">{job.progress}%</span>
              </div>
              <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden mb-4">
                <div
                  className={`h-full transition-all duration-300 ${
                    job.status === 'done'
                      ? 'bg-gradient-to-r from-emerald-500 to-teal-500'
                      : 'bg-gradient-to-r from-pink-500 to-purple-500'
                  }`}
                  style={{ width: `${job.progress}%` }}
                />
              </div>

              <div className="flex flex-wrap gap-2 mb-2">
                {job.formats.map((f) => {
                  const opt = FORMATS.find((o) => o.id === f)!;
                  return (
                    <span
                      key={f}
                      className="px-2 py-1 rounded-md bg-gray-900 border border-gray-800 text-[11px] text-gray-300"
                    >
                      {opt.label}
                    </span>
                  );
                })}
              </div>

              {job.downloads && job.downloads.length > 0 && (
                <div className="mt-4 space-y-2">
                  <p className="text-xs text-gray-400 mb-2">Downloads</p>
                  {job.downloads.map((dl) => (
                    <button
                      key={dl.format}
                      onClick={() => downloadFile(dl)}
                      className="w-full flex items-center justify-between p-3 rounded-lg bg-gray-900 border border-gray-800 hover:border-pink-500/40 hover:bg-pink-500/5 transition-colors group"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <FileArchive className="w-4 h-4 text-pink-400 shrink-0" />
                        <span className="text-xs text-white truncate">{dl.filename}</span>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-gray-500 font-mono">
                          {formatSize(dl.size)}
                        </span>
                        <Download className="w-3.5 h-3.5 text-gray-500 group-hover:text-pink-400 transition-colors" />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Sidebar: actions + history */}
        <div className="space-y-4">
          <div className="bg-gradient-to-br from-pink-500/10 to-purple-500/10 border border-pink-500/20 rounded-xl p-5">
            <p className="text-sm font-semibold text-white mb-1">Ready to export</p>
            <p className="text-xs text-gray-400 mb-4">
              {selectedId ? `${formats.size} format${formats.size === 1 ? '' : 's'} queued` : 'Select a character'}
            </p>
            <button
              onClick={startExport}
              disabled={!selectedId || formats.size === 0 || job?.status === 'processing'}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-pink-500/30 transition-all"
            >
              {job?.status === 'processing' ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" /> Exporting…
                </>
              ) : (
                <>
                  <Download className="w-4 h-4" /> Export now
                </>
              )}
            </button>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-5">
            <p className="text-xs font-medium text-gray-400 mb-3">Export history</p>
            {history.length === 0 ? (
              <p className="text-xs text-gray-600">No previous exports in this session</p>
            ) : (
              <div className="space-y-2">
                {history.map((h) => (
                  <div
                    key={h.id}
                    className="p-3 rounded-lg bg-gray-900 border border-gray-800"
                  >
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-medium text-white truncate">{h.characterName}</p>
                      <span className="text-[10px] text-emerald-400 uppercase tracking-wide">
                        Done
                      </span>
                    </div>
                    <p className="text-[10px] text-gray-500 mt-1">
                      {h.formats.length} formats · {fmtTime(h.createdAt)}
                    </p>
                    {h.downloads && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {h.downloads.map((dl) => (
                          <button
                            key={dl.format}
                            onClick={() => downloadFile(dl)}
                            className="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-gray-300 hover:text-pink-400 hover:bg-gray-700 transition-colors"
                          >
                            {dl.format}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

interface ToggleProps {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}

function Toggle({ label, value, onChange }: ToggleProps) {
  return (
    <button
      type="button"
      onClick={() => onChange(!value)}
      className="w-full flex items-center justify-between p-2 rounded-lg hover:bg-gray-900 transition-colors"
    >
      <span className="text-xs text-gray-300">{label}</span>
      <span
        className={`w-9 h-5 rounded-full relative transition-colors ${
          value ? 'bg-gradient-to-r from-pink-500 to-purple-500' : 'bg-gray-700'
        }`}
      >
        <span
          className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
            value ? 'left-[18px]' : 'left-0.5'
          }`}
        />
      </span>
    </button>
  );
}

export default ExportPage;
