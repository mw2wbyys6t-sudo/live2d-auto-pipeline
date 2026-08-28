package services

import (
	"testing"

	"live2d-api/config"
)

// TestValidateCharacterID 验证 :id 参数的路径穿越/注入防护。
func TestValidateCharacterID(t *testing.T) {
	bad := []string{
		"", "..", ".", "../etc", "a/b", "a\\b", "x/../y",
		"..\x00", "/abs/path", "x/y/z",
	}
	for _, id := range bad {
		if err := validateCharacterID(id); err == nil {
			t.Errorf("expected reject for id %q, got accept", id)
		}
	}
	good := []string{"abc123", "char_20260809_001", "deadbeef"}
	for _, id := range good {
		if err := validateCharacterID(id); err != nil {
			t.Errorf("expected accept for id %q, got %v", id, err)
		}
	}
}

// TestGetCharacter_RejectsTraversal 确保穿越 ID 不会到达文件系统。
func TestGetCharacter_RejectsTraversal(t *testing.T) {
	svc := NewCharacterService(&config.Config{
		Python:    config.PythonConfig{ScriptsDir: t.TempDir()},
		Character: config.CharacterConfig{StorageDir: t.TempDir() + "/chars"},
	})
	if _, err := svc.GetCharacter("../../../../etc/passwd"); err == nil {
		t.Error("expected error for traversal id, got nil")
	}
	if err := svc.DeleteCharacter("../../etc/passwd"); err == nil {
		t.Error("expected error for traversal delete id, got nil")
	}
}
