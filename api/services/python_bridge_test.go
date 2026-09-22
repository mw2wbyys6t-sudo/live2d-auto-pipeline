package services

import "testing"

// FF-16：Go→Python 路径/命令注入防护的回归钉子。
// 之前 validatePath 只做黑名单过滤，`..` 仅靠后续 os.Stat 的存在性兜底 ——
// 已存在的越界文件仍可被读取。

func TestValidatePathRejectsTraversal(t *testing.T) {
	cases := []string{
		"../secret.txt",
		"../../etc/passwd",
		"assets/../../.env",
		`..\..\windows\system32\config`,
		"a/../b",
	}
	for _, p := range cases {
		if err := validatePath(p); err == nil {
			t.Errorf("validatePath(%q) 应拒绝目录穿越，但通过了", p)
		}
	}
}

func TestValidatePathAcceptsNormalPaths(t *testing.T) {
	cases := []string{
		"/app/assets/characters/char_1.json",
		"assets/output/gen_001.png",
		"my..file.png", // 含 ".." 但不是独立路径段，必须放行
		"a/b/c.png",
	}
	for _, p := range cases {
		if err := validatePath(p); err != nil {
			t.Errorf("validatePath(%q) 不应被拒绝：%v", p, err)
		}
	}
}

func TestValidatePathRejectsShellMetaAndLeadingDash(t *testing.T) {
	for _, p := range []string{"", "a;rm -rf /", "a|b", "a$b", "a&b", "a*b", "-flag"} {
		if err := validatePath(p); err == nil {
			t.Errorf("validatePath(%q) 应被拒绝", p)
		}
	}
}
