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
