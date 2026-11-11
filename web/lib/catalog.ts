import type { Emotion, ExportFormat, Resolution } from '../types';
import type { PreviewBackground } from './preview-runtime';

/**
 * UI 枚举的唯一来源。
 *
 * 这些列表以前散落在各个面板组件里，改一处要翻五个文件；统一收在这里，
 * 页面上只负责渲染。真正随数据变化的东西（作品、模型、分类、角色）一律走后端，
 * 不要往这里加。
 */

export const RESOLUTIONS: Resolution[] = [512, 768, 1024, 2048];

export const PROMPT_ENGINES: Array<{ id: string; label: string }> = [
  { id: 'auto', label: '自动选择（云端优先）' },
  { id: 'comfyui', label: 'ComfyUI（本地 · 免费 · 可微调）' },
  { id: 'sd_webui', label: 'SD WebUI（本地 · 免费 · 可微调）' },
];

export const PROMPT_TAG_GROUPS: Array<{ group: string; tags: string[] }> = [
  { group: '风格', tags: ['日系动漫风格', '赛璐璐上色', '厚涂质感', '水彩风格', '赛博朋克', '国风插画'] },
  { group: '人物', tags: ['少女', '少年', '双马尾', '长发', '猫耳', '水手服', '和服', '异色瞳'] },
  { group: '表情', tags: ['微笑', '害羞', '温柔注视', '认真', '惊讶'] },
  { group: '构图', tags: ['半身像', '正面视角', '居中构图', '留白背景'] },
  { group: '光照', tags: ['柔和光影', '逆光', '边缘光', '午后阳光'] },
  { group: '质量', tags: ['高清细节', '精致五官', '清晰线条', '大师作品'] },
];

export const PROMPT_TEMPLATES: Array<{ name: string; prompt: string }> = [
  {
    name: '甜美少女',
    prompt:
      '一位粉色双马尾少女，蓝色眼睛，身穿水手服，面带微笑，日系动漫风格，柔和光影，半身像，干净背景，高清细节',
  },
  {
    name: '古风少年',
    prompt:
      '一位古风少年，长发束冠，身穿青白色长袍，手持折扇，神情温和，国风插画，淡雅配色，半身像，留白背景，精致细节',
  },
  {
    name: '赛博少女',
    prompt:
      '一位银色短发的未来少女，机械义肢，霓虹灯背景，赛博朋克风格，冷色调边缘光，正面半身构图，高清细节',
  },
  {
    name: '温柔学姐',
    prompt:
      '一位温柔的大姐姐，栗色长发，针织毛衣，手捧书本，午后阳光，日系动画风格，柔和色调，半身像',
  },
];

export const EXPRESSIONS: Array<{ id: Emotion; label: string }> = [
  { id: 'neutral', label: '中性' },
  { id: 'happy', label: '开心' },
  { id: 'sad', label: '难过' },
  { id: 'angry', label: '生气' },
  { id: 'surprised', label: '惊讶' },
  { id: 'shy', label: '害羞' },
  { id: 'thinking', label: '思考' },
  { id: 'excited', label: '兴奋' },
];

export const PREVIEW_BACKGROUNDS: Array<{ id: PreviewBackground; label: string; css: string }> = [
  {
    id: 'transparent',
    label: '透明',
    css: 'bg-[repeating-conic-gradient(#ffffff_0%_25%,#f1ecf9_0%_50%)] bg-[length:14px_14px]',
  },
  { id: 'dark', label: '深色', css: 'bg-[#14101d]' },
  { id: 'light', label: '浅色', css: 'bg-[#f4f2f8]' },
  { id: 'sky', label: '天空', css: 'bg-gradient-to-b from-sky-300 to-indigo-200' },
];

export const LAYER_PRESETS: Array<{ id: 'minimal' | 'default' | 'full'; label: string }> = [
  { id: 'minimal', label: '精简' },
  { id: 'default', label: '标准' },
  { id: 'full', label: '完整' },
];

export const EXPORT_FORMATS: Array<{
  id: ExportFormat;
  label: string;
  hint: string;
  needsLayers: boolean;
}> = [
  {
    id: 'live2d-package',
    label: 'Live2D 模型包',
    hint: 'moc3 + 贴图 + model3.json，打包为 zip',
    needsLayers: true,
  },
  { id: 'psd', label: 'PSD 分层方案', hint: '按部位输出分层规划 JSON', needsLayers: false },
];

/** 启动页展示的四步流水线。工作台阶段轨道用的是 STAGES，不含生成之外的差异。 */
export const PIPELINE_FLOW = ['生成立绘', '语义分层', 'Live2D 绑定', '导出交付'];
