import { KeyboardEvent } from 'react';
import { AlertTriangle, Info, Link2, Link2Off, Loader2, Wand2, XCircle } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { codePointLength, isGenerateShortcut, sliceCodePoints } from '../../../lib/ime';
import type { Resolution } from '../../../types';

const MAX_PROMPT = 2000;
const RESOLUTIONS: Resolution[] = [512, 768, 1024, 2048];

const TAG_GROUPS: Array<{ group: string; tags: string[] }> = [
  { group: '风格', tags: ['日系动漫风格', '赛璐璐上色', '厚涂质感', '水彩风格', '赛博朋克', '国风插画'] },
  { group: '人物', tags: ['少女', '少年', '双马尾', '长发', '猫耳', '水手服', '和服', '异色瞳'] },
  { group: '表情', tags: ['微笑', '害羞', '温柔注视', '认真', '惊讶'] },
  { group: '构图', tags: ['半身像', '正面视角', '居中构图', '留白背景'] },
  { group: '光照', tags: ['柔和光影', '逆光', '边缘光', '午后阳光'] },
  { group: '质量', tags: ['高清细节', '精致五官', '清晰线条', '大师作品'] },
];

const TEMPLATES: Array<{ name: string; prompt: string }> = [
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

const LANG_LABEL: Record<string, string> = {
  zh: '中文',
  en: '英文',
  ja: '日文',
  mixed: '中英混合',
  unknown: '未知',
};

export default function PromptPanel() {
  const {
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
    characters,
    current,
    appendPromptTag,
    generation,
    generate,
    cancelGenerate,
    setStage,
  } = useWorkbench();

  const length = codePointLength(prompt);
  const sentDiffers = !!current?.promptSent && current.promptSent !== prompt.trim();

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!isGenerateShortcut(e)) return;
    e.preventDefault();
    if (!generation.running && prompt.trim()) void generate();
  };

  return (
    <div className="space-y-5">
      <section>
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="prompt-input" className="text-xs font-medium text-gray-400">
            提示词 <span className="text-gray-600">（中文直接写）</span>
          </label>
          <span className={`text-[10px] tabular-nums ${length > MAX_PROMPT * 0.9 ? 'text-amber-400' : 'text-gray-600'}`}>
            {length}/{MAX_PROMPT}
          </span>
        </div>
        <textarea
          id="prompt-input"
          value={prompt}
          onChange={(e) => setPrompt(sliceCodePoints(e.target.value, MAX_PROMPT))}
          onKeyDown={onKeyDown}
          rows={6}
          placeholder="例如：一位粉色双马尾少女，蓝色眼睛，身穿水手服，日系动漫风格，柔和光影，半身像，高清细节"
          className="w-full px-3 py-2.5 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-pink-500 transition-colors resize-none leading-relaxed"
        />
        <p className="mt-1 text-[10px] text-gray-600">Ctrl/⌘ + Enter 开始生成 · 输入法组词时的回车不会误触</p>

        <div className="mt-3 space-y-2">
          {TAG_GROUPS.map((group) => (
            <div key={group.group} className="flex items-start gap-2">
              <span className="w-8 shrink-0 pt-1 text-[10px] text-gray-600">{group.group}</span>
              <div className="flex flex-wrap gap-1">
                {group.tags.map((tag) => (
                  <button
                    key={tag}
                    onClick={() => appendPromptTag(tag)}
                    className="px-1.5 py-0.5 rounded border border-gray-700 bg-gray-900 text-[11px] text-gray-400 hover:border-pink-500/50 hover:text-pink-300 transition-colors"
                  >
                    {tag}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-3 flex items-center gap-2">
          <span className="text-[10px] text-gray-600 shrink-0">模板</span>
          <div className="flex flex-wrap gap-1">
            {TEMPLATES.map((template) => (
              <button
                key={template.name}
                onClick={() => setPrompt(template.prompt)}
                title={`用模板替换当前提示词：${template.prompt}`}
                className="px-2 py-1 rounded-md border border-gray-700 bg-gray-900 text-[11px] text-gray-300 hover:border-purple-500/50 hover:text-purple-300 transition-colors"
              >
                {template.name}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section>
        <label htmlFor="negative-prompt" className="text-xs font-medium text-gray-400 block mb-1.5">
          负向提示词
        </label>
        <textarea
          id="negative-prompt"
          value={negativePrompt}
          onChange={(e) => setNegativePrompt(e.target.value)}
          rows={2}
          placeholder="低清晰度，错误的人体结构，多余手指，模糊…"
          className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-xs text-white placeholder:text-gray-600 focus:outline-none focus:border-pink-500 transition-colors resize-none"
        />
      </section>

      <section>
        <div className="text-xs font-medium text-gray-400 mb-1.5">分辨率</div>
        <div className="flex gap-1">
          {RESOLUTIONS.map((r) => (
            <button
              key={r}
              onClick={() => setResolution(r)}
              className={`flex-1 py-1.5 rounded-md text-xs border transition-colors tabular-nums ${
                resolution === r
                  ? 'bg-pink-500/20 border-pink-500/50 text-pink-300'
                  : 'bg-gray-900 border-gray-800 text-gray-400 hover:border-gray-700'
              }`}
            >
              {r}²
            </button>
          ))}
        </div>
      </section>

      <section>
        <button
          onClick={() => setConsistency(!consistency)}
          className={`w-full flex items-center justify-between p-2.5 rounded-lg border transition-colors ${
            consistency ? 'bg-purple-500/10 border-purple-500/40' : 'bg-gray-900 border-gray-800'
          }`}
        >
          <span className="flex items-center gap-2 text-xs text-gray-300">
            {consistency ? <Link2 className="w-4 h-4 text-purple-400" /> : <Link2Off className="w-4 h-4 text-gray-500" />}
            角色一致性
          </span>
          <span className={`w-9 h-5 rounded-full relative transition-colors ${consistency ? 'bg-purple-500' : 'bg-gray-700'}`}>
            <span
              className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
                consistency ? 'left-[18px]' : 'left-0.5'
              }`}
            />
          </span>
        </button>
        {consistency && (
          <select
            value={characterId}
            onChange={(e) => setCharacterId(e.target.value)}
            className="mt-2 w-full px-2.5 py-2 bg-gray-900 border border-gray-700 rounded-lg text-xs text-white focus:outline-none focus:border-pink-500"
          >
            <option value="">— 选择角色（可选）—</option>
            {characters.map((character) => (
              <option key={character.id} value={character.id}>
                {character.name}
              </option>
            ))}
          </select>
        )}
      </section>

      {sentDiffers && (
        <section className="rounded-lg border border-gray-700 bg-gray-900/60 p-2.5 space-y-1">
          <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
            <Info className="w-3.5 h-3.5" />
            上次实际发送
            <span className="px-1 rounded bg-gray-800 text-[10px] text-gray-300">
              {LANG_LABEL[current?.promptLang || 'unknown'] || current?.promptLang}
            </span>
          </div>
          <p className="text-[11px] text-gray-500 leading-relaxed break-words">{current?.promptSent}</p>
          {current?.promptMismatch && (
            <p className="flex items-start gap-1 text-[11px] text-amber-400/90">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              上游生成服务对中文支持有限；原文不会被改写，但效果可能不稳。可改用英文关键词，或换用中文原生的生成模型。
            </p>
          )}
        </section>
      )}

      <section className="space-y-2">
        {generation.error && (
          <p className="flex items-start gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-2.5 py-2 text-[11px] text-red-300">
            <XCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            {generation.error}
          </p>
        )}
        {generation.message && !generation.error && (
          <p className="text-[11px] text-gray-500">{generation.message}</p>
        )}
        {generation.running ? (
          <button
            onClick={cancelGenerate}
            className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-red-500/10 border border-red-500/40 text-red-300 text-sm font-medium hover:bg-red-500/20 transition-colors"
          >
            <Loader2 className="w-4 h-4 animate-spin" /> 停止生成
          </button>
        ) : (
          <button
            onClick={() => void generate()}
            disabled={!prompt.trim()}
            className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-pink-500/30 transition-all"
          >
            <Wand2 className="w-4 h-4" /> 开始生成
          </button>
        )}
        {current?.layersDir && (
          <button
            onClick={() => setStage('layers')}
            className="w-full px-3 py-2 rounded-lg bg-gray-900 border border-gray-700 text-xs text-gray-300 hover:bg-gray-800 transition-colors"
          >
            查看图层
          </button>
        )}
      </section>
    </div>
  );
}
