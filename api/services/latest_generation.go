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
		"character_id":     resp.CharacterID,
		"image_path":       resp.ImagePath,
		"image_url":        outputURLForPath(cfg, resp.ImagePath, resp.ImageURL),
		"layers_dir":       resp.LayersDir,
		"psd_path":         resp.PSDPath,
		"model3_json":      resp.Model3JSON,
		"output_dir":       resp.OutputDir,
		"width":            resp.Width,
		"height":           resp.Height,
		"prompt":           resp.PromptUser,
		"effective_prompt": resp.EffectivePrompt,
		"prompt_sent":      resp.PromptSent,
		"prompt_lang":      resp.PromptLang,
		"prompt_mismatch":  resp.PromptMismatch,
		// 占位图必须能在重开界面后仍然认得出来：只挂在当次响应上的话，
		// 用户刷新一次左栏，那张假立绘就又变成一次"正常成功"了。
		"fallback":        resp.Fallback,
		"fallback_reason": resp.FallbackReason,
		"created_at":      time.Now().Format(time.RFC3339),
	}
	writeLatestGeneration(cfg, record)
	appendGenerationHistory(cfg, record)
}

// outputURLForPath 优先由 output 目录内的真实路径推导 Web URL；推导失败
// （例如路径不在 output 下）才退回调用方给的原值。
func outputURLForPath(cfg *config.Config, path, fallback string) string {
	if url := OutputURLFor(cfg, path); url != "" {
		return url
	}
	return fallback
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
		if json.Unmarshal(data, &record) == nil && len(record) > 0 && recordFilesExist(record) {
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
	// 先记下这条记录在历史里的身份，再动它。
	identity := exportIdentity(record)
	record["model3_json"] = model3JSON
	record["output_dir"] = outputDir
	record["model_dir"] = filepath.Dir(model3JSON)
	record["model3_url"] = OutputURLFor(cfg, model3JSON)
	record["runtime_ready"] = runtimeReady
	if _, ok := record["created_at"]; !ok {
		record["created_at"] = time.Now().Format(time.RFC3339)
	}
	writeLatestGeneration(cfg, record)
	// 历史条目必须一起改，否则它停在「没有模型」的状态：前端按
	// [layers_dir, model3_json, output_dir, image_path] 算作品 id，两份记录的
	// id 因此不同，左栏同一件作品出现两条（一条带「模型」徽章一条不带），
	// 而 latest 里那份还可能挂着**上一个**作品的 model3 —— 预览/导出就被
	// 标成已完成，点进去却是空作品。实测（2026-10-30 安装包）：
	// /api/generations 报 6 条，左栏显示 7 件作品。
	mergeIntoGenerationHistory(cfg, identity, map[string]interface{}{
		"model3_json":   model3JSON,
		"output_dir":    outputDir,
		"model_dir":     filepath.Dir(model3JSON),
		"model3_url":    OutputURLFor(cfg, model3JSON),
		"runtime_ready": runtimeReady,
	})
}

// exportIdentity 取一条记录里**不会因导出而变**的字段，用来在历史里找回同一次作品。
//
// 刻意不用 historyEntryKey：导出会把 output_dir / model3_json 从空变成有值，
// 键跟着变，「更新同一条」就退化成「新增一条」。
func exportIdentity(record map[string]interface{}) string {
	for _, field := range []string{"image_path", "layers_dir"} {
		if v, ok := record[field].(string); ok && v != "" {
			return v
		}
	}
	return ""
}

// mergeIntoGenerationHistory 把导出结果就地并进历史里的同一条记录。
// 找不到对应条目时什么都不做（不新增、不猜测），宁可少更新也不要造出第二条。
func mergeIntoGenerationHistory(cfg *config.Config, identity string, fields map[string]interface{}) {
	if cfg == nil || identity == "" {
		return
	}
	history := readGenerationHistory(cfg)
	for _, item := range history {
		if item == nil {
			continue
		}
		if exportIdentity(item) != identity {
			continue
		}
		for key, value := range fields {
			item[key] = value
		}
		writeGenerationHistory(cfg, history)
		return
	}
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

// generations_history.json 是工作台左栏「作品」列表的数据源：每次成功生成
// 追加一条，最新的排在最前。latest_generation.json 只保留一条，不足以支撑
// 多作品切换，也不该让「打开软件 → 继续上一件作品」依赖扫描目录的运气。
const (
	generationsHistoryFileName = "generations_history.json"
	maxGenerationHistory       = 50
	defaultGenerationLimit     = 20
)

func generationsHistoryPath(cfg *config.Config) string {
	return filepath.Join(cfg.Output.BaseDir, generationsHistoryFileName)
}

// historyEntryKey 用「这一件作品独有的产物路径」识别同一次作品：分层目录每次生成
// 都新建，是唯一稳定的身份；output_dir 在角色工作流里就是产物根目录（所有作品共用），
// 拿它当键会把历史压成一条。同一作品重生成时分层目录复用 → 替换旧条目而不是堆出一长串。
func historyEntryKey(record map[string]interface{}) string {
	for _, field := range []string{"layers_dir", "output_dir", "image_path"} {
		if v, ok := record[field].(string); ok && v != "" {
			return v
		}
	}
	if id, ok := record["character_id"].(string); ok && id != "" {
		if ts, ok := record["created_at"].(string); ok && ts != "" {
			return id + "@" + ts
		}
	}
	return ""
}

// readGenerationHistory 读取历史；文件缺失或损坏都返回空列表（左栏降级为空，
// 不是接口报错）。坏文件在下一次写入时被覆盖。
func readGenerationHistory(cfg *config.Config) []map[string]interface{} {
	data, err := os.ReadFile(generationsHistoryPath(cfg))
	if err != nil {
		return nil
	}
	var history []map[string]interface{}
	if json.Unmarshal(data, &history) != nil {
		return nil
	}
	return history
}

// appendGenerationHistory 把一条记录插到最前，按 key 去重并封顶。
// 先写临时文件再 rename，避免进程中断留下半截 JSON 把整个历史弄失效。
func appendGenerationHistory(cfg *config.Config, record map[string]interface{}) {
	if cfg == nil || record == nil {
		return
	}
	key := historyEntryKey(record)
	kept := make([]map[string]interface{}, 0, maxGenerationHistory)
	kept = append(kept, record)
	for _, item := range readGenerationHistory(cfg) {
		if key != "" && historyEntryKey(item) == key {
			continue
		}
		kept = append(kept, item)
		if len(kept) >= maxGenerationHistory {
			break
		}
	}
	writeGenerationHistory(cfg, kept)
}

func writeGenerationHistory(cfg *config.Config, history []map[string]interface{}) {
	b, err := json.MarshalIndent(history, "", "  ")
	if err != nil {
		return
	}
	if err := os.MkdirAll(cfg.Output.BaseDir, 0o755); err != nil {
		return
	}
	tmp := generationsHistoryPath(cfg) + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return
	}
	if err := os.Rename(tmp, generationsHistoryPath(cfg)); err != nil {
		_ = os.Remove(tmp)
	}
}

// ListGenerations 返回工作台左栏的作品列表（新的在前）。limit<=0 用默认值，
// 超过上限按上限截断。
func ListGenerations(cfg *config.Config, limit int) []map[string]interface{} {
	if cfg == nil {
		return []map[string]interface{}{}
	}
	if limit <= 0 {
		limit = defaultGenerationLimit
	}
	if limit > maxGenerationHistory {
		limit = maxGenerationHistory
	}
	history := readGenerationHistory(cfg)
	if len(history) > limit {
		history = history[:limit]
	}
	alive := history[:0]
	for _, record := range history {
		if record == nil {
			continue
		}
		// 指向已被删除产物的记录不再列出：缩略图必然 404、整条也点不开。
		// 开发期清理 output/ 或用户自己删图片之后，界面应该像一台新软件，
		// 而不是排着一列坏掉的旧作品。
		if path := stringField(record, "image_path"); path != "" {
			if _, err := os.Stat(path); err != nil {
				continue
			}
		}
		enrichLatestGeneration(cfg, record)
		if url := outputURLForPath(cfg, stringField(record, "image_path"), stringField(record, "image_url")); url != "" {
			record["image_url"] = url
		}
		alive = append(alive, record)
	}
	return alive
}

func stringField(record map[string]interface{}, field string) string {
	v, _ := record[field].(string)
	return v
}

// recordFilesExist 判断记录引用的产物是否还在盘上。
// 只校验非空字段：老记录可能没有 model3_json（还没导出过），那不是"文件被删"。
func recordFilesExist(record map[string]interface{}) bool {
	for _, field := range []string{"image_path", "model3_json"} {
		path := stringField(record, field)
		if path == "" {
			continue
		}
		if _, err := os.Stat(path); err != nil {
			return false
		}
	}
	return true
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
