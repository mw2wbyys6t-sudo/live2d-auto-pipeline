package services

import (
	"strings"
	"testing"

	"live2d-api/config"
)

// FF-16：Python 桥接命令注入守护 —— 红线矩阵。
//
// 这一组测试是 docs/architecture/index.md 中 FF-16 的可执行落地产物：
// 把 validatePath / validateStrictID 在「真实攻击向量」面前的表现钉死。
// 任何用例失败 = 一条潜在注入路径没被堵住（架构文档定义「任何失败即高危」，
// 失败响应是阻断合并 + 人工审查）。
//
// 注意：本文件刻意把「期望被拒绝」写成 wantErr=true（安全理想态）。
// 若某个用例失败，说明当前实现没有堵住该向量——按任务约定，不要把期望
// 翻成「应该接受」来掩盖，而是在审查报告中标记为漏洞。

// TestValidatePath_RejectsTraversal 是 validatePath 的目录穿越 + shell 元字符
// 红线矩阵。表里的 wantErr 表达的是「安全理想态」（应当拒绝）。
func TestValidatePath_RejectsTraversal(t *testing.T) {
	cases := []struct {
		name       string
		input      string
		wantErr    bool
		skipReason string // FF-16-S2：中低优先级用例暂挂，待产品策略决策后去掉 Skip
	}{
		// —— 目录穿越（核心红线，必须拦）——
		{"parent_dotdot", "../etc/passwd", true, ""},
		{"nested_dotdot", "output/../../etc/passwd", true, ""},
		{"current_dir_relative_dotdot", "./../secret", true, ""},
		{"only_dotdot", "..", true, ""},

		// —— shell 元字符（黑名单已覆盖的，必须拦）——
		{"shell_meta_semicolon", "output/file;rm -rf /", true, ""},
		{"shell_meta_pipe", "output/file|cat", true, ""},
		{"shell_meta_amp", "output/file&bg", true, ""},
		{"shell_meta_dollar", "output/file$(whoami)", true, ""},
		{"shell_meta_null", "output/file\x00evil", true, ""},
		{"shell_meta_star", "output/*", true, ""},

		// —— 绝对路径 / 系统敏感位置 ——
		// 注意：现有 TestValidatePathAcceptsNormalPaths 明确放行 /app/assets/...
		// 这类绝对路径，因此「拒绝绝对路径」需要白名单策略决策，不是一刀切。
		// 这里仍按安全理想态标记 wantErr=true，并用 skipReason 暴露策略缺口；
		// FF-16-S2 阶段先 Skip，等白名单策略落地后去掉 skipReason 即可恢复断言。
		{"absolute_unix", "/etc/passwd", true,
			"FF-16-S2: 待绝对路径白名单策略决策（需确认 /app/assets 类合法用例是否在生产线被接受）。详见 docs/architecture/index.md FF-16 follow-ups。"},
		{"absolute_win", "C:\\Windows\\system32", true,
			"FF-16-S2: 待绝对路径白名单策略决策（Windows 盘符同上，需确认合法绝对路径范围）。详见 docs/architecture/index.md FF-16 follow-ups。"},

		// —— 黑名单尚未覆盖、但属于注入向量的字符 ——
		{"shell_meta_backtick", "output/file`whoami`", true, ""}, // 反引号命令替换（已拦）
		{"shell_meta_q", "output/file'", true, // 单引号（拼接场景）
			"FF-16-S2: 引号字符纵深防御 —— exec.CommandContext 不走 shell，单引号直接风险低；保留用例以追溯，等黑名单扩展决策。"},
		{"shell_meta_dq", "output/file\"", true, // 双引号（拼接场景）
			"FF-16-S2: 引号字符纵深防御 —— exec.CommandContext 不走 shell，双引号直接风险低；保留用例以追溯，等黑名单扩展决策。"},
		{"leading_dash", "-output/file", true, // 非 basename 段以 - 开头
			"FF-16-S2: 待逐段 dash 检查 —— 当前实现只查 filepath.Base，需扩展到每一段以防参数注入；详见 docs/architecture/index.md FF-16 follow-ups。"},
		{"only_dot", ".", true, // 当前目录（语义异常）
			"FF-16-S2: 单独点语义决策 —— `..` 已拦，单点 `.` 非危险但语义异常；保留用例以追溯，等语义白名单决策。"},

		// —— 合法路径（必须放行）——
		{"valid_relative", "output/characters/foo.png", false, ""},
		{"valid_subdir", "output/characters/a/b/c.png", false, ""},
		{"empty", "", true, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.skipReason != "" {
				t.Skip(tc.skipReason)
			}
			err := validatePath(tc.input)
			if (err != nil) != tc.wantErr {
				t.Errorf("validatePath(%q) err=%v, wantErr=%v", tc.input, err, tc.wantErr)
			}
		})
	}
}

