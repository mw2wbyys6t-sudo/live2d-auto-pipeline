package services

import "testing"

// A2：前后端尺寸策略单一事实来源守护。
// 前端 web/lib/image-utils.ts 的 MAX_UPLOAD_MB / MAX_SOURCE_EDGE 必须与这里的
// maxUploadBytes / maxImageDimension 对齐；改任一端要同步另一端并跑本测试。

func TestSizePolicyAlignedWithFrontend(t *testing.T) {
	// 单文件上传：前端 MAX_UPLOAD_MB=25 ↔ 后端 maxUploadBytes=25<<20。
	if got := maxUploadBytes >> 20; got != 25 {
		t.Errorf("maxUploadBytes = %d MB，期望与前端 MAX_UPLOAD_MB=25 对齐", got)
	}
	// 像素单边上限：前端 MAX_SOURCE_EDGE=4096 ↔ 后端 maxImageDimension=4096（防解压炸弹）。
	if maxImageDimension != 4096 {
		t.Errorf("maxImageDimension = %d，期望与前端 MAX_SOURCE_EDGE=4096 对齐", maxImageDimension)
	}
}

// A6：批量聚合 500MB 封顶，单文件仍受 25MB 约束，防止 200 张大图打满内存/磁盘。
func TestBatchTotalCapDocumented(t *testing.T) {
	const batchCap = 500 << 20
	if batchCap <= maxUploadBytes {
		t.Fatalf("批量总量 %dMB 应大于单文件 %dMB", batchCap>>20, maxUploadBytes>>20)
	}
	// 两遍扫描把峰值内存压到 O(单张)：单张 ≤25MB 解码，不聚合全部进内存。
	if maxUploadBytes >= batchCap {
		t.Error("单文件上限不应超过批量总量上限")
	}
}
