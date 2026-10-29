package services

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"live2d-api/config"
)

// LayerItem 是前端分层面板直接可渲染的一层：URL 由产物路径推导，
// 顺序以 draw_index 为准（即 PSD 的绘制顺序）。
type LayerItem struct {
	Name       string `json:"name"`
	DrawIndex  int    `json:"draw_index"`
	URL        string `json:"url"`
	BBox       []int  `json:"bbox,omitempty"`
	Size       []int  `json:"size,omitempty"`
	PixelCount int    `json:"pixel_count,omitempty"`
}

// LayersIndex 是分层目录的可视化索引（layer_guide.json 的 Web 版本）。
type LayersIndex struct {
	LayersDir  string      `json:"layers_dir"`
	PreviewURL string      `json:"preview_url,omitempty"`
	LayerCount int         `json:"layer_count"`
	Layers     []LayerItem `json:"layers"`
}

type layerGuide struct {
	Layers []struct {
		Name       string `json:"name"`
		DrawIndex  int    `json:"draw_index"`
		Path       string `json:"path"`
		BBox       []int  `json:"bbox"`
		Size       []int  `json:"size"`
		PixelCount int    `json:"pixel_count"`
	} `json:"layers"`
}

const layerPreviewName = "composite_preview.png"

// ListLayers 读取分层目录的 layer_guide.json，输出可直接加载的图层列表。
// dir 来自请求参数，必须位于 output 根目录内；越界返回 ErrPathOutsideBase。
func ListLayers(cfg *config.Config, dir string) (*LayersIndex, error) {
	if cfg == nil {
		return nil, fmt.Errorf("服务配置缺失")
	}
	if strings.TrimSpace(dir) == "" {
		return nil, fmt.Errorf("缺少分层目录参数 dir")
	}
	if err := validateWithinBase(dir, cfg.Output.BaseDir, "分层目录"); err != nil {
		return nil, err
	}
	info, err := os.Stat(dir)
	if err != nil || !info.IsDir() {
		return nil, fmt.Errorf("分层目录不存在: %s", filepath.Base(dir))
	}

	index := &LayersIndex{LayersDir: dir, Layers: []LayerItem{}}
	if preview := filepath.Join(dir, layerPreviewName); fileExists(preview) {
		index.PreviewURL = OutputURLFor(cfg, preview)
	}

	if guide, ok := readLayerGuide(dir); ok {
		for _, layer := range guide.Layers {
			layerPath := layer.Path
			if layerPath == "" {
				layerPath = filepath.Join(dir, layer.Name+".png")
			}
			url := OutputURLFor(cfg, layerPath)
			if url == "" && fileExists(filepath.Join(dir, filepath.Base(layerPath))) {
				url = OutputURLFor(cfg, filepath.Join(dir, filepath.Base(layerPath)))
			}
			index.Layers = append(index.Layers, LayerItem{
				Name:       layer.Name,
				DrawIndex:  layer.DrawIndex,
				URL:        url,
				BBox:       layer.BBox,
				Size:       layer.Size,
				PixelCount: layer.PixelCount,
			})
		}
		sort.SliceStable(index.Layers, func(i, j int) bool {
			return index.Layers[i].DrawIndex < index.Layers[j].DrawIndex
		})
	}

	if len(index.Layers) == 0 {
		index.Layers = scanLayerPNGs(cfg, dir)
	}
	index.LayerCount = len(index.Layers)
	return index, nil
}

func readLayerGuide(dir string) (layerGuide, bool) {
	var guide layerGuide
	data, err := os.ReadFile(filepath.Join(dir, "layer_guide.json"))
	if err != nil {
		return guide, false
	}
	if json.Unmarshal(data, &guide) != nil || len(guide.Layers) == 0 {
		return guide, false
	}
	return guide, true
}

// scanLayerPNGs 是旧产物的回退：没有 layer_guide.json 时按文件名列出 PNG，
// 排除合成预览图。此时没有 draw_index 语义，只能保证「看得到」。
func scanLayerPNGs(cfg *config.Config, dir string) []LayerItem {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	items := make([]LayerItem, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasSuffix(strings.ToLower(name), ".png") || name == layerPreviewName {
			continue
		}
		items = append(items, LayerItem{
			Name:      strings.TrimSuffix(name, filepath.Ext(name)),
			DrawIndex: len(items),
			URL:       OutputURLFor(cfg, filepath.Join(dir, name)),
		})
	}
	sort.SliceStable(items, func(i, j int) bool { return items[i].Name < items[j].Name })
	for i := range items {
		items[i].DrawIndex = i
	}
	return items
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
