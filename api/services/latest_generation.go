package services

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"live2d-api/config"
	"live2d-api/models"
)

// latest_generation.json 是「生成 → 分层 → 导出 → 桌宠」跨页面接力的记录文件，
// 与 Python 端 api_server.py 的 _record_generation 保持同一份契约。
const latestGenerationFileName = "latest_generation.json"

func latestGenerationPath(cfg *config.Config) string {
	return filepath.Join(cfg.Output.BaseDir, latestGenerationFileName)
}

// RecordLatestGeneration 在角色工作流成功后落盘最近一次生成记录（尽力而为，
// 失败不影响主流程）。字段与 Python 端 _record_generation 对齐。
func RecordLatestGeneration(cfg *config.Config, resp *models.GenerateImageResponse) {
	if cfg == nil || resp == nil {
		return
	}
	record := map[string]interface{}{
		"character_id": resp.CharacterID,
		"image_path":   resp.ImagePath,
		"image_url":    resp.ImageURL,
		"layers_dir":   resp.LayersDir,
		"psd_path":     resp.PSDPath,
		"model3_json":  resp.Model3JSON,
		"output_dir":   resp.OutputDir,
		"created_at":   time.Now().Format(time.RFC3339),
	}
	writeLatestGeneration(cfg, record)
}

func writeLatestGeneration(cfg *config.Config, record map[string]interface{}) {
	b, err := json.MarshalIndent(record, "", "  ")
	if err != nil {
		return
	}
	if err := os.MkdirAll(cfg.Output.BaseDir, 0o755); err != nil {
		return
	}
	_ = os.WriteFile(latestGenerationPath(cfg), b, 0o644)
}

// OutputURLFor 把 output 目录内文件的绝对路径转换为可被 /output/* 静态路由
// 直接服务的 Web URL；不在 output 目录内或出错时返回空串。
func OutputURLFor(cfg *config.Config, path string) string {
	if path == "" || cfg == nil {
		return ""
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return ""
	}
	base, err := filepath.Abs(cfg.Output.BaseDir)
	if err != nil {
		return ""
	}
	rel, err := filepath.Rel(base, abs)
	if err != nil || rel == ".." || strings.HasPrefix(rel, "..") {
		return ""
	}
	return "/output/" + filepath.ToSlash(rel)
}

// enrichLatestGeneration 在读取到的记录上补充前端可直接使用的派生字段：
// model3_url（Web 可加载的 model3.json 地址）、model_dir（桌宠部署所需目录）、
// psd_url。旧记录或缺字段的回退扫描结果都会被补齐。
func enrichLatestGeneration(cfg *config.Config, record map[string]interface{}) {
	model3JSON, _ := record["model3_json"].(string)
	if model3JSON != "" {
		if record["model_dir"] == "" || record["model_dir"] == nil {
			record["model_dir"] = filepath.Dir(model3JSON)
		}
		if record["model3_url"] == "" || record["model3_url"] == nil {
			record["model3_url"] = OutputURLFor(cfg, model3JSON)
		}
	}
	psdPath, _ := record["psd_path"].(string)
	if psdPath != "" && (record["psd_url"] == "" || record["psd_url"] == nil) {
		record["psd_url"] = OutputURLFor(cfg, psdPath)
	}
}

// GetLatestGeneration 返回最近一次成功生成的产物。
// 优先读取 latest_generation.json；不存在时回退为扫描 output 目录下
// 最新的 *.model3.json（覆盖纯 Go 侧生成/导出后未写记录的场景）。
func GetLatestGeneration(cfg *config.Config) (map[string]interface{}, error) {
	if data, err := os.ReadFile(latestGenerationPath(cfg)); err == nil {
		var record map[string]interface{}
		if json.Unmarshal(data, &record) == nil && len(record) > 0 {
			enrichLatestGeneration(cfg, record)
			return record, nil
		}
	}

	model3JSON, err := findNewestModel3(cfg.Output.BaseDir)
	if err != nil {
		return nil, err
	}
	record := map[string]interface{}{
		"character_id": "",
		"image_path":   "",
		"image_url":    "",
		"layers_dir":   "",
		"psd_path":     "",
		"model3_json":  model3JSON,
		"output_dir":   filepath.Dir(model3JSON),
		"created_at":   time.Now().Format(time.RFC3339),
	}
	enrichLatestGeneration(cfg, record)
	return record, nil
}

// RecordLatestExport 在成功导出后把模型产物并入最近生成记录。
// 这是「导出 → 预览 / 桌宠」的交接枢纽：预览页自动加载与桌宠部署的
// model_dir 都来自这份记录，导出完成即立即可用。
func RecordLatestExport(cfg *config.Config, model3JSON, outputDir string, runtimeReady bool) {
	if cfg == nil || model3JSON == "" {
		return
	}
	record := map[string]interface{}{}
	if data, err := os.ReadFile(latestGenerationPath(cfg)); err == nil {
		_ = json.Unmarshal(data, &record)
	}
	record["model3_json"] = model3JSON
	record["output_dir"] = outputDir
	record["model_dir"] = filepath.Dir(model3JSON)
	record["model3_url"] = OutputURLFor(cfg, model3JSON)
	record["runtime_ready"] = runtimeReady
	if _, ok := record["created_at"]; !ok {
		record["created_at"] = time.Now().Format(time.RFC3339)
	}
	writeLatestGeneration(cfg, record)
}

// ListExportedModels 列出 output 目录下全部已导出的 model3.json（按时间倒序），
// 供预览页的模型选择列表使用。
func ListExportedModels(cfg *config.Config) []map[string]interface{} {
	results := make([]map[string]interface{}, 0, 8)
	_ = filepath.WalkDir(cfg.Output.BaseDir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		name := d.Name()
		if d.IsDir() {
			if strings.HasPrefix(name, ".") || name == "node_modules" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(name, ".model3.json") {
			return nil
		}
		entry := map[string]interface{}{
			"model3_json": path,
			"output_dir":  filepath.Dir(path),
			"model3_url":  OutputURLFor(cfg, path),
			"name":        filepath.Base(filepath.Dir(path)),
		}
		if info, err := d.Info(); err == nil {
			entry["mod_time"] = info.ModTime().Format(time.RFC3339)
		}
		results = append(results, entry)
		return nil
	})
	return results
}

// findNewestModel3 递归查找 output 目录下 mtime 最新的 *.model3.json。
func findNewestModel3(baseDir string) (string, error) {
	var newest string
	var newestMod time.Time
	err := filepath.WalkDir(baseDir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil // 跳过不可读条目，不让单个损坏目录拖垮扫描
		}
		name := d.Name()
		if d.IsDir() {
			if strings.HasPrefix(name, ".") || name == "node_modules" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(name, ".model3.json") {
			return nil
		}
		if info, err := d.Info(); err == nil && info.ModTime().After(newestMod) {
			newestMod = info.ModTime()
			newest = path
		}
		return nil
	})
	if err != nil {
		return "", fmt.Errorf("扫描产物目录失败: %v", err)
	}
	if newest == "" {
		return "", fmt.Errorf("尚未有任何生成记录或可用的 Live2D 模型")
	}
	return newest, nil
}
