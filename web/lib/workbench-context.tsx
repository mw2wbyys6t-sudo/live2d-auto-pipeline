import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type {
  Character,
  ExportDownload,
  ExportFormat,
  ExportJob,
  GenerationRequest,
  GenerationStep,
  LayerInfo,
  Resolution,
  SegmentationMethod,
} from '../types';
import {
  apiClient,
  getBackendWsUrl,
  modelUrlFromPath,
  outputUrlFromPath,
  type ImportResult,
  type SegmentResult,
} from './api-client';

import {
  appendPromptTag as appendPromptTagRaw,
  makeInitialSteps,
  readDraft,
  stageForTab,
  tabForStage,
  toLayerInfo,
  workFromRecord,
  workFromResult,
  writeDraft,
  type InspectorTab,
  type StageId,
  type Workpiece,
} from './workbench';

export interface SourceUpload {
  file: File;
  url: string;
  width: number;
  height: number;
}

export interface SegmentationManifest {
  version: string;
  source: string;
  rigReady: boolean;
  previewReady: boolean;
  missing: string[];
  warnings: string[];
  customModels: string[];
  url: string;
}

export interface GenerationState {
  running: boolean;
  steps: GenerationStep[];
  message: string;
  error: string | null;
}

export interface SegmentationState {
  running: boolean;
  importing: boolean;
  method: SegmentationMethod;
  importPreset: 'minimal' | 'default' | 'full';
  error: string | null;
  manifest: SegmentationManifest | null;
  importSummary: string;
}

export interface ExportState {
  formats: ExportFormat[];
  job: ExportJob | null;
  packingFormat: string | null;
  error: string | null;
  blocker: string;
}

export interface PetState {
  busy: boolean;
  message: string | null;
}

interface WorkbenchValue {
  hydrated: boolean;
  works: Workpiece[];
  worksLoading: boolean;
  current: Workpiece | null;
  characters: Character[];
  refreshWorks: () => Promise<void>;
  selectWork: (id: string) => void;
  startNew: () => void;

  stage: StageId;
  inspector: InspectorTab;
  setStage: (stage: StageId) => void;
  setInspector: (tab: InspectorTab) => void;

  prompt: string;
  setPrompt: (value: string) => void;
  negativePrompt: string;
  setNegativePrompt: (value: string) => void;
  resolution: Resolution;
  setResolution: (value: Resolution) => void;
  consistency: boolean;
  setConsistency: (value: boolean) => void;
  characterId: string;
  setCharacterId: (value: string) => void;
  appendPromptTag: (tag: string) => void;

  generation: GenerationState;
  generate: () => Promise<void>;
  cancelGenerate: () => void;

  layers: LayerInfo[];
  layersLoading: boolean;
  layersError: string | null;
  layersPreviewUrl: string;
  selectedLayerId: string | null;
  selectedLayer: LayerInfo | null;
  setSelectedLayerId: (id: string | null) => void;
  updateLayer: (id: string, patch: Partial<LayerInfo>) => void;
  toggleLayerVisibility: (id: string) => void;
  moveLayer: (fromId: string, toId: string) => void;
  sourceUpload: SourceUpload | null;
  setSourceFile: (file: File | null) => void;

  segmentation: SegmentationState;
  setSegmentationMethod: (method: SegmentationMethod) => void;
  setImportPreset: (preset: 'minimal' | 'default' | 'full') => void;
  runSegmentation: () => Promise<void>;
  importPSD: (file: File | null) => Promise<void>;
  importPNGs: (files: FileList | null) => Promise<void>;

  exportState: ExportState;
  toggleExportFormat: (format: ExportFormat) => void;
  startExport: () => Promise<void>;
  downloadExport: (download: ExportDownload) => Promise<void>;
  resetExport: () => void;

  pet: PetState;
  launchPet: () => Promise<void>;
}

const WorkbenchContext = createContext<WorkbenchValue | null>(null);

const RESOLUTIONS: readonly Resolution[] = [512, 768, 1024, 2048];

function isWorkpiece(value: Workpiece | null): value is Workpiece {
  return value !== null;
}

function resolutionOf(width: number): Resolution | null {
  return (RESOLUTIONS as readonly number[]).includes(width) ? (width as Resolution) : null;
}

function byCreatedDesc(a: Workpiece, b: Workpiece): number {
  return (Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0);
}