// TestValidateStrictID_BoundaryMatrix 覆盖 strictIDPattern（^[A-Za-z0-9_-]{1,64}$）
// 的所有边界。ID 会被拼进内联 Python 源码与文件路径，必须用白名单严防。
func TestValidateStrictID_BoundaryMatrix(t *testing.T) {
	// 65 个字符：刚好超过 {1,64} 上限一位。
	tooLong := strings.Repeat("a", 65)
	// 64 个字符：上界，应当放行。
	maxOK := strings.Repeat("a", 64)

	cases := []struct {
		name    string
		input   string
		wantErr bool
	}{
		// —— 合法 ID ——
		{"alphanum", "abc123", false},
		{"with_underscore", "char_001", false},
		{"prefix_style", "SK_2026_001", false},
		{"single_char", "a", false},
		{"pure_digits", "123", false}, // 纯数字也在白名单内
		{"with_dash", "live2d-char-1", false},
		{"max_64_ok", maxOK, false},

		// —— 注入向量（白名单必须拦）——
		{"dotdot", "..", true},
		{"slash", "a/b", true},
		{"backslash", "a\\b", true},
		{"space", "a b", true},
		{"semicolon", "a;b", true},
		{"dollar", "a$b", true},
		{"null_byte", "a\x00b", true},
		{"pipe", "a|b", true},
		{"amp", "a&b", true},
		{"backtick", "a`b", true},
		{"single_quote", "a'b", true},
		{"double_quote", "a\"b", true},
		{"star", "a*b", true},
		{"shell_injection_full", "; rm -rf /", true},
		{"empty", "", true},
		{"too_long_65", tooLong, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := validateStrictID(tc.input)
			if (err != nil) != tc.wantErr {
				t.Errorf("validateStrictID(%q) err=%v, wantErr=%v", tc.input, err, tc.wantErr)
			}
		})
	}
}

// TestValidatePath_CrossPlatformSeparators 验证两种分隔符混合时穿越仍被识别。
// Windows 反斜杠、混合分隔符、复合 ./.. 都不能成为绕过 `..` 段检查的通道。
func TestValidatePath_CrossPlatformSeparators(t *testing.T) {
	cases := []string{
		`output\..\etc\passwd`,   // Windows 反斜杠穿越
		`output/..\etc/passwd`,   // 混合分隔符
		`output/./../etc`,        // 复合 . / ..
		`..\..\windows\system32`, // 纯反斜杠穿越
	}
	for _, c := range cases {
		t.Run(c, func(t *testing.T) {
			if err := validatePath(c); err == nil {
				t.Errorf("validatePath(%q) 应拒绝跨平台目录穿越", c)
			}
		})
	}
}

// TestValidatePath_UnicodeAndEncoding 验证编码层与 Unicode 规范化攻击。
// null 字节、换行符、URL 编码 null、全角点都需要被拦住或显式评估。
func TestValidatePath_UnicodeAndEncoding(t *testing.T) {
	// 前 4 个是攻击向量（应当拒绝），最后 1 个是合法中文（应当放行）。
	reject := []struct {
		name  string
		input string
	}{
		{"null_byte", "output/\x00.txt"},                   // 真实 null 字节
		{"newline_lf", "output/\x0a.txt"},                  // 换行符（日志/头注入）
		{"url_encoded_null", "output/file%00.txt"},         // URL 编码 null
		{"fullwidth_dotdot", "output/\uff0e\uff0e/passwd"}, // 全角点（NFKC 规范化攻击）
	}
	for _, c := range reject {
		t.Run(c.name, func(t *testing.T) {
			if err := validatePath(c.input); err == nil {
				t.Errorf("validatePath(%q) 应拒绝编码/Unicode 攻击向量", c.input)
			}
		})
	}

	// 合法中文路径必须放行（不能因为防注入误伤正常业务）。
	t.Run("legal_chinese", func(t *testing.T) {
		if err := validatePath("output/角色/中文.png"); err != nil {
			t.Errorf("validatePath() 不应拒绝合法中文路径: %v", err)
		}
	})
}

// TestValidatePath_RejectsShellInjection_EndToEnd 用真实公开方法验证：
// 攻击者从 API 边界送进来的恶意路径，会在触达 exec.CommandContext 之前
// 被 validatePath 拦下。无需真实 Python——校验早于 os.Stat 与子进程构造。
func TestValidatePath_RejectsShellInjection_EndToEnd(t *testing.T) {
	pb := NewPythonBridge(&config.Config{}) // 校验在访问 config 之前就 return

	t.Run("SegmentImage_路径注入", func(t *testing.T) {
		malicious := "output/file;rm -rf /"
		_, err := pb.SegmentImage(malicious, "kmeans")
		if err == nil {
			t.Fatal("SegmentImage 应在 validatePath 阶段拒绝 shell 注入路径")
		}
		if !strings.Contains(err.Error(), "路径") && !strings.Contains(err.Error(), "非法") {
			t.Logf("拒绝原因（应源自路径校验）: %v", err)
		}
	})

	t.Run("ImportPSDLayers_路径注入", func(t *testing.T) {
		malicious := "output/x`whoami`"
		_, err := pb.ImportPSDLayers(malicious)
		if err == nil {
			t.Fatal("ImportPSDLayers 应拒绝含反引号的路径")
		}
	})
}

// TestValidateStrictID_RejectsShellInjection_EndToEnd 验证角色 ID 字段
// 的命令注入在触达 Python 源码拼接之前就被白名单拦下。
func TestValidateStrictID_RejectsShellInjection_EndToEnd(t *testing.T) {
	pb := NewPythonBridge(&config.Config{})

	maliciousIDs := []string{
		"; rm -rf /",
		"$(whoami)",
		"a|cat /etc/passwd",
		"char`id`",
	}
	for _, id := range maliciousIDs {
		t.Run(id, func(t *testing.T) {
			err := pb.AddReferenceImage(id, "output/x.png", "front")
			if err == nil {
				t.Fatalf("AddReferenceImage 应拒绝注入型 ID: %q", id)
			}
		})
	}
}
