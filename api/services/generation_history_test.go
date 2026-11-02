package services

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"live2d-api/config"
	"live2d-api/models"
)

func historyTestConfig(t *testing.T) *config.Config {
	t.Helper()
	cfg := &config.Config{}
	cfg.Output.BaseDir = t.TempDir()
	return cfg
}

func sampleGeneration(t *testing.T, cfg *config.Config, outputDir, imagePath, prompt string) *models.GenerateImageResponse {
	t.Helper()
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		t.Fatalf("创建产物目录失败: %v", err)
	}
	if err := os.WriteFile(imagePath, []byte("png"), 0o644); err != nil {
		t.Fatalf("写入图片失败: %v", err)
	}
	return &models.GenerateImageResponse{
		ImagePath:       imagePath,
		ImageURL:        "/output/" + filepath.Base(imagePath),
		LayersDir:       filepath.Join(outputDir, "layers_1"),
		OutputDir:       outputDir,
		CharacterID:     "char_1",
		Success:         true,
		PromptUser:      prompt,
		EffectivePrompt: prompt + "，风格要求：celluloid",
		PromptSent:      prompt + "，风格要求：celluloid",
		PromptLang:      "zh",
		Width:           1024,
		Height:          1024,
	}
}

// image_url 必须由 image_path 推导：角色产物落在 output/characters/<id>/ 下，
// 旧实现直接取 resp.ImageURL（"/output/"+basename），浏览器必然 404。
func TestRecordLatestGenerationDerivesImageURL(t *testing.T) {
	cfg := historyTestConfig(t)
	charDir := filepath.Join(cfg.Output.BaseDir, "characters", "char_1")
	img := filepath.Join(charDir, "image.png")
	resp := sampleGeneration(t, cfg, charDir, img, "银发红瞳的少女")

	RecordLatestGeneration(cfg, resp)

	record, err := GetLatestGeneration(cfg)
	if err != nil {
		t.Fatalf("读取最近生成失败: %v", err)
	}
	want := "/output/characters/char_1/image.png"
	if got := record["image_url"]; got != want {
		t.Fatalf("image_url = %v，应为 %q", got, want)
	}
	if got := record["prompt"]; got != "银发红瞳的少女" {
		t.Fatalf("prompt = %v，应为用户原文", got)
	}
	if got := record["prompt_lang"]; got != "zh" {
		t.Fatalf("prompt_lang = %v", got)
	}
}

// 历史按「新的在前」排列，同一 output_dir 重复生成只保留最新一条。
func TestListGenerationsNewestFirstAndDeduped(t *testing.T) {
	cfg := historyTestConfig(t)
	first := filepath.Join(cfg.Output.BaseDir, "characters", "char_1")
	second := filepath.Join(cfg.Output.BaseDir, "characters", "char_2")

	RecordLatestGeneration(cfg, sampleGeneration(t, cfg, first, filepath.Join(first, "image.png"), "第一个角色"))
	RecordLatestGeneration(cfg, sampleGeneration(t, cfg, second, filepath.Join(second, "image.png"), "第二个角色"))
	// 同一目录再生成一次：应替换旧记录而不是堆两条
	RecordLatestGeneration(cfg, sampleGeneration(t, cfg, first, filepath.Join(first, "image.png"), "第一个角色（重生成）"))

	items := ListGenerations(cfg, 0)
	if len(items) != 2 {
		t.Fatalf("历史条数 = %d，应为 2", len(items))
	}
	if got := items[0]["prompt"]; got != "第一个角色（重生成）" {
		t.Fatalf("最新一条 prompt = %v", got)
	}
	if got := items[1]["prompt"]; got != "第二个角色" {
		t.Fatalf("第二条 prompt = %v", got)
	}
}

