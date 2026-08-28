package services

import "testing"

// TestDetectEmotion 验证关键词情绪检测（与 Python EmotionAnalyzer 对齐）。
func TestDetectEmotion(t *testing.T) {
	cases := map[string]string{
		"今天好开心呀哈哈":       "happy",
		"我好难过，想哭":          "sad",
		"哼，生气了！讨厌":         "angry",
		"哎呀，好害羞，脸红了":     "shy",
		"哇！真的假的！天哪":       "surprised",
		"好的，我明白了":          "calm",
		"今天天气不错":            "neutral",
	}
	for text, want := range cases {
		got := detectEmotion(text)
		if got != want {
			t.Errorf("detectEmotion(%q) = %q, want %q", text, got, want)
		}
	}
}
