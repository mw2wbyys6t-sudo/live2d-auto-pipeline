package config

import (
	"os"
	"path/filepath"
	"testing"
)

// 产品改名后，老用户已有的数据目录必须仍被读到：
// 必须仍被读到：新位置优先，旧位置存在即沿用。

func TestResolveDataRootPrefersNewName(t *testing.T) {
	root := t.TempDir()
	got := resolveDataRoot("windows", root, filepath.Join(root, "home"))
	want := filepath.Join(root, appDirName)
	if got != want {
		t.Fatalf("空机器应落在新目录 %s，实际 %s", want, got)
	}
}

func TestResolveDataRootKeepsLegacyWindowsDir(t *testing.T) {
	root := t.TempDir()
	legacy := filepath.Join(root, legacyDirNameOld)
	if err := os.MkdirAll(filepath.Join(legacy, "characters"), 0o755); err != nil {
		t.Fatalf("构造旧目录失败: %v", err)
	}

	got := resolveDataRoot("windows", root, filepath.Join(root, "home"))
	if got != legacy {
		t.Fatalf("已有 %s 时必须继续沿用，实际返回 %s", legacyDirNameOld, got)
	}
}

func TestResolveDataRootKeepsLegacyKebabDir(t *testing.T) {
	root := t.TempDir()
	legacy := filepath.Join(root, legacyDirNameKebab)
	if err := os.MkdirAll(legacy, 0o755); err != nil {
		t.Fatalf("构造旧目录失败: %v", err)
	}

	got := resolveDataRoot("windows", root, filepath.Join(root, "home"))
	if got != legacy {
		t.Fatalf("改名前的 kebab 目录也应被认出来，实际返回 %s", got)
	}
}

func TestResolveDataRootNewWinsWhenBothExist(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{appDirName, legacyDirNameOld} {
		if err := os.MkdirAll(filepath.Join(root, name), 0o755); err != nil {
			t.Fatalf("构造目录失败: %v", err)
		}
	}

	got := resolveDataRoot("windows", root, filepath.Join(root, "home"))
	if got != filepath.Join(root, appDirName) {
		t.Fatalf("两边都在时应走新目录，实际 %s", got)
	}
}

func TestResolveDataRootUnixLegacyDotDir(t *testing.T) {
	root := t.TempDir()
	home := filepath.Join(root, "home")
	legacy := filepath.Join(home, legacyDirNameDot)
	if err := os.MkdirAll(legacy, 0o755); err != nil {
		t.Fatalf("构造旧目录失败: %v", err)
	}

	got := resolveDataRoot("linux", "", home)
	if got != legacy {
		t.Fatalf("Unix 上 legacy 目录 ~/.live2d-master-agent 必须仍可用，实际 %s", got)
	}
}

func TestResolveDocumentsRootKeepsLegacyDir(t *testing.T) {
	root := t.TempDir()
	docs := filepath.Join(root, "Documents")
	legacy := filepath.Join(docs, legacyDirNameOld)
	if err := os.MkdirAll(filepath.Join(legacy, "output"), 0o755); err != nil {
		t.Fatalf("构造旧产物目录失败: %v", err)
	}

	got := resolveDocumentsRoot("windows", root, root)
	if got != legacy {
		t.Fatalf("已有文档目录必须沿用（否则用户的导出会「消失」），实际 %s", got)
	}
	if new := filepath.Join(docs, appDirName); got == new {
		t.Fatal("新目录不存在时不应凭空切过去")
	}
}

func TestDocumentsAndDataRootsAreDistinctDirectories(t *testing.T) {
	// 产物落「文档」、配置落 APPDATA 是既定布局；改名不能把两者并成一个，
	// 否则卸载/清理指引会写错。
	root := t.TempDir()
	data := resolveDataRoot("windows", root, filepath.Join(root, "home"))
	docs := resolveDocumentsRoot("windows", root, root)
	if data == docs {
		t.Fatalf("数据根与文档根不应相同: %s", data)
	}
	if filepath.Base(data) != appDirName || filepath.Base(docs) != appDirName {
		t.Errorf("两处目录名都应是 %s，实际 %s / %s", appDirName, filepath.Base(data), filepath.Base(docs))
	}
}

func TestResolveDataRootFreshUnixUsesDotName(t *testing.T) {
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatalf("构造 home 失败: %v", err)
	}
	got := resolveDataRoot("linux", "", home)
	if got != filepath.Join(home, appDirNameUnix) {
		t.Fatalf("Unix 空机器应落在 %s，实际 %s", appDirNameUnix, got)
	}
}
