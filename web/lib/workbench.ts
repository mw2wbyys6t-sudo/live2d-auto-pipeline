import type { GenerationRecord, LayersIndex, LatestGeneration } from './api-client';
import { modelDirFromRecord, modelUrlFromRecord, outputUrlFromPath } from './api-client';
import type {
  GenerationResult,
  GenerationStep,
  LayerInfo,
  PipelineStatus,
  Resolution,
} from '../types';

export type StageId = 'generate' | 'layers' | 'preview' | 'export';
export type InspectorTab = 'prompt' | 'layers' | 'params' | 'export';

export interface Workpiece {
  id: string;
  characterId: string;
  prompt: string;
  imagePath: string;
  imageUrl: string;
  layersDir: string;
  psdPath: string;
  psdUrl: string;
  model3Json: string;
  model3Url: string;
  modelDir: string;
  width: number;
  height: number;
  promptSent: string;
  effectivePrompt: string;
  promptLang: string;
  promptMismatch: boolean;
  createdAt: string;
}

export const STAGES: Array<{ id: StageId; label: string; hint: string }> = [
  { id: 'generate', label: '生成', hint: '输入中文提示词，产出角色立绘' },
  { id: 'layers', label: '分层', hint: '检查图层、顺序与 PSD 产物' },
  { id: 'preview', label: '预览', hint: '用参数与表情驱动 Live2D 模型' },
  { id: 'export', label: '导出', hint: '打包模型、PSD 或启动桌宠' },
];

export const INSPECTOR_TABS: Array<{ id: InspectorTab; label: string }> = [
  { id: 'prompt', label: '提示词' },
  { id: 'layers', label: '图层' },
  { id: 'params', label: '参数' },
  { id: 'export', label: '导出' },
];

/** 阶段与右栏标签是同一件事的两面：切阶段就切到对应操作面板。 */
export function tabForStage(stage: StageId): InspectorTab {
  return stage === 'generate' ? 'prompt' : stage === 'layers' ? 'layers' : stage === 'preview' ? 'params' : 'export';
}

export function stageForTab(tab: InspectorTab): StageId {
  return tab === 'prompt' ? 'generate' : tab === 'layers' ? 'layers' : tab === 'params' ? 'preview' : 'export';
}

/**
 * 作品身份：分层目录每次生成新建、唯一；output_dir 在角色工作流里就是产物根目录
 * （所有作品共用同一个值），拿它当 id 会让第二件作品覆盖第一件。
 */
function workId(candidates: Array<string | undefined>): string {
  for (const candidate of candidates) {
    if (candidate) return candidate;
  }
  return '';
}

export function workFromRecord(record: GenerationRecord | LatestGeneration): Workpiece | null {
  const id = workId([record.layers_dir, record.model3_json, record.output_dir, record.image_path]);
  if (!id) return null;
  return {
    id,
    characterId: record.character_id || '',
    prompt: record.prompt || '',
    imagePath: record.image_path || '',
    imageUrl: record.image_url || outputUrlFromPath(record.image_path || ''),
    layersDir: record.layers_dir || '',
    psdPath: record.psd_path || '',
    psdUrl: record.psd_url || outputUrlFromPath(record.psd_path || ''),
    model3Json: record.model3_json || '',
    model3Url: modelUrlFromRecord(record),
    modelDir: modelDirFromRecord(record),
    width: record.width || 1024,
    height: record.height || 1024,
    promptSent: record.prompt_sent || '',
    effectivePrompt: ('effective_prompt' in record && record.effective_prompt) || '',
    promptLang: record.prompt_lang || '',
    promptMismatch: !!record.prompt_mismatch,
    createdAt: record.created_at || '',
  };
}

export function workFromResult(result: GenerationResult, prompt: string): Workpiece | null {
  const id = workId([result.layersDir, result.model3Json, result.outputDir, result.imagePath]);
  if (!id) return null;
  return {
    id,
    characterId: result.characterId || '',
    prompt,
    imagePath: result.imagePath || '',
    imageUrl: result.imageUrl || outputUrlFromPath(result.imagePath || ''),
    layersDir: result.layersDir || '',
    psdPath: result.psdPath || '',
    psdUrl: result.psdUrl || outputUrlFromPath(result.psdPath || ''),
    model3Json: result.model3Json || '',
    model3Url: result.model3Url || '',
    modelDir: result.outputDir || '',
    width: result.width || 1024,
    height: result.height || 1024,
    promptSent: result.promptSent || '',
    effectivePrompt: result.effectivePrompt || '',
    promptLang: result.promptLang || '',
    promptMismatch: !!result.promptMismatch,
    createdAt: result.createdAt || new Date().toISOString(),
  };
}

/** 阶段是否已有可展示的产物（用于阶段条上的完成勾）。缺产物就是缺，不假装。 */
export function stageReady(work: Workpiece | null, stage: StageId): boolean {
  if (!work) return false;
  switch (stage) {
    case 'generate':
      return !!work.imageUrl;
    case 'layers':
      return !!work.layersDir;
    case 'preview':
      return !!work.model3Url;
    case 'export':
      // 导出可以直接从分层目录构建，不必先有已导出的模型。
      return !!work.model3Url || !!work.layersDir;
  }
}