// 角色工作流的 output_dir 就是产物根目录（每次生成都一样），分层目录才是这一件
// 作品的身份。用 output_dir 当去重键会把所有作品压成一条，左栏永远只有一件。
func TestHistoryKeyUsesLayersDirNotSharedOutputRoot(t *testing.T) {
	cfg := historyTestConfig(t)

	first := sampleGeneration(t, cfg, cfg.Output.BaseDir, filepath.Join(cfg.Output.BaseDir, "character_1.png"), "第一位角色")
	first.LayersDir = filepath.Join(cfg.Output.BaseDir, "layers_1")
	second := sampleGeneration(t, cfg, cfg.Output.BaseDir, filepath.Join(cfg.Output.BaseDir, "character_2.png"), "第二位角色")
	second.LayersDir = filepath.Join(cfg.Output.BaseDir, "layers_2")

	RecordLatestGeneration(cfg, first)
	RecordLatestGeneration(cfg, second)

	items := ListGenerations(cfg, 0)
	if len(items) != 2 {
		t.Fatalf("历史条数 = %d，应为 2（两件作品共用同一个 output_dir）", len(items))
	}
	if got := items[0]["prompt"]; got != "第二位角色" {
		t.Fatalf("最新一条 prompt = %v", got)
	}

	// 同一分层目录重生成：替换而不是堆条目。
	again := sampleGeneration(t, cfg, cfg.Output.BaseDir, filepath.Join(cfg.Output.BaseDir, "character_3.png"), "第二位角色（重生成）")
	again.LayersDir = second.LayersDir
	RecordLatestGeneration(cfg, again)
	if items := ListGenerations(cfg, 0); len(items) != 2 {
		t.Fatalf("重生成后历史条数 = %d，应仍为 2", len(items))
	} else if got := items[0]["prompt"]; got != "第二位角色（重生成）" {
		t.Fatalf("重生成未替换最新条目，prompt = %v", got)
	}
}

func TestListGenerationsCapsHistory(t *testing.T) {
	cfg := historyTestConfig(t)
	for i := 0; i < maxGenerationHistory+5; i++ {
		dir := filepath.Join(cfg.Output.BaseDir, "gen", string(rune('a'+i%26)), string(rune('a'+i/26)))
		RecordLatestGeneration(cfg, sampleGeneration(t, cfg, dir, filepath.Join(dir, "image.png"), "p"))
	}
	if items := ListGenerations(cfg, 0); len(items) != defaultGenerationLimit {
		t.Fatalf("默认返回 %d 条，应为 %d", len(items), defaultGenerationLimit)
	}
	if items := ListGenerations(cfg, maxGenerationHistory+10); len(items) != maxGenerationHistory {
		t.Fatalf("超大 limit 返回 %d 条，应封顶 %d", len(items), maxGenerationHistory)
	}
	if items := ListGenerations(cfg, 3); len(items) != 3 {
		t.Fatalf("limit=3 时返回 %d 条", len(items))
	}
}

// 历史文件损坏时不能拖垮左栏：当成空历史，而不是 500。
func TestListGenerationsToleratesCorruptHistory(t *testing.T) {
	cfg := historyTestConfig(t)
	if err := os.WriteFile(filepath.Join(cfg.Output.BaseDir, generationsHistoryFileName), []byte("{not json"), 0o644); err != nil {
		t.Fatalf("写入损坏历史失败: %v", err)
	}
	if items := ListGenerations(cfg, 10); len(items) != 0 {
		t.Fatalf("损坏历史应返回空列表，实际 %d 条", len(items))
	}
}

// 历史条目要能被 JSON 序列化后直接给前端（不能夹带 map 里的非 JSON 值）。
func TestGenerationHistoryIsJSONSerializable(t *testing.T) {
	cfg := historyTestConfig(t)
	dir := filepath.Join(cfg.Output.BaseDir, "characters", "char_1")
	RecordLatestGeneration(cfg, sampleGeneration(t, cfg, dir, filepath.Join(dir, "image.png"), "提示词"))

	data, err := os.ReadFile(filepath.Join(cfg.Output.BaseDir, generationsHistoryFileName))
	if err != nil {
		t.Fatalf("历史文件未落盘: %v", err)
	}
	var decoded []map[string]interface{}
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("历史文件不是合法 JSON: %v", err)
	}
	if len(decoded) != 1 {
		t.Fatalf("历史文件条目 = %d", len(decoded))
	}
}

