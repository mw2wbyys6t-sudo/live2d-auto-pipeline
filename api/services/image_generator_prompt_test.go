package services

import (
	"encoding/json"
	"testing"

	"live2d-api/models"
)

const workflowPromptJSON = `{
  "success": true,
  "character_image": "output/character_1.png",
  "layers_dir": "output/layers_1",
  "prompt": {
    "user": "正面全身立绘，粉色双马尾",
    "effective": "正面全身立绘，粉色双马尾，oval face",
    "sent": "正面全身立绘，粉色双马尾，oval face，动漫风格，高质量",
    "negative": "低分辨率，画面模糊",
    "provider": "seedream",
    "lang": "zh",
    "mismatch": false
  }
}`

func TestWorkflowPromptUnmarshalsChinese(t *testing.T) {
	var py pythonWorkflowResult
	if err := json.Unmarshal([]byte(workflowPromptJSON), &py); err != nil {
		t.Fatalf("解析工作流结果失败: %v", err)
	}
	if py.Prompt == nil {
		t.Fatal("prompt 记录没有被解析（json 标签与 Python 侧不一致？）")
	}
	if py.Prompt.User != "正面全身立绘，粉色双马尾" {
		t.Fatalf("user = %q", py.Prompt.User)
	}
	if py.Prompt.Sent != "正面全身立绘，粉色双马尾，oval face，动漫风格，高质量" {
		t.Fatalf("sent = %q", py.Prompt.Sent)
	}
	if py.Prompt.Lang != "zh" {
		t.Fatalf("lang = %q", py.Prompt.Lang)
	}
}

func TestApplyWorkflowPromptMapsResponse(t *testing.T) {
	var py pythonWorkflowResult
	if err := json.Unmarshal([]byte(workflowPromptJSON), &py); err != nil {
		t.Fatalf("解析工作流结果失败: %v", err)
	}
	resp := &models.GenerateImageResponse{}
	applyWorkflowPrompt(resp, &py)

	if resp.EffectivePrompt != py.Prompt.Effective {
		t.Fatalf("effective_prompt = %q", resp.EffectivePrompt)
	}
	if resp.PromptSent != py.Prompt.Sent {
		t.Fatalf("prompt_sent = %q", resp.PromptSent)
	}
	if resp.PromptLang != "zh" || resp.PromptMismatch {
		t.Fatalf("lang/mismatch = %q/%v", resp.PromptLang, resp.PromptMismatch)
	}
}

func TestApplyWorkflowPromptWithoutRecordIsNoop(t *testing.T) {
	resp := &models.GenerateImageResponse{EffectivePrompt: "保留"}
	applyWorkflowPrompt(resp, &pythonWorkflowResult{})
	applyWorkflowPrompt(resp, nil)
	if resp.EffectivePrompt != "保留" {
		t.Fatalf("缺 prompt 记录时不应清空既有值: %q", resp.EffectivePrompt)
	}
	if resp.PromptSent != "" {
		t.Fatal("缺 prompt 记录时 prompt_sent 应为空")
	}
}

func TestApplyWorkflowPromptCarriesMismatch(t *testing.T) {
	py := &pythonWorkflowResult{
		Prompt: &workflowPrompt{Sent: "中文 prompt, masterpiece", Lang: "zh", Mismatch: true},
	}
	resp := &models.GenerateImageResponse{}
	applyWorkflowPrompt(resp, py)
	if !resp.PromptMismatch {
		t.Fatal("中文提示词配英文标签时必须透出 prompt_mismatch")
	}
}
