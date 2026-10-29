package services

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func writeLayerGuide(t *testing.T, dir string, guide map[string]interface{}) {
	t.Helper()
	data, err := json.MarshalIndent(guide, "", "  ")
	if err != nil {
		t.Fatalf("序列化 layer_guide 失败: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "layer_guide.json"), data, 0o644); err != nil {
		t.Fatalf("写入 layer_guide 失败: %v", err)
	}
}

// 分层索引把 layer_guide.json 翻译成前端可直接加载的 URL 列表，
// 顺序以 draw_index 为准（PSD 的绘制顺序），而不是文件系统顺序。
func TestListLayersFromGuide(t *testing.T) {
	cfg := historyTestConfig(t)
	dir := filepath.Join(cfg.Output.BaseDir, "layers_100")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("创建分层目录失败: %v", err)
	}
	for _, name := range []string{"hair_back.png", "face_base.png", "composite_preview.png"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("png"), 0o644); err != nil {
			t.Fatalf("写入图层失败: %v", err)
		}
	}
	writeLayerGuide(t, dir, map[string]interface{}{
		"layers": []map[string]interface{}{
			{"name": "face_base", "draw_index": 1, "path": filepath.Join(dir, "face_base.png"), "bbox": []int{10, 20, 30, 40}, "size": []int{100, 120}},
			{"name": "hair_back", "draw_index": 0, "path": filepath.Join(dir, "hair_back.png"), "bbox": []int{0, 0, 50, 60}, "size": []int{80, 90}},
		},
	})

	index, err := ListLayers(cfg, dir)
	if err != nil {
		t.Fatalf("列举图层失败: %v", err)
	}
	if index.LayerCount != 2 {
		t.Fatalf("图层数 = %d", index.LayerCount)
	}
	if index.Layers[0].Name != "hair_back" || index.Layers[1].Name != "face_base" {
		t.Fatalf("图层顺序应按 draw_index：%v", []string{index.Layers[0].Name, index.Layers[1].Name})
	}
	want := "/output/layers_100/hair_back.png"
	if index.Layers[0].URL != want {
		t.Fatalf("图层 URL = %q，应为 %q", index.Layers[0].URL, want)
	}
	if index.PreviewURL != "/output/layers_100/composite_preview.png" {
		t.Fatalf("预览 URL = %q", index.PreviewURL)
	}
}

// 没有 guide 时退化为扫描 PNG（排除预览图），至少让老产物还能看。
func TestListLayersFallsBackToPNGs(t *testing.T) {
	cfg := historyTestConfig(t)
	dir := filepath.Join(cfg.Output.BaseDir, "layers_200")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("创建分层目录失败: %v", err)
	}
	for _, name := range []string{"b_layer.png", "a_layer.png", "composite_preview.png"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("png"), 0o644); err != nil {
			t.Fatalf("写入图层失败: %v", err)
		}
	}

	index, err := ListLayers(cfg, dir)
	if err != nil {
		t.Fatalf("列举图层失败: %v", err)
	}
	if index.LayerCount != 2 {
		t.Fatalf("图层数 = %d", index.LayerCount)
	}
	if index.Layers[0].Name != "a_layer" || index.Layers[1].Name != "b_layer" {
		t.Fatalf("回退按文件名排序：%v", []string{index.Layers[0].Name, index.Layers[1].Name})
	}
}

// dir 是请求参数：必须限定在 output 根目录内，绝对路径穿越直接拒绝。
func TestListLayersRejectsOutsideBase(t *testing.T) {
	cfg := historyTestConfig(t)
	outside := t.TempDir()
	_, err := ListLayers(cfg, outside)
	if !errors.Is(err, ErrPathOutsideBase) {
		t.Fatalf("越界目录应返回 ErrPathOutsideBase，实际 %v", err)
	}
}

func TestListLayersRejectsMissingDir(t *testing.T) {
	cfg := historyTestConfig(t)
	_, err := ListLayers(cfg, filepath.Join(cfg.Output.BaseDir, "layers_nope"))
	if err == nil {
		t.Fatal("不存在的分层目录应报错")
	}
	if errors.Is(err, ErrPathOutsideBase) {
		t.Fatal("目录不存在不应被误判为越界")
	}
}
