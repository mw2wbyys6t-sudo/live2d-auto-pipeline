import type { KeyboardEvent } from 'react';

/**
 * 中文输入法正在组词时的回车不能当「发送/生成」：那是用户按下的选词键。
 * Safari 旧版没有 isComposing，用 keyCode 229 兜底（组词中的按键统一报 229）。
 */
export function isImeComposing(e: KeyboardEvent<HTMLElement>): boolean {
  const native = e.nativeEvent as KeyboardEvent<HTMLTextAreaElement>['nativeEvent'] & {
    isComposing?: boolean;
    keyCode?: number;
  };
  return Boolean(native?.isComposing) || native?.keyCode === 229;
}

/** Ctrl/⌘ + Enter 生成；输入法组词中一律忽略。 */
export function isGenerateShortcut(e: KeyboardEvent<HTMLElement>): boolean {
  return e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !isImeComposing(e);
}

/** 回车发送（聊天）：组词中的回车放行给输入法。 */
export function isSendEnter(e: KeyboardEvent<HTMLElement>): boolean {
  return e.key === 'Enter' && !e.shiftKey && !isImeComposing(e);
}

/** 按 Unicode 码点计数：汉字、emoji 都算一个字符，和 Python/Go 侧一致。 */
export function codePointLength(value: string): number {
  return Array.from(value).length;
}

/** 按码点截断，绝不切在汉字或 emoji 中间。 */
export function sliceCodePoints(value: string, max: number): string {
  const chars = Array.from(value);
  return chars.length <= max ? value : chars.slice(0, max).join('');
}