// 占位图标记必须随记录落盘。
//
// 实测（2026-10-30，已安装的 v0.10.10）：免密上游 402 时 GenerateWithCharacter
// 把真实错误写进 stderr、改用本地占位图，然后返回 success=true；界面上是
// 一次正常成功，左栏重开之后连那条 stderr 都看不到了。
// 所以 fallback 不能只挂在当次响应上，必须进 latest_generation.json 与历史。
func TestFallbackFlagSurvivesIntoHistory(t *testing.T) {
	cfg := historyTestConfig(t)
	dir := filepath.Join(cfg.Output.BaseDir, "characters", "char_1")
	resp := sampleGeneration(t, cfg, dir, filepath.Join(dir, "image.png"), "银发少女，赛璐璐画风")
	resp.Fallback = true
	resp.FallbackReason = "生成链路失败，已回退本地占位图：HTTP 402 配额耗尽"
	RecordLatestGeneration(cfg, resp)

	read := func(file string) map[string]interface{} {
		t.Helper()
		data, err := os.ReadFile(filepath.Join(cfg.Output.BaseDir, file))
		if err != nil {
			t.Fatalf("读 %s 失败: %v", file, err)
		}
		if file == generationsHistoryFileName {
			var items []map[string]interface{}
			if err := json.Unmarshal(data, &items); err != nil {
				t.Fatalf("历史不是合法 JSON: %v", err)
			}
			if len(items) != 1 {
				t.Fatalf("历史条目 = %d", len(items))
			}
			return items[0]
		}
		var one map[string]interface{}
		if err := json.Unmarshal(data, &one); err != nil {
			t.Fatalf("latest 不是合法 JSON: %v", err)
		}
		return one
	}

	for _, file := range []string{latestGenerationFileName, generationsHistoryFileName} {
		record := read(file)
		if got, _ := record["fallback"].(bool); !got {
			t.Errorf("%s: fallback 应为 true，实得 %v", file, record["fallback"])
		}
		if reason, _ := record["fallback_reason"].(string); reason == "" {
			t.Errorf("%s: fallback_reason 为空，用户就不知道为什么只拿到占位图", file)
		}
	}
}

// 反方向：正常的工作流成功不得被标成占位图，否则这个标记很快就没人信了。
func TestRealGenerationIsNotMarkedFallback(t *testing.T) {
	cfg := historyTestConfig(t)
	dir := filepath.Join(cfg.Output.BaseDir, "characters", "char_1")
	RecordLatestGeneration(cfg, sampleGeneration(t, cfg, dir, filepath.Join(dir, "image.png"), "少女"))

	data, err := os.ReadFile(filepath.Join(cfg.Output.BaseDir, latestGenerationFileName))
	if err != nil {
		t.Fatalf("latest 未落盘: %v", err)
	}
	var record map[string]interface{}
	if err := json.Unmarshal(data, &record); err != nil {
		t.Fatalf("latest 不是合法 JSON: %v", err)
	}
	if got, ok := record["fallback"].(bool); !ok || got {
		t.Fatalf("fallback = %v（真实生成必须显式为 false）", record["fallback"])
	}
}

