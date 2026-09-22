package services

import (
	"os"
	"path/filepath"
	"testing"

	"live2d-api/models"
)

// 回归钉子：estimateSizeMB 曾恒定返回 0，导致 maxSizeMB 容量上限形同虚设。
func TestEstimateSizeMBDoesNotDegenerateToZero(t *testing.T) {
	dir := t.TempDir()

	big := filepath.Join(dir, "big.png")
	if err := os.WriteFile(big, make([]byte, 3<<20), 0o600); err != nil { // 3 MiB
		t.Fatal(err)
	}
	if got := estimateSizeMB(&models.GenerateImageResponse{ImagePath: big}); got != 3 {
		t.Errorf("3MiB 文件应估算为 3，实为 %d", got)
	}

	small := filepath.Join(dir, "small.png")
	if err := os.WriteFile(small, make([]byte, 10), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := estimateSizeMB(&models.GenerateImageResponse{ImagePath: small}); got != 1 {
		t.Errorf("小文件应向上取整为 1（而非 0），实为 %d", got)
	}
}

func TestEstimateSizeMBBounds(t *testing.T) {
	if got := estimateSizeMB(nil); got != 0 {
		t.Errorf("nil 应为 0，实为 %d", got)
	}
	// 无路径 / 路径不可达：必须给保守下限，绝不能是 0
	if got := estimateSizeMB(&models.GenerateImageResponse{}); got != 1 {
		t.Errorf("无路径条目应为保守下限 1，实为 %d", got)
	}
	missing := &models.GenerateImageResponse{
		ImagePath: filepath.Join(t.TempDir(), "does-not-exist.png")}
	if got := estimateSizeMB(missing); got != 1 {
		t.Errorf("文件不可达时应为 1，实为 %d", got)
	}
}
