package services

import "testing"

// TestPreviewURL 验证 model3 路径到 /api/preview/ URL 的转换与穿越防护。
func TestPreviewURL(t *testing.T) {
	base := "/workspace/output"
	cases := []struct{ in, want string }{
		{"/workspace/output/rigged_1/char.model3.json", "/api/preview/rigged_1/char.model3.json"},
		{"/workspace/output/rigged_1/sub/a.model3.json", "/api/preview/rigged_1/sub/a.model3.json"},
		{"/etc/passwd", ""},                 // 在 base 之外 → 空
		{"/workspace/output/../secret", ""}, // 穿越 → 空
		{"", ""},                            // 空
	}
	for _, c := range cases {
		got := previewURL(base, c.in)
		if got != c.want {
			t.Errorf("previewURL(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}