// 导出必须把历史条目一起改掉，否则左栏会把同一件作品显示成两条。
//
// 实测（2026-10-30，已安装的 v0.10.10）：/api/generations 返回 6 条，
// 左栏却显示「7 件作品」，其中两条同名、一条带「模型」徽章一条不带；
// 带徽章那条的 model3 其实属于**上一个**作品，于是阶段条把预览/导出
// 标成已完成，点进去后端记录里 model3_json 与 layers_dir 都是空的。
// 机制：RecordLatestExport 只写 latest_generation.json，
// 而前端按 [layers_dir, model3_json, output_dir, image_path] 算作品 id ——
// 两份记录算出两个 id。
func TestExportUpdatesHistoryEntryInPlace(t *testing.T) {
	cfg := historyTestConfig(t)
	dir := filepath.Join(cfg.Output.BaseDir, "characters", "char_1")
	resp := sampleGeneration(t, cfg, dir, filepath.Join(dir, "image.png"), "银发少女")
	RecordLatestGeneration(cfg, resp)

	model3 := filepath.Join(cfg.Output.BaseDir, "live2d_exports", "char_1", "char_1.model3.json")
	if err := os.MkdirAll(filepath.Dir(model3), 0o755); err != nil {
		t.Fatal(err)
	}
	RecordLatestExport(cfg, model3, filepath.Dir(model3), true)

	readAll := func(file string) []map[string]interface{} {
		t.Helper()
		data, err := os.ReadFile(filepath.Join(cfg.Output.BaseDir, file))
		if err != nil {
			t.Fatalf("读 %s 失败: %v", file, err)
		}
		if file == generationsHistoryFileName {
			var items []map[string]interface{}
			if err := json.Unmarshal(data, &items); err != nil {
				t.Fatalf("历史不是合法 JSON: %v", err)
			}
			return items
		}
		var one map[string]interface{}
		if err := json.Unmarshal(data, &one); err != nil {
			t.Fatalf("latest 不是合法 JSON: %v", err)
		}
		return []map[string]interface{}{one}
	}

	history := readAll(generationsHistoryFileName)
	if len(history) != 1 {
		t.Fatalf("历史条目 = %d，导出应就地更新而不是新增一条", len(history))
	}
	if got, _ := history[0]["model3_json"].(string); got != model3 {
		t.Errorf("历史条目的 model3_json = %q，应为 %q", got, model3)
	}
	if ready, _ := history[0]["runtime_ready"].(bool); !ready {
		t.Error("历史条目没有同步 runtime_ready")
	}

	// 前端用这四个字段算作品 id：两边必须一致，否则同一件作品会出现两份。
	latest := readAll(latestGenerationFileName)[0]
	for _, field := range []string{"layers_dir", "model3_json", "output_dir", "image_path"} {
		if history[0][field] != latest[field] {
			t.Errorf("%s 在历史与 latest 之间不一致（%v vs %v）—— 作品 id 会算成两个",
				field, history[0][field], latest[field])
		}
	}
}

// 反方向：历史里找不到对应条目时不要凭空新增一条。
func TestExportWithoutMatchingHistoryAddsNothing(t *testing.T) {
	cfg := historyTestConfig(t)
	model3 := filepath.Join(cfg.Output.BaseDir, "live2d_exports", "x", "x.model3.json")
	RecordLatestExport(cfg, model3, filepath.Dir(model3), false)

	if _, err := os.ReadFile(filepath.Join(cfg.Output.BaseDir, generationsHistoryFileName)); err == nil {
		data, _ := os.ReadFile(filepath.Join(cfg.Output.BaseDir, generationsHistoryFileName))
		var items []map[string]interface{}
		_ = json.Unmarshal(data, &items)
		if len(items) != 0 {
			t.Fatalf("没有对应生成记录时不该造历史条目，实得 %d 条", len(items))
		}
	}
}

// 只有 image_path 能认出的那条路 —— 实测坏掉的就是它。
//
// 离线兜底生成的记录 layers_dir 与 output_dir 都是空的，导出后 output_dir
// 才从无变有。若按"会变的字段"（output_dir 优先）去找历史条目，导出前那条
// 记录的键是空的 → 匹配不上 → 历史永远停在"没有模型"，左栏于是出现两份
// 同一作品（一份带模型徽章、一份不带）。
func TestExportMatchesHistoryByStableFieldWhenOthersEmpty(t *testing.T) {
	cfg := historyTestConfig(t)
	dir := filepath.Join(cfg.Output.BaseDir, "characters", "char_2")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	img := filepath.Join(dir, "image.png")
	if err := os.WriteFile(img, []byte("png"), 0o644); err != nil {
		t.Fatal(err)
	}
	RecordLatestGeneration(cfg, &models.GenerateImageResponse{
		ImagePath:  img,
		ImageURL:   "/output/characters/char_2/image.png",
		Success:    true,
		PromptUser: "银发少女，赛璐璐画风",
		Width:      512,
		Height:     512,
	})

	model3 := filepath.Join(cfg.Output.BaseDir, "live2d_exports", "char_2", "char_2.model3.json")
	RecordLatestExport(cfg, model3, filepath.Dir(model3), true)

	data, err := os.ReadFile(filepath.Join(cfg.Output.BaseDir, generationsHistoryFileName))
	if err != nil {
		t.Fatalf("历史未落盘: %v", err)
	}
	var items []map[string]interface{}
	if err := json.Unmarshal(data, &items); err != nil {
		t.Fatalf("历史不是合法 JSON: %v", err)
	}
	if len(items) != 1 {
		t.Fatalf("历史条目 = %d，应为 1（就地更新）", len(items))
	}
	if got, _ := items[0]["model3_json"].(string); got != model3 {
		t.Errorf("历史条目的 model3_json = %q，导出结果没并进去", got)
	}
}

