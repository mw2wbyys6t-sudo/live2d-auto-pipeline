package services

import (
	"errors"
	"strings"
	"testing"

	"live2d-api/models"
)

// 引擎参数校验必须在启动 Python 之前生效：放过去的话，Python argparse 会以
// 退出码 2 结束且不产出 JSON，最终被 GenerateWithCharacter 兜底成占位图，
// 用户看到的是"生成成功但图没变"。
func TestValidateEngineSelection(t *testing.T) {
	tests := []struct {
		name    string
		req     models.GenerateRequest
		wantErr bool
		// 命中错误时必须能 errors.Is 回原 sentinel，handler 靠它决定返回 400。
		wantMessage string
	}{
		{
			name: "全空保持旧行为",
			req:  models.GenerateRequest{},
		},
		{
			name: "auto 不传模型也合法",
			req:  models.GenerateRequest{Provider: "auto"},
		},
		{
			name: "comfyui 带底模与 LoRA",
			req: models.GenerateRequest{
				Provider: "comfyui",
				Model:    "illustriousXL_v01.safetensors",
				Lora:     "char_style_v1.safetensors",
			},
		},
		{
			name: "sd_webui 只带底模",
			req:  models.GenerateRequest{Provider: "sd_webui", Model: "anything-v5"},
		},
		{
			name:        "未知引擎",
			req:         models.GenerateRequest{Provider: "comfy_ui"},
			wantErr:     true,
			wantMessage: "未知的图像引擎",
		},
		{
			name:        "模型没有指定引擎",
			req:         models.GenerateRequest{Model: "illustriousXL_v01.safetensors"},
			wantErr:     true,
			wantMessage: "必须同时指定引擎",
		},
		{
			name:        "云端引擎收到本地模型",
			req:         models.GenerateRequest{Provider: "openai", Model: "illustriousXL_v01.safetensors"},
			wantErr:     true,
			wantMessage: "不支持指定模型",
		},
		{
			name:        "auto 收到 LoRA",
			req:         models.GenerateRequest{Provider: "auto", Lora: "char_style_v1"},
			wantErr:     true,
			wantMessage: "不支持指定模型",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateEngineSelection(tt.req)
			if !tt.wantErr {
				if err != nil {
					t.Fatalf("不应报错，实际: %v", err)
				}
				return
			}
			if err == nil {
				t.Fatal("应当报错，实际通过了校验")
			}
			if !errors.Is(err, ErrInvalidEngineSelection) {
				t.Fatalf("必须能被 errors.Is 命中 sentinel，实际: %v", err)
			}
			if !strings.Contains(err.Error(), tt.wantMessage) {
				t.Fatalf("错误文案缺少关键信息 %q：%v", tt.wantMessage, err)
			}
		})
	}
}

// GenerateWithCharacter 不能在参数错时回退占位图：它必须在调用 Python 之前
// 就把错误交出去（这里通过"脚本目录不存在也不会被走到"来间接验证顺序）。
func TestGenerateWithCharacterRejectsBadEngineBeforeRunningPython(t *testing.T) {
	g := &ImageGenerator{}
	_, err := g.GenerateWithCharacter(models.GenerateRequest{
		Prompt:   "1girl",
		Provider: "not_a_real_engine",
	})
	if !errors.Is(err, ErrInvalidEngineSelection) {
		t.Fatalf("期望参数校验先失败，实际: %v", err)
	}
}
