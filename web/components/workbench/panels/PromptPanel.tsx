import { KeyboardEvent } from 'react';
import { AlertTriangle, Info, Link2, Link2Off, Loader2, Wand2, XCircle } from 'lucide-react';
import { useWorkbench } from '../../../lib/workbench-context';
import { codePointLength, isGenerateShortcut, sliceCodePoints } from '../../../lib/ime';
import {
  PROMPT_ENGINES,
  PROMPT_TAG_GROUPS as TAG_GROUPS,
  PROMPT_TEMPLATES as TEMPLATES,
  RESOLUTIONS,
} from '../../../lib/catalog';

const MAX_PROMPT = 2000;

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
    engine,
    setEngine,
    imageModel,
    setImageModel,
    lora,
    setLora,
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
        <div className="mb-1.5 flex items-center justify-between">
          <label htmlFor="prompt-input" className="section-label">
            提示词 <span className="text-haze-600">（中文直接写）</span>
          </label>
          <span
            className={`text-[10px] tabular-nums ${
              length > MAX_PROMPT * 0.9 ? 'text-amber-600' : 'text-haze-600'
            }`}
          >
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
          className="field resize-none text-sm leading-relaxed"
        />
        <p className="mt-1 text-[10px] text-haze-600">Ctrl/⌘ + Enter 开始生成 · 输入法组词时的回车不会误触</p>

        <div className="mt-3 space-y-2">
          {TAG_GROUPS.map((group) => (
            <div key={group.group} className="flex items-start gap-2">
              <span className="w-8 shrink-0 pt-0.5 text-[10px] text-haze-600">{group.group}</span>
              <div className="flex flex-wrap gap-1">
                {group.tags.map((tag) => (
                  <button key={tag} onClick={() => appendPromptTag(tag)} className="chip px-2 py-0.5 text-[11px]">
                    {tag}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-3 flex items-start gap-2">
          <span className="shrink-0 pt-1 text-[10px] text-haze-600">模板</span>
          <div className="flex flex-wrap gap-1">
            {TEMPLATES.map((template) => (
              <button
                key={template.name}
                onClick={() => setPrompt(template.prompt)}
                title={`用模板替换当前提示词：${template.prompt}`}
                className="chip border-iris-500/25 bg-iris-500/[0.06] px-2 py-1 text-[11px] text-iris-600 hover:border-iris-500/45 hover:text-iris-200"
              >
                {template.name}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section>
        <label htmlFor="negative-prompt" className="section-label mb-1.5 block">
          负向提示词
        </label>
        <textarea
          id="negative-prompt"
          value={negativePrompt}
          onChange={(e) => setNegativePrompt(e.target.value)}
          rows={2}
          placeholder="低清晰度，错误的人体结构，多余手指，模糊…"
          className="field resize-none leading-relaxed"
        />
      </section>

      <section>
        <div className="section-label mb-1.5">分辨率</div>
        <div className="flex gap-1 rounded-xl border border-black/[0.07] bg-ink-950/50 p-1">
          {RESOLUTIONS.map((r) => (
            <button
              key={r}
              onClick={() => setResolution(r)}
              className={`flex-1 rounded-lg py-1.5 text-xs transition-colors ${
                resolution === r
                  ? 'bg-sakura-500/15 text-sakura-600 ring-1 ring-inset ring-sakura-500/30'
                  : 'text-haze-400 hover:bg-black/[0.04] hover:text-haze-200'
              }`}
            >
              <span className="tabular-nums">{r}²</span>
            </button>
          ))}
        </div>
      </section>

      <section>
        <div className="section-label mb-1.5">生图引擎</div>
        <select value={engine} onChange={(e) => setEngine(e.target.value)} className="field">
          {PROMPT_ENGINES.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>

        {engine !== 'auto' && (
          <div className="mt-2 space-y-2">
            <div>
              <label htmlFor="engine-model" className="mb-1 block text-[10px] text-haze-500">
                底模文件名 <span className="text-haze-600">（留空 = 用引擎当前默认模型）</span>
              </label>
              <input
                id="engine-model"
                value={imageModel}
                onChange={(e) => setImageModel(e.target.value)}
                placeholder="例如 illustriousXL_v01.safetensors"
                className="field"
              />
            </div>
            <div>
              <label htmlFor="engine-lora" className="mb-1 block text-[10px] text-haze-500">
                LoRA 文件名 <span className="text-haze-600">（角色一致性，留空 = 不注入）</span>
              </label>
              <input
                id="engine-lora"
                value={lora}
                onChange={(e) => setLora(e.target.value)}
                placeholder="例如 char_style_v1.safetensors"
                className="field"
              />
            </div>
            <p className="text-[10px] leading-relaxed text-haze-600">
              底模放 models/checkpoints，LoRA 放 models/loras。6GB 显存建议先用 SD1.5
              二次元底模调提示词，定稿再换 SDXL（启动 ComfyUI 时加 --lowvram）。
            </p>
          </div>
        )}
      </section>

      <section>
        <button
          onClick={() => setConsistency(!consistency)}
          className={`flex w-full items-center justify-between rounded-xl border p-2.5 transition-colors ${
            consistency
              ? 'border-iris-500/40 bg-iris-500/[0.08]'
              : 'border-black/[0.07] bg-ink-950/40 hover:border-black/[0.14]'
          }`}
        >
          <span className="flex items-center gap-2 text-xs text-haze-200">
            {consistency ? (
              <Link2 className="h-4 w-4 text-iris-600" />
            ) : (
              <Link2Off className="h-4 w-4 text-haze-500" />
            )}
            角色一致性
          </span>
          <span
            className={`relative h-5 w-9 rounded-full transition-colors ${
              consistency ? 'bg-iris-500' : 'bg-black/15'
            }`}
          >
            <span
              className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                consistency ? 'left-[18px]' : 'left-0.5'
              }`}
            />
          </span>
        </button>
        {consistency && (
          <select
            value={characterId}
            onChange={(e) => setCharacterId(e.target.value)}
            className="field mt-2"
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
        <section className="card space-y-1 p-2.5">
          <div className="flex items-center gap-1.5 text-[11px] text-haze-400">
            <Info className="h-3.5 w-3.5" />
            上次实际发送
            <span className="rounded bg-black/[0.05] px-1 text-[10px] text-haze-300">
              {LANG_LABEL[current?.promptLang || 'unknown'] || current?.promptLang}
            </span>
          </div>
          <p className="break-words text-[11px] leading-relaxed text-haze-500">{current?.promptSent}</p>
          {current?.promptMismatch && (
            <p className="flex items-start gap-1 text-[11px] text-amber-700/90">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              上游生成服务对中文支持有限；原文不会被改写，但效果可能不稳。可改用英文关键词，或换用中文原生的生成模型。
            </p>
          )}
        </section>
      )}

      <section className="space-y-2">
        {generation.error && (
          <p className="flex items-start gap-1.5 rounded-xl border border-rose-400/30 bg-rose-500/10 px-2.5 py-2 text-[11px] text-rose-600">
            <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {generation.error}
          </p>
        )}
        {generation.message && !generation.error && (
          <p className="text-[11px] text-haze-500">{generation.message}</p>
        )}
        {generation.running ? (
          <button onClick={cancelGenerate} className="btn-danger w-full py-2.5 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> 停止生成
          </button>
        ) : (
          <button onClick={() => void generate()} disabled={!prompt.trim()} className="btn-primary w-full py-2.5 text-sm">
            <Wand2 className="h-4 w-4" /> 开始生成
          </button>
        )}
        {current?.layersDir && (
          <button onClick={() => setStage('layers')} className="btn-ghost w-full py-2">
            查看图层
          </button>
        )}
      </section>
    </div>
  );
}
