package services

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"live2d-api/config"
	"live2d-api/models"
)

func TestGenerateWithCharacterDoesNotReturnPlaceholderOnWorkflowFailure(t *testing.T) {
	cfg := &config.Config{Output: config.OutputConfig{BaseDir: t.TempDir()}}
	generator := NewImageGenerator(cfg)
	generator.runWorkflow = func(models.GenerateRequest) (*models.GenerateImageResponse, error) {
		return nil, ErrNoImageEngine
	}

	resp, err := generator.GenerateWithCharacter(models.GenerateRequest{Provider: "auto"})
	if resp != nil || !errors.Is(err, ErrNoImageEngine) {
		t.Fatalf("应返回无引擎错误而不是占位图，resp=%+v err=%v", resp, err)
	}
}

func TestGenerateImageDoesNotCallPlaceholderGenerator(t *testing.T) {
	generator := NewImageGenerator(&config.Config{Output: config.OutputConfig{BaseDir: t.TempDir()}})
	resp, err := generator.GenerateImage(models.GenerateImageRequest{Prompt: "portrait"})
	if resp != nil || err == nil {
		t.Fatalf("未配置真实生成流程时应返回错误，resp=%+v err=%v", resp, err)
	}
}

// 部分成功契约：立绘已经画出来、后续分层/绑定失败时，必须把真图交出去，
// 而不是让调用方回退本地占位图。这里钉住三条边界：
// 1) 声明 partial 且图片存在 → 返回真图 + incomplete；
// 2) 声明 partial 但图片不在盘上 → 不假装成功；
// 3) 没声明 partial → 仍按整条失败处理。
func TestPartialFailureResponse(t *testing.T) {
	outDir := t.TempDir()
	imagePath := filepath.Join(outDir, "character_123.png")
	if err := os.WriteFile(imagePath, []byte("png"), 0o644); err != nil {
		t.Fatalf("准备测试图片失败: %v", err)
	}
	cfg := &config.Config{Output: config.OutputConfig{BaseDir: outDir}}
	req := models.GenerateRequest{Seed: 7, Width: 1024, Height: 1536}

	t.Run("真图存在时返回半成功响应", func(t *testing.T) {
		py := &pythonWorkflowResult{
			Success:        false,
			Partial:        true,
			CharacterImage: imagePath,
			Error:          "DLL load failed while importing _fblas",
			ErrorState:     "layering",
		}
		resp := partialFailureResponse(cfg, req, py)
		if resp == nil {
			t.Fatal("声明 partial 且图片存在时必须返回响应，不能退回占位图路径")
		}
		if !resp.Success || !resp.Incomplete {
			t.Fatalf("应为 success=true + incomplete=true，实际 success=%v incomplete=%v",
				resp.Success, resp.Incomplete)
		}
		if resp.Fallback {
			t.Fatal("真图不能被标成占位图")
		}
		if got := resp.ImageURL; got != "/output/character_123.png" {
			t.Fatalf("图片 URL 必须由真实路径推导，实际 %q", got)
		}
		for _, want := range []string{"layering", "_fblas"} {
			if !strings.Contains(resp.IncompleteReason, want) {
				t.Fatalf("未完成原因应包含 %q，实际 %q", want, resp.IncompleteReason)
			}
		}
	})

	t.Run("只在 steps.generate 留痕也能找回图片", func(t *testing.T) {
		py := &pythonWorkflowResult{
			Partial: true,
			Error:   "boom",
			Steps: map[string]interface{}{
				"generate": map[string]interface{}{"path": "character_123.png"},
			},
		}
		resp := partialFailureResponse(cfg, req, py)
		if resp == nil || resp.ImagePath != imagePath {
			t.Fatalf("相对路径应拼到 output 根目录，实际 %+v", resp)
		}
	})

	t.Run("图片缺失时不假装成功", func(t *testing.T) {
		py := &pythonWorkflowResult{
			Partial:        true,
			CharacterImage: filepath.Join(outDir, "missing.png"),
			Error:          "boom",
		}
		if resp := partialFailureResponse(cfg, req, py); resp != nil {
			t.Fatalf("图片不存在时必须返回 nil，实际 %+v", resp)
		}
	})

	t.Run("没有 partial 标记仍按失败处理", func(t *testing.T) {
		py := &pythonWorkflowResult{CharacterImage: imagePath, Error: "boom"}
		if resp := partialFailureResponse(cfg, req, py); resp != nil {
			t.Fatalf("未声明 partial 时不应返回半成功响应，实际 %+v", resp)
		}
	})

	t.Run("整条成功时不打 incomplete", func(t *testing.T) {
		py := &pythonWorkflowResult{Success: true, Partial: true, CharacterImage: imagePath}
		if resp := partialFailureResponse(cfg, req, py); resp != nil {
			t.Fatalf("成功的响应不该走半成功分支，实际 %+v", resp)
		}
	})
}

