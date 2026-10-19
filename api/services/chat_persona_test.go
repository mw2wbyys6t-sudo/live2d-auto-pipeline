package services

import (
	"testing"

	"live2d-api/config"
	"live2d-api/models"
)

func TestBuildMessagesUsesPersonaSystemPrompt(t *testing.T) {
	svc := NewChatService(&config.Config{})
	persona := "只用一句话回答，语气冷淡。"
	req := models.ChatRequest{
		SystemPrompt: persona,
		Message:      "你好",
		History: []models.ChatMessage{
			{Role: "user", Content: "在吗"},
			{Role: "assistant", Content: "在"},
		},
	}

	msgs := svc.buildMessages(req)

	if len(msgs) != 4 {
		t.Fatalf("消息数 = %d，应为 4（system + 2 条历史 + 当前）", len(msgs))
	}
	if msgs[0].Role != "system" || msgs[0].Content != persona {
		t.Fatalf("首条应为用户人设 system 消息，实得 %+v", msgs[0])
	}
	if msgs[len(msgs)-1].Content != "你好" {
		t.Fatalf("末条应为当前用户消息，实得 %+v", msgs[len(msgs)-1])
	}
}

func TestBuildMessagesFallsBackToDefaultPersona(t *testing.T) {
	svc := NewChatService(&config.Config{})

	msgs := svc.buildMessages(models.ChatRequest{Message: "hi"})

	if len(msgs) != 2 {
		t.Fatalf("消息数 = %d，应为 2（system + 当前）", len(msgs))
	}
	if msgs[0].Role != "system" || msgs[0].Content == "" {
		t.Fatalf("未提供人设时应使用默认 system 提示，实得 %+v", msgs[0])
	}
}
