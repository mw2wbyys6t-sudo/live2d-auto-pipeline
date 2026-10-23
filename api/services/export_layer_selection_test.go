package services

import (
	"strings"
	"testing"
)

// 生产导出路径的选层契约。
//
// 背景（实测，2026-10）：选层逻辑（剔整图预览 + 按 draw_index 排序）曾被内联
// 复制到 3 处，只有 1 处做了这两项。生产导出实际走 exportStagePythonCode
// （wsHub 恒非 nil → tracker 恒非 nil → ExportLive2DModelTracked），而这份副本
// 两项都没做 —— 于是「验收全绿（runtime_ready=true）但画面是一张静止的平图」
// 一直活着，preview/composite_preview 被当成图层烘进 moc3。
//
// 这些脚本以字符串嵌在 Go 里，无法 import 也无法被 Python 测试覆盖，所以
// 只能在这里对**脚本文本**做源码级断言：它必须调用共享的 layer_loader，
// 且不得再内联 glob 选层。

const sharedLoaderImport = "from live2d_builder.layer_loader import load_export_inputs"

func TestStageExportScriptUsesSharedLayerLoader(t *testing.T) {
	code := exportStagePythonCode("/root", "/root/layers", "/root/out", "char", "job-1")

	if !strings.Contains(code, sharedLoaderImport) {
		t.Fatalf("生产导出脚本没有调用共享的 layer_loader —— 副本会再次漂移\n脚本:\n%s", code)
	}
	if strings.Contains(code, `glob.glob(os.path.join(layers_dir, "*.png"))`) {
		t.Error("生产导出脚本又内联了 glob 选层：预览图会再次被当成图层")
	}
	if !strings.Contains(code, "inputs.paths.items()") {
		t.Error("生产导出脚本没有使用 layer_loader 排好序的结果")
	}
}

func TestBridgeExportScriptUsesSharedLayerLoader(t *testing.T) {
	if !strings.Contains(bridgeExportLive2DScript, sharedLoaderImport) {
		t.Fatalf("导出桥脚本没有调用共享的 layer_loader")
	}
	if strings.Contains(bridgeExportLive2DScript, `glob.glob(os.path.join(layers_dir, "*.png"))`) {
		t.Error("导出桥脚本又内联了 glob 选层")
	}
}

// 两份导出脚本必须回传同一组排障字段，否则前端/闸门会在两条路径上看到不同的
// 契约（历史上就因为两边字段不一致而漏掉了 runtime_ready）。
func TestBothExportScriptsReportSameDiagnosticFields(t *testing.T) {
	stage := exportStagePythonCode("/root", "/root/layers", "/root/out", "char", "job-1")
	fields := []string{
		"layers_used",
		"layer_order_source",
		"layers_unknown",
		"layer_order_issues",
	}
	for _, field := range fields {
		if !strings.Contains(stage, `summary["`+field+`"]`) {
			t.Errorf("生产导出脚本缺少排障字段 %q", field)
		}
		if !strings.Contains(bridgeExportLive2DScript, `summary["`+field+`"]`) {
			t.Errorf("导出桥脚本缺少排障字段 %q", field)
		}
	}
}
