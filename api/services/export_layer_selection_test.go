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

// 空心分层的拒绝必须在**两条**导出路径上都存在。
//
// 背景（实测，2026-10-30，已安装的 v0.10.10）：一张纯色无结构图分层出 4 个色块，
// layer_manifest.json 写着 preview_ready=false、缺 12 个必要部件，
// /api/export/live2d 却返回 runtime_ready=true 并产出 moc3 —— 用户拿到一个
// 没有眼嘴的"角色"，而界面上没有任何地方说这件事。
// 生产导出走的是阶段事件桥那份脚本，所以只给一份加闸门等于没加。
func TestBothExportScriptsRefuseHollowLayering(t *testing.T) {
	stage := exportStagePythonCode("/root", "/root/layers", "/root/out", "char", "job-1")
	for name, code := range map[string]string{
		"生产导出脚本": stage,
		"导出桥脚本":   bridgeExportLive2DScript,
	} {
		// 必须断言"闸门那一行"，不能只断言标识符出现过：
		// inputs.parts_hollow 同样出现在 build_meta 的落盘块里，只查子串的话
		// 把 if 改成 if False 测试照样绿（变异实测踩过）。
		if !strings.Contains(code, "if inputs.parts_hollow:") {
			t.Errorf("%s没有空心分层闸门（缺 `if inputs.parts_hollow:`）", name)
		}
		if !strings.Contains(code, "parts_blocker_message(inputs.missing_preview,") {
			t.Errorf("%s的拒绝理由没有把分层来源一起传进 parts_blocker_message（颜色聚类与上游没画出人，下一步不一样）", name)
		}
	}
}

// 部件结论必须落盘：runtime_ready 只证明内核肯加载，不证明是个有眼有嘴的角色。
// 落盘后下载闸门与前端读的是同一份事实，不用重新分层才能知道。
func TestBothExportScriptsRecordPartsVerdict(t *testing.T) {
	stage := exportStagePythonCode("/root", "/root/layers", "/root/out", "char", "job-1")
	for name, code := range map[string]string{
		"生产导出脚本": stage,
		"导出桥脚本":   bridgeExportLive2DScript,
	} {
		for _, key := range []string{`meta["parts"]`, `"missing_preview": list(inputs.missing_preview)`} {
			if !strings.Contains(code, key) {
				t.Errorf("%s没有把部件结论写进 build_meta.json（缺 %q）", name, key)
			}
		}
	}
}