// 工作流成功但没有落盘图片时，必须如实报错而不是返回一个必然 404 的 URL。
func TestWorkflowImagePathRejectsMissingFile(t *testing.T) {
	cfg := &config.Config{Output: config.OutputConfig{BaseDir: t.TempDir()}}
	py := &pythonWorkflowResult{CharacterImage: "not_there.png"}
	if _, ok := workflowImagePath(cfg, py); ok {
		t.Fatal("文件不存在时必须返回 ok=false")
	}
}

// 用户原文必须回传到响应：历史记录的 prompt 取自这里，缺了会让左栏把
// 作品显示成「未命名作品」，也看不出是哪句提示词生成的。
func TestApplyWorkflowPromptKeepsUserPrompt(t *testing.T) {
	resp := &models.GenerateImageResponse{}
	applyWorkflowPrompt(resp, &pythonWorkflowResult{
		Prompt: &workflowPrompt{
			User:      "粉色双马尾少女",
			Effective: "粉色双马尾少女",
			Sent:      "pink twin tails girl, anime style",
			Lang:      "zh",
		},
	})

	if resp.PromptUser != "粉色双马尾少女" {
		t.Fatalf("PromptUser 丢失，实际 %q", resp.PromptUser)
	}
	if resp.PromptSent == "" || resp.PromptLang != "zh" {
		t.Fatalf("提示词链路不完整: %+v", resp)
	}
}

// 响应里的尺寸必须取上游真实像素，不能拿请求尺寸冒充。
// 免密通道实测会把 1024×1536 降到 627×940；界面按请求尺寸展示会误导用户。
func TestBuildWorkflowResponseUsesActualPixels(t *testing.T) {
	cfg := &config.Config{Output: config.OutputConfig{BaseDir: t.TempDir()}}
	py := &pythonWorkflowResult{
		Steps: map[string]interface{}{
			"generate": map[string]interface{}{
				"width":           float64(627),
				"height":          float64(940),
				"size_downgraded": true,
			},
		},
	}

	resp := buildWorkflowResponse(cfg, models.GenerateRequest{Width: 1024, Height: 1536},
		py, filepath.Join(cfg.Output.BaseDir, "a.png"))

	if resp.Width != 627 || resp.Height != 940 {
		t.Fatalf("应使用真实像素 627×940，实际 %d×%d", resp.Width, resp.Height)
	}
	if !resp.SizeDowngraded {
		t.Fatal("上游降尺寸时必须置 size_downgraded")
	}
}

// 没有 generate 步骤记录时回退到请求尺寸，保证老数据也能出响应。
func TestBuildWorkflowResponseFallsBackToRequestedSize(t *testing.T) {
	cfg := &config.Config{Output: config.OutputConfig{BaseDir: t.TempDir()}}
	resp := buildWorkflowResponse(cfg, models.GenerateRequest{Width: 768, Height: 1152},
		&pythonWorkflowResult{}, filepath.Join(cfg.Output.BaseDir, "a.png"))

	if resp.Width != 768 || resp.Height != 1152 {
		t.Fatalf("缺记录时应回退请求尺寸，实际 %d×%d", resp.Width, resp.Height)
	}
	if resp.SizeDowngraded {
		t.Fatal("没有降尺寸证据时不得标记降尺寸")
	}
}