/** 远端记录是权威元数据，但本地已导出的模型字段不能被它清空。 */
function mergeRemote(local: Workpiece | undefined, remote: Workpiece): Workpiece {
  if (!local) return remote;
  return {
    ...remote,
    prompt: remote.prompt || local.prompt,
    imageUrl: remote.imageUrl || local.imageUrl,
    layersDir: remote.layersDir || local.layersDir,
    psdPath: remote.psdPath || local.psdPath,
    psdUrl: remote.psdUrl || local.psdUrl,
    model3Json: remote.model3Json || local.model3Json,
    model3Url: remote.model3Url || local.model3Url,
    modelDir: remote.modelDir || local.modelDir,
    width: remote.width || local.width,
    height: remote.height || local.height,
    promptSent: remote.promptSent || local.promptSent,
    effectivePrompt: remote.effectivePrompt || local.effectivePrompt,
    promptLang: remote.promptLang || local.promptLang,
    promptMismatch: remote.promptMismatch || local.promptMismatch,
    createdAt: remote.createdAt || local.createdAt,
  };
}

/** 把后端进度事件并入步骤列表：进入下一步时，之前的步骤一律收尾。 */
function mergeStep(steps: GenerationStep[], incoming: GenerationStep): GenerationStep[] {
  const index = steps.findIndex((s) => s.id === incoming.id);
  if (index < 0) return steps;
  return steps.map((step, i): GenerationStep => {
    if (i < index) {
      return step.status === 'done' ? step : { ...step, status: 'done', progress: 100 };
    }
    if (i > index) return step;
    return {
      ...step,
      status: incoming.status,
      progress: incoming.status === 'done' ? 100 : Math.max(step.progress, incoming.progress || 0),
      message: incoming.message || step.message,
    };
  });
}

function finishSteps(steps: GenerationStep[], status: 'done' | 'error' | 'pending'): GenerationStep[] {
  return steps.map((step) => {
    if (status === 'done') return { ...step, status: 'done', progress: 100 };
    if (status === 'error') {
      return step.status === 'active' ? { ...step, status: 'error' } : step;
    }
    return step.status === 'active' ? { ...step, status: 'pending', progress: 0 } : step;
  });
}