/** 流水线总进度：已完成步骤 + 当前步骤内部进度，封顶 99（完成后单独置 100）。 */
export function overallProgress(steps: GenerationStep[]): number {
  if (steps.length === 0) return 0;
  if (steps.every((step) => step.status === 'done')) return 100;
  const done = steps.filter((step) => step.status === 'done').length;
  const active = steps.find((step) => step.status === 'active');
  const fraction = (done + (active ? Math.min(100, Math.max(0, active.progress)) / 100 : 0)) / steps.length;
  return Math.min(99, Math.round(fraction * 100));
}

export function activeStepLabel(steps: GenerationStep[]): string {
  return steps.find((step) => step.status === 'active')?.label || '';
}

// ---------- 流水线步骤（中文） ----------

export const PIPELINE_STEPS: Array<{ id: PipelineStatus; label: string }> = [
  { id: 'queued', label: '排队' },
  { id: 'generating', label: '生成图像' },
  { id: 'qa', label: '质量检查' },
  { id: 'optimizing', label: '优化图像' },
  { id: 'segmenting', label: '语义分层' },
  { id: 'layering', label: '合成图层' },
  { id: 'rigging', label: '绑定模型' },
  { id: 'done', label: '完成' },
];

export function makeInitialSteps(): GenerationStep[] {
  return PIPELINE_STEPS.map((s) => ({
    id: s.id,
    label: s.label,
    status: 'pending',
    progress: 0,
  }));
}

// ---------- 图层 ----------

export function toLayerInfo(
  entries: Array<{ name: string; url?: string }>,
  width = 1024,
  height = 1024,
): LayerInfo[] {
  return entries.map((entry, index) => ({
    id: `layer-${index}-${entry.name}`,
    name: entry.name || `layer_${index}`,
    index,
    visible: true,
    opacity: 1,
    blendMode: 'normal',
    offsetX: 0,
    offsetY: 0,
    width,
    height,
    imageUrl: entry.url || '',
    // 分层 PNG 都是整幅画布（内容只在 bbox 内非透明），bounds 覆盖全图。
    bounds: { x: 0, y: 0, width, height },
    isGroup: false,
  }));
}

export function layersFromIndex(index: LayersIndex, width = 1024, height = 1024): LayerInfo[] {
  return toLayerInfo(
    index.layers.map((layer) => ({ name: layer.name, url: layer.url })),
    width,
    height,
  );
}

// ---------- 草稿持久化 ----------

const DRAFT_KEY = 'pygmalion.workbench.v1';

export interface WorkbenchDraft {
  prompt: string;
  negativePrompt: string;
  resolution: Resolution;
  consistency: boolean;
  characterId: string;
  stage: StageId;
  inspector: InspectorTab;
  currentId: string;
}

const STAGE_IDS: StageId[] = ['generate', 'layers', 'preview', 'export'];
const TAB_IDS: InspectorTab[] = ['prompt', 'layers', 'params', 'export'];

export function readDraft(): Partial<WorkbenchDraft> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Partial<WorkbenchDraft>;
    const draft: Partial<WorkbenchDraft> = {};
    if (typeof parsed.prompt === 'string') draft.prompt = parsed.prompt;
    if (typeof parsed.negativePrompt === 'string') draft.negativePrompt = parsed.negativePrompt;
    if ([512, 768, 1024, 2048].includes(parsed.resolution as number)) {
      draft.resolution = parsed.resolution;
    }
    if (typeof parsed.consistency === 'boolean') draft.consistency = parsed.consistency;
    if (typeof parsed.characterId === 'string') draft.characterId = parsed.characterId;
    if (STAGE_IDS.includes(parsed.stage as StageId)) draft.stage = parsed.stage;
    if (TAB_IDS.includes(parsed.inspector as InspectorTab)) draft.inspector = parsed.inspector;
    if (typeof parsed.currentId === 'string') draft.currentId = parsed.currentId;
    return draft;
  } catch {
    return {};
  }
}

export function writeDraft(draft: WorkbenchDraft): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // 隐私模式/配额上限：草稿存不下不影响当前会话使用
  }
}

// ---------- 中文提示词 ----------

/** 把标签追加到提示词：中文用「，」连接，已包含则不重复添加。 */
export function appendPromptTag(prompt: string, tag: string): string {
  const trimmed = prompt.trim();
  if (!trimmed) return tag;
  if (trimmed.includes(tag)) return prompt;
  const last = trimmed[trimmed.length - 1];
  const needsSeparator = !'，。！？；、,.!?;'.includes(last);
  return `${trimmed}${needsSeparator ? '，' : ''}${tag}`;
}

export function promptExcerpt(prompt: string, max = 20): string {
  if (!prompt) return '未记录提示词';
  const chars = Array.from(prompt);
  return chars.length <= max ? prompt : `${chars.slice(0, max).join('')}…`;
}

/** 作品时间：当天只显示时分，跨天带上月日。 */
export function formatWorkTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (d.toDateString() === now.toDateString()) return hm;
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}
