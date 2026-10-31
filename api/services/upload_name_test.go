package services

import (
	"strings"
	"testing"
	"unicode/utf8"
)

// 上传文件名的清洗规则。
//
// 实测（本机 output/uploads/）：用户 10-19 起上传的立绘全部存成
// `upload_1792410534611_________.png` —— 中文名被逐字折成下划线，
// 回看时完全认不出是哪张图。清洗的目的是**不让文件名参与路径构造**，
// 不是把非 ASCII 当危险字符。

func TestSanitizeUploadNameKeepsChinese(t *testing.T) {
	got := sanitizeUploadName("少女立绘")

	if got != "少女立绘" {
		t.Errorf("中文名应原样保留，实得 %q", got)
	}
}

func TestSanitizeUploadNameStillNeutralizesPathPieces(t *testing.T) {
	for _, in := range []string{
		"../../etc/passwd",
		`C:\Users\me\..\..\windows\system32`,
		"a\x00b.png",
		"..",
	} {
		got := sanitizeUploadName(in)
		if strings.ContainsAny(got, `/\.`) {
			t.Errorf("%q 清洗后仍含路径字符: %q", in, got)
		}
		if strings.HasPrefix(got, "..") {
			t.Errorf("%q 清洗后仍以点开头: %q", in, got)
		}
	}
}

func TestSanitizeUploadNameTruncatesByCodePoint(t *testing.T) {
	// 90 个汉字 = 270 字节。按字节裁 64 会切在汉字中间，落盘得到非法
	// UTF-8 字节序列，左栏标题跟着变乱码（与提示词按字节截断同一类缺陷）。
	long := strings.Repeat("银", 90)

	got := sanitizeUploadName(long)

	if n := utf8.RuneCountInString(got); n > 64 {
		t.Errorf("长度 = %d 码点，应裁到 64 以内", n)
	}
	if !utf8.ValidString(got) {
		t.Errorf("裁剪后不是合法 UTF-8: %q", got)
	}
}

func TestSanitizeUploadNameFallsBackForBlank(t *testing.T) {
	for _, in := range []string{"", "   ", "\t\n"} {
		if got := sanitizeUploadName(in); got != "image" {
			t.Errorf("空白名应退回 image，实得 %q", got)
		}
	}
}

func TestSanitizeUploadNameFoldsSpacesAndPunctuation(t *testing.T) {
	if got := sanitizeUploadName("my girl v2 (final)"); got != "my_girl_v2__final_" {
		t.Errorf("空格与括号应折成下划线，实得 %q", got)
	}
}