// 产物被清掉后，左栏不该留下点开的死条目。开发期清理 output/、用户自己删图片，
// 都会让历史指向不存在的文件 —— 那时界面看起来是"坏掉的旧软件"而不是新软件。
func TestListGenerationsDropsRecordsWhoseFilesAreGone(t *testing.T) {
	cfg := historyTestConfig(t)
	goodDir := filepath.Join(cfg.Output.BaseDir, "g_ok")
	goodImg := filepath.Join(goodDir, "character.png")
	goneDir := filepath.Join(cfg.Output.BaseDir, "g_gone")
	goneImg := filepath.Join(goneDir, "character.png")

	RecordLatestGeneration(cfg, sampleGeneration(t, cfg, goneDir, goneImg, "文件会被删掉的作品"))
	RecordLatestGeneration(cfg, sampleGeneration(t, cfg, goodDir, goodImg, "留下来的作品"))

	if err := os.Remove(goneImg); err != nil {
		t.Fatalf("删除图片失败，前置条件不成立: %v", err)
	}

	items := ListGenerations(cfg, 0)
	if len(items) != 1 {
		t.Fatalf("应只剩 1 条活记录，得到 %d 条：%v", len(items), items)
	}
	if got := stringField(items[0], "prompt"); got != "留下来的作品" {
		t.Fatalf("留下的应是活的那条，得到 prompt=%q", got)
	}
}

// 反向保险：文件都还在时不得误删任何记录。
func TestListGenerationsKeepsRecordsWhoseFilesExist(t *testing.T) {
	cfg := historyTestConfig(t)
	for _, name := range []string{"a", "b"} {
		dir := filepath.Join(cfg.Output.BaseDir, "g_"+name)
		RecordLatestGeneration(cfg, sampleGeneration(t, cfg, dir,
			filepath.Join(dir, "character.png"), "作品"+name))
	}
	if items := ListGenerations(cfg, 0); len(items) != 2 {
		t.Fatalf("两条记录都该留下，得到 %d", len(items))
	}
}

// 工作台首加载读的是 latest_generation.json。清过产物（或用户删了图片）之后，
// 这条记录仍指向不存在的文件 —— 首屏就会拿到一个打不开的"当前作品"。
// 正确行为是回退去扫描真实存在的产物。
func TestGetLatestGenerationFallsBackWhenRecordFilesAreGone(t *testing.T) {
	cfg := historyTestConfig(t)
	dir := filepath.Join(cfg.Output.BaseDir, "g_gone")
	img := filepath.Join(dir, "character.png")
	RecordLatestGeneration(cfg, sampleGeneration(t, cfg, dir, img, "文件会被删掉的作品"))

	if err := os.Remove(img); err != nil {
		t.Fatalf("删除图片失败，前置条件不成立: %v", err)
	}

	record, err := GetLatestGeneration(cfg)
	if err != nil {
		// 没有任何其它产物时报「尚未有生成记录」是诚实的空状态
		if !os.IsExist(err) && record != nil {
			t.Fatalf("既报错又返回记录：%v / %v", err, record)
		}
		return
	}
	if got := stringField(record, "prompt"); got == "文件会被删掉的作品" {
		t.Fatal("仍返回指向已删文件的死记录，首屏会拿到打不开的作品")
	}
}
