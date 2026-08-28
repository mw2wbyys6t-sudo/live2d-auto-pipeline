package services

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"live2d-api/config"
	"live2d-api/models"
)

// CharacterService 角色卡片管理服务
// 管理存储为 JSON 文件的角色卡片，作为 Python CharacterManager 的 Go 端桥接
type CharacterService struct {
	cfg        *config.Config
	storageDir string
}

// NewCharacterService 创建角色服务
func NewCharacterService(cfg *config.Config) *CharacterService {
	storageDir := cfg.Character.StorageDir
	if storageDir == "" {
		storageDir = filepath.Join(cfg.Python.ScriptsDir, "assets", "characters")
	}
	os.MkdirAll(storageDir, 0755)
	return &CharacterService{
		cfg:        cfg,
		storageDir: storageDir,
	}
}

// cardPath 返回角色卡片 JSON 路径
func (s *CharacterService) cardPath(characterID string) string {
	return filepath.Join(s.storageDir, characterID+".json")
}

// CreateCharacter 创建新角色
func (s *CharacterService) CreateCharacter(req models.CharacterRequest) (*models.CharacterCard, error) {
	now := time.Now().UTC().Format(time.RFC3339)
	card := &models.CharacterCard{
		CharacterID: generateID(),
		Name:        req.Name,
		CreatedAt:   now,
		UpdatedAt:   now,
		Face:        req.Face,
		Hair:        req.Hair,
		Body:        req.Body,
		Palette:     req.Palette,
		Outfit:      req.Outfit,
		Persona:     req.Persona,
		Style:       req.Style,
	}

	// 设置默认值
	if card.Face.Shape == "" {
		card.Face.Shape = "oval"
	}
	if card.Face.EyeColor == "" {
		card.Face.EyeColor = "#4a90d9"
	}
	if card.Hair.Color == "" {
		card.Hair.Color = "#3a2a1a"
	}
	if card.Hair.Style == "" {
		card.Hair.Style = "long"
	}
	if card.Body.Type == "" {
		card.Body.Type = "slim"
	}
	if card.Palette.SkinTone == "" {
		card.Palette.SkinTone = "#f5d5b8"
	}

	if err := s.save(card); err != nil {
		return nil, err
	}

	// 如果提供了参考图，调用 Python 提取 embedding
	if req.RefImage != "" {
		// Always record the reference path on the card so /api/characters/{id}
		// exposes it even if the Python bridge (CLIP embedding) is unavailable.
		card.References.Front = req.RefImage
		if err := s.save(card); err != nil {
			return nil, err
		}
		pb := NewPythonBridge(s.cfg)
		_ = pb.AddReferenceImage(card.CharacterID, req.RefImage, "front")
		// 重新加载以获取 embedding
		if loaded, err := s.GetCharacter(card.CharacterID); err == nil {
			card = loaded
		}
	}

	return card, nil
}

// GetCharacter 获取单个角色
func (s *CharacterService) GetCharacter(characterID string) (*models.CharacterCard, error) {
	path := s.cardPath(characterID)
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("角色不存在: %s", characterID)
	}
	var card models.CharacterCard
	if err := json.Unmarshal(data, &card); err != nil {
		return nil, fmt.Errorf("角色数据解析失败: %v", err)
	}
	return &card, nil
}

// ListCharacters 列出所有角色
func (s *CharacterService) ListCharacters() ([]models.CharacterListResponse, error) {
	pattern := filepath.Join(s.storageDir, "*.json")
	files, err := filepath.Glob(pattern)
	if err != nil {
		return nil, err
	}

	var results []models.CharacterListResponse
	for _, f := range files {
		data, err := os.ReadFile(f)
		if err != nil {
			continue
		}
		var card models.CharacterCard
		if err := json.Unmarshal(data, &card); err != nil {
			continue
		}
		results = append(results, models.CharacterListResponse{
			CharacterID:  card.CharacterID,
			Name:         card.Name,
			CreatedAt:    card.CreatedAt,
			UpdatedAt:    card.UpdatedAt,
			HasEmbedding: len(card.Embedding) > 0,
			HairColor:    card.Hair.Color,
			EyeColor:     card.Face.EyeColor,
		})
	}
	return results, nil
}

