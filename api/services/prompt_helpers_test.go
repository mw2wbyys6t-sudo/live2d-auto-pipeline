package services

import (
	"strings"
	"testing"
	"unicode/utf8"
)

func TestTruncatePromptRunesKeepsAscii(t *testing.T) {
	in := strings.Repeat("a", promptMaxRunes+500)
	out := truncatePromptRunes(in, promptMaxRunes)
	if len(out) != promptMaxRunes {
		t.Fatalf("ASCII 截断长度 = %d, want %d", len(out), promptMaxRunes)
	}
}

// 4000 个汉字是 12000 字节：旧实现按字节切，只剩 1333 个汉字且不保证边界。
func TestTruncatePromptRunesCountsCharactersNotBytes(t *testing.T) {
	in := strings.Repeat("雪", promptMaxRunes+1)
	out := truncatePromptRunes(in, promptMaxRunes)
	got := utf8.RuneCountInString(out)
	if got != promptMaxRunes {
		t.Fatalf("汉字截断后字符数 = %d, want %d", got, promptMaxRunes)
	}
	if !utf8.ValidString(out) {
		t.Fatal("截断结果不是合法 UTF-8（汉字被切掉半个）")
	}
	if strings.ContainsRune(out, '\uFFFD') {
		t.Fatal("截断结果含替换字符 U+FFFD")
	}
}

func TestTruncatePromptRunesKeepsShortPromptIntact(t *testing.T) {
	in := "正面全身立绘，粉色双马尾，蓝色眼睛，水手服，柔和光线，简洁白色背景"
	if out := truncatePromptRunes(in, promptMaxRunes); out != in {
		t.Fatalf("短提示词被改动: %q", out)
	}
}

// emoji 是 4 字节、UTF-16 里是两个码元：按字节或按 UTF-16 切都会产生半个字符。
func TestTruncatePromptRunesDoesNotSplitEmoji(t *testing.T) {
	in := strings.Repeat("🎀", 10)
	out := truncatePromptRunes(in, 7)
	if utf8.RuneCountInString(out) != 7 {
		t.Fatalf("emoji 截断后字符数 = %d, want 7", utf8.RuneCountInString(out))
	}
	if !utf8.ValidString(out) {
		t.Fatal("emoji 被截成非法 UTF-8")
	}
}

func TestTruncatePromptRunesExactLimitUnchanged(t *testing.T) {
	in := strings.Repeat("雪", promptMaxRunes)
	if out := truncatePromptRunes(in, promptMaxRunes); out != in {
		t.Fatal("恰好达到上限时不应改动提示词")
	}
}

func TestTruncatePromptRunesNonPositiveLimit(t *testing.T) {
	if out := truncatePromptRunes("雪", 0); out != "" {
		t.Fatalf("上限为 0 时应返回空串, got %q", out)
	}
}