function safeFileName(value: string): string {
  return (value || 'character').replace(/[\\/:*?"<>|\s]+/g, '_');
}

const EXPORT_FORMATS: readonly ExportFormat[] = ['live2d-package', 'psd'];

export function WorkbenchProvider({ children }: { children: ReactNode }) {
  const [hydrated, setHydrated] = useState(false);
  const [works, setWorks] = useState<Workpiece[]>([]);
  const [worksLoading, setWorksLoading] = useState(true);
  const [currentId, setCurrentId] = useState('');
  const [characters, setCharacters] = useState<Character[]>([]);

  const [stage, setStageState] = useState<StageId>('generate');
  const [inspector, setInspectorState] = useState<InspectorTab>('prompt');

  const [prompt, setPrompt] = useState('');
  const [negativePrompt, setNegativePrompt] = useState('');
  const [resolution, setResolution] = useState<Resolution>(1024);
  const [consistency, setConsistency] = useState(true);
  const [characterId, setCharacterId] = useState('');

  const [generation, setGeneration] = useState<GenerationState>({
    running: false,
    steps: makeInitialSteps(),
    message: '',
    error: null,
  });
  const abortRef = useRef<AbortController | null>(null);

  const [layers, setLayers] = useState<LayerInfo[]>([]);
  const [layersLoading, setLayersLoading] = useState(false);
  const [layersError, setLayersError] = useState<string | null>(null);
  const [layersPreviewUrl, setLayersPreviewUrl] = useState('');
  const [selectedLayerId, setSelectedLayerId] = useState<string | null>(null);
  const [layersReloadKey, setLayersReloadKey] = useState(0);

  const [sourceUpload, setSourceUpload] = useState<SourceUpload | null>(null);
  const sourceUploadRef = useRef<SourceUpload | null>(null);

  const [segmentation, setSegmentation] = useState<SegmentationState>({
    running: false,
    importing: false,
    method: 'semantic',
    importPreset: 'default',
    error: null,
    manifest: null,
    importSummary: '',
  });

  const [exportState, setExportState] = useState<ExportState>({
    formats: ['live2d-package'],
    job: null,
    packingFormat: null,
    error: null,
    blocker: '',
  });

  const [pet, setPet] = useState<PetState>({ busy: false, message: null });

  const current = useMemo(
    () => works.find((work) => work.id === currentId) ?? null,
    [works, currentId],
  );

  const selectedLayer = useMemo(
    () => layers.find((layer) => layer.id === selectedLayerId) ?? null,
    [layers, selectedLayerId],
  );

  const setStage = useCallback((next: StageId) => {
    setStageState(next);
    setInspectorState(tabForStage(next));
  }, []);

  const setInspector = useCallback((next: InspectorTab) => {
    setInspectorState(next);
    setStageState(stageForTab(next));
  }, []);

  const upsertWork = useCallback((work: Workpiece) => {
    setWorks((prev) => {
      const exists = prev.some((item) => item.id === work.id);
      const next = exists ? prev.map((item) => (item.id === work.id ? work : item)) : [work, ...prev];
      return next.sort(byCreatedDesc);
    });
  }, []);

  const refreshWorks = useCallback(async () => {
    const records = await apiClient.listGenerations(50);
    const remote = records.map(workFromRecord).filter(isWorkpiece);
    setWorks((prev) => {
      const localById = new Map(prev.map((work) => [work.id, work]));
      const merged = remote.map((work) => mergeRemote(localById.get(work.id), work));
      const remoteIds = new Set(remote.map((work) => work.id));
      // 导入/上传产生的本地作品不在生成历史里，必须原样保留。
      const localOnly = prev.filter((work) => !remoteIds.has(work.id));
      return [...merged, ...localOnly].sort(byCreatedDesc);
    });
  }, []);

  // ---------- 首次加载：后端历史 + 草稿 ----------
  useEffect(() => {
    let cancelled = false;
    const draft = readDraft();
    (async () => {
      const [chars, latest, records] = await Promise.all([
        apiClient.getCharacters().catch(() => [] as Character[]),
        apiClient.getLatestGeneration().catch(() => null),
        apiClient.listGenerations(50),
      ]);
      if (cancelled) return;
      const list = records.map(workFromRecord).filter(isWorkpiece);
      if (latest) {
        const latestWork = workFromRecord(latest);
        if (latestWork && !list.some((work) => work.id === latestWork.id)) {
          list.unshift(latestWork);
        }
      }
      setCharacters(chars);
      setWorks(list.sort(byCreatedDesc));
      setWorksLoading(false);

      const wanted =
        (draft.currentId && list.find((work) => work.id === draft.currentId)) || list[0] || null;
      if (wanted) {
        setCurrentId(wanted.id);
        setPrompt(draft.prompt !== undefined ? draft.prompt : wanted.prompt);
        setCharacterId(wanted.characterId);
        const res = resolutionOf(wanted.width);
        if (res) setResolution(res);
      } else {
        setPrompt(draft.prompt ?? '');
        setCharacterId(draft.characterId ?? '');
      }
      if (draft.negativePrompt !== undefined) setNegativePrompt(draft.negativePrompt);
      if (draft.resolution) setResolution(draft.resolution);
      if (draft.consistency !== undefined) setConsistency(draft.consistency);
      const nextStage = draft.stage ?? 'generate';
      setStageState(nextStage);
      setInspectorState(draft.inspector ?? tabForStage(nextStage));
      setHydrated(true);
    })().catch(() => {
      if (!cancelled) {
        setWorksLoading(false);
        setHydrated(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // ---------- 草稿持久化 ----------
  useEffect(() => {
    if (!hydrated) return;
    writeDraft({
      prompt,
      negativePrompt,
      resolution,
      consistency,
      characterId,
      stage,
      inspector,
      currentId: current?.id || '',
    });
  }, [hydrated, prompt, negativePrompt, resolution, consistency, characterId, stage, inspector, current?.id]);

  // ---------- 图层索引 ----------
  useEffect(() => {
    const dir = current?.layersDir || '';
    if (!dir) {
      setLayers([]);
      setLayersPreviewUrl('');
      setLayersError(null);
      setLayersLoading(false);
      return;
    }
    let cancelled = false;
    setLayersLoading(true);
    setLayersError(null);
    const width = current?.width || 1024;
    const height = current?.height || 1024;
    apiClient
      .listLayers(dir)
      .then((index) => {
        if (cancelled) return;
        const ordered = [...index.layers].sort((a, b) => (a.draw_index ?? 0) - (b.draw_index ?? 0));
        setLayers(
          toLayerInfo(
            ordered.map((entry) => ({ name: entry.name, url: entry.url })),
            width,
            height,
          ),
        );
        setLayersPreviewUrl(index.preview_url || '');
        setSelectedLayerId(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setLayers([]);
        setLayersPreviewUrl('');
        setLayersError(err instanceof Error ? err.message : '读取图层失败');
      })
      .finally(() => {
        if (!cancelled) setLayersLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [current?.layersDir, current?.width, current?.height, layersReloadKey]);

  useEffect(() => {
    sourceUploadRef.current = sourceUpload;
  }, [sourceUpload]);

  useEffect(() => {
    return () => {
      if (sourceUploadRef.current) URL.revokeObjectURL(sourceUploadRef.current.url);
      abortRef.current?.abort();
    };
  }, []);

  const setSourceFileState = useCallback((file: File | null) => {
    if (!file) {
      setSourceUpload((prev) => {
        if (prev) URL.revokeObjectURL(prev.url);
        return null;
      });
      return;
    }
    const url = URL.createObjectURL(file);
    setSourceUpload((prev) => {
      if (prev) URL.revokeObjectURL(prev.url);
      return { file, url, width: 0, height: 0 };
    });
    const img = new Image();
    img.onload = () => {
      setSourceUpload((prev) =>
        prev && prev.url === url
          ? { ...prev, width: img.naturalWidth || 0, height: img.naturalHeight || 0 }
          : prev,
      );
    };
    img.src = url;
  }, []);

  // ---------- 作品选择 ----------
  const selectWork = useCallback(
    (id: string) => {
      const work = works.find((item) => item.id === id);
      if (!work) return;
      setCurrentId(work.id);
      setPrompt(work.prompt);
      setCharacterId(work.characterId);
      const res = resolutionOf(work.width);
      if (res) setResolution(res);
      setSourceFileState(null);
      // 同一阶段在新作品上未必有产物：能留就留，留不住退到最近的可展示阶段。
      const target: StageId =
        stage === 'layers'
          ? work.layersDir
            ? 'layers'
            : 'generate'
          : stage === 'preview'
            ? work.model3Url
              ? 'preview'
              : work.layersDir
                ? 'layers'
                : 'generate'
            : stage === 'export'
              ? work.model3Url || work.layersDir
                ? 'export'
                : 'generate'
              : 'generate';
      if (target !== stage) setStage(target);
    },
    [works, stage, setStage, setSourceFileState],
  );

  const startNew = useCallback(() => {
    setCurrentId('');
    setPrompt('');
    setNegativePrompt('');
    setCharacterId('');
    setSourceFileState(null);
    setSelectedLayerId(null);
    setStage('generate');
  }, [setStage, setSourceFileState]);

  // ---------- 生成 ----------
  const generate = useCallback(async () => {
    const text = prompt.trim();
    if (!text || generation.running) return;
    setGeneration({ running: true, steps: makeInitialSteps(), message: '已提交生成任务…', error: null });
    const controller = new AbortController();
    abortRef.current = controller;
    const request: GenerationRequest = {
      prompt: text,
      negativePrompt: negativePrompt.trim() || undefined,
      width: resolution,
      height: resolution,
      characterConsistency: consistency,
      characterId: consistency && characterId ? characterId : undefined,
    };
    try {
      const result = await apiClient.generateStream(
        request,
        (step) => {
          setGeneration((prev) => {
            if (!prev.running) return prev;
            return {
              ...prev,
              steps: mergeStep(prev.steps, step),
              message: step.message || prev.message,
            };
          });
        },
        controller.signal,
      );
      const work = workFromResult(result, result.promptUser || text);
      if (work) {
        upsertWork(work);
        setCurrentId(work.id);
        if (stage === 'generate') setStage(work.layersDir ? 'layers' : 'generate');
      }
      setGeneration((prev) => ({
        ...prev,
        running: false,
        message: '生成完成',
        steps: finishSteps(prev.steps, 'done'),
      }));
      void refreshWorks();
    } catch (err) {
      if (controller.signal.aborted) {
        setGeneration((prev) => ({
          ...prev,
          running: false,
          message: '已取消',
          steps: finishSteps(prev.steps, 'pending'),
        }));
        return;
      }
      setGeneration((prev) => ({
        ...prev,
        running: false,
        message: '',
        error: err instanceof Error ? err.message : '生成失败',
        steps: finishSteps(prev.steps, 'error'),
      }));
    } finally {
      abortRef.current = null;
    }
  }, [
    prompt,
    negativePrompt,
    resolution,
    consistency,
    characterId,
    generation.running,
    stage,
    setStage,
    upsertWork,
    refreshWorks,
  ]);

  const cancelGenerate = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  // ---------- 图层编辑 ----------
  const updateLayer = useCallback((id: string, patch: Partial<LayerInfo>) => {
    setLayers((prev) => prev.map((layer) => (layer.id === id ? { ...layer, ...patch } : layer)));
  }, []);

  const toggleLayerVisibility = useCallback((id: string) => {
    setLayers((prev) =>
      prev.map((layer) => (layer.id === id ? { ...layer, visible: !layer.visible } : layer)),
    );
  }, []);

  const moveLayer = useCallback((fromId: string, toId: string) => {
    if (fromId === toId) return;
    setLayers((prev) => {
      const from = prev.findIndex((layer) => layer.id === fromId);
      const to = prev.findIndex((layer) => layer.id === toId);
      if (from < 0 || to < 0) return prev;
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next.map((layer, i) => ({ ...layer, index: i }));
    });
  }, []);

  const setSegmentationMethod = useCallback((method: SegmentationMethod) => {
    setSegmentation((prev) => ({ ...prev, method }));
  }, []);

  const setImportPreset = useCallback((importPreset: 'minimal' | 'default' | 'full') => {
    setSegmentation((prev) => ({ ...prev, importPreset }));
  }, []);

  // ---------- 分层 ----------
  const runSegmentation = useCallback(async () => {
    if (segmentation.running) return;
    const work = current;
    const upload = sourceUpload;
    setSegmentation((prev) => ({ ...prev, running: true, error: null }));
    try {
      let imagePath = '';
      let imageUrl = '';
      if (upload) {
        const uploaded = await apiClient.uploadImage(upload.file);
        imagePath = uploaded.path;
        imageUrl = uploaded.url;
      } else if (work?.imagePath) {
        imagePath = work.imagePath;
        imageUrl = work.imageUrl;
      }
      if (!imagePath) {
        throw new Error('还没有可分层的主图：先在「生成」产出一张立绘，或在右侧上传图片。');
      }
      const result: SegmentResult = await apiClient.segmentImage(imagePath, segmentation.method);
      const width = upload?.width || work?.width || 1024;
      const height = upload?.height || work?.height || 1024;
      setLayers(toLayerInfo(result.layers || [], width, height));
      setLayersPreviewUrl(result.composite_preview || '');
      setSelectedLayerId(null);
      const psdFailed = result.psd_success === false && !!result.psd_url;
      setSegmentation((prev) => ({
        ...prev,
        importSummary: '',
        manifest: {
          version: result.manifest_version || '',
          source: result.source || result.method || segmentation.method,
          rigReady: !!result.rig_ready,
          previewReady: !!result.preview_ready,
          missing: result.missing_required || [],
          warnings: result.warnings || [],
          customModels: result.custom_models || [],
          url: result.manifest_url || '',
        },
        error: psdFailed ? '分层完成，但 PSD 写出失败 —— 可在导出面板重试。' : null,
      }));
      const nextWork: Workpiece = work
        ? {
            ...work,
            imagePath,
            imageUrl: imageUrl || work.imageUrl,
            layersDir: result.layers_dir || work.layersDir,
            psdPath: result.psd_path || work.psdPath,
            psdUrl: result.psd_url || work.psdUrl,
            width,
            height,
          }
        : {
            id: result.layers_dir || `upload-${Date.now()}`,
            characterId: '',
            prompt: '',
            imagePath,
            imageUrl,
            layersDir: result.layers_dir || '',
            psdPath: result.psd_path || '',
            psdUrl: result.psd_url || '',
            model3Json: '',
            model3Url: '',
            modelDir: '',
            width,
            height,
            promptSent: '',
            effectivePrompt: '',
            promptLang: '',
            promptMismatch: false,
            createdAt: new Date().toISOString(),
          };
      upsertWork(nextWork);
      setCurrentId(nextWork.id);
      setSourceFileState(null);
      setLayersReloadKey((key) => key + 1);
      setStage('layers');
    } catch (err) {
      setSegmentation((prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : '分层失败',
      }));
    } finally {
      setSegmentation((prev) => ({ ...prev, running: false }));
    }
  }, [segmentation.running, segmentation.method, current, sourceUpload, upsertWork, setStage, setSourceFileState]);

  const applyImportResult = useCallback(
    (result: ImportResult) => {
      const width = result.canvas?.[0] || 1024;
      const height = result.canvas?.[1] || 1024;
      const work: Workpiece = {
        id: result.layers_dir || `import-${Date.now()}`,
        characterId: '',
        prompt: '',
        imagePath: '',
        imageUrl: '',
        layersDir: result.layers_dir || '',
        psdPath: '',
        psdUrl: result.psd_url || '',
        model3Json: '',
        model3Url: '',
        modelDir: '',
        width,
        height,
        promptSent: '',
        effectivePrompt: '',
        promptLang: '',
        promptMismatch: false,
        createdAt: new Date().toISOString(),
      };
      upsertWork(work);
      setCurrentId(work.id);
      setSegmentation((prev) => ({
        ...prev,
        manifest: null,
        importSummary: result.preset
          ? `${result.preset} 预设 · 保留 ${result.layer_count} / ${result.total_found ?? result.layer_count} 层 · 已识别 ${result.recognized_count ?? 0}`
          : '',
      }));
      setStage('layers');
    },
    [upsertWork, setStage],
  );

  const importPSD = useCallback(
    async (file: File | null) => {
      if (!file || segmentation.importing) return;
      setSegmentation((prev) => ({ ...prev, importing: true, error: null }));
      try {
        const result = await apiClient.importPSD(file, segmentation.importPreset);
        applyImportResult(result);
      } catch (err) {
        setSegmentation((prev) => ({
          ...prev,
          error: err instanceof Error ? err.message : 'PSD 导入失败',
        }));
      } finally {
        setSegmentation((prev) => ({ ...prev, importing: false }));
      }
    },
    [segmentation.importing, segmentation.importPreset, applyImportResult],
  );

  const importPNGs = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0 || segmentation.importing) return;
      setSegmentation((prev) => ({ ...prev, importing: true, error: null }));
      try {
        const result = await apiClient.importPNGs(Array.from(files));
        applyImportResult(result);
      } catch (err) {
        setSegmentation((prev) => ({
          ...prev,
          error: err instanceof Error ? err.message : 'PNG 导入失败',
        }));
      } finally {
        setSegmentation((prev) => ({ ...prev, importing: false }));
      }
    },
    [segmentation.importing, applyImportResult],
  );

  // ---------- 导出 ----------
  const toggleExportFormat = useCallback((format: ExportFormat) => {
    if (!EXPORT_FORMATS.includes(format)) return;
    setExportState((prev) => ({
      ...prev,
      formats: prev.formats.includes(format)
        ? prev.formats.filter((item) => item !== format)
        : [...prev.formats, format],
    }));
  }, []);

  const resetExport = useCallback(() => {
    setExportState((prev) => ({ ...prev, job: null, error: null, blocker: '' }));
  }, []);

  const startExport = useCallback(async () => {
    const work = current;
    if (!work) {
      setExportState((prev) => ({ ...prev, error: '先选择一个作品再导出。' }));
      return;
    }
    const formats = exportState.formats;
    if (formats.length === 0) {
      setExportState((prev) => ({ ...prev, error: '至少选择一种导出格式。' }));
      return;
    }
    if (exportState.job?.status === 'processing') return;

    const missing: string[] = [];
    if (formats.includes('live2d-package') && !work.layersDir) missing.push('Live2D 包需要分层目录');
    if (formats.includes('psd') && !work.imagePath) missing.push('PSD 分层方案需要主图路径');
    if (missing.length > 0) {
      setExportState((prev) => ({ ...prev, error: missing.join('；') }));
      return;
    }

    const characterName =
      (work.characterId && characters.find((item) => item.id === work.characterId)?.name) || '当前作品';
    const startedAt = new Date().toISOString();
    setExportState((prev) => ({
      ...prev,
      error: null,
      blocker: '',
      job: {
        id: `job-${Date.now()}`,
        characterId: work.characterId,
        characterName,
        formats,
        status: 'processing',
        progress: 0,
        createdAt: startedAt,
      },
    }));

    // 真实进度：导出期间 Go 向 /api/ws 广播 Python 阶段事件（StageTracker）
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(getBackendWsUrl('/api/ws'));
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as { type?: string; progress?: number };
          if (msg.type === 'progress' && typeof msg.progress === 'number' && msg.progress > 0) {
            setExportState((prev) =>
              prev.job && prev.job.status === 'processing'
                ? { ...prev, job: { ...prev.job, progress: Math.min(99, msg.progress!) } }
                : prev,
            );
          }
        } catch {
          /* 忽略坏帧 */
        }
      };
    } catch {
      /* WS 不可用时退化为无过程进度 */
    }

    const downloads: ExportDownload[] = [];
    let errorMessage: string | null = null;
    let blocker = '';
    let model3Json = '';
    let model3Url = '';
    let modelDir = '';
    let runtimeReady: boolean | null = null;
    let psdPath = '';
    let psdUrl = '';

    try {
      for (const format of formats) {
        try {
          if (format === 'psd') {
            // PSD 分层规划基于立绘本身：传的是主图路径，不是分层目录。
            const psd = await apiClient.exportPSDPlan(work.imagePath);
            if (!psd.success) throw new Error('PSD 分层方案生成失败');
            const summary = JSON.stringify(
              { layers: psd.layers, plan: psd.plan, plan_dir: psd.plan_dir, applied: psd.applied },
              null,
              2,
            );
            downloads.push({
              format,
              filename: `${safeFileName(characterName)}_psd-plan.json`,
              size: summary.length,
              url: `data:application/json;charset=utf-8,${encodeURIComponent(summary)}`,
            });
            if (psd.psd_path) {
              psdPath = psd.psd_path;
              psdUrl = outputUrlFromPath(psd.psd_path);
            }
          } else {
            const result = await apiClient.exportModel(
              work.characterId || '',
              format,
              work.layersDir || undefined,
            );
            if (!result.success) {
              throw new Error(result.blocker || '模型导出失败');
            }
            const url = modelUrlFromPath(result.model3_json || '');
            if (url) {
              model3Url = url;
              model3Json = result.model3_json || '';
              modelDir = result.output_dir || '';
            }
            if (typeof result.runtime_ready === 'boolean') runtimeReady = result.runtime_ready;
            if (result.runtime_ready === false && result.blocker) blocker = result.blocker;
            if (!result.output_dir) {
              throw new Error('导出没有返回模型目录，无法打包 zip');
            }
            downloads.push({
              format,
              filename: `${safeFileName(characterName)}_live2d.zip`,
              size: 0,
              sizePending: true,
              url: '',
              modelDir: result.output_dir,
              characterId: work.characterId,
            });
          }
        } catch (err) {
          errorMessage = err instanceof Error ? err.message : `导出失败：${format}`;
        }
      }
    } finally {
      if (ws) {
        ws.onmessage = null;
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    }

    const completed: ExportJob = {
      id: `job-${Date.now()}`,
      characterId: work.characterId,
      characterName,
      formats,
      status: downloads.length > 0 && !errorMessage ? 'done' : 'error',
      progress: 100,
      downloads,
      createdAt: startedAt,
      completedAt: new Date().toISOString(),
      error: downloads.length > 0 && !errorMessage ? undefined : errorMessage || '导出失败',
    };

    setExportState((prev) => ({ ...prev, job: completed, blocker, error: errorMessage }));

    if (model3Json || modelDir || psdPath) {
      setWorks((prev) =>
        prev.map((item) =>
          item.id === work.id
            ? {
                ...item,
                model3Json: model3Json || item.model3Json,
                model3Url: model3Url || item.model3Url,
                modelDir: modelDir || item.modelDir,
                psdPath: psdPath || item.psdPath,
                psdUrl: psdUrl || item.psdUrl,
              }
            : item,
        ),
      );
    }
    if (runtimeReady === false && blocker) {
      setExportState((prev) => ({ ...prev, blocker }));
    }
  }, [current, characters, exportState.formats, exportState.job?.status]);

  const downloadExport = useCallback(
    async (download: ExportDownload) => {
      if (download.modelDir) {
        if (exportState.packingFormat) return;
        setExportState((prev) => ({ ...prev, packingFormat: download.format }));
        let objectUrl = '';
        try {
          const { blob, filename } = await apiClient.exportModelZip(
            download.characterId || current?.characterId || '',
            { modelDir: download.modelDir },
          );
          objectUrl = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = objectUrl;
          a.download = filename;
          a.click();
          setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
        } catch (err) {
          if (objectUrl) URL.revokeObjectURL(objectUrl);
          const message = err instanceof Error ? err.message : 'zip 打包下载失败';
          // 下载被后端拒绝（例如层序闸门）时，这次导出不能继续显示「完成」：
          // 唯一的交付物拿不到，成功结论就是假的。
          setExportState((prev) => ({
            ...prev,
            error: message,
            job: prev.job
              ? { ...prev.job, status: 'error', error: `${download.filename}：${message}` }
              : prev.job,
          }));
        } finally {
          setExportState((prev) => ({ ...prev, packingFormat: null }));
        }
        return;
      }
      const a = document.createElement('a');
      a.href = download.url;
      a.download = download.filename;
      a.click();
    },
    [exportState.packingFormat, current?.characterId],
  );

  // ---------- 桌宠 ----------
  const launchPet = useCallback(async () => {
    if (pet.busy) return;
    const modelDir = current?.modelDir || '';
    if (!modelDir) {
      setPet({ busy: false, message: '当前作品还没有可部署的模型目录 —— 先在「导出」完成一次打包。' });
      return;
    }
    setPet({ busy: true, message: '正在验收模型并启动桌宠…' });
    try {
      await apiClient.deployDesktop(modelDir);
      setPet({ busy: false, message: '桌宠已启动并通过首帧验收；透明外观请在桌面确认。' });
    } catch (err) {
      setPet({ busy: false, message: err instanceof Error ? err.message : '桌宠启动失败' });
    }
  }, [pet.busy, current?.modelDir]);

  const appendPromptTag = useCallback((tag: string) => {
    setPrompt((prev) => appendPromptTagRaw(prev, tag));
  }, []);

  const value = useMemo<WorkbenchValue>(
    () => ({
      hydrated,
      works,
      worksLoading,
      current,
      characters,
      refreshWorks,
      selectWork,
      startNew,
      stage,
      inspector,
      setStage,
      setInspector,
      prompt,
      setPrompt,
      negativePrompt,
      setNegativePrompt,
      resolution,
      setResolution,
      consistency,
      setConsistency,
      characterId,
      setCharacterId,
      appendPromptTag,
      generation,
      generate,
      cancelGenerate,
      layers,
      layersLoading,
      layersError,
      layersPreviewUrl,
      selectedLayerId,
      selectedLayer,
      setSelectedLayerId,
      updateLayer,
      toggleLayerVisibility,
      moveLayer,
      sourceUpload,
      setSourceFile: setSourceFileState,
      segmentation,
      setSegmentationMethod,
      setImportPreset,
      runSegmentation,
      importPSD,
      importPNGs,
      exportState,
      toggleExportFormat,
      startExport,
      downloadExport,
      resetExport,
      pet,
      launchPet,
    }),
    [
      hydrated,
      works,
      worksLoading,
      current,
      characters,
      refreshWorks,
      selectWork,
      startNew,
      stage,
      inspector,
      setStage,
      setInspector,
      prompt,
      negativePrompt,
      resolution,
      consistency,
      characterId,
      appendPromptTag,
      generation,
      generate,
      cancelGenerate,
      layers,
      layersLoading,
      layersError,
      layersPreviewUrl,
      selectedLayerId,
      selectedLayer,
      updateLayer,
      toggleLayerVisibility,
      moveLayer,
      sourceUpload,
      setSourceFileState,
      segmentation,
      setSegmentationMethod,
      setImportPreset,
      runSegmentation,
      importPSD,
      importPNGs,
      exportState,
      toggleExportFormat,
      startExport,
      downloadExport,
      resetExport,
      pet,
      launchPet,
    ],
  );

  return <WorkbenchContext.Provider value={value}>{children}</WorkbenchContext.Provider>;
}

export function useWorkbench(): WorkbenchValue {
  const value = useContext(WorkbenchContext);
  if (!value) throw new Error('useWorkbench 必须在 WorkbenchProvider 内使用');
  return value;
}