// UpdateCharacter 更新角色
func (s *CharacterService) UpdateCharacter(characterID string, req models.CharacterRequest) (*models.CharacterCard, error) {
	card, err := s.GetCharacter(characterID)
	if err != nil {
		return nil, err
	}

	if req.Name != "" {
		card.Name = req.Name
	}
	if req.Face.Shape != "" {
		card.Face = req.Face
	}
	if req.Hair.Color != "" {
		card.Hair = req.Hair
	}
	if req.Body.Type != "" {
		card.Body = req.Body
	}
	if req.Palette.SkinTone != "" {
		card.Palette = req.Palette
	}
	if req.Persona.Personality != "" || req.Persona.VoiceStyle != "" || req.Persona.Backstory != "" {
		card.Persona = req.Persona
	}
	if req.Style.Constraints != "" || req.Style.NegativePrompt != "" {
		card.Style = req.Style
	}

	card.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	if err := s.save(card); err != nil {
		return nil, err
	}
	return card, nil
}

// DeleteCharacter 删除角色
func (s *CharacterService) DeleteCharacter(characterID string) error {
	path := s.cardPath(characterID)
	if err := os.Remove(path); err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("角色不存在: %s", characterID)
		}
		return err
	}
	// 清理参考图目录
	refDir := filepath.Join(s.storageDir, characterID)
	os.RemoveAll(refDir)
	return nil
}

// save 保存角色卡片到 JSON 文件
func (s *CharacterService) save(card *models.CharacterCard) error {
	data, err := json.MarshalIndent(card, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.cardPath(card.CharacterID), data, 0644)
}

// GetGenerationPrompt 获取角色生成提示词
func (s *CharacterService) GetGenerationPrompt(characterID, basePrompt string) (string, error) {
	card, err := s.GetCharacter(characterID)
	if err != nil {
		return "", err
	}

	// 构建风格提示词后缀
	styleParts := []string{}
	styleParts = append(styleParts, fmt.Sprintf("%s face", card.Face.Shape))
	styleParts = append(styleParts, fmt.Sprintf("%s %s eyes", card.Face.EyeShape, card.Face.EyeColor))
	styleParts = append(styleParts, fmt.Sprintf("%s %s hair", card.Hair.Color, card.Hair.Style))
	styleParts = append(styleParts, fmt.Sprintf("%s body", card.Body.Type))
	if card.Palette.SkinTone != "" {
		styleParts = append(styleParts, fmt.Sprintf("%s skin tone", card.Palette.SkinTone))
	}
	if card.Style.Constraints != "" {
		styleParts = append(styleParts, card.Style.Constraints)
	}
	styleParts = append(styleParts, fmt.Sprintf("character sheet of %s", card.Name))
	styleParts = append(styleParts, "consistent character design, same character, reference sheet")

	styleSuffix := ""
	for _, p := range styleParts {
		if p != "" {
			if styleSuffix != "" {
				styleSuffix += ", "
			}
			styleSuffix += p
		}
	}

	if basePrompt != "" {
		return basePrompt + ", " + styleSuffix, nil
	}
	return styleSuffix, nil
}

// GetNegativePrompt 获取角色负面提示词
func (s *CharacterService) GetNegativePrompt(characterID string) string {
	card, err := s.GetCharacter(characterID)
	if err != nil {
		return "different character, inconsistent design, mutated face, bad anatomy"
	}
	defaults := "different character, inconsistent design, multiple characters, mutated face, bad anatomy, wrong hair color, wrong eye color"
	if card.Style.NegativePrompt != "" {
		return card.Style.NegativePrompt + ", " + defaults
	}
	return defaults
}

// generateID 生成唯一 ID
func generateID() string {
	return fmt.Sprintf("char_%x", time.Now().UnixNano())
}
